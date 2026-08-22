from __future__ import annotations

import json
import sqlite3
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import DispatchMode, TrackType
from .runtime import (
    AVAILABLE_CLOCK_STYLES,
    RUNTIME_SCHEMA_VERSION,
    RuntimePublication,
    RuntimePublicationError,
)


LOCAL_CONFIGURATION_SCHEMA_VERSION = 1
SLOT_KEYS = ("A", "B", "C", "D")
DISPLAY_SIDES = {"left", "right"}


class LocalConfigurationError(ValueError):
    pass


class ConfigurationRevisionConflict(LocalConfigurationError):
    pass


def empty_local_configuration() -> dict[str, Any]:
    return {
        "schema_version": LOCAL_CONFIGURATION_SCHEMA_VERSION,
        "id": "local-meet",
        "name": "Min träff",
        "timezone": "Europe/Stockholm",
        "active_day": "Dagl",
        "default_dispatch_mode": DispatchMode.CLEARANCE.value,
        "clock_time": "12:00",
        "stations": [],
        "connections": [],
        "panels": [],
        # Traffic, not just topology. A local configuration used to carry
        # stations and lines but never trains, so the package it built had
        # `trains: []` and the server ran a railway with nothing on it. That
        # special case is what D2 asked to remove.
        "tracks": [],
        "trains": [],
    }


#: Anything the server mints gets this prefix, so a later Cloud publication
#: can never collide with it however Cloud numbers its own rows.
LOCAL_ID_PREFIX = "local-"


def local_id(kind: str, *seed: str) -> str:
    """A stable id in the server's own namespace."""
    tail = "-".join(part.strip().casefold().replace(" ", "-") for part in seed if part)
    return f"{LOCAL_ID_PREFIX}{kind}-{tail}" if tail else f"{LOCAL_ID_PREFIX}{kind}"


