from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .runtime import AVAILABLE_CLOCK_STYLES, RuntimePublication


class SQLiteOperationsStore:
    """Persistent local clock and last-known train positions for display clients."""

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
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runtime_clock (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                publication_id TEXT NOT NULL,
                base_seconds REAL NOT NULL,
                base_recorded_at TEXT NOT NULL,
                speed REAL NOT NULL CHECK(speed > 0),
                running INTEGER NOT NULL CHECK(running IN (0, 1)),
                stopped_reason TEXT,
                show_seconds INTEGER NOT NULL CHECK(show_seconds IN (0, 1)),
                available_styles_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS train_positions (
                train_number TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK(status IN ('station', 'connection')),
                station_id TEXT,
                connection_id TEXT,
                from_station_id TEXT,
                to_station_id TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )

    def ensure_publication(self, publication: RuntimePublication) -> None:
        with self._lock:
            row = self._connection.execute(
                "SELECT publication_id FROM runtime_clock WHERE singleton = 1"
            ).fetchone()
            if row is not None and str(row[0]) == publication.publication_id:
                return
            clock = publication.payload.get("clock", {})
            start_time = str(
                clock.get("start_time")
                or publication.payload.get("meet", {}).get("clock_time")
                or "12:00"
            )
            base_seconds = _time_to_seconds(start_time)
            speed = max(float(clock.get("speed", 1)), 0.01)
            show_seconds = bool(clock.get("show_seconds", True))
            styles = clock.get("available_styles") or list(AVAILABLE_CLOCK_STYLES)
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO runtime_clock(
                        singleton, publication_id, base_seconds, base_recorded_at,
                        speed, running, stopped_reason, show_seconds, available_styles_json
                    ) VALUES (1, ?, ?, ?, ?, 0, NULL, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        publication_id = excluded.publication_id,
                        base_seconds = excluded.base_seconds,
                        base_recorded_at = excluded.base_recorded_at,
                        speed = excluded.speed,
                        running = 0,
                        stopped_reason = NULL,
                        show_seconds = excluded.show_seconds,
                        available_styles_json = excluded.available_styles_json
                    """,
                    (
                        publication.publication_id,
                        base_seconds,
                        _now_iso(),
                        speed,
                        1 if show_seconds else 0,
                        json.dumps(styles, ensure_ascii=False),
                    ),
                )
                self._connection.execute("DELETE FROM train_positions")
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def clock_status(self, *, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(timezone.utc)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT publication_id, base_seconds, base_recorded_at, speed,
                       running, stopped_reason, show_seconds, available_styles_json
                FROM runtime_clock WHERE singleton = 1
                """
            ).fetchone()
        if row is None:
            return {
                "configured": False,
                "time": "12:00:00",
                "speed": 1,
                "running": False,
                "stopped_reason": None,
                "show_seconds": True,
                "available_styles": list(AVAILABLE_CLOCK_STYLES),
            }
        seconds = float(row[1])
        if bool(row[4]):
            recorded_at = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
            seconds += max((moment - recorded_at).total_seconds(), 0) * float(row[3])
        return {
            "configured": True,
            "publication_id": str(row[0]),
            "time": _seconds_to_time(seconds),
            "speed": float(row[3]),
            "running": bool(row[4]),
            "stopped_reason": row[5],
            "show_seconds": bool(row[6]),
            "available_styles": json.loads(row[7]),
        }

    def start_clock(
        self,
        *,
        time_value: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or datetime.now(timezone.utc)
        status = self.clock_status(now=moment)
        base_seconds = _time_to_seconds(time_value) if time_value else _time_to_seconds(status["time"])
        with self._lock:
            self._connection.execute(
                """
                UPDATE runtime_clock
                SET base_seconds = ?, base_recorded_at = ?, running = 1, stopped_reason = NULL
                WHERE singleton = 1
                """,
                (base_seconds, _datetime_iso(moment)),
            )
        return self.clock_status(now=moment)

    def stop_clock(self, reason: str | None = None) -> dict[str, Any]:
        status = self.clock_status()
        with self._lock:
            self._connection.execute(
                """
                UPDATE runtime_clock
                SET base_seconds = ?, base_recorded_at = ?, running = 0, stopped_reason = ?
                WHERE singleton = 1
                """,
                (_time_to_seconds(status["time"]), _now_iso(), reason or None),
            )
        return self.clock_status()

    def set_speed(self, speed: float) -> dict[str, Any]:
        if speed <= 0:
            raise ValueError("Klockhastigheten måste vara större än noll")
        status = self.clock_status()
        with self._lock:
            self._connection.execute(
                """
                UPDATE runtime_clock
                SET base_seconds = ?, base_recorded_at = ?, speed = ?
                WHERE singleton = 1
                """,
                (_time_to_seconds(status["time"]), _now_iso(), speed),
            )
        return self.clock_status()

    def record_engine_transition(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        _recorded_at: datetime,
    ) -> None:
        before_connections = before.get("connections", {})
        after_connections = after.get("connections", {})
        for connection_id, current in after_connections.items():
            previous = before_connections.get(connection_id, {})
            if current == previous:
                continue
            current_state = current.get("state")
            previous_state = previous.get("state")
            if current_state in {"requested", "reserved"} and current.get("train_number"):
                self._upsert_position(
                    str(current["train_number"]),
                    status="station",
                    station_id=current.get("from_station_id"),
                )
            elif current_state == "occupied" and current.get("train_number"):
                self._upsert_position(
                    str(current["train_number"]),
                    status="connection",
                    connection_id=connection_id,
                    from_station_id=current.get("from_station_id"),
                    to_station_id=current.get("to_station_id"),
                )
            elif previous_state == "occupied" and current_state == "free" and previous.get("train_number"):
                self._upsert_position(
                    str(previous["train_number"]),
                    status="station",
                    station_id=previous.get("to_station_id"),
                )

    def positions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT train_number, status, station_id, connection_id,
                       from_station_id, to_station_id, updated_at
                FROM train_positions ORDER BY train_number
                """
            ).fetchall()
        return [
            {
                "train_number": row[0],
                "status": row[1],
                "station_id": row[2],
                "connection_id": row[3],
                "from_station_id": row[4],
                "to_station_id": row[5],
                "updated_at": row[6],
            }
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _upsert_position(
        self,
        train_number: str,
        *,
        status: str,
        station_id: str | None = None,
        connection_id: str | None = None,
        from_station_id: str | None = None,
        to_station_id: str | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO train_positions(
                    train_number, status, station_id, connection_id,
                    from_station_id, to_station_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(train_number) DO UPDATE SET
                    status = excluded.status,
                    station_id = excluded.station_id,
                    connection_id = excluded.connection_id,
                    from_station_id = excluded.from_station_id,
                    to_station_id = excluded.to_station_id,
                    updated_at = excluded.updated_at
                """,
                (
                    train_number,
                    status,
                    station_id,
                    connection_id,
                    from_station_id,
                    to_station_id,
                    _now_iso(),
                ),
            )


def _time_to_seconds(value: str | None) -> float:
    parts = str(value or "12:00").split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(float(parts[2])) if len(parts) > 2 else 0
    except (TypeError, ValueError) as error:
        raise ValueError("Klocktiden ska anges som TT:MM eller TT:MM:SS") from error
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        raise ValueError("Klocktiden ska anges som TT:MM eller TT:MM:SS")
    return float(hour * 3600 + minute * 60 + second)


def _seconds_to_time(value: float) -> str:
    seconds = int(value) % (24 * 60 * 60)
    hour, remainder = divmod(seconds, 3600)
    minute, second = divmod(remainder, 60)
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _now_iso() -> str:
    return _datetime_iso(datetime.now(timezone.utc))


def _datetime_iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
