from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

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
            CREATE TABLE IF NOT EXISTS tkl_shifts (
                shift_id TEXT PRIMARY KEY,
                publication_id TEXT NOT NULL,
                active_day TEXT NOT NULL,
                station_id TEXT NOT NULL,
                operator_name TEXT NOT NULL,
                terminal_name TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('active', 'handover', 'closed')),
                started_at TEXT NOT NULL,
                ended_at TEXT,
                handover_note TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_tkl_shift_per_station
                ON tkl_shifts(publication_id, active_day, station_id)
                WHERE status = 'active';
            CREATE TABLE IF NOT EXISTS tkl_movement_states (
                publication_id TEXT NOT NULL,
                active_day TEXT NOT NULL,
                movement_id TEXT NOT NULL,
                station_id TEXT NOT NULL,
                arrival_status TEXT NOT NULL CHECK(arrival_status IN ('none', 'approaching', 'arrived')),
                departure_status TEXT NOT NULL CHECK(departure_status IN ('none', 'positioned', 'ready', 'departed')),
                actual_track TEXT,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(publication_id, active_day, movement_id)
            );
            CREATE TABLE IF NOT EXISTS tkl_events (
                event_id TEXT PRIMARY KEY,
                publication_id TEXT NOT NULL,
                active_day TEXT NOT NULL,
                station_id TEXT NOT NULL,
                shift_id TEXT,
                movement_id TEXT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS train_readiness (
                publication_id TEXT NOT NULL,
                active_day TEXT NOT NULL,
                movement_id TEXT NOT NULL,
                station_id TEXT NOT NULL,
                operating_point_id TEXT,
                status TEXT NOT NULL CHECK(status IN ('ready', 'acknowledged', 'revoked')),
                prepared_by_role TEXT NOT NULL CHECK(prepared_by_role IN ('tkl', 'ranger')),
                prepared_by TEXT NOT NULL,
                prepared_at TEXT NOT NULL,
                acknowledged_by TEXT,
                acknowledged_at TEXT,
                revoked_by TEXT,
                revoked_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(publication_id, active_day, movement_id)
            );
            CREATE INDEX IF NOT EXISTS train_readiness_for_station
                ON train_readiness(publication_id, active_day, station_id, status);
            CREATE TABLE IF NOT EXISTS clearances (
                clearance_id TEXT PRIMARY KEY,
                publication_id TEXT NOT NULL,
                active_day TEXT NOT NULL,
                movement_id TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                from_station_id TEXT NOT NULL,
                to_station_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'waiting', 'approved', 'rejected', 'cancelled', 'expired',
                    'invalidated_by_revision'
                )),
                requested_by TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                responded_by TEXT,
                responded_at TEXT,
                revision INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS clearances_by_channel
                ON clearances(publication_id, active_day, channel_id, status);
            CREATE INDEX IF NOT EXISTS clearances_by_movement
                ON clearances(publication_id, active_day, movement_id);
            CREATE TABLE IF NOT EXISTS line_messages (
                message_id TEXT PRIMARY KEY,
                publication_id TEXT NOT NULL,
                active_day TEXT NOT NULL,
                movement_id TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                from_station_id TEXT NOT NULL,
                to_station_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'delivered_to_device', 'display_acknowledged'
                )),
                sent_by TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                acknowledged_at TEXT,
                revision INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS line_messages_by_station
                ON line_messages(publication_id, active_day, to_station_id, status);
            """
        )
        movement_columns = {
            row[1]
            for row in self._connection.execute("PRAGMA table_info(tkl_movement_states)").fetchall()
        }
        if "crew_ready" not in movement_columns:
            # TKL's own "lokförare på plats" declaration (protocol v2, decision
            # B2). Deliberately a separate column from the ranger-facing
            # train_readiness table above, not merged into it — TKL owns this
            # end-to-end in TMBox, rangers get their own future panel/flow.
            self._connection.execute(
                "ALTER TABLE tkl_movement_states ADD COLUMN crew_ready INTEGER NOT NULL DEFAULT 0 CHECK(crew_ready IN (0, 1))"
            )
        if "revision" not in movement_columns:
            # Per-movement revision (protocol v2 decision B4), independent of
            # any other movement's or station's revision.
            self._connection.execute(
                "ALTER TABLE tkl_movement_states ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )
        if "assigned_track_id" not in movement_columns:
            # TKL's chosen track for today's run (monsterprompt §9.3), distinct
            # from actual_track (where the train really ended up). Validated
            # against the runtime's track catalog by the caller, not here.
            self._connection.execute(
                "ALTER TABLE tkl_movement_states ADD COLUMN assigned_track_id TEXT"
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

    def tkl_station_state(
        self,
        publication_id: str,
        active_day: str,
        station_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            shift_row = self._connection.execute(
                """
                SELECT shift_id, operator_name, terminal_name, status, started_at,
                       ended_at, handover_note, updated_at
                FROM tkl_shifts
                WHERE publication_id = ? AND active_day = ? AND station_id = ?
                  AND status = 'active'
                LIMIT 1
                """,
                (publication_id, active_day, station_id),
            ).fetchone()
            previous_shift_row = self._connection.execute(
                """
                SELECT shift_id, operator_name, terminal_name, status, started_at,
                       ended_at, handover_note, updated_at
                FROM tkl_shifts
                WHERE publication_id = ? AND active_day = ? AND station_id = ?
                  AND status != 'active'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (publication_id, active_day, station_id),
            ).fetchone()
            movement_rows = self._connection.execute(
                """
                SELECT movement_id, arrival_status, departure_status, actual_track,
                       crew_ready, revision, updated_by, updated_at, assigned_track_id
                FROM tkl_movement_states
                WHERE publication_id = ? AND active_day = ? AND station_id = ?
                ORDER BY updated_at
                """,
                (publication_id, active_day, station_id),
            ).fetchall()
        return {
            "shift": _shift_from_row(shift_row),
            "previous_shift": _shift_from_row(previous_shift_row),
            "movements": {
                row[0]: {
                    "arrival": row[1],
                    "departure": row[2],
                    "actualTrack": row[3],
                    "crewReady": bool(row[4]),
                    "revision": row[5],
                    "updated_by": row[6],
                    "updated_at": row[7],
                    "assignedTrackId": row[8],
                }
                for row in movement_rows
            },
        }

    def start_tkl_shift(
        self,
        publication_id: str,
        active_day: str,
        station_id: str,
        operator_name: str,
        terminal_name: str,
        *,
        take_over: bool = False,
    ) -> dict[str, Any]:
        operator_name = operator_name.strip()
        terminal_name = terminal_name.strip()
        if not operator_name or len(operator_name) > 80:
            raise ValueError("Operatörens namn måste anges")
        if not terminal_name or len(terminal_name) > 80:
            raise ValueError("Terminalens namn måste anges")
        now = _now_iso()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._connection.execute(
                    """
                    SELECT shift_id, operator_name, terminal_name, status, started_at,
                           ended_at, handover_note, updated_at
                    FROM tkl_shifts
                    WHERE publication_id = ? AND active_day = ? AND station_id = ?
                      AND status = 'active'
                    LIMIT 1
                    """,
                    (publication_id, active_day, station_id),
                ).fetchone()
                if current is not None and not take_over:
                    self._connection.execute("COMMIT")
                    return _shift_from_row(current) or {}
                if current is not None:
                    self._connection.execute(
                        """
                        UPDATE tkl_shifts
                        SET status = 'handover', ended_at = ?, updated_at = ?
                        WHERE shift_id = ?
                        """,
                        (now, now, current[0]),
                    )
                shift_id = str(uuid4())
                self._connection.execute(
                    """
                    INSERT INTO tkl_shifts(
                        shift_id, publication_id, active_day, station_id,
                        operator_name, terminal_name, status, started_at,
                        ended_at, handover_note, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, NULL, NULL, ?)
                    """,
                    (
                        shift_id,
                        publication_id,
                        active_day,
                        station_id,
                        operator_name,
                        terminal_name,
                        now,
                        now,
                    ),
                )
                self._insert_tkl_event_locked(
                    publication_id,
                    active_day,
                    station_id,
                    "shift_started" if current is None else "shift_taken_over",
                    {"operator_name": operator_name, "terminal_name": terminal_name},
                    shift_id=shift_id,
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return self.tkl_station_state(publication_id, active_day, station_id)["shift"]

    def finish_tkl_shift(
        self,
        shift_id: str,
        *,
        status: str,
        note: str = "",
    ) -> dict[str, Any]:
        if status not in {"handover", "closed"}:
            raise ValueError("Trafikpasset måste lämnas över eller avslutas")
        note = note.strip()
        if len(note) > 1000:
            raise ValueError("Överlämningsanteckningen är för lång")
        now = _now_iso()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT publication_id, active_day, station_id
                    FROM tkl_shifts WHERE shift_id = ? AND status = 'active'
                    """,
                    (shift_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("Trafikpasset är inte längre aktivt")
                self._connection.execute(
                    """
                    UPDATE tkl_shifts
                    SET status = ?, ended_at = ?, handover_note = ?, updated_at = ?
                    WHERE shift_id = ?
                    """,
                    (status, now, note or None, now, shift_id),
                )
                self._insert_tkl_event_locked(
                    row[0],
                    row[1],
                    row[2],
                    "shift_handed_over" if status == "handover" else "shift_closed",
                    {"note": note},
                    shift_id=shift_id,
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return {"shift_id": shift_id, "status": status, "ended_at": now, "handover_note": note or None}

    def update_tkl_movement(
        self,
        publication_id: str,
        active_day: str,
        station_id: str,
        movement_id: str,
        *,
        arrival: str,
        departure: str,
        actual_track: str | None,
        updated_by: str,
        shift_id: str | None,
        event_type: str,
    ) -> dict[str, Any]:
        if arrival not in {"none", "approaching", "arrived"}:
            raise ValueError("Ogiltigt ankomstläge")
        if departure not in {"none", "positioned", "ready", "departed"}:
            raise ValueError("Ogiltigt avgångsläge")
        track = (actual_track or "").strip() or None
        now = _now_iso()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO tkl_movement_states(
                        publication_id, active_day, movement_id, station_id,
                        arrival_status, departure_status, actual_track,
                        updated_by, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(publication_id, active_day, movement_id) DO UPDATE SET
                        station_id = excluded.station_id,
                        arrival_status = excluded.arrival_status,
                        departure_status = excluded.departure_status,
                        actual_track = excluded.actual_track,
                        revision = tkl_movement_states.revision + 1,
                        updated_by = excluded.updated_by,
                        updated_at = excluded.updated_at
                    """,
                    (
                        publication_id,
                        active_day,
                        movement_id,
                        station_id,
                        arrival,
                        departure,
                        track,
                        updated_by,
                        now,
                    ),
                )
                revision = self._connection.execute(
                    """
                    SELECT revision FROM tkl_movement_states
                    WHERE publication_id = ? AND active_day = ? AND movement_id = ?
                    """,
                    (publication_id, active_day, movement_id),
                ).fetchone()[0]
                self._insert_tkl_event_locked(
                    publication_id,
                    active_day,
                    station_id,
                    event_type,
                    {
                        "arrival": arrival,
                        "departure": departure,
                        "actual_track": track,
                        "updated_by": updated_by,
                    },
                    shift_id=shift_id,
                    movement_id=movement_id,
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return {
            "movement_id": movement_id,
            "arrival": arrival,
            "departure": departure,
            "actualTrack": track,
            "revision": revision,
            "updated_by": updated_by,
            "updated_at": now,
        }

    def set_crew_ready(
        self,
        publication_id: str,
        active_day: str,
        station_id: str,
        movement_id: str,
        *,
        crew_ready: bool,
        updated_by: str,
        shift_id: str | None,
        event_type: str = "crew_ready_set",
    ) -> dict[str, Any]:
        """TKL's own 'lokförare på plats' declaration (protocol v2, decision B2).

        Kept separate from update_tkl_movement so a caller never has to fetch
        and re-supply arrival/departure just to touch this field, and from
        train_readiness (the ranger-facing table) so the two flows can never
        collide.
        """
        now = _now_iso()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO tkl_movement_states(
                        publication_id, active_day, movement_id, station_id,
                        arrival_status, departure_status, actual_track,
                        crew_ready, updated_by, updated_at
                    ) VALUES (?, ?, ?, ?, 'none', 'none', NULL, ?, ?, ?)
                    ON CONFLICT(publication_id, active_day, movement_id) DO UPDATE SET
                        station_id = excluded.station_id,
                        crew_ready = excluded.crew_ready,
                        revision = tkl_movement_states.revision + 1,
                        updated_by = excluded.updated_by,
                        updated_at = excluded.updated_at
                    """,
                    (
                        publication_id,
                        active_day,
                        movement_id,
                        station_id,
                        1 if crew_ready else 0,
                        updated_by,
                        now,
                    ),
                )
                revision = self._connection.execute(
                    """
                    SELECT revision FROM tkl_movement_states
                    WHERE publication_id = ? AND active_day = ? AND movement_id = ?
                    """,
                    (publication_id, active_day, movement_id),
                ).fetchone()[0]
                self._insert_tkl_event_locked(
                    publication_id,
                    active_day,
                    station_id,
                    event_type,
                    {"crew_ready": crew_ready, "updated_by": updated_by},
                    shift_id=shift_id,
                    movement_id=movement_id,
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return {
            "movement_id": movement_id,
            # crewReady, not crew_ready: must match tkl_station_state()'s
            # movement dict shape (actualTrack/crewReady) so a caller can
            # merge a mutation response straight into a read snapshot.
            "crewReady": crew_ready,
            "revision": revision,
            "updated_by": updated_by,
            "updated_at": now,
        }

    def assign_track(
        self,
        publication_id: str,
        active_day: str,
        station_id: str,
        movement_id: str,
        track_id: str,
        *,
        updated_by: str,
        shift_id: str | None,
        event_type: str = "track_assigned",
    ) -> dict[str, Any]:
        """TKL's chosen track for today's run (monsterprompt §9.2/§9.3).

        Rejects a track another not-yet-departed movement at the same
        station/day already has assigned — the "SPAR UPPTAG" case in the
        flow reference. Track-catalog membership is validated by the HTTP
        layer, which has the SessionConfig; this store only knows ids.
        """
        now = _now_iso()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                busy = self._connection.execute(
                    """
                    SELECT movement_id FROM tkl_movement_states
                    WHERE publication_id = ? AND active_day = ? AND station_id = ?
                      AND assigned_track_id = ? AND movement_id != ?
                      AND departure_status != 'departed'
                    """,
                    (publication_id, active_day, station_id, track_id, movement_id),
                ).fetchone()
                if busy is not None:
                    self._connection.execute("COMMIT")
                    return {"status": "rejected", "reason": "track_occupied"}
                self._connection.execute(
                    """
                    INSERT INTO tkl_movement_states(
                        publication_id, active_day, movement_id, station_id,
                        arrival_status, departure_status, actual_track,
                        assigned_track_id, revision, updated_by, updated_at
                    ) VALUES (?, ?, ?, ?, 'none', 'none', NULL, ?, 1, ?, ?)
                    ON CONFLICT(publication_id, active_day, movement_id) DO UPDATE SET
                        station_id = excluded.station_id,
                        assigned_track_id = excluded.assigned_track_id,
                        revision = tkl_movement_states.revision + 1,
                        updated_by = excluded.updated_by,
                        updated_at = excluded.updated_at
                    """,
                    (publication_id, active_day, movement_id, station_id, track_id, updated_by, now),
                )
                revision = self._connection.execute(
                    """
                    SELECT revision FROM tkl_movement_states
                    WHERE publication_id = ? AND active_day = ? AND movement_id = ?
                    """,
                    (publication_id, active_day, movement_id),
                ).fetchone()[0]
                self._insert_tkl_event_locked(
                    publication_id, active_day, station_id, event_type,
                    {"assigned_track_id": track_id, "updated_by": updated_by},
                    shift_id=shift_id, movement_id=movement_id,
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return {
            "status": "assigned",
            "movement_id": movement_id,
            "assignedTrackId": track_id,
            "revision": revision,
        }

    @staticmethod
    def channel_id(connection_id: str, track_type: str, from_station_id: str) -> str:
        """A single-track connection has one shared channel; a double-track
        connection has one independent channel per direction (protocol v2
        decision B7) — trains going opposite ways never block each other.
        """
        if track_type == "double":
            return f"{connection_id}:{from_station_id}"
        return connection_id

    def request_clearance(
        self,
        publication_id: str,
        active_day: str,
        movement_id: str,
        connection_id: str,
        track_type: str,
        from_station_id: str,
        to_station_id: str,
        *,
        requested_by: str,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        channel = self.channel_id(connection_id, track_type, from_station_id)
        clearance_id = str(uuid4())
        expires_at = now + timedelta(seconds=ttl_seconds)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._expire_stale_locked(publication_id, active_day, now)
                busy = self._connection.execute(
                    """
                    SELECT clearance_id FROM clearances
                    WHERE publication_id = ? AND active_day = ? AND channel_id = ?
                      AND status IN ('waiting', 'approved')
                    """,
                    (publication_id, active_day, channel),
                ).fetchone()
                if busy is not None:
                    self._connection.execute("COMMIT")
                    return {"status": "rejected", "reason": "connection_busy"}
                self._connection.execute(
                    """
                    INSERT INTO clearances(
                        clearance_id, publication_id, active_day, movement_id,
                        connection_id, channel_id, from_station_id, to_station_id,
                        status, requested_by, requested_at, expires_at, revision,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?, ?, 1, ?)
                    """,
                    (
                        clearance_id,
                        publication_id,
                        active_day,
                        movement_id,
                        connection_id,
                        channel,
                        from_station_id,
                        to_station_id,
                        requested_by,
                        now.isoformat(),
                        expires_at.isoformat(),
                        now.isoformat(),
                    ),
                )
                self._insert_tkl_event_locked(
                    publication_id,
                    active_day,
                    from_station_id,
                    "clearance_requested",
                    {"clearance_id": clearance_id, "channel_id": channel, "to_station_id": to_station_id},
                    movement_id=movement_id,
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return self.clearance(clearance_id)

    def respond_clearance(
        self,
        clearance_id: str,
        *,
        accept: bool,
        responded_by: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT status, publication_id, active_day FROM clearances WHERE clearance_id = ?",
                    (clearance_id,),
                ).fetchone()
                if row is None or row[0] != "waiting":
                    self._connection.execute("COMMIT")
                    return {"status": "rejected", "reason": "request_no_longer_pending"}
                self._expire_stale_locked(row[1], row[2], now)
                refreshed = self._connection.execute(
                    "SELECT status FROM clearances WHERE clearance_id = ?", (clearance_id,)
                ).fetchone()
                if refreshed[0] != "waiting":
                    self._connection.execute("COMMIT")
                    return {"status": "rejected", "reason": "request_no_longer_pending"}
                new_status = "approved" if accept else "rejected"
                self._connection.execute(
                    """
                    UPDATE clearances
                    SET status = ?, responded_by = ?, responded_at = ?,
                        revision = revision + 1, updated_at = ?
                    WHERE clearance_id = ?
                    """,
                    (new_status, responded_by, now.isoformat(), now.isoformat(), clearance_id),
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return self.clearance(clearance_id)

    def cancel_clearance(
        self,
        clearance_id: str,
        *,
        cancelled_by: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT status FROM clearances WHERE clearance_id = ?", (clearance_id,)
                ).fetchone()
                if row is None or row[0] != "waiting":
                    self._connection.execute("COMMIT")
                    return {"status": "rejected", "reason": "request_no_longer_pending"}
                self._connection.execute(
                    """
                    UPDATE clearances
                    SET status = 'cancelled', responded_by = ?, responded_at = ?,
                        revision = revision + 1, updated_at = ?
                    WHERE clearance_id = ?
                    """,
                    (cancelled_by, now.isoformat(), now.isoformat(), clearance_id),
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return self.clearance(clearance_id)

    def invalidate_clearance(self, clearance_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Called when a movement's track changes while its clearance is waiting
        (gap-analys §3.2/§9.5) — the receiving station must see a fresh state,
        never a silently stale one."""
        now = now or datetime.now(timezone.utc)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    UPDATE clearances
                    SET status = 'invalidated_by_revision', revision = revision + 1, updated_at = ?
                    WHERE clearance_id = ? AND status = 'waiting'
                    """,
                    (now.isoformat(), clearance_id),
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return self.clearance(clearance_id)

    def clearance(self, clearance_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT clearance_id, movement_id, connection_id, channel_id,
                       from_station_id, to_station_id, status, requested_by,
                       requested_at, expires_at, responded_by, responded_at, revision,
                       publication_id, active_day
                FROM clearances WHERE clearance_id = ?
                """,
                (clearance_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Okänt klareringsärende")
        return _clearance_from_row(row)

    def active_clearance_for_channel(
        self,
        publication_id: str,
        active_day: str,
        connection_id: str,
        track_type: str,
        from_station_id: str,
    ) -> dict[str, Any] | None:
        channel = self.channel_id(connection_id, track_type, from_station_id)
        with self._lock:
            self._expire_stale_locked(publication_id, active_day, datetime.now(timezone.utc))
            row = self._connection.execute(
                """
                SELECT clearance_id, movement_id, connection_id, channel_id,
                       from_station_id, to_station_id, status, requested_by,
                       requested_at, expires_at, responded_by, responded_at, revision,
                       publication_id, active_day
                FROM clearances
                WHERE publication_id = ? AND active_day = ? AND channel_id = ?
                  AND status IN ('waiting', 'approved')
                """,
                (publication_id, active_day, channel),
            ).fetchone()
        return _clearance_from_row(row) if row else None

    def _expire_stale_locked(self, publication_id: str, active_day: str, now: datetime) -> None:
        self._connection.execute(
            """
            UPDATE clearances
            SET status = 'expired', revision = revision + 1, updated_at = ?
            WHERE publication_id = ? AND active_day = ? AND status = 'waiting' AND expires_at < ?
            """,
            (now.isoformat(), publication_id, active_day, now.isoformat()),
        )

    def publish_line_message(
        self,
        publication_id: str,
        active_day: str,
        movement_id: str,
        connection_id: str,
        from_station_id: str,
        to_station_id: str,
        *,
        sent_by: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Ensidigt 'linjen är ledig' (monsterprompt §12.2/protokoll v2 §8.3).

        No 'waiting' status and no busy-check against another request — a
        one-way notice is never a clearance decision. Starts delivered
        (this is a synchronous HTTP simulator, not a real device rundtrip)
        and only distinguishes sent vs. display-acknowledged.
        """
        now = now or datetime.now(timezone.utc)
        message_id = str(uuid4())
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO line_messages(
                        message_id, publication_id, active_day, movement_id,
                        connection_id, from_station_id, to_station_id, status,
                        sent_by, sent_at, revision, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'delivered_to_device', ?, ?, 1, ?)
                    """,
                    (
                        message_id, publication_id, active_day, movement_id,
                        connection_id, from_station_id, to_station_id,
                        sent_by, now.isoformat(), now.isoformat(),
                    ),
                )
                self._insert_tkl_event_locked(
                    publication_id, active_day, from_station_id, "line_available_published",
                    {"message_id": message_id, "to_station_id": to_station_id},
                    movement_id=movement_id,
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return self.line_message(message_id)

    def acknowledge_line_message(self, message_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    UPDATE line_messages
                    SET status = 'display_acknowledged', acknowledged_at = ?,
                        revision = revision + 1, updated_at = ?
                    WHERE message_id = ? AND status != 'display_acknowledged'
                    """,
                    (now.isoformat(), now.isoformat(), message_id),
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return self.line_message(message_id)

    def line_message(self, message_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT message_id, movement_id, connection_id, from_station_id,
                       to_station_id, status, sent_by, sent_at, acknowledged_at, revision
                FROM line_messages WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
        if row is None:
            raise ValueError("Okänt linjemeddelande")
        return _line_message_from_row(row)

    def active_line_messages_for_station(
        self, publication_id: str, active_day: str, station_id: str
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT message_id, movement_id, connection_id, from_station_id,
                       to_station_id, status, sent_by, sent_at, acknowledged_at, revision
                FROM line_messages
                WHERE publication_id = ? AND active_day = ? AND status != 'display_acknowledged'
                  AND (from_station_id = ? OR to_station_id = ?)
                ORDER BY sent_at
                """,
                (publication_id, active_day, station_id, station_id),
            ).fetchall()
        return [_line_message_from_row(row) for row in rows]

    def train_readiness(
        self,
        publication_id: str,
        active_day: str,
        station_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT movement_id, operating_point_id, status, prepared_by_role,
                       prepared_by, prepared_at, acknowledged_by, acknowledged_at,
                       revoked_by, revoked_at, updated_at
                FROM train_readiness
                WHERE publication_id = ? AND active_day = ? AND station_id = ?
                ORDER BY updated_at
                """,
                (publication_id, active_day, station_id),
            ).fetchall()
        return [_train_readiness_from_row(row, station_id) for row in rows]

    def set_train_readiness(
        self,
        publication_id: str,
        active_day: str,
        station_id: str,
        movement_id: str,
        operating_point_id: str | None,
        *,
        action: str,
        actor: str,
        actor_role: str,
        shift_id: str | None,
    ) -> dict[str, Any]:
        if action not in {"ready", "acknowledge", "revoke"}:
            raise ValueError("Ogiltig ändring av Tåg klart")
        actor = actor.strip()
        if not actor:
            raise ValueError("Operatörens namn måste anges")
        if actor_role not in {"tkl", "ranger"}:
            raise ValueError("Tåg klart måste registreras av TKL eller rangerare")
        now = _now_iso()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._connection.execute(
                    """
                    SELECT status, prepared_by_role FROM train_readiness
                    WHERE publication_id = ? AND active_day = ? AND movement_id = ?
                    """,
                    (publication_id, active_day, movement_id),
                ).fetchone()
                if action == "ready":
                    self._connection.execute(
                        """
                        INSERT INTO train_readiness(
                            publication_id, active_day, movement_id, station_id,
                            operating_point_id, status, prepared_by_role, prepared_by, prepared_at,
                            acknowledged_by, acknowledged_at, revoked_by, revoked_at,
                            updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                        ON CONFLICT(publication_id, active_day, movement_id) DO UPDATE SET
                            station_id = excluded.station_id,
                            operating_point_id = excluded.operating_point_id,
                            status = excluded.status,
                            prepared_by_role = excluded.prepared_by_role,
                            prepared_by = excluded.prepared_by,
                            prepared_at = excluded.prepared_at,
                            acknowledged_by = NULL,
                            acknowledged_at = NULL,
                            revoked_by = NULL,
                            revoked_at = NULL,
                            updated_at = excluded.updated_at
                        """,
                        (
                            publication_id,
                            active_day,
                            movement_id,
                            station_id,
                            operating_point_id,
                            "acknowledged" if actor_role == "tkl" else "ready",
                            actor_role,
                            actor,
                            now,
                            actor if actor_role == "tkl" else None,
                            now if actor_role == "tkl" else None,
                            now,
                        ),
                    )
                    event_type = "train_ready_by_tkl" if actor_role == "tkl" else "train_ready_by_ranger"
                elif action == "acknowledge":
                    if actor_role != "tkl":
                        raise ValueError("Endast TKL kan kvittera Tåg klart")
                    if current is None or current[0] != "ready":
                        raise ValueError("Tåget är inte längre väntande för TKL")
                    self._connection.execute(
                        """
                        UPDATE train_readiness
                        SET status = 'acknowledged', acknowledged_by = ?,
                            acknowledged_at = ?, updated_at = ?
                        WHERE publication_id = ? AND active_day = ? AND movement_id = ?
                        """,
                        (actor, now, now, publication_id, active_day, movement_id),
                    )
                    event_type = "train_ready_acknowledged"
                else:
                    if current is None or current[0] != "ready":
                        raise ValueError("Endast ett väntande Tåg klart kan återkallas")
                    if actor_role == "ranger" and current[1] != "ranger":
                        raise ValueError("Rangeraren kan endast återkalla sin egen Tåg klart-status")
                    self._connection.execute(
                        """
                        UPDATE train_readiness
                        SET status = 'revoked', revoked_by = ?, revoked_at = ?, updated_at = ?
                        WHERE publication_id = ? AND active_day = ? AND movement_id = ?
                        """,
                        (actor, now, now, publication_id, active_day, movement_id),
                    )
                    event_type = "train_ready_revoked"
                self._insert_tkl_event_locked(
                    publication_id,
                    active_day,
                    station_id,
                    event_type,
                    {
                        "operating_point_id": operating_point_id,
                        "actor": actor,
                        "actor_role": actor_role,
                    },
                    shift_id=shift_id,
                    movement_id=movement_id,
                )
                row = self._connection.execute(
                    """
                    SELECT movement_id, operating_point_id, status, prepared_by_role,
                           prepared_by, prepared_at, acknowledged_by, acknowledged_at,
                           revoked_by, revoked_at, updated_at
                    FROM train_readiness
                    WHERE publication_id = ? AND active_day = ? AND movement_id = ?
                    """,
                    (publication_id, active_day, movement_id),
                ).fetchone()
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return _train_readiness_from_row(row, station_id)

    def _insert_tkl_event_locked(
        self,
        publication_id: str,
        active_day: str,
        station_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        shift_id: str | None = None,
        movement_id: str | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO tkl_events(
                event_id, publication_id, active_day, station_id, shift_id,
                movement_id, event_type, payload_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                publication_id,
                active_day,
                station_id,
                shift_id,
                movement_id,
                event_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                _now_iso(),
            ),
        )

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


def _shift_from_row(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "shift_id": row[0],
        "operator_name": row[1],
        "terminal_name": row[2],
        "status": row[3],
        "started_at": row[4],
        "ended_at": row[5],
        "handover_note": row[6],
        "updated_at": row[7],
    }


def _train_readiness_from_row(row: tuple[Any, ...], station_id: str) -> dict[str, Any]:
    return {
        "movement_id": row[0],
        "station_id": station_id,
        "operating_point_id": row[1],
        "status": row[2],
        "prepared_by_role": row[3],
        "prepared_by": row[4],
        "prepared_at": row[5],
        "acknowledged_by": row[6],
        "acknowledged_at": row[7],
        "revoked_by": row[8],
        "revoked_at": row[9],
        "updated_at": row[10],
    }


def _clearance_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "clearance_id": row[0],
        "movement_id": row[1],
        "connection_id": row[2],
        "channel_id": row[3],
        "from_station_id": row[4],
        "to_station_id": row[5],
        "status": row[6],
        "requested_by": row[7],
        "requested_at": row[8],
        "expires_at": row[9],
        "responded_by": row[10],
        "responded_at": row[11],
        "revision": row[12],
        "publication_id": row[13],
        "active_day": row[14],
    }


def _line_message_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "message_id": row[0],
        "movement_id": row[1],
        "connection_id": row[2],
        "from_station_id": row[3],
        "to_station_id": row[4],
        "status": row[5],
        "sent_by": row[6],
        "sent_at": row[7],
        "acknowledged_at": row[8],
        "revision": row[9],
    }


def _seconds_to_time(value: float) -> str:
    seconds = int(value) % (24 * 60 * 60)
    hour, remainder = divmod(seconds, 3600)
    minute, second = divmod(remainder, 60)
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _now_iso() -> str:
    return _datetime_iso(datetime.now(timezone.utc))


def _datetime_iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
