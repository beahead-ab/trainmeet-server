from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import (
    ConnectionConfig,
    DispatchMode,
    PanelConfig,
    SessionConfig,
    StationConfig,
    TrackType,
)


RUNTIME_SCHEMA_VERSION = 1
DAY_ORDER = ("Mån", "Tis", "Ons", "Tor", "Fre", "Lör", "Sön")
SHORT_DAYS = {
    "M": "Mån",
    "Ti": "Tis",
    "O": "Ons",
    "To": "Tor",
    "Fr": "Fre",
    "L": "Lör",
    "Sö": "Sön",
}


class RuntimePublicationError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimePublication:
    publication_id: str
    published_at: str
    meet_id: str
    meet_name: str
    active_day: str
    timezone: str
    checksum: str
    payload: dict[str, Any]

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "RuntimePublication":
        if payload.get("schema_version") != RUNTIME_SCHEMA_VERSION:
            raise RuntimePublicationError("Driftpaketet har en version som inte stöds")

        publication_id = _required_text(payload, "publication_id")
        published_at = _required_text(payload, "published_at")
        try:
            datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise RuntimePublicationError("published_at är inte ett giltigt datum") from error

        meet = _required_object(payload, "meet")
        meet_id = _required_text(meet, "id")
        meet_name = _required_text(meet, "name")
        active_day = _required_text(meet, "active_day")
        timezone = str(meet.get("timezone") or "Europe/Stockholm")

        stations = _required_list(payload, "stations")
        station_ids = _unique_ids(stations, "station")
        for station in stations:
            _required_text(station, "code")
            _required_text(station, "name")

        connections = _required_list(payload, "connections")
        connection_ids = _unique_ids(connections, "connection")
        connection_endpoints: dict[str, set[str]] = {}
        for connection in connections:
            station_a_id = _required_text(connection, "station_a_id")
            station_b_id = _required_text(connection, "station_b_id")
            if station_a_id not in station_ids:
                raise RuntimePublicationError("En sträcka hänvisar till en okänd station")
            if station_b_id not in station_ids:
                raise RuntimePublicationError("En sträcka hänvisar till en okänd station")
            connection_endpoints[_required_text(connection, "id")] = {
                station_a_id,
                station_b_id,
            }
            _enum_value(connection.get("track_type", "single"), TrackType, "track_type")
            override = connection.get("dispatch_mode_override")
            if override is not None:
                _enum_value(override, DispatchMode, "dispatch_mode_override")

        panels = _required_list(payload, "panels")
        _unique_ids(panels, "panel")
        if not panels:
            raise RuntimePublicationError("Driftpaketet innehåller inga Tambox-paneler")
        for panel in panels:
            if _required_text(panel, "station_id") not in station_ids:
                raise RuntimePublicationError("En panel hänvisar till en okänd station")
            slots = _required_object(panel, "slots")
            unknown_keys = set(slots) - {"A", "B", "C", "D"}
            if unknown_keys:
                raise RuntimePublicationError("En panel innehåller andra platser än A–D")
            for key in ("A", "B", "C", "D"):
                connection_id = slots.get(key)
                if connection_id is not None and connection_id not in connection_ids:
                    raise RuntimePublicationError(f"Panelplats {key} hänvisar till en okänd sträcka")
                if connection_id is not None and panel["station_id"] not in connection_endpoints[connection_id]:
                    raise RuntimePublicationError(
                        f"Panelplats {key} hänvisar till en sträcka som inte når stationen"
                    )

        trains = _required_list(payload, "trains")
        _unique_ids(trains, "train movement")
        for train in trains:
            _required_text(train, "train_number")
            if _required_text(train, "station_id") not in station_ids:
                raise RuntimePublicationError("En tågrad hänvisar till en okänd station")
            _required_text(train, "days")
            _required_text(train, "sort_time")

        routes = _required_list(payload, "routes")
        for route in routes:
            _required_text(route, "train_number")
            if _required_text(route, "station_id") not in station_ids:
                raise RuntimePublicationError("Ett ruttstopp hänvisar till en okänd station")
            try:
                int(route["stop_order"])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimePublicationError("Ett ruttstopp saknar giltig ordning") from error

        default_mode = meet.get("default_dispatch_mode", DispatchMode.CLEARANCE.value)
        _enum_value(default_mode, DispatchMode, "default_dispatch_mode")

        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return cls(
            publication_id=publication_id,
            published_at=published_at,
            meet_id=meet_id,
            meet_name=meet_name,
            active_day=active_day,
            timezone=timezone,
            checksum=hashlib.sha256(canonical).hexdigest(),
            payload=payload,
        )

    def session_config(self) -> SessionConfig:
        meet = self.payload["meet"]
        stations = {
            value["id"]: StationConfig(
                id=value["id"],
                code=value["code"],
                name=value["name"],
            )
            for value in self.payload["stations"]
        }
        connections = {
            value["id"]: ConnectionConfig(
                id=value["id"],
                station_a_id=value["station_a_id"],
                station_b_id=value["station_b_id"],
                track_type=TrackType(value.get("track_type", TrackType.SINGLE.value)),
                dispatch_mode_override=(
                    DispatchMode(value["dispatch_mode_override"])
                    if value.get("dispatch_mode_override")
                    else None
                ),
            )
            for value in self.payload["connections"]
        }
        panels = {
            value["id"]: PanelConfig(
                id=value["id"],
                station_id=value["station_id"],
                name=value.get("name") or f"{stations[value['station_id']].code} Tambox",
                slots={key: value["slots"].get(key) for key in ("A", "B", "C", "D")},
            )
            for value in self.payload["panels"]
        }
        return SessionConfig(
            # A publication is a distinct run, so mutable state from another
            # version can never be restored into it by accident.
            id=self.publication_id,
            name=self.meet_name,
            default_dispatch_mode=DispatchMode(
                meet.get("default_dispatch_mode", DispatchMode.CLEARANCE.value)
            ),
            stations=stations,
            connections=connections,
            panels=panels,
            clock_time=str(meet.get("clock_time") or "12:00")[:5],
        )

    def timetable(self, *, active_day: str, station_id: str | None = None) -> dict[str, Any]:
        trains = [
            train
            for train in self.payload["trains"]
            if (station_id is None or train["station_id"] == station_id)
            and matches_active_day(str(train["days"]), active_day)
        ]
        trains.sort(
            key=lambda value: (
                int(value.get("manual_sort_order") or 0),
                str(value.get("sort_time") or "99:99"),
                str(value.get("train_number") or ""),
            )
        )
        train_numbers = {str(train["train_number"]) for train in trains}
        routes = [
            route
            for route in self.payload["routes"]
            if str(route["train_number"]) in train_numbers
        ]
        routes.sort(key=lambda value: (str(value["train_number"]), int(value["stop_order"])))
        return {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "publication_id": self.publication_id,
            "meet": self.payload["meet"],
            "active_day": active_day,
            "stations": self.payload["stations"],
            "trains": trains,
            "routes": routes,
        }