def validate_local_configuration(
    value: dict[str, Any],
    *,
    require_runnable: bool = False,
) -> dict[str, Any]:
    if value.get("schema_version") != LOCAL_CONFIGURATION_SCHEMA_VERSION:
        raise LocalConfigurationError("Konfigurationen har en version som inte stöds")

    configuration = deepcopy(value)
    configuration["id"] = _required_text(configuration, "id")
    configuration["name"] = _required_text(configuration, "name")
    configuration["timezone"] = str(configuration.get("timezone") or "Europe/Stockholm")
    configuration["active_day"] = str(configuration.get("active_day") or "Dagl")
    configuration["clock_time"] = _clock_time(configuration.get("clock_time"))
    configuration["default_dispatch_mode"] = _enum_value(
        configuration.get("default_dispatch_mode", DispatchMode.CLEARANCE.value),
        DispatchMode,
        "trafikläge",
    )

    stations = _required_list(configuration, "stations")
    station_ids = _unique_ids(stations, "stations-id")
    station_codes: set[str] = set()
    for station in stations:
        station["id"] = _required_text(station, "id")
        station["code"] = _required_text(station, "code").upper()[:8]
        station["name"] = _required_text(station, "name")
        normalized_code = station["code"].casefold()
        if normalized_code in station_codes:
            raise LocalConfigurationError("Två stationer har samma stationskod")
        station_codes.add(normalized_code)

    connections = _required_list(configuration, "connections")
    connection_ids = _unique_ids(connections, "sträck-id")
    endpoint_pairs: set[frozenset[str]] = set()
    endpoint_map: dict[str, set[str]] = {}
    for connection in connections:
        connection["id"] = _required_text(connection, "id")
        station_a_id = _required_text(connection, "station_a_id")
        station_b_id = _required_text(connection, "station_b_id")
        if station_a_id == station_b_id:
            raise LocalConfigurationError("En sträcka måste gå mellan två olika stationer")
        if station_a_id not in station_ids or station_b_id not in station_ids:
            raise LocalConfigurationError("En sträcka hänvisar till en station som inte finns")
        pair = frozenset((station_a_id, station_b_id))
        if pair in endpoint_pairs:
            raise LocalConfigurationError("Samma två stationer är sammankopplade mer än en gång")
        endpoint_pairs.add(pair)
        endpoint_map[connection["id"]] = {station_a_id, station_b_id}
        connection["track_type"] = _enum_value(
            connection.get("track_type", TrackType.SINGLE.value),
            TrackType,
            "spårtyp",
        )
        override = connection.get("dispatch_mode_override")
        connection["dispatch_mode_override"] = (
            _enum_value(override, DispatchMode, "trafikläge på sträckan")
            if override
            else None
        )
        connection["display_side_a"] = _display_side(connection.get("display_side_a"), "right")
        connection["display_side_b"] = _display_side(connection.get("display_side_b"), "left")
        connection["display_order_a"] = _non_negative_int(connection.get("display_order_a", 0))
        connection["display_order_b"] = _non_negative_int(connection.get("display_order_b", 0))

    panels = _required_list(configuration, "panels")
    _unique_ids(panels, "panel-id")
    panel_names: set[tuple[str, str]] = set()
    stations_with_panel: set[str] = set()
    for panel in panels:
        panel["id"] = _required_text(panel, "id")
        station_id = _required_text(panel, "station_id")
        if station_id not in station_ids:
            raise LocalConfigurationError("En panel hänvisar till en station som inte finns")
        panel["name"] = _required_text(panel, "name")
        name_key = (station_id, panel["name"].casefold())
        if name_key in panel_names:
            raise LocalConfigurationError("Två paneler på samma station har samma namn")
        panel_names.add(name_key)
        stations_with_panel.add(station_id)
        slots = panel.get("slots")
        if not isinstance(slots, dict):
            raise LocalConfigurationError("En panel saknar A–D-konfiguration")
        unknown_slots = set(slots) - set(SLOT_KEYS)
        if unknown_slots:
            raise LocalConfigurationError("En panel innehåller andra platser än A–D")
        normalized_slots: dict[str, str | None] = {}
        used_connections: set[str] = set()
        for slot_key in SLOT_KEYS:
            connection_id = slots.get(slot_key) or None
            if connection_id is not None:
                if connection_id not in connection_ids:
                    raise LocalConfigurationError(f"Panelplats {slot_key} pekar på en sträcka som inte finns")
                if station_id not in endpoint_map[connection_id]:
                    raise LocalConfigurationError(
                        f"Panelplats {slot_key} pekar på en sträcka som inte når stationen"
                    )
                if connection_id in used_connections:
                    raise LocalConfigurationError("Samma sträcka används två gånger på en panel")
                used_connections.add(connection_id)
            normalized_slots[slot_key] = connection_id
        panel["slots"] = normalized_slots

    # A draft saved before D2 has neither key. Reading is what migrates it,
    # so an existing meet keeps working without a separate migration pass.
    configuration.setdefault("tracks", [])
    configuration.setdefault("trains", [])
    base = configuration.get("base_publication_id")
    configuration["base_publication_id"] = str(base).strip() if base else None

    tracks = _required_list(configuration, "tracks")
    track_ids = _unique_ids(tracks, "spår-id")
    track_stations: dict[str, str] = {}
    for track in tracks:
        track["id"] = _required_text(track, "id")
        station_id = _required_text(track, "station_id")
        if station_id not in station_ids:
            raise LocalConfigurationError("Ett spår hänvisar till en station som inte finns")
        track["station_id"] = station_id
        track["display_label"] = _required_text(track, "display_label")
        track["sort_order"] = _non_negative_int(track.get("sort_order", 0))
        track["active"] = bool(track.get("active", True))
        track_stations[track["id"]] = station_id

    trains = _required_list(configuration, "trains")
    _unique_ids(trains, "tågrörelse-id")
    for train in trains:
        train["id"] = _required_text(train, "id")
        train["train_number"] = _required_text(train, "train_number")
        station_id = _required_text(train, "station_id")
        if station_id not in station_ids:
            raise LocalConfigurationError("En tågrörelse hänvisar till en station som inte finns")
        train["station_id"] = station_id
        train["days"] = str(train.get("days") or "Dagl")
        arrival = _optional_clock(train.get("arrival_time"))
        departure = _optional_clock(train.get("departure_time"))
        if arrival is None and departure is None:
            raise LocalConfigurationError(
                f"Tåg {train['train_number']} saknar både ankomst- och avgångstid"
            )
        train["arrival_time"] = arrival
        train["departure_time"] = departure
        train["sort_time"] = arrival or departure
        track_id = train.get("track_id") or None
        if track_id is not None:
            if track_id not in track_ids:
                raise LocalConfigurationError(
                    f"Tåg {train['train_number']} hänvisar till ett spår som inte finns"
                )
            if track_stations[track_id] != station_id:
                raise LocalConfigurationError(
                    f"Tåg {train['train_number']} hänvisar till ett spår på fel station"
                )
        train["track_id"] = track_id
        train["train_type"] = str(train.get("train_type") or "person").lower()
        train["no_stop"] = bool(train.get("no_stop", False))
        for key in ("arrival_from", "departure_to", "arrival_from_next", "departure_to_next"):
            value = train.get(key)
            train[key] = str(value).strip() if value else None

    if require_runnable:
        if not stations:
            raise LocalConfigurationError("Lägg till minst en station före aktivering")
        if not panels:
            raise LocalConfigurationError("Lägg till minst en TMBox-panel före aktivering")
        missing = station_ids - stations_with_panel
        if missing:
            raise LocalConfigurationError("Varje station måste ha minst en TMBox-panel")

    return configuration


