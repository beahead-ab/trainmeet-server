"""Dedicated SQLite command journal. Stores domain IDs and outcomes, never sessions."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from threading import RLock

from .framework import Command, CommandResult, IntegrationError, Outcome, Scope


def _key(scope: Scope, command_id: str) -> tuple[str, ...]:
    return (scope.meet_id, scope.provider_id, scope.account_id, scope.station_id, command_id)


class SqliteCommandJournal:
    """One handle per service; independent handles/processes can share a file.

INSERT OR IGNORE atomically reserves a command BEFORE transport execution. A
reservation has unknown outcome, including after process death. It is never
automatically reclaimed for sending. FULL synchronous commits precede writes.
Use a dedicated local database file, not a network filesystem or the runtime DB.
"""

    def __init__(self, path: str | Path = ":memory:"):
        self._lock = RLock()
        self._db = sqlite3.connect(str(path), timeout=5, isolation_level=None,
                                   check_same_thread=False)
        self._db.execute("PRAGMA synchronous = FULL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS integration_commands (
                meet_id TEXT NOT NULL, provider_id TEXT NOT NULL,
                account_id TEXT NOT NULL, station_id TEXT NOT NULL,
                command_id TEXT NOT NULL, train_id TEXT NOT NULL, action TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK(outcome IN ('unknown','pending','confirmed')),
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                PRIMARY KEY (meet_id, provider_id, account_id, station_id, command_id)
            )
        """)

    @contextmanager
    def _transaction(self):
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                yield
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def _get(self, scope: Scope, command_id: str) -> tuple[Command, CommandResult] | None:
        row = self._db.execute("""
            SELECT train_id, action, outcome FROM integration_commands
            WHERE meet_id=? AND provider_id=? AND account_id=? AND station_id=? AND command_id=?
        """, _key(scope, command_id)).fetchone()
        if row is None:
            return None
        return Command(command_id, row[0], row[1]), CommandResult(command_id, Outcome(row[2]))

    def get(self, scope: Scope, command_id: str) -> tuple[Command, CommandResult] | None:
        with self._lock:
            return self._get(scope, command_id)

    def unresolved(self, scope: Scope) -> tuple[Command, ...]:
        with self._lock:
            rows = self._db.execute("""
                SELECT command_id, train_id, action FROM integration_commands
                WHERE meet_id=? AND provider_id=? AND account_id=? AND station_id=?
                    AND outcome != 'confirmed'
                ORDER BY created_at, command_id
            """, _key(scope, "")[:4]).fetchall()
            return tuple(Command(*row) for row in rows)

    def claim(self, scope: Scope, command: Command) -> tuple[bool, CommandResult]:
        with self._transaction():
            inserted = self._db.execute("""
                INSERT OR IGNORE INTO integration_commands
                    (meet_id,provider_id,account_id,station_id,command_id,train_id,action,outcome)
                VALUES (?,?,?,?,?,?,?,'unknown')
            """, (*_key(scope, command.command_id), command.train_id, command.action)).rowcount == 1
            previous = self._get(scope, command.command_id)
            if previous is None or previous[0] != command:
                raise IntegrationError("Command ID already used for different content")
            return inserted, previous[1]

    def record(self, scope: Scope, command: Command, outcome: Outcome) -> CommandResult:
        with self._transaction():
            previous = self._get(scope, command.command_id)
            if previous is None or previous[0] != command:
                raise IntegrationError("Command reservation does not match")
            # Concurrent readers/writers must not downgrade an established result.
            rank = {Outcome.UNKNOWN: 0, Outcome.PENDING: 1, Outcome.CONFIRMED: 2}
            outcome = max((previous[1].outcome, outcome), key=rank.__getitem__)
            self._db.execute("""
                UPDATE integration_commands SET outcome=?,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE meet_id=? AND provider_id=? AND account_id=? AND station_id=? AND command_id=?
            """, (outcome.value, *_key(scope, command.command_id)))
            return CommandResult(command.command_id, outcome)

    def close(self) -> None:
        with self._lock:
            self._db.close()
