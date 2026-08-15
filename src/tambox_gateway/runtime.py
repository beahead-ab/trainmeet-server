from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import (
    ConnectionConfig,
    DispatchMode,
    PanelConfig,
    SessionConfig,
    StationConfig,
    TrackType,
)


RUNTIME_SCHEMA_VERSION = 2
AVAILABLE_CLOCK_STYLES = (
    "swiss",
    "swedish",
    "norwegian",
    "danish",
    "german",
    "finnish",
    "polish",
    "dutch",
    "french",
    "italian",
    "american",
    "digital",
)
DAY_ORDER = ("Mån", "Tis", "Ons", "Tor", "Fre", "Lör", "Sön")
SHORT_DAYS = {
    "M": "Mån",
    "Ti": "Tis",
    "On": "Ons",
    "O": "Ons",
    "To": "Tor",
    "Fr": "Fre",
    "Lö": "Lör",
    "L": "Lör",
    "Sö": "Sön",
}


class RuntimePublicationError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimePublication:
    schema_version: int
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
        schema_version = payload.get("schema_version")
        if schema_version != RUNTIME_SCHEMA_VERSION:
            raise RuntimePublicationError(
                f"Driftpaketet måste ha schema_version {RUNTIME_SCHEMA_VERSION}"
            )

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
        operating_point_stations: dict[str, str] = {}
        multi_point_stations: set[str] = set()
        for station in stations:
            _required_text(station, "code")
            _required_text(station, "name")
            operating_points = station.get("operating_points", [])
            if not isinstance(operating_points, list):
                raise RuntimePublicationError("En stations driftplatser måste vara en lista")
            if len(operating_points) > 1:
                multi_point_stations.add(str(station["id"]))
            for operating_point in operating_points:
                if not isinstance(operating_point, dict):
                    raise RuntimePublicationError("En driftplats måste vara ett objekt")
                operating_point_id = _required_text(operating_point, "id")
                if operating_point_id in operating_point_stations:
                    raise RuntimePublicationError("Två driftplatser har samma id")
                _required_text(operating_point, "name")
                _required_text(operating_point, "code")
                kind = str(operating_point.get("kind") or "station")
                if kind not in {"station", "yard"}:
                    raise RuntimePublicationError("En driftplats har ogiltig typ")
                aliases = operating_point.get("aliases", [])
                if not isinstance(aliases, list) or not all(
                    isinstance(alias, str) and alias.strip() for alias in aliases
                ):
                    raise RuntimePublicationError("En driftplats har ogiltiga alias")
                operating_point_stations[operating_point_id] = str(station["id"])

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
        movement_fingerprints: set[tuple[Any, ...]] = set()
        for train in trains:
            _required_text(train, "train_number")
            train_station_id = _required_text(train, "station_id")
            if train_station_id not in station_ids:
                raise RuntimePublicationError("En tågrad hänvisar till en okänd station")
            operating_point_id = train.get("operating_point_id")
            if operating_point_id is not None:
                if not isinstance(operating_point_id, str) or not operating_point_id.strip():
                    raise RuntimePublicationError("En tågrad har ett ogiltigt driftplats-id")
                if operating_point_stations.get(operating_point_id) != train_station_id:
                    raise RuntimePublicationError(
                        "En tågrad hänvisar till en driftplats på fel station"
                    )
            elif train_station_id in multi_point_stations:
                raise RuntimePublicationError(
                    "En tågrad på en station med flera driftplatser saknar operating_point_id"
                )
            _required_text(train, "days")
            _required_text(train, "sort_time")
            fingerprint = _movement_fingerprint(train)
            if fingerprint in movement_fingerprints:
                raise RuntimePublicationError(
                    "Driftpaketet innehåller samma tågrörelse flera gånger"
                )
            movement_fingerprints.add(fingerprint)

        routes = _required_list(payload, "routes")
        for route in routes:
            _required_text(route, "train_number")
            if _required_text(route, "station_id") not in station_ids:
                raise RuntimePublicationError("Ett ruttstopp hänvisar till en okänd station")
            try:
                int(route["stop_order"])
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimePublicationError("Ett ruttstopp saknar giltig ordning") from error

        clock = _required_object(payload, "clock")
        _required_text(clock, "source")
        _required_text(clock, "start_time")
        try:
            speed = float(clock.get("speed", 1))
        except (TypeError, ValueError) as error:
            raise RuntimePublicationError("Klockhastigheten är ogiltig") from error
        if speed <= 0:
            raise RuntimePublicationError("Klockhastigheten måste vara större än noll")

        services = _required_list(payload, "services")
        service_ids = _unique_ids(services, "tågtur")
        for service in services:
            _required_text(service, "train_number")
            _required_text(service, "days")
            stops = _required_list(service, "stops")
            for stop in stops:
                if _required_text(stop, "station_id") not in station_ids:
                    raise RuntimePublicationError("En tågtur hänvisar till en okänd station")
                try:
                    int(stop["stop_order"])
                    int(stop["service_minute"])
                except (KeyError, TypeError, ValueError) as error:
                    raise RuntimePublicationError("En tågtur saknar giltig stopptid eller ordning") from error
        for train in trains:
            linked_service = _required_text(train, "service_id")
            if linked_service not in service_ids:
                raise RuntimePublicationError("En tågrad hänvisar till en okänd tågtur")

        display = _required_object(payload, "display")
        graph_order = display.get("graph_station_order", [])
        if (
            not isinstance(graph_order, list)
            or len(graph_order) != len(station_ids)
            or set(graph_order) != station_ids
        ):
            raise RuntimePublicationError("Diagrammets stationsordning är ofullständig")

        autonomous_links = _required_list(payload, "autonomous_links")
        for link in autonomous_links:
            if _required_text(link, "autonomous_station_id") not in station_ids:
                raise RuntimePublicationError("En autonom koppling hänvisar till en okänd station")
            if _required_text(link, "related_station_id") not in station_ids:
                raise RuntimePublicationError("En autonom koppling hänvisar till en okänd station")

        default_mode = meet.get("default_dispatch_mode", DispatchMode.CLEARANCE.value)
        _enum_value(default_mode, DispatchMode, "default_dispatch_mode")

        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return cls(
            schema_version=int(schema_version),
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
        services = [
            service
            for service in self.payload["services"]
            if matches_active_day(str(service.get("days", "Dagl")), active_day)
            and (
                station_id is None
                or any(stop.get("station_id") == station_id for stop in service.get("stops", []))
            )
        ]
        return {
            "schema_version": self.schema_version,
            "publication_id": self.publication_id,
            "meet": self.payload["meet"],
            "active_day": active_day,
            "stations": self.payload["stations"],
            "trains": trains,
            "routes": routes,
            "services": services,
            "connections": self.payload["connections"],
            "autonomous_links": self.payload["autonomous_links"],
            "display": self.payload["display"],
            "clock": self.payload["clock"],
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
            CREATE TABLE IF NOT EXISTS cloud_change_outbox (
                id TEXT PRIMARY KEY,
                meet_id TEXT NOT NULL,
                base_publication_id TEXT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                operation TEXT NOT NULL CHECK(operation IN ('upsert', 'delete')),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT
            );
            CREATE INDEX IF NOT EXISTS cloud_change_outbox_pending
            ON cloud_change_outbox(sent_at, created_at);
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

    def publication(self, publication_id: str) -> RuntimePublication | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM runtime_publications WHERE publication_id = ?",
                (publication_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return RuntimePublication.parse(json.loads(row[0]))
        except json.JSONDecodeError as error:
            raise RuntimePublicationError("Det hämtade driftpaketet är skadat") from error

    def activate(self, publication_id: str) -> RuntimePublication:
        publication = self.publication(publication_id)
        if publication is None:
            raise RuntimePublicationError("Den hämtade versionen finns inte")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute("UPDATE runtime_publications SET active = 0 WHERE active = 1")
                updated = self._connection.execute(
                    "UPDATE runtime_publications SET active = 1 WHERE publication_id = ?",
                    (publication_id,),
                )
                if updated.rowcount != 1:
                    raise RuntimePublicationError("Den hämtade versionen finns inte")
                self._connection.execute(
                    """
                    INSERT INTO runtime_settings(key, value, updated_at)
                    VALUES ('active_day', ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                    """,
                    (publication.active_day,),
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return publication

    def save_link_token(self, token: str) -> None:
        token = token.strip()
        if not token:
            return
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO runtime_settings(key, value, updated_at)
                VALUES ('central_link_token', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """,
                (token,),
            )

    def link_token(self) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM runtime_settings WHERE key = 'central_link_token'"
            ).fetchone()
        return str(row[0]) if row else None

    def save_server_name(self, name: str) -> str:
        value = name.strip()
        if not 2 <= len(value) <= 80:
            raise RuntimePublicationError("Servernamnet måste vara 2–80 tecken")
        self._save_setting("server_name", value)
        return value

    def server_name(self) -> str | None:
        return self._setting("server_name")

    def save_central_url(self, url: str) -> str:
        value = url.strip().rstrip("/")
        if not value:
            raise RuntimePublicationError("Adressen till centrala TrainMeet saknas")
        self._save_setting("central_runtime_url", value)
        return value

    def central_url(self) -> str | None:
        return self._setting("central_runtime_url")

    def set_cloud_auto_sync(self, enabled: bool) -> bool:
        self._save_setting("cloud_auto_sync", "1" if enabled else "0")
        return enabled

    def cloud_auto_sync_enabled(self) -> bool:
        return self._setting("cloud_auto_sync") == "1"

    def queue_cloud_changes(
        self,
        meet_id: str,
        base_publication_id: str | None,
        changes: list[dict[str, Any]],
    ) -> list[str]:
        queued: list[str] = []
        with self._lock, self._connection:
            for change in changes:
                change_id = str(change.get("id") or uuid4())
                entity_type = str(change.get("entity_type") or "").strip()
                entity_id = str(change.get("entity_id") or "").strip()
                operation = str(change.get("operation") or "upsert")
                payload = change.get("payload") if isinstance(change.get("payload"), dict) else {}
                if not entity_type or not entity_id or operation not in {"upsert", "delete"}:
                    raise RuntimePublicationError("En lokal Cloud-ändring är ogiltig")
                self._connection.execute(
                    """INSERT INTO cloud_change_outbox(
                         id, meet_id, base_publication_id, entity_type, entity_id, operation, payload_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (change_id, meet_id, base_publication_id, entity_type, entity_id, operation, json.dumps(payload, ensure_ascii=False)),
                )
                queued.append(change_id)
        return queued

    def pending_cloud_changes(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT id,meet_id,base_publication_id,entity_type,entity_id,operation,payload_json,created_at
                   FROM cloud_change_outbox WHERE sent_at IS NULL ORDER BY created_at,id"""
            ).fetchall()
        result = []
        for row in rows:
            item = {
                "id": row[0], "meet_id": row[1], "base_publication_id": row[2],
                "entity_type": row[3], "entity_id": row[4], "operation": row[5],
                "payload": json.loads(row[6]), "created_at": row[7],
            }
            result.append(item)
        return result

    def mark_cloud_changes_sent(self, change_ids: list[str]) -> None:
        if not change_ids:
            return
        with self._lock, self._connection:
            self._connection.executemany(
                "UPDATE cloud_change_outbox SET sent_at=CURRENT_TIMESTAMP WHERE id=?",
                [(change_id,) for change_id in change_ids],
            )

    def pending_cloud_change_count(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM cloud_change_outbox WHERE sent_at IS NULL"
            ).fetchone()
        return int(row[0])

    def begin_installation(self) -> None:
        if self._setting("installation_required") is None:
            self._save_setting("installation_required", "1")

    def installation_required(self) -> bool:
        return self._setting("installation_required") == "1"

    def complete_installation(self) -> None:
        self._save_setting("installation_required", "0")

    def _save_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO runtime_settings(key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )

    def _setting(self, key: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM runtime_settings WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row[0]) if row else None

    def latest_staged(self) -> RuntimePublication | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT payload_json FROM runtime_publications
                WHERE active = 0 ORDER BY installed_at DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        try:
            return RuntimePublication.parse(json.loads(row[0]))
        except json.JSONDecodeError as error:
            raise RuntimePublicationError("Det hämtade driftpaketet är skadat") from error

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
            return {
                "configured": False,
                "linked": self.link_token() is not None,
                "server_name": self.server_name(),
                "pending_cloud_changes": self.pending_cloud_change_count(),
                "cloud_auto_sync": self.cloud_auto_sync_enabled(),
            }
        staged = self.latest_staged()
        return {
            "configured": True,
            "schema_version": publication.schema_version,
            "publication_id": publication.publication_id,
            "published_at": publication.published_at,
            "checksum": publication.checksum,
            "meet_id": publication.meet_id,
            "meet_name": publication.meet_name,
            "active_day": self.active_day(),
            "timezone": publication.timezone,
            "train_count": len(publication.payload["trains"]),
            "station_count": len(publication.payload["stations"]),
            "linked": self.link_token() is not None,
            "server_name": self.server_name(),
            "pending_cloud_changes": self.pending_cloud_change_count(),
            "cloud_auto_sync": self.cloud_auto_sync_enabled(),
            "available_publication_id": (
                staged.publication_id
                if staged is not None and staged.publication_id != publication.publication_id
                else None
            ),
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


def _fingerprint_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _movement_fingerprint(train: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _fingerprint_text(train.get("station_id")),
        _fingerprint_text(train.get("operating_point_id")),
        _fingerprint_text(train.get("train_number")),
        _fingerprint_text(train.get("days")),
        _fingerprint_text(train.get("track")),
        _fingerprint_text(train.get("arrival_time")),
        _fingerprint_text(train.get("departure_time")),
        _fingerprint_text(train.get("arrival_from")),
        _fingerprint_text(train.get("departure_to")),
        _fingerprint_text(train.get("arrival_from_next")),
        _fingerprint_text(train.get("departure_to_next")),
        _fingerprint_text(train.get("sort_time")),
        _fingerprint_text(train.get("train_type")),
        bool(train.get("no_stop")),
    )


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