def _timetable(normalized: dict[str, Any]) -> tuple[list, list, list]:
    """Trains, routes and services, derived the way Cloud derives them.

    Routes and services are not something an operator edits - they fall out
    of the train rows. Deriving them here rather than storing them is what
    keeps a locally edited time from disagreeing with the timetable that
    shows it.
    """
    trains: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in normalized["trains"]:
        grouped.setdefault((row["train_number"], row["days"]), []).append(row)

    routes: list[dict[str, Any]] = []
    services: list[dict[str, Any]] = []
    station_names = {station["id"]: station["name"] for station in normalized["stations"]}

    for (number, days), rows in sorted(grouped.items()):
        service_id = local_id("service", number, days)
        ordered = sorted(rows, key=lambda item: _minutes(item["sort_time"]))
        stops = []
        previous = None
        day_offset = 0
        for index, row in enumerate(ordered):
            minute = _minutes(row["sort_time"])
            if previous is not None and minute < previous:
                day_offset += 1
            previous = minute
            stop = {
                "station_id": row["station_id"],
                "station_name": station_names.get(row["station_id"], row["station_id"]),
                "stop_order": index,
                "arrival_time": row["arrival_time"],
                "departure_time": row["departure_time"],
                "service_day_offset": day_offset,
                "service_minute": minute + day_offset * 24 * 60,
            }
            stops.append(stop)
            routes.append({
                "id": f"{service_id}-{index}",
                "service_id": service_id,
                "train_number": number,
                "days": days,
                **stop,
            })
            trains.append({**row, "service_id": service_id})
        services.append({
            "id": service_id,
            "train_number": number,
            "days": days,
            "train_type": ordered[0]["train_type"],
            "stops": stops,
        })
    return trains, routes, services


def _minutes(value: str) -> int:
    hours, _, minutes = value.partition(":")
    return int(hours) * 60 + int(minutes)


