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
    }


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

    if require_runnable:
        if not stations:
            raise LocalConfigurationError("Lägg till minst en station före aktivering")
        if not panels:
            raise LocalConfigurationError("Lägg till minst en Tambox-panel före aktivering")
        missing = station_ids - stations_with_panel
        if missing:
            raise LocalConfigurationError("Varje station måste ha minst en Tambox-panel")

    return configuration


def local_configuration_runtime_package(
    configuration: dict[str, Any],
    *,
    revision: int,
    published_at: datetime | None = None,
) -> dict[str, Any]:
    normalized = validate_local_configuration(configuration, require_runnable=True)
    timestamp = (published_at or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    runtime_package = {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "publication_id": f"local-{normalized['id']}-r{revision}",
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
        "autonomous_links": [],
        "panels": normalized["panels"],
        "trains": [],
        "routes": [],
        "services": [],
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
