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
            -- Vad som gör en rörelse till "samma rörelse" över en ny
            -- publicering. Cloud myntar nya id vid varje omimport, så id
            -- duger inte. Tågnummer, station och besöksordning är vad en
            -- människa menar - och besöksordningen finns med för det
            -- sällsynta fallet att ett tåg vänder och kommer tillbaka.
            CREATE TABLE IF NOT EXISTS movement_identity (
                publication_id TEXT NOT NULL,
                movement_id TEXT NOT NULL,
                train_number TEXT NOT NULL,
                station_id TEXT NOT NULL,
                stop_index INTEGER NOT NULL,
                PRIMARY KEY(publication_id, movement_id)
            );
            CREATE TABLE IF NOT EXISTS tkl_movement_states (
                publication_id TEXT NOT NULL,
                active_day TEXT NOT NULL,
                movement_id TEXT NOT NULL,
                station_id TEXT NOT NULL,
                arrival_status TEXT NOT NULL CHECK(arrival_status IN ('none', 'approaching', 'arrived')),
                departure_status TEXT NOT NULL CHECK(departure_status IN ('none', 'positioned', 'ready', 'departed')),
                actual_track TEXT,
                operator_note TEXT,
                updated_by TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                crew_ready INTEGER NOT NULL DEFAULT 0,
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
                    'waiting', 'approved', 'rejected', 'cancelled',
                    'expired', 'invalidated_by_revision'
                )),
                track_id TEXT,
                revision INTEGER NOT NULL DEFAULT 1,
                requested_by TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                settled_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS clearances_on_channel
                ON clearances(publication_id, active_day, channel_id, status);
            CREATE INDEX IF NOT EXISTS clearances_for_movement
                ON clearances(publication_id, active_day, movement_id, status);
            CREATE TABLE IF NOT EXISTS clearance_events (
                event_id TEXT PRIMARY KEY,
                clearance_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS clearance_events_by_case
                ON clearance_events(clearance_id, recorded_at);
            CREATE TABLE IF NOT EXISTS line_available_messages (
                message_id TEXT PRIMARY KEY,
                publication_id TEXT NOT NULL,
                active_day TEXT NOT NULL,
                connection_id TEXT NOT NULL,
                from_station_id TEXT NOT NULL,
                to_station_id TEXT NOT NULL,
                movement_id TEXT,
                status TEXT NOT NULL CHECK(status IN (
                    'delivered_to_device', 'display_acknowledged'
                )),
                revision INTEGER NOT NULL DEFAULT 1,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                acknowledged_by TEXT,
                acknowledged_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS line_messages_for_station
                ON line_available_messages(publication_id, active_day, to_station_id, status);
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                correlation_id TEXT NOT NULL,
                source TEXT NOT NULL,
                actor TEXT NOT NULL,
                station_id TEXT,
                movement_id TEXT,
                action TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reason TEXT,
                detail_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS audit_events_by_correlation
                ON audit_events(correlation_id, recorded_at);
            CREATE TABLE IF NOT EXISTS device_commands (
                device_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                response_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY(device_id, message_id)
            );
            """
        )
        # Movement revision arrived with protocol v2. An installation from
        # before it keeps its rows and starts counting from zero.
        self._add_missing_columns(
            "tkl_movement_states",
            {
                "revision": "INTEGER NOT NULL DEFAULT 0",
                "crew_ready": "INTEGER NOT NULL DEFAULT 0",
                # Operatörens egen anteckning på en avgång. Den hör till
                # driften, inte till planen: Cloud äger tidtabellen men aldrig
                # det tågklareraren skrivit under träffen.
                "operator_note": "TEXT",
            },
        )

    def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        existing = {
            row[1]
            for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                self._connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                )

    def ensure_publication(self, publication: RuntimePublication) -> None:
        with self._lock:
            row = self._connection.execute(
                "SELECT publication_id FROM runtime_clock WHERE singleton = 1"
            ).fetchone()
            if row is not None and str(row[0]) == publication.publication_id:
                return
            previous_publication = str(row[0]) if row is not None else None
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
                self._carry_operational_state_locked(previous_publication, publication)
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def _carry_operational_state_locked(
        self, previous_publication: str | None, publication: RuntimePublication
    ) -> None:
        """Låt driften överleva en ny plan.

        Cloud äger tidtabellen och skriver över den. Men vilket spår ett tåg
        faktiskt fick, om det ankommit, och vad tågklareraren antecknat är
        inte planen - det är vad som hände. Det ska inte försvinna för att
        någon publicerade om mitt under träffen.

        Rörelserna paras ihop på tågnummer, station och besöksordning, inte
        på id: Cloud myntar nya id vid varje omimport. En rörelse som inte
        finns kvar i den nya planen tappar sitt läge, och en ny rörelse börjar
        rent - båda är rätt.
        """

        identities = _movement_identities(publication.payload)
        self._connection.executemany(
            "INSERT OR REPLACE INTO movement_identity"
            " (publication_id, movement_id, train_number, station_id, stop_index)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (publication.publication_id, movement_id, train, station, index)
                for movement_id, train, station, index in identities
            ],
        )

        if previous_publication is None:
            self._connection.execute("DELETE FROM train_positions")
            return

        # Nyckel -> nytt rörelse-id i den publikation som just aktiverats.
        arriving = {
            (train, station, index): movement_id
            for movement_id, train, station, index in identities
        }

        carried = 0
        rows = self._connection.execute(
            """
            SELECT s.active_day, s.movement_id, s.station_id, s.arrival_status,
                   s.departure_status, s.actual_track, s.operator_note,
                   s.updated_by, s.updated_at, s.revision, s.crew_ready,
                   i.train_number, i.stop_index
            FROM tkl_movement_states AS s
            JOIN movement_identity AS i
              ON i.publication_id = s.publication_id AND i.movement_id = s.movement_id
            WHERE s.publication_id = ?
            """,
            (previous_publication,),
        ).fetchall()
        for row in rows:
            target = arriving.get((str(row[11]), str(row[2]), int(row[12])))
            if target is None:
                continue
            self._connection.execute(
                """
                INSERT OR REPLACE INTO tkl_movement_states(
                    publication_id, active_day, movement_id, station_id,
                    arrival_status, departure_status, actual_track, operator_note,
                    updated_by, updated_at, revision, crew_ready
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    publication.publication_id, row[0], target, row[2],
                    row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10],
                ),
            )
            carried += 1

        # Passen hör till stationen, inte till planen. En station som finns
        # kvar behåller sin tågklarerare.
        stations = {str(station.get("id")) for station in publication.payload.get("stations") or []}
        for shift in self._connection.execute(
            "SELECT shift_id, active_day, station_id FROM tkl_shifts"
            " WHERE publication_id = ? AND status != 'closed'",
            (previous_publication,),
        ).fetchall():
            if str(shift[2]) in stations:
                self._connection.execute(
                    "UPDATE tkl_shifts SET publication_id = ? WHERE shift_id = ?",
                    (publication.publication_id, shift[0]),
                )

        # Positionerna nycklas på tågnummer och överlever därför av sig själva.
        # De rensas bara för tåg som inte längre finns i planen.
        numbers = {train for _, train, _, _ in identities}
        for existing in self._connection.execute(
            "SELECT train_number FROM train_positions"
        ).fetchall():
            if str(existing[0]) not in numbers:
                self._connection.execute(
                    "DELETE FROM train_positions WHERE train_number = ?", (existing[0],)
                )

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
            # A request or reservation is not yet a real movement - it can
            # still be rejected or cancelled and fall straight back to free,
            # a transition this loop otherwise never sees, which used to
            # leave the train shown at the station indefinitely. Waiting for
            # "occupied" to actually be reached means there is nothing to
            # leave behind if it never gets there.
            if current_state == "occupied" and current.get("train_number"):
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
                       updated_by, updated_at, revision, crew_ready, operator_note
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
                    "departure": derived_departure(row[2], bool(row[7])),
                    "storedDeparture": row[2],
                    "operatorNote": row[8],
                    "actualTrack": row[3],
                    "updated_by": row[4],
                    "updated_at": row[5],
                    "revision": int(row[6] or 0),
                    "crewReady": bool(row[7]),
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
        crew_ready: bool | None = None,
        operator_note: str | None = None,
    ) -> dict[str, Any]:
        if arrival not in {"none", "approaching", "arrived"}:
            raise ValueError("Ogiltigt ankomstläge")
        if departure not in {"none", "positioned", "ready", "departed"}:
            raise ValueError("Ogiltigt avgångsläge")
        # REDO is derived, never stored: it is what TKL's two declarations
        # add up to. A terminal that still sends ready is saying both, so both
        # are recorded and the derived value comes back out unchanged.
        if departure == "ready":
            departure = "positioned"
            crew_ready = True
        track = (actual_track or "").strip() or None
        # None betyder "lämna anteckningen som den är". Tom sträng betyder
        # "ta bort den". En box som bara byter spår ska inte råka radera vad
        # någon annan skrivit.
        note = None if operator_note is None else (operator_note.strip() or None)
        now = _now_iso()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO tkl_movement_states(
                        publication_id, active_day, movement_id, station_id,
                        arrival_status, departure_status, actual_track, operator_note,
                        updated_by, updated_at, revision, crew_ready
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(publication_id, active_day, movement_id) DO UPDATE SET
                        station_id = excluded.station_id,
                        arrival_status = excluded.arrival_status,
                        departure_status = excluded.departure_status,
                        actual_track = excluded.actual_track,
                        -- Flaggan sist i bindningen avgör om anteckningen
                        -- alls var med i anropet. Utan den gick "lämna som
                        -- den är" inte att skilja från "radera".
                        operator_note = CASE WHEN ?
                            THEN excluded.operator_note
                            ELSE tkl_movement_states.operator_note END,
                        updated_by = excluded.updated_by,
                        updated_at = excluded.updated_at,
                        revision = tkl_movement_states.revision + 1,
                        crew_ready = excluded.crew_ready
                    """,
                    (
                        publication_id,
                        active_day,
                        movement_id,
                        station_id,
                        arrival,
                        departure,
                        track,
                        note,
                        updated_by,
                        now,
                        1 if crew_ready else 0,
                        operator_note is not None,
                    ),
                )
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
                revision = int(
                    self._connection.execute(
                        """
                        SELECT revision FROM tkl_movement_states
                        WHERE publication_id = ? AND active_day = ? AND movement_id = ?
                        """,
                        (publication_id, active_day, movement_id),
                    ).fetchone()[0]
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return {
            "movement_id": movement_id,
            "arrival": arrival,
            "departure": derived_departure(departure, bool(crew_ready)),
            "storedDeparture": departure,
            "crewReady": bool(crew_ready),
            "actualTrack": track,
            "operatorNote": note,
            "updated_by": updated_by,
            "updated_at": now,
            "revision": revision,
        }

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

    # ------------------------------------------------------------ clearances
    #
    # A clearance is its own case with a stable id, a state machine and a
    # time to live, not a flag on a connection. The channel it occupies is
    # directed on a double-track connection, so two trains meeting head to
    # head on different tracks never block each other.

    #: A case is decided by its status, but the line stays occupied until the
    #: train is in. approved with no settled_at means "granted, still out
    #: there"; approved with settled_at means the channel is free again.

    @staticmethod
    def channel_id(connection_id: str, from_station_id: str, *, double_track: bool) -> str:
        """The occupancy channel a clearance takes.

        Single track is one shared channel. Double track is one independent
        channel per direction - modelled as two channels rather than as flags
        on one, so nothing has to remember which flag means what.
        """
        return f"{connection_id}:{from_station_id}" if double_track else connection_id

    def expire_due_clearances(
        self,
        publication_id: str,
        active_day: str,
        *,
        now: datetime | None = None,
    ) -> list[str]:
        """Expire whatever has run out, lazily.

        Correctness never depends on a background job having run, so this is
        called on the way into every request and every response.
        """
        moment = (now or datetime.now(timezone.utc)).isoformat()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT clearance_id FROM clearances
                WHERE publication_id = ? AND active_day = ?
                  AND status = 'waiting' AND expires_at <= ?
                """,
                (publication_id, active_day, moment),
            ).fetchall()
            for row in rows:
                self._settle_clearance_locked(row[0], "expired", "server", {})
        return [row[0] for row in rows]

    def open_clearance_on_channel(
        self,
        publication_id: str,
        active_day: str,
        channel_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                f"""
                SELECT {_CLEARANCE_COLUMNS} FROM clearances
                WHERE publication_id = ? AND active_day = ? AND channel_id = ?
                  AND (status = 'waiting' OR (status = 'approved' AND settled_at IS NULL))
                ORDER BY requested_at
                LIMIT 1
                """,
                (publication_id, active_day, channel_id),
            ).fetchone()
        return _clearance_from_row(row) if row else None

    def clearance(self, clearance_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                f"SELECT {_CLEARANCE_COLUMNS} FROM clearances WHERE clearance_id = ?",
                (clearance_id,),
            ).fetchone()
        return _clearance_from_row(row) if row else None

    def open_clearances_for_station(
        self,
        publication_id: str,
        active_day: str,
        station_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT {_CLEARANCE_COLUMNS} FROM clearances
                WHERE publication_id = ? AND active_day = ?
                  AND (status = 'waiting' OR (status = 'approved' AND settled_at IS NULL))
                  AND (from_station_id = ? OR to_station_id = ?)
                ORDER BY requested_at
                """,
                (publication_id, active_day, station_id, station_id),
            ).fetchall()
        return [_clearance_from_row(row) for row in rows]

    def request_clearance(
        self,
        publication_id: str,
        active_day: str,
        *,
        clearance_id: str,
        movement_id: str,
        connection_id: str,
        channel_id: str,
        from_station_id: str,
        to_station_id: str,
        track_id: str | None,
        requested_by: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        moment = now or datetime.now(timezone.utc)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO clearances(
                        clearance_id, publication_id, active_day, movement_id,
                        connection_id, channel_id, from_station_id, to_station_id,
                        status, track_id, revision, requested_by, requested_at,
                        expires_at, settled_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?, 1, ?, ?, ?, NULL, ?)
                    """,
                    (
                        clearance_id,
                        publication_id,
                        active_day,
                        movement_id,
                        connection_id,
                        channel_id,
                        from_station_id,
                        to_station_id,
                        track_id,
                        requested_by,
                        moment.isoformat(),
                        (moment + timedelta(seconds=ttl_seconds)).isoformat(),
                        moment.isoformat(),
                    ),
                )
                self._record_clearance_event_locked(
                    clearance_id,
                    "requested",
                    requested_by,
                    {"connection_id": connection_id, "channel_id": channel_id},
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return self.clearance(clearance_id)

    def settle_clearance(
        self,
        clearance_id: str,
        status: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            self._settle_clearance_locked(clearance_id, status, actor, payload or {})
        return self.clearance(clearance_id)

    def invalidate_clearances_for_movement(
        self,
        publication_id: str,
        active_day: str,
        movement_id: str,
        actor: str,
        reason: str,
    ) -> list[str]:
        """A waiting request stops meaning what it meant when it was made.

        A track change under a pending request invalidates that request
        explicitly. Silently rewriting it would leave two stations holding
        different pictures of the same train.
        """
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT clearance_id FROM clearances
                WHERE publication_id = ? AND active_day = ? AND movement_id = ?
                  AND status = 'waiting'
                """,
                (publication_id, active_day, movement_id),
            ).fetchall()
            for row in rows:
                self._settle_clearance_locked(
                    row[0], "invalidated_by_revision", actor, {"reason": reason}
                )
        return [row[0] for row in rows]

    def clearance_history(self, clearance_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT event_type, actor, payload_json, recorded_at
                FROM clearance_events WHERE clearance_id = ?
                ORDER BY recorded_at, event_id
                """,
                (clearance_id,),
            ).fetchall()
        return [
            {
                "event_type": row[0],
                "actor": row[1],
                "payload": json.loads(row[2]),
                "recorded_at": row[3],
            }
            for row in rows
        ]

    def release_clearance(self, clearance_id: str, actor: str) -> dict[str, Any] | None:
        """Free the channel an approved clearance still occupies."""
        now = _now_iso()
        with self._lock:
            updated = self._connection.execute(
                """
                UPDATE clearances
                SET settled_at = ?, revision = revision + 1, updated_at = ?
                WHERE clearance_id = ? AND status = 'approved' AND settled_at IS NULL
                """,
                (now, now, clearance_id),
            )
            if updated.rowcount:
                self._record_clearance_event_locked(clearance_id, "released", actor, {})
        return self.clearance(clearance_id)

    def _settle_clearance_locked(
        self,
        clearance_id: str,
        status: str,
        actor: str,
        payload: dict[str, Any],
    ) -> None:
        now = _now_iso()
        # An approved case keeps its channel until the train is in, so only a
        # refusal, a cancellation or a timeout closes it here.
        settled_at = None if status == "approved" else now
        updated = self._connection.execute(
            """
            UPDATE clearances
            SET status = ?, revision = revision + 1, settled_at = ?, updated_at = ?
            WHERE clearance_id = ? AND status = 'waiting'
            """,
            (status, settled_at, now, clearance_id),
        )
        if updated.rowcount == 0:
            return
        self._record_clearance_event_locked(clearance_id, status, actor, payload)

    def _record_clearance_event_locked(
        self,
        clearance_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO clearance_events(
                event_id, clearance_id, event_type, actor, payload_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                clearance_id,
                event_type,
                actor,
                json.dumps(payload, ensure_ascii=False),
                _now_iso(),
            ),
        )

    # ------------------------------------------------------- line available
    #
    # One-sided information, never a question. It carries two delivery levels
    # and no decision, and it is never checked against channel occupancy - a
    # clearance case with only one party would be a lie about what it is.

    def publish_line_available(
        self,
        publication_id: str,
        active_day: str,
        *,
        message_id: str,
        connection_id: str,
        from_station_id: str,
        to_station_id: str,
        movement_id: str | None,
        created_by: str,
    ) -> dict[str, Any]:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO line_available_messages(
                    message_id, publication_id, active_day, connection_id,
                    from_station_id, to_station_id, movement_id, status,
                    revision, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'delivered_to_device', 1, ?, ?, ?)
                """,
                (
                    message_id,
                    publication_id,
                    active_day,
                    connection_id,
                    from_station_id,
                    to_station_id,
                    movement_id,
                    created_by,
                    now,
                    now,
                ),
            )
        return self.line_message(message_id)

    def acknowledge_line_available(self, message_id: str, actor: str) -> dict[str, Any] | None:
        now = _now_iso()
        with self._lock:
            self._connection.execute(
                """
                UPDATE line_available_messages
                SET status = 'display_acknowledged', revision = revision + 1,
                    acknowledged_by = ?, acknowledged_at = ?, updated_at = ?
                WHERE message_id = ? AND status = 'delivered_to_device'
                """,
                (actor, now, now, message_id),
            )
        return self.line_message(message_id)

    def line_message(self, message_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                f"SELECT {_LINE_MESSAGE_COLUMNS} FROM line_available_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return _line_message_from_row(row) if row else None

    def open_line_messages_for_station(
        self,
        publication_id: str,
        active_day: str,
        station_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT {_LINE_MESSAGE_COLUMNS} FROM line_available_messages
                WHERE publication_id = ? AND active_day = ?
                  AND status = 'delivered_to_device'
                  AND (to_station_id = ? OR from_station_id = ?)
                ORDER BY created_at
                """,
                (publication_id, active_day, station_id, station_id),
            ).fetchall()
        return [_line_message_from_row(row) for row in rows]

    # ---------------------------------------------------------------- audit
    #
    # One journal for everything a command touches, keyed on the correlation
    # id it carried, so a train's history can be pulled out in one query
    # rather than reconstructed from timestamps across three places.

    def record_audit_event(
        self,
        *,
        correlation_id: str,
        source: str,
        actor: str,
        action: str,
        outcome: str,
        station_id: str | None = None,
        movement_id: str | None = None,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO audit_events(
                    event_id, correlation_id, source, actor, station_id,
                    movement_id, action, outcome, reason, detail_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    correlation_id,
                    source,
                    actor,
                    station_id,
                    movement_id,
                    action,
                    outcome,
                    reason,
                    json.dumps(detail or {}, ensure_ascii=False),
                    _now_iso(),
                ),
            )

    def audit_trail(self, correlation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT correlation_id, source, actor, station_id, movement_id,
                       action, outcome, reason, detail_json, recorded_at
                FROM audit_events WHERE correlation_id = ?
                ORDER BY recorded_at, event_id
                """,
                (correlation_id,),
            ).fetchall()
        return [
            {
                "correlation_id": row[0],
                "source": row[1],
                "actor": row[2],
                "station_id": row[3],
                "movement_id": row[4],
                "action": row[5],
                "outcome": row[6],
                "reason": row[7],
                "detail": json.loads(row[8]),
                "recorded_at": row[9],
            }
            for row in rows
        ]

    def device_command_response(self, device_id: str, message_id: str) -> dict[str, Any] | None:
        """The answer a device already got for this message, if any.

        A box that loses an acknowledgement reuses the same message id to ask
        what happened - never to make a second decision. Keeping the answer
        durable means a server restart cannot turn that question into one.
        """
        with self._lock:
            row = self._connection.execute(
                """
                SELECT response_json FROM device_commands
                WHERE device_id = ? AND message_id = ?
                """,
                (device_id, message_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def remember_device_command(
        self,
        device_id: str,
        message_id: str,
        response: dict[str, Any],
    ) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO device_commands(device_id, message_id, response_json, recorded_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(device_id, message_id) DO NOTHING
                """,
                (device_id, message_id, json.dumps(response, ensure_ascii=False), _now_iso()),
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


_LINE_MESSAGE_COLUMNS = (
    "message_id, connection_id, from_station_id, to_station_id, movement_id, "
    "status, revision, created_by, created_at, acknowledged_by, acknowledged_at"
)


def _line_message_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "message_id": row[0],
        "connection_id": row[1],
        "from_station_id": row[2],
        "to_station_id": row[3],
        "movement_id": row[4],
        "status": row[5],
        "revision": int(row[6]),
        "created_by": row[7],
        "created_at": row[8],
        "acknowledged_by": row[9],
        "acknowledged_at": row[10],
    }


