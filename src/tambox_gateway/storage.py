from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from .models import SessionConfig


STATE_FORMAT_VERSION = 1


class StateStore(Protocol):
    def load(self, session_id: str, config_fingerprint: str) -> dict[str, Any] | None: ...

    def save(
        self,
        session_id: str,
        config_fingerprint: str,
        revision: int,
        state: dict[str, Any],
    ) -> None: ...


class StateStoreError(RuntimeError):
    pass


class ConfigurationMismatchError(StateStoreError):
    pass


class CorruptStateError(StateStoreError):
    pass


def session_config_fingerprint(config: SessionConfig) -> str:
    canonical = json.dumps(
        asdict(config),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class SQLiteStateStore:
    """Single-row, transaction-safe state store for one or more sessions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=10000")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS engine_state (
                session_id TEXT PRIMARY KEY,
                config_fingerprint TEXT NOT NULL,
                revision INTEGER NOT NULL,
                state_format_version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def load(self, session_id: str, config_fingerprint: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT config_fingerprint, revision, state_format_version, payload_json
                FROM engine_state
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()

        if row is None:
            return None
        stored_fingerprint, revision, format_version, payload_json = row
        if stored_fingerprint != config_fingerprint:
            raise ConfigurationMismatchError(
                "The persisted run uses another published configuration"
            )
        if format_version != STATE_FORMAT_VERSION:
            raise CorruptStateError(f"Unsupported state format {format_version}")
        try:
            state = json.loads(payload_json)
        except json.JSONDecodeError as error:
            raise CorruptStateError("Persisted state is not valid JSON") from error
        if not isinstance(state, dict) or state.get("revision") != revision:
            raise CorruptStateError("Persisted revision does not match its payload")
        return state

    def save(
        self,
        session_id: str,
        config_fingerprint: str,
        revision: int,
        state: dict[str, Any],
    ) -> None:
        payload = json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    """
                    INSERT INTO engine_state (
                        session_id,
                        config_fingerprint,
                        revision,
                        state_format_version,
                        payload_json,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(session_id) DO UPDATE SET
                        config_fingerprint = excluded.config_fingerprint,
                        revision = excluded.revision,
                        state_format_version = excluded.state_format_version,
                        payload_json = excluded.payload_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        session_id,
                        config_fingerprint,
                        revision,
                        STATE_FORMAT_VERSION,
                        payload,
                    ),
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()