class SQLiteRuntimeStore:
    """Stores immutable TrainMeet publications and local runtime settings."""

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
            CREATE TABLE IF NOT EXISTS runtime_publications (
                publication_id TEXT PRIMARY KEY,
                meet_id TEXT NOT NULL,
                meet_name TEXT NOT NULL,
                published_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0, 1)),
                installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_runtime_publication
            ON runtime_publications(active) WHERE active = 1;
            CREATE TABLE IF NOT EXISTS runtime_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    def install(self, payload: dict[str, Any], *, activate: bool = True) -> RuntimePublication:
        publication = RuntimePublication.parse(payload)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT checksum FROM runtime_publications WHERE publication_id = ?",
                    (publication.publication_id,),
                ).fetchone()
                if existing is not None and existing[0] != publication.checksum:
                    raise RuntimePublicationError(
                        "Samma publication_id används för två olika driftpaket"
                    )
                if activate:
                    self._connection.execute("UPDATE runtime_publications SET active = 0 WHERE active = 1")
                self._connection.execute(
                    """
                    INSERT INTO runtime_publications (
                        publication_id, meet_id, meet_name, published_at,
                        checksum, payload_json, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(publication_id) DO UPDATE SET
                        active = CASE WHEN excluded.active = 1 THEN 1 ELSE runtime_publications.active END
                    """,
                    (
                        publication.publication_id,
                        publication.meet_id,
                        publication.meet_name,
                        publication.published_at,
                        publication.checksum,
                        encoded,
                        1 if activate else 0,
                    ),
                )
                if activate:
                    self._connection.execute(
                        """
                        INSERT INTO runtime_settings(key, value, updated_at)
                        VALUES ('active_day', ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(key) DO UPDATE SET
                            value = excluded.value,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (publication.active_day,),
                    )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return publication

    def active(self) -> RuntimePublication | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM runtime_publications WHERE active = 1"
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row[0])
        except json.JSONDecodeError as error:
            raise RuntimePublicationError("Det aktiva driftpaketet är skadat") from error
        return RuntimePublication.parse(payload)

    def active_day(self) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM runtime_settings WHERE key = 'active_day'"
            ).fetchone()
        if row is not None:
            return str(row[0])
        publication = self.active()
        return publication.active_day if publication else None

    def set_active_day(self, value: str) -> str:
        day = value.strip()
        if not day:
            raise RuntimePublicationError("Trafikdagen får inte vara tom")
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO runtime_settings(key, value, updated_at)
                VALUES ('active_day', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (day,),
            )
        return day

    def summary(self) -> dict[str, Any]:
        publication = self.active()
        if publication is None:
            return {"configured": False}
        return {
            "configured": True,
            "publication_id": publication.publication_id,
            "published_at": publication.published_at,
            "checksum": publication.checksum,
            "meet_id": publication.meet_id,
            "meet_name": publication.meet_name,
            "active_day": self.active_day(),
            "timezone": publication.timezone,
            "train_count": len(publication.payload["trains"]),
            "station_count": len(publication.payload["stations"]),
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def matches_active_day(train_days: str, active_day: str) -> bool:
    train_days = train_days.strip()
    active_day = active_day.strip()
    if train_days == "Dagl" or active_day == "Dagl" or train_days == active_day:
        return True
    if "," in train_days:
        return active_day in {_resolve_day(part.strip()) for part in train_days.split(",")}
    if "-" in train_days:
        start_raw, end_raw = train_days.split("-", 1)
        start = _resolve_day(start_raw.strip())
        end = _resolve_day(end_raw.strip())
        try:
            start_index = DAY_ORDER.index(start)
            end_index = DAY_ORDER.index(end)
            active_index = DAY_ORDER.index(active_day)
        except ValueError:
            return False
        if start_index <= end_index:
            return start_index <= active_index <= end_index
        return active_index >= start_index or active_index <= end_index
    return False


def _resolve_day(value: str) -> str:
    return SHORT_DAYS.get(value, value)


def _required_text(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise RuntimePublicationError(f"Driftpaketet saknar {key}")
    return result.strip()


def _required_object(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise RuntimePublicationError(f"Driftpaketet saknar objektet {key}")
    return result


def _required_list(value: dict[str, Any], key: str) -> list[dict[str, Any]]:
    result = value.get(key)
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise RuntimePublicationError(f"Driftpaketet saknar listan {key}")
    return result


def _unique_ids(values: list[dict[str, Any]], label: str) -> set[str]:
    ids = {_required_text(value, "id") for value in values}
    if len(ids) != len(values):
        raise RuntimePublicationError(f"Driftpaketet innehåller dubbla {label}-id:n")
    return ids


def _enum_value(value: Any, enum_type: type, key: str) -> None:
    try:
        enum_type(value)
    except (TypeError, ValueError) as error:
        raise RuntimePublicationError(f"Driftpaketet innehåller ogiltigt {key}") from error