def derived_departure(stored: str, crew_ready: bool) -> str:
    """REDO is what TKL's two declarations add up to, never a stored value.

    The train is set up and the driver is on board, and the server's own rules
    hold: only then is a train ready. No client can shortcut to it.
    """
    if stored == "positioned" and crew_ready:
        return "ready"
    return stored


_CLEARANCE_COLUMNS = (
    "clearance_id, movement_id, connection_id, channel_id, from_station_id, "
    "to_station_id, status, track_id, revision, requested_by, requested_at, "
    "expires_at, settled_at"
)


def _clearance_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "clearance_id": row[0],
        "movement_id": row[1],
        "connection_id": row[2],
        "channel_id": row[3],
        "from_station_id": row[4],
        "to_station_id": row[5],
        "status": row[6],
        "track_id": row[7],
        "revision": int(row[8]),
        "requested_by": row[9],
        "requested_at": row[10],
        "expires_at": row[11],
        "settled_at": row[12],
    }


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


def _movement_identities(payload: dict[str, Any]) -> list[tuple[str, str, str, int]]:
    """Vad som identifierar varje rörelse, oberoende av dess id.

    Tågnummer och station är det en människa menar med "samma rörelse".
    Besöksordningen finns med för att ett tåg i sällsynta fall vänder och
    kommer tillbaka till samma station samma dag - traditionellt går jämna
    tågnummer åt ett håll och udda åt det andra, så det är ovanligt, men en
    nyckel som stämmer nästan alltid är en nyckel som sviker obegripligt.
    """

    def minute(value: Any) -> int:
        text = str(value or "")
        if ":" not in text:
            return 0
        hours, _, minutes = text.partition(":")
        try:
            return int(hours) * 60 + int(minutes)
        except ValueError:
            return 0

    rows = sorted(
        (row for row in payload.get("trains") or [] if row.get("id")),
        key=lambda row: minute(row.get("sort_time") or row.get("departure_time") or row.get("arrival_time")),
    )
    seen: dict[tuple[str, str], int] = {}
    out = []
    for row in rows:
        train = str(row.get("train_number") or "")
        station = str(row.get("station_id") or "")
        key = (train, station)
        index = seen.get(key, 0)
        seen[key] = index + 1
        out.append((str(row["id"]), train, station, index))
    return out


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


def _seconds_to_time(value: float) -> str:
    seconds = int(value) % (24 * 60 * 60)
    hour, remainder = divmod(seconds, 3600)
    minute, second = divmod(remainder, 60)
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def _now_iso() -> str:
    return _datetime_iso(datetime.now(timezone.utc))


def _datetime_iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