def local_configuration_runtime_package(
    configuration: dict[str, Any],
    *,
    revision: int,
    published_at: datetime | None = None,
) -> dict[str, Any]:
    normalized = validate_local_configuration(configuration, require_runnable=True)
    timestamp = (published_at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    trains, routes, services = _timetable(normalized)
    runtime_package = {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "publication_id": (
            f"{normalized['base_publication_id']}+local-r{revision}"
            if normalized.get("base_publication_id")
            else f"local-{normalized['id']}-r{revision}"
        ),
        "published_at": timestamp,
        "meet": {
            "id": normalized["id"],
            "name": normalized["name"],
            "slug": normalized["id"],
            "source": "local",
            "configuration_revision": revision,
            "active_day": normalized["active_day"],
            "timezone": normalized["timezone"],
            "default_dispatch_mode": normalized["default_dispatch_mode"],
            "clock_time": normalized["clock_time"],
        },
        "clock": {
            "source": "local",
            "start_time": normalized["clock_time"],
            "speed": 1,
            "show_seconds": True,
            "available_styles": list(AVAILABLE_CLOCK_STYLES),
            "stop_reasons": [
                {"key": "trafikstopp", "label": "Trafikstopp"},
                {"key": "rast", "label": "Rast"},
                {"key": "tekniskt", "label": "Tekniskt stopp"},
            ],
        },
        "stations": [
            {
                **station,
                "diagram_order": index,
                "is_autonomous": False,
                "is_topology_branch": False,
            }
            for index, station in enumerate(normalized["stations"])
        ],
        "connections": normalized["connections"],
        # Cloud mints the catalogue at import; a working copy seeded from a
        # publication carries it across, and a locally built meet starts with
        # none until somebody adds one.
        "tracks": [
            {
                "id": track["id"],
                "station_id": track["station_id"],
                "operating_point_id": None,
                "display_label": track["display_label"],
                "sort_order": track["sort_order"],
                "active": track["active"],
            }
            for track in normalized["tracks"]
        ],
        "autonomous_links": [],
        "panels": normalized["panels"],
        "trains": trains,
        "routes": routes,
        "services": services,
        "display": {
            "graph_station_order": [station["id"] for station in normalized["stations"]],
            "topology_branch_station_ids": [],
            "default_theme": "dark",
        },
    }
    try:
        RuntimePublication.parse(runtime_package)
    except RuntimePublicationError as error:
        raise LocalConfigurationError(str(error)) from error
    return runtime_package


def local_configuration_from_publication(payload: dict[str, Any]) -> dict[str, Any]:
    """Open a package fetched from Cloud as an editable working copy.

    This is the path D2 was missing. Before it, a server that could not reach
    Cloud could edit a configuration it had built itself and nothing else -
    which is no use at all during a meet, when the thing that needs correcting
    is the timetable Cloud published an hour ago.

    Cloud's ids are carried across unchanged. Only rows the server adds later
    get the `local-` prefix, so a later Cloud publication cannot collide with
    them and it stays visible which side a row came from.
    """
    meet = payload.get("meet") or {}
    clock = payload.get("clock") or {}
    stations = []
    for station in payload.get("stations") or []:
        stations.append({
            "id": str(station["id"]),
            "code": str(station.get("code") or station["id"])[:8].upper(),
            "name": str(station.get("name") or station["id"]),
        })
    known = {station["id"] for station in stations}

    connections = []
    for connection in payload.get("connections") or []:
        connections.append({
            "id": str(connection["id"]),
            "station_a_id": str(connection.get("station_a_id") or ""),
            "station_b_id": str(connection.get("station_b_id") or ""),
            "track_type": str(connection.get("track_type") or TrackType.SINGLE.value),
            "dispatch_mode_override": connection.get("dispatch_mode_override") or None,
            "display_side_a": connection.get("display_side_a") or "right",
            "display_side_b": connection.get("display_side_b") or "left",
            "display_order_a": connection.get("display_order_a") or 0,
            "display_order_b": connection.get("display_order_b") or 0,
        })

    tracks = []
    for track in payload.get("tracks") or []:
        if str(track.get("station_id")) not in known:
            continue
        tracks.append({
            "id": str(track["id"]),
            "station_id": str(track["station_id"]),
            "display_label": str(track.get("display_label") or track["id"]),
            "sort_order": int(track.get("sort_order") or 0),
            "active": bool(track.get("active", True)),
        })

    trains = []
    for row in payload.get("trains") or []:
        if str(row.get("station_id")) not in known:
            continue
        trains.append({
            "id": str(row["id"]),
            "train_number": str(row.get("train_number") or ""),
            "station_id": str(row["station_id"]),
            "days": str(row.get("days") or "Dagl"),
            "arrival_time": row.get("arrival_time"),
            "departure_time": row.get("departure_time"),
            "track_id": row.get("track_id"),
            "train_type": str(row.get("train_type") or "person"),
            "no_stop": bool(row.get("no_stop", False)),
            "arrival_from": row.get("arrival_from"),
            "departure_to": row.get("departure_to"),
            "arrival_from_next": row.get("arrival_from_next"),
            "departure_to_next": row.get("departure_to_next"),
        })

    draft = {
        "schema_version": LOCAL_CONFIGURATION_SCHEMA_VERSION,
        "id": str(meet.get("id") or "local-meet"),
        "name": str(meet.get("name") or "Hämtad träff"),
        "timezone": str(meet.get("timezone") or "Europe/Stockholm"),
        "active_day": str(meet.get("active_day") or "Dagl"),
        "default_dispatch_mode": str(
            meet.get("default_dispatch_mode") or DispatchMode.CLEARANCE.value
        ),
        "clock_time": str(clock.get("start_time") or meet.get("clock_time") or "12:00"),
        "stations": stations,
        "connections": connections,
        "panels": [dict(panel) for panel in payload.get("panels") or []],
        "tracks": tracks,
        "trains": trains,
        # Which publication this copy started from. An activation writes
        # `<base>+local-rN`, so a box can see that it is running a local
        # revision of a known Cloud package rather than something unrelated.
        "base_publication_id": str(payload.get("publication_id") or "") or None,
    }
    return validate_local_configuration(draft)


class SQLiteLocalConfigurationStore:
    """Versioned editable local configuration kept separately from active runtime state."""

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
            CREATE TABLE IF NOT EXISTS local_configuration_revisions (
                configuration_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0),
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(configuration_id, revision)
            );
            CREATE TABLE IF NOT EXISTS local_configuration_current (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                configuration_id TEXT NOT NULL,
                revision INTEGER NOT NULL
            );
            """
        )

    def current(self) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT current.revision, revisions.payload_json, revisions.created_at
                FROM local_configuration_current current
                JOIN local_configuration_revisions revisions
                  ON revisions.configuration_id = current.configuration_id
                 AND revisions.revision = current.revision
                WHERE current.singleton = 1
                """
            ).fetchone()
        if row is None:
            return {
                "configured": False,
                "revision": 0,
                "draft": empty_local_configuration(),
                "updated_at": None,
            }
        try:
            payload = json.loads(row[1])
        except json.JSONDecodeError as error:
            raise LocalConfigurationError("Det lokala konfigurationsutkastet är skadat") from error
        return {
            "configured": True,
            "revision": int(row[0]),
            "draft": validate_local_configuration(payload),
            "updated_at": row[2],
        }

    def save(self, payload: dict[str, Any], *, expected_revision: int | None = None) -> dict[str, Any]:
        normalized = validate_local_configuration(payload)
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT configuration_id, revision FROM local_configuration_current WHERE singleton = 1"
                ).fetchone()
                current_revision = int(row[1]) if row is not None else 0
                if expected_revision is not None and expected_revision != current_revision:
                    raise ConfigurationRevisionConflict(
                        "Konfigurationen ändrades av en annan klient. Ladda om och försök igen."
                    )
                revision = current_revision + 1
                self._connection.execute(
                    """
                    INSERT INTO local_configuration_revisions(
                        configuration_id, revision, payload_json
                    ) VALUES (?, ?, ?)
                    """,
                    (normalized["id"], revision, encoded),
                )
                self._connection.execute(
                    """
                    INSERT INTO local_configuration_current(singleton, configuration_id, revision)
                    VALUES (1, ?, ?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        configuration_id = excluded.configuration_id,
                        revision = excluded.revision
                    """,
                    (normalized["id"], revision),
                )
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        return self.current()

    def seed_from_publication(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Open a fetched Cloud package as the editable working copy.

        Saved as a new revision like any other edit, so the previous working
        copy is not lost - a server that seeds by mistake can go back.
        """
        return self.save(local_configuration_from_publication(payload))

    def runtime_package(self, *, expected_revision: int | None = None) -> dict[str, Any]:
        current = self.current()
        if not current["configured"]:
            raise LocalConfigurationError("Spara den lokala konfigurationen före aktivering")
        if expected_revision is not None and expected_revision != current["revision"]:
            raise ConfigurationRevisionConflict(
                "Konfigurationen ändrades av en annan klient. Ladda om och försök igen."
            )
        return local_configuration_runtime_package(
            current["draft"],
            revision=current["revision"],
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _required_text(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise LocalConfigurationError(f"Fältet {key} får inte vara tomt")
    return result.strip()


def _required_list(value: dict[str, Any], key: str) -> list[dict[str, Any]]:
    result = value.get(key)
    if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
        raise LocalConfigurationError(f"Konfigurationen saknar en giltig lista för {key}")
    return result


def _unique_ids(values: list[dict[str, Any]], label: str) -> set[str]:
    ids = {_required_text(value, "id") for value in values}
    if len(ids) != len(values):
        raise LocalConfigurationError(f"Konfigurationen innehåller dubbla {label}")
    return ids


def _enum_value(value: Any, enum_type: type, label: str) -> str:
    try:
        return enum_type(value).value
    except (TypeError, ValueError) as error:
        raise LocalConfigurationError(f"Ogiltigt {label}") from error


def _optional_clock(value: Any) -> str | None:
    """`HH:MM`, or None when there is no such leg for this row."""
    if value is None or str(value).strip() == "":
        return None
    return _clock_time(value)


def _clock_time(value: Any) -> str:
    text = str(value or "12:00")
    parts = text.split(":")
    if len(parts) != 2:
        raise LocalConfigurationError("Klocktiden ska anges som TT:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as error:
        raise LocalConfigurationError("Klocktiden ska anges som TT:MM") from error
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise LocalConfigurationError("Klocktiden ska anges som TT:MM")
    return f"{hour:02d}:{minute:02d}"


def _display_side(value: Any, default: str) -> str:
    side = str(value or default)
    if side not in DISPLAY_SIDES:
        raise LocalConfigurationError("Visuell sida ska vara vänster eller höger")
    return side


def _non_negative_int(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise LocalConfigurationError("Visuell ordning måste vara ett heltal") from error
    if result < 0:
        raise LocalConfigurationError("Visuell ordning får inte vara negativ")
    return result
