from __future__ import annotations

import json
import ipaddress
import logging
import mimetypes
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .engine import TrafficEngine
from .central_sync import (
    DEFAULT_RUNTIME_PUBLICATION_URL,
    CentralRuntimeDownload,
    CentralRuntimeManifest,
    CentralSyncError,
    canonical_runtime_url,
    fetch_linked_runtime,
    fetch_runtime_download,
)
from .identity import (
    AdminAccessError,
    DeviceKind,
    IdentityStore,
    PairingError,
    PairingService,
    PairedClient,
)
from .local_config import (
    ConfigurationRevisionConflict,
    LocalConfigurationError,
    SQLiteLocalConfigurationStore,
)
from .models import Command, TrackConfig, TrackType, UnknownTrackError, resolve_track_id
from .observability import log_event, use_correlation
from .operations import SQLiteOperationsStore
from .protocol_v2 import TMBoxStationService, find_track_conflict
from .runtime import (
    AVAILABLE_CLOCK_STYLES,
    DISPLAY_SCREENS,
    RuntimePublication,
    RuntimePublicationError,
    SQLiteRuntimeStore,
)
from .software_update import (
    SoftwareUpdateError,
    installed_build,
    installed_version,
    latest_version,
    read_update_status,
    start_update,
)


LOGGER = logging.getLogger("tmbox_gateway.http")
MAX_REQUEST_BYTES = 4 * 1024 * 1024
ADMIN_COOKIE_NAME = "trainmeet_admin"
ADMIN_COOKIE_MAX_AGE = 12 * 60 * 60


def _tkl_engine_reason(reason: str) -> str:
    return {
        "connection_busy": "Sträckan är redan upptagen",
        "departure_not_reserved": "Tåget saknar beviljad klarering",
        "train_not_departed": "Tåget finns inte registrerat på sträckan",
        "request_no_longer_pending": "Klareringsförfrågan gäller inte längre",
        "interaction_owned": "En annan terminal arbetar redan med samma A–D-panel",
    }.get(reason, "Sträckåtgärden kunde inte genomföras")


@dataclass(frozen=True)
class HTTPServerConfig:
    gateway_id: str = "gateway-local"
    mqtt_port: int = 1883
    advertised_mqtt_host: str | None = None
    local_development: bool = False
    central_runtime_url: str = DEFAULT_RUNTIME_PUBLICATION_URL
    allow_restart: bool = False
    allow_software_update: bool = False
    state_dir: str = "data/local"
    force_external_auth: bool = False
    http_port: int = 8787
    local_ip: str = ""
    connection_code: str = ""


class TrainMeetHTTPApplication:
    def __init__(
        self,
        engine: TrafficEngine,
        identities: IdentityStore,
        pairing: PairingService,
        config: HTTPServerConfig,
        runtime_store: SQLiteRuntimeStore | None = None,
        local_configuration_store: SQLiteLocalConfigurationStore | None = None,
        runtime_fetcher: Callable[[str, str], Any] | None = None,
        linked_runtime_fetcher: Callable[[str, str, bool], Any] | None = None,
        operations_store: SQLiteOperationsStore | None = None,
        station_service: TMBoxStationService | None = None,
    ):
        self.engine = engine
        self.identities = identities
        self.pairing = pairing
        self.config = config
        self.runtime_store = runtime_store
        self.local_configuration_store = local_configuration_store
        self.operations_store = operations_store
        self._station_service = station_service
        self.runtime_fetcher = runtime_fetcher or (
            lambda code, url: fetch_runtime_download(
                code,
                url,
                server_name=(self.runtime_store.server_name() if self.runtime_store else self.config.gateway_id),
            )
        )
        self.linked_runtime_fetcher = linked_runtime_fetcher or (
            lambda token, url, manifest_only: fetch_linked_runtime(
                token, url, manifest_only=manifest_only
            )
        )
        self.web_root = files("tmbox_gateway").joinpath("web")
        self.tkl_web_root = files("tmbox_gateway").joinpath("tkl")

        if self.runtime_store is not None and self.runtime_store.central_url():
            saved_url = self.runtime_store.central_url() or ""
            migrated_url = canonical_runtime_url(saved_url)
            if migrated_url != saved_url:
                self.runtime_store.save_central_url(migrated_url)

        if self.operations_store is not None:
            self.engine.set_transition_observer(self.operations_store.record_engine_transition)
            self.engine.set_clock_source(self.operations_store.clock_status)
            if self.runtime_store is not None:
                active = self.runtime_store.active()
                if active is not None:
                    self.operations_store.ensure_publication(active)

    def local_admin(self) -> PairedClient:
        return PairedClient(
            client_id="local-web-admin",
            display_name="Lokal administratör",
            kind=DeviceKind.WEB_ADMIN,
            panel_ids=tuple(sorted(self.engine.config.panels)),
        )

    def admin_access(self, client: PairedClient) -> dict[str, object]:
        self._require_admin(client)
        return self.identities.admin_access_summary()

    def configure_admin_access(
        self,
        client: PairedClient,
        payload: dict[str, Any],
    ) -> dict[str, object]:
        self._require_admin(client)
        password_value = payload.get("password")
        password = None if password_value in {None, ""} else str(password_value)
        try:
            return self.identities.configure_admin_access(
                str(payload.get("username", "")),
                password,
            )
        except AdminAccessError as error:
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_admin_access",
                str(error),
            ) from error

    def installation_status(self) -> dict[str, Any]:
        access = self.identities.admin_access_summary()
        runtime = self.runtime_store.summary() if self.runtime_store is not None else {"configured": False}
        required = (
            not bool(access["password_configured"])
            or bool(self.runtime_store and self.runtime_store.installation_required())
        )
        server_name = self.runtime_store.server_name() if self.runtime_store is not None else None
        if not access["password_configured"]:
            step = "admin"
        elif not server_name:
            step = "server"
        elif not runtime.get("configured"):
            step = "central"
        else:
            step = "finish"
        return {
            "required": required,
            "step": step,
            "admin_configured": bool(access["password_configured"]),
            # No username either. /v1/setup is unauthenticated by design -
            # it has to answer before anyone can log in - so it must not name
            # the administrator. Nothing read it.
            "server_name": server_name,
            "central_url": (
                self.runtime_store.central_url()
                if self.runtime_store is not None and self.runtime_store.central_url()
                else self.config.central_runtime_url
            ),
            "runtime": runtime,
        }

    def create_initial_admin(self, payload: dict[str, Any]) -> dict[str, object]:
        if self.identities.admin_access_summary()["password_configured"]:
            raise HTTPAPIError(
                HTTPStatus.CONFLICT,
                "admin_already_configured",
                "Administratören är redan skapad",
            )
        password = str(payload.get("password", ""))
        try:
            return self.identities.configure_admin_access(
                str(payload.get("username", "")),
                password,
            )
        except AdminAccessError as error:
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_admin_access",
                str(error),
            ) from error

    def save_initial_server_name(
        self,
        client: PairedClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal lagring saknas")
        try:
            name = self.runtime_store.save_server_name(str(payload.get("server_name", "")))
        except RuntimePublicationError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_server_name", str(error)) from error
        return {"server_name": name, "installation": self.installation_status()}

    def complete_installation(
        self,
        client: PairedClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_admin(client)
        if self.runtime_store is None or self.runtime_store.active() is None:
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "runtime_not_configured",
                "Hämta och aktivera en träff innan installationen avslutas",
            )
        if not self.runtime_store.server_name():
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "server_name_missing",
                "Ge servern ett namn innan installationen avslutas",
            )
        active_day = str(payload.get("active_day") or self.runtime_store.active_day() or "").strip()
        try:
            self.runtime_store.set_active_day(active_day)
        except RuntimePublicationError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_active_day", str(error)) from error
        self.runtime_store.complete_installation()
        return {
            "completed": True,
            "active_day": active_day,
            "restart_required": self.runtime_store.active().session_config() != self.engine.config,
            "message": "Grundinstallationen är klar. Starta om servern för att börja köra träffen.",
        }

    def pair(self, payload: dict[str, Any], request_host: str) -> dict[str, Any]:
        try:
            kind = DeviceKind(str(payload.get("device_kind", DeviceKind.SWIFT_PANEL.value)))
        except ValueError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_device_kind", "Okänd enhetstyp") from error

        try:
            result = self.pairing.pair(
                pairing_code=str(payload.get("pairing_code", "")),
                client_id=str(payload.get("client_id", "")),
                display_name=str(payload.get("display_name", "")),
                kind=kind,
            )
        except PairingError as error:
            raise HTTPAPIError(HTTPStatus.UNAUTHORIZED, error.code, str(error)) from error

        mqtt_host = self.config.advertised_mqtt_host or _hostname_without_port(request_host)
        response = {
            "protocol_version": 1,
            "gateway_id": self.config.gateway_id,
            "client_id": result.client.client_id,
            "device_kind": result.client.kind.value,
            "assigned_panel_ids": list(result.client.panel_ids),
            "mqtt": {
                "host": mqtt_host,
                "port": self.config.mqtt_port,
                "tls": False,
            },
        }
        if result.client.kind in {
            DeviceKind.SWIFT_PANEL,
            DeviceKind.WEB_ADMIN,
            DeviceKind.SWIFT_ADMIN,
            DeviceKind.TKL_TERMINAL,
        }:
            response["access_token"] = result.access_token
        return response

    def snapshots(self, client: PairedClient) -> dict[str, Any]:
        snapshots = self.engine.snapshots()
        return {
            "protocol_version": 1,
            "snapshots": [
                snapshots[panel_id]
                for panel_id in client.panel_ids
                if panel_id in snapshots
            ],
        }

    def command(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        panel_id = str(payload.get("panel_id", ""))
        if panel_id not in client.panel_ids:
            raise HTTPAPIError(
                HTTPStatus.FORBIDDEN,
                "panel_not_assigned",
                "Enheten har inte tillgång till den panelen",
            )
        now = datetime.now(timezone.utc)
        try:
            command = Command(
                command_id=str(payload.get("command_id") or uuid4()),
                client_id=client.client_id,
                traffic_session_id=self.engine.config.id,
                panel_id=panel_id,
                expected_revision=int(payload["expected_revision"]),
                key=str(payload["key"]),
                sent_at=now,
                expires_at=now + timedelta(seconds=5),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_command",
                "Kommandot saknar giltig tangent eller version",
            ) from error

        ack = self.engine.press(command).to_dict()
        ack["snapshots"] = {
            panel_id: snapshot
            for panel_id, snapshot in ack["snapshots"].items()
            if panel_id in client.panel_ids
        }
        return ack

    # ------------------------------------------------------------- protocol v2
    #
    # These four calls are the MQTT gateway's four operations over HTTP, and
    # nothing more. A box reads three retained topics and publishes complete
    # commands; the simulator does the same over request/response, so what it
    # exercises is the wire contract itself rather than a parallel API shaped
    # for a browser.

    @property
    def station_service(self) -> TMBoxStationService:
        if self._station_service is None:
            if self.runtime_store is None or self.operations_store is None:
                raise HTTPAPIError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "runtime_missing",
                    "Servern har ingen aktiv träff att simulera mot",
                )
            self._station_service = TMBoxStationService(
                self.runtime_store, self.operations_store, self.identities
            )
        return self._station_service

    def tmbox_v2_assignment(self, client: PairedClient, device_id: str) -> dict[str, Any]:
        self._require_admin(client)
        if not device_id:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "device_required", "Ange en enhet")
        return self.station_service.assignment_payload(device_id)

    def tmbox_v2_config(self, client: PairedClient, station_id: str) -> dict[str, Any]:
        self._require_admin(client)
        payload = self.station_service.config_payload(station_id)
        if payload is None:
            raise HTTPAPIError(
                HTTPStatus.NOT_FOUND, "unknown_station", "Stationen finns inte i den aktiva träffen"
            )
        return payload

    def tmbox_v2_snapshot(self, client: PairedClient, station_id: str) -> dict[str, Any]:
        self._require_admin(client)
        payload = self.station_service.snapshot_payload(station_id)
        if payload is None:
            raise HTTPAPIError(
                HTTPStatus.NOT_FOUND, "unknown_station", "Stationen finns inte i den aktiva träffen"
            )
        return payload

    def tmbox_v2_command(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        device_id = str(payload.get("device_id") or "").strip()
        if not device_id:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "device_required", "Ange en enhet")
        envelope = payload.get("command")
        if not isinstance(envelope, dict):
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST, "command_required", "Kommandot saknas eller har fel form"
            )
        # A rejection is an answer, not a transport failure: the box renders
        # the reason on its display, so the simulator has to receive it too.
        return self.station_service.handle_command(device_id, envelope)

    def tmbox_v2_stations(self, client: PairedClient) -> dict[str, Any]:
        """Which stations the simulator can stand in for, and which boxes exist."""
        self._require_admin(client)
        config = self.station_service.session_config()
        stations = (
            [
                {"id": station.id, "name": station.name, "code": getattr(station, "code", "")}
                for station in config.stations.values()
            ]
            if config is not None
            else []
        )
        return {"stations": sorted(stations, key=lambda entry: entry["name"])}

    def devices(self, client: PairedClient) -> dict[str, Any]:
        self._require_admin(client)
        return {
            "devices": [
                {
                    "device_id": device.device_id,
                    "device_code": device.device_code,
                    "model": device.model,
                    "firmware_version": device.firmware_version,
                    "last_seen_at": device.last_seen_at,
                    "assigned_panel_ids": list(device.panel_ids),
                    "station_id": device.station_id,
                    "hardware_version": device.hardware_version,
                    "protocol_version": device.protocol_version,
                    "display": device.display.to_dict(),
                }
                for device in self.identities.discovered_devices()
            ],
            "stations": [
                {"id": station.id, "code": station.code, "name": station.name}
                for station in self.engine.config.stations.values()
            ],
        }

    def runtime_summary(self, client: PairedClient) -> dict[str, Any]:
        del client
        if self.runtime_store is None:
            return {"configured": False, "central_url": canonical_runtime_url(self.config.central_runtime_url)}
        return {
            **self.runtime_store.summary(),
            "central_url": canonical_runtime_url(self.runtime_store.central_url() or self.config.central_runtime_url),
        }

    def display_snapshot(self, request_host: str = "") -> dict[str, Any]:
        publication = self.runtime_store.active() if self.runtime_store is not None else None
        if publication is not None:
            active_day = self.runtime_store.active_day() or publication.active_day
            timetable = publication.timetable(active_day=active_day)
            stations = publication.payload["stations"]
            connections = publication.payload["connections"]
            autonomous_links = publication.payload.get("autonomous_links", [])
            display = publication.payload.get("display", {})
            meet = publication.payload["meet"]
            publication_id = publication.publication_id
        else:
            active_day = "Dagl"
            timetable = {"trains": [], "routes": [], "services": []}
            stations = [
                {"id": station.id, "code": station.code, "name": station.name, "diagram_order": index}
                for index, station in enumerate(self.engine.config.stations.values())
            ]
            connections = [
                {
                    "id": connection.id,
                    "station_a_id": connection.station_a_id,
                    "station_b_id": connection.station_b_id,
                    "track_type": connection.track_type.value,
                }
                for connection in self.engine.config.connections.values()
            ]
            autonomous_links = []
            display = {"graph_station_order": [station["id"] for station in stations], "default_theme": "dark"}
            meet = {"id": self.engine.config.id, "name": self.engine.config.name, "active_day": active_day}
            publication_id = self.engine.config.id

        runtime_connections = self.engine.export_state()["connections"]
        connection_states = [
            {"id": connection["id"], **runtime_connections.get(connection["id"], {"state": "free"})}
            for connection in connections
        ]
        if self.operations_store is not None:
            clock = self.operations_store.clock_status()
            positions = self.operations_store.positions()
        else:
            clock = {
                "configured": True,
                "time": f"{self.engine.config.clock_time[:5]}:00",
                "speed": 1,
                "running": True,
                "stopped_reason": None,
                "show_seconds": True,
                "available_styles": list(AVAILABLE_CLOCK_STYLES),
            }
            positions = []
        return {
            "protocol_version": 1,
            "revision": self.engine.revision,
            "publication_id": publication_id,
            "meet": meet,
            "active_day": active_day,
            "stations": stations,
            "connections": connections,
            "connection_states": connection_states,
            "autonomous_links": autonomous_links,
            "tracks": timetable.get("tracks", []),
            "trains": timetable.get("trains", []),
            "routes": timetable.get("routes", []),
            "services": timetable.get("services", []),
            "display": display,
            "clock": clock,
            "train_positions": positions,
            "connection": self.connection_details(request_host),
            "server_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    def connection_details(self, request_host: str = "") -> dict[str, Any]:
        """Address and code a TMBox needs, plus the screens allowed to show it.

        A guessed LAN address is used when there is one. On a host whose own
        name does not resolve to anything but loopback - a cloud droplet, most
        Docker setups - that guess is 127.0.0.1, which is useless to a TMBox
        on someone else's network. The address the display page itself was
        just loaded through is a working fallback, the same way pair() already
        derives the MQTT host devices should use.
        """
        screens = (
            self.runtime_store.connection_badge_screens()
            if self.runtime_store is not None
            else list(DISPLAY_SCREENS)
        )
        host = self.config.local_ip
        if (not host or _is_loopback_address(host)) and request_host:
            host = _hostname_without_port(request_host)
        return {
            "host": host,
            "port": self.config.http_port,
            "code": self.config.connection_code,
            "screens": screens,
            "validity_hours": (
                self.runtime_store.connection_code_validity_hours()
                if self.runtime_store is not None
                else 0
            ),
        }

    def configure_connection_badge(
        self,
        client: PairedClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal lagring saknas")
        screens = payload.get("screens")
        if not isinstance(screens, list):
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_screens", "Skärmvalet måste vara en lista")
        self.runtime_store.set_connection_badge_screens(screens)
        previous_hours = self.runtime_store.connection_code_validity_hours()
        changed_validity = False
        if payload.get("validity_hours") is not None:
            try:
                hours = int(payload["validity_hours"])
            except (TypeError, ValueError) as error:
                raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_validity", "Ogiltig giltighetstid") from error
            try:
                self.runtime_store.set_connection_code_validity_hours(hours)
            except RuntimePublicationError as error:
                raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_validity", str(error)) from error
            changed_validity = hours != previous_hours
        return {
            **self.connection_details(),
            # Screens follow immediately; the lifetime is applied to the code the
            # next time the server issues it, which happens at start-up.
            "restart_required": changed_validity,
        }

    def track_catalogue(self) -> dict[str, TrackConfig]:
        """The catalogue that governs writes right now.

        The active publication owns it. The engine's own copy is the fallback
        for a server running a locally built configuration, where there is no
        publication to read.
        """
        publication = self.runtime_store.active() if self.runtime_store is not None else None
        if publication is not None:
            return publication.track_catalogue()
        return self.engine.config.tracks

    def tkl_context(self, client: PairedClient, station_id: str) -> dict[str, Any]:
        if self.operations_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "tkl_unavailable", "TKL-driftlagret är inte tillgängligt")
        snapshot = self.display_snapshot()
        station = next((item for item in snapshot["stations"] if item["id"] == station_id), None)
        if station is None:
            raise HTTPAPIError(HTTPStatus.NOT_FOUND, "station_not_found", "Stationen finns inte i den aktiva träffen")
        self._require_station_access(client, station_id)
        related_connection_ids = {
            connection["id"]
            for connection in snapshot["connections"]
            if station_id in {connection["station_a_id"], connection["station_b_id"]}
        }
        connection_states = [
            state for state in snapshot["connection_states"] if state["id"] in related_connection_ids
        ]
        trains = [train for train in snapshot["trains"] if train.get("station_id") == station_id]
        state = self.operations_store.tkl_station_state(
            snapshot["publication_id"],
            snapshot["active_day"],
            station_id,
        )
        return {
            "protocol_version": 1,
            "publication_id": snapshot["publication_id"],
            "meet": snapshot["meet"],
            "active_day": snapshot["active_day"],
            "station": station,
            "terminal": {"client_id": client.client_id, "display_name": client.display_name, "kind": client.kind.value},
            "preflight": {
                "server_online": True,
                "clock_configured": bool(snapshot["clock"].get("configured", True)),
                "clock_running": bool(snapshot["clock"].get("running", False)),
                "track_count": sum(
                    1
                    for track in self.track_catalogue().values()
                    if track.station_id == station_id and track.active
                ),
                "connection_count": len(related_connection_ids),
                "train_count": len(trains),
                "open_connection_count": sum(1 for state in connection_states if state.get("state") != "free"),
            },
            "shift": state["shift"],
            "previous_shift": state["previous_shift"],
            "movements": state["movements"],
            "connection_states": connection_states,
        }

    def start_tkl_shift(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        if self.operations_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "tkl_unavailable", "TKL-driftlagret är inte tillgängligt")
        station_id = str(payload.get("station_id") or "")
        self._require_station_access(client, station_id)
        snapshot = self.display_snapshot()
        if not any(station["id"] == station_id for station in snapshot["stations"]):
            raise HTTPAPIError(HTTPStatus.NOT_FOUND, "station_not_found", "Stationen finns inte i den aktiva träffen")
        try:
            shift = self.operations_store.start_tkl_shift(
                snapshot["publication_id"],
                snapshot["active_day"],
                station_id,
                str(payload.get("operator_name") or ""),
                str(payload.get("terminal_name") or client.display_name),
                take_over=bool(payload.get("take_over", False)),
            )
        except ValueError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_tkl_shift", str(error)) from error
        return {"shift": shift}

    def finish_tkl_shift(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        if self.operations_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "tkl_unavailable", "TKL-driftlagret är inte tillgängligt")
        station_id = str(payload.get("station_id") or "")
        self._require_station_access(client, station_id)
        snapshot = self.display_snapshot()
        current_shift = self.operations_store.tkl_station_state(
            snapshot["publication_id"], snapshot["active_day"], station_id
        )["shift"]
        if current_shift is None or current_shift["shift_id"] != str(payload.get("shift_id") or ""):
            raise HTTPAPIError(
                HTTPStatus.CONFLICT,
                "tkl_shift_not_active",
                "Trafikpasset är inte längre aktivt på den här stationen",
            )
        status = str(payload.get("status") or "")
        if status == "closed":
            related_ids = {
                connection["id"]
                for connection in snapshot["connections"]
                if station_id in {connection["station_a_id"], connection["station_b_id"]}
            }
            blockers = [
                state for state in snapshot["connection_states"]
                if state["id"] in related_ids and state.get("state") != "free"
            ]
            if blockers:
                raise HTTPAPIError(
                    HTTPStatus.CONFLICT,
                    "tkl_shift_has_open_connections",
                    "Stationen har pågående klareringar eller tåg på linjen och kan inte avslutas",
                )
        try:
            result = self.operations_store.finish_tkl_shift(
                str(payload.get("shift_id") or ""),
                status=status,
                note=str(payload.get("note") or ""),
            )
        except ValueError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_tkl_shift", str(error)) from error
        return {"shift": result}

    def update_tkl_movement(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        if self.operations_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "tkl_unavailable", "TKL-driftlagret är inte tillgängligt")
        station_id = str(payload.get("station_id") or "")
        movement_id = str(payload.get("movement_id") or "")
        self._require_station_access(client, station_id)
        snapshot = self.display_snapshot()
        movement = next(
            (
                train for train in snapshot["trains"]
                if train.get("id") == movement_id and train.get("station_id") == station_id
            ),
            None,
        )
        if movement is None:
            raise HTTPAPIError(HTTPStatus.NOT_FOUND, "movement_not_found", "Tågrörelsen finns inte på stationen")
        current_shift = self.operations_store.tkl_station_state(
            snapshot["publication_id"], snapshot["active_day"], station_id
        )["shift"]
        if current_shift is None:
            raise HTTPAPIError(HTTPStatus.CONFLICT, "tkl_shift_not_started", "Starta trafikpasset innan tågrörelser hanteras")
        try:
            actual_track = resolve_track_id(
                self.track_catalogue(), station_id, str(payload.get("actual_track") or "")
            )
        except UnknownTrackError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "unknown_track", str(error)) from error
        if actual_track:
            publication = self.runtime_store.active() if self.runtime_store is not None else None
            if publication is not None:
                conflict = find_track_conflict(
                    publication.payload["trains"],
                    self.operations_store.tkl_station_state(
                        snapshot["publication_id"], snapshot["active_day"], station_id
                    )["movements"],
                    station_id,
                    snapshot["active_day"],
                    movement_id,
                    actual_track,
                )
                if conflict is not None:
                    raise HTTPAPIError(
                        HTTPStatus.CONFLICT,
                        "track_occupied",
                        f"Spåret är upptaget av tåg {conflict.get('train_number') or '?'}",
                    )
        try:
            result = self.operations_store.update_tkl_movement(
                snapshot["publication_id"],
                snapshot["active_day"],
                station_id,
                movement_id,
                arrival=str(payload.get("arrival") or "none"),
                departure=str(payload.get("departure") or "none"),
                actual_track=actual_track,
                updated_by=current_shift["operator_name"],
                shift_id=current_shift["shift_id"],
                event_type=str(payload.get("event_type") or "movement_updated")[:80],
            )
        except ValueError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_tkl_movement", str(error)) from error
        return {"movement": result}

    def tkl_clearance_action(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        """Drive one clearance step for a TKL terminal.

        This used to be called tkl_line_action, which read as if it were the
        one-sided line-available message. It is not: it requests, answers,
        cancels and closes a clearance. The real line-available message has
        its own endpoint.
        """
        if self.operations_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "tkl_unavailable", "TKL-driftlagret är inte tillgängligt")
        station_id = str(payload.get("station_id") or "")
        connection_id = str(payload.get("connection_id") or "")
        train_number = str(payload.get("train_number") or "").strip()
        action = str(payload.get("action") or "")
        self._require_station_access(client, station_id)
        snapshot = self.display_snapshot()
        current_shift = self.operations_store.tkl_station_state(
            snapshot["publication_id"], snapshot["active_day"], station_id
        )["shift"]
        if current_shift is None:
            raise HTTPAPIError(
                HTTPStatus.CONFLICT,
                "tkl_shift_not_started",
                "Starta trafikpasset innan en tågklarering hanteras",
            )
        connection = self.engine.config.connections.get(connection_id)
        if connection is None or station_id not in (
            connection.station_a_id,
            connection.station_b_id,
        ):
            raise HTTPAPIError(
                HTTPStatus.FORBIDDEN,
                "connection_not_assigned",
                "Terminalen har inte tillgång till sträckan",
            )
        if action not in {"request", "accept", "reject", "cancel", "depart", "arrive"} or (
            action == "request" and (not train_number or not train_number.isdigit())
        ):
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_tkl_clearance_action",
                "Ogiltig sträckåtgärd eller tågnummer",
            )
        with use_correlation(f"tkl-{uuid4().hex[:12]}") as trace:
            accepted, reason = self.engine.perform(
                station_id=station_id,
                connection_id=connection_id,
                action=action,
                train_number=train_number,
                client_id=client.client_id,
            )
            self.operations_store.record_audit_event(
                correlation_id=trace,
                source="tkl",
                actor=client.client_id,
                action=f"clearance.{action}",
                outcome="accepted" if accepted else "rejected",
                station_id=station_id,
                reason=None if accepted else str(reason or ""),
                detail={"connection_id": connection_id, "train_number": train_number},
            )
            log_event(
                LOGGER,
                "clearance.accepted" if accepted else "clearance.rejected",
                level=logging.INFO if accepted else logging.WARNING,
                actor=client.client_id,
                station_id=station_id,
                connection_id=connection_id,
                action=action,
                reason=None if accepted else str(reason or ""),
            )
        if not accepted:
            raise HTTPAPIError(
                HTTPStatus.CONFLICT,
                "tkl_clearance_action_rejected",
                _tkl_engine_reason(str(reason or "")),
            )
        snapshot = self.display_snapshot()
        state = next((item for item in snapshot["connection_states"] if item["id"] == connection_id), None)
        return {"action": action, "connection": state, "revision": self.engine.revision}

    def tkl_line_available(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        """Send or acknowledge a line-available message.

        One-sided information, never a question. It is never checked against
        channel occupancy and never becomes a clearance case, so a receiving
        station can only acknowledge that it was shown.
        """
        if self.operations_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "tkl_unavailable", "TKL-driftlagret är inte tillgängligt")
        station_id = str(payload.get("station_id") or "")
        self._require_station_access(client, station_id)
        snapshot = self.display_snapshot()
        action = str(payload.get("action") or "publish")
        if action == "acknowledge":
            message_id = str(payload.get("message_id") or "")
            message = self.operations_store.line_message(message_id)
            if message is None or message["to_station_id"] != station_id:
                raise HTTPAPIError(
                    HTTPStatus.NOT_FOUND, "unknown_message", "Meddelandet finns inte för stationen"
                )
            return {
                "message": self.operations_store.acknowledge_line_available(
                    message_id, client.client_id
                )
            }
        connection = self.engine.config.connections.get(str(payload.get("connection_id") or ""))
        if connection is None or station_id not in (
            connection.station_a_id,
            connection.station_b_id,
        ):
            raise HTTPAPIError(
                HTTPStatus.FORBIDDEN,
                "connection_not_assigned",
                "Terminalen har inte tillgång till sträckan",
            )
        return {
            "message": self.operations_store.publish_line_available(
                snapshot["publication_id"],
                snapshot["active_day"],
                message_id=f"line-{uuid4().hex[:8]}",
                connection_id=connection.id,
                from_station_id=station_id,
                to_station_id=connection.other_station(station_id),
                movement_id=str(payload.get("movement_id") or "") or None,
                created_by=client.client_id,
            )
        }

    def _require_station_access(self, client: PairedClient, station_id: str) -> None:
        if client.kind in {DeviceKind.WEB_ADMIN, DeviceKind.SWIFT_ADMIN}:
            return
        if client.station_id is not None and client.station_id == station_id:
            return
        station_panels = {
            panel.id for panel in self.engine.config.panels.values() if panel.station_id == station_id
        }
        if not station_panels.intersection(client.panel_ids):
            raise HTTPAPIError(HTTPStatus.FORBIDDEN, "station_not_assigned", "Terminalen har inte tillgång till stationen")

    def control_clock(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        if self.operations_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "clock_unavailable", "Den lokala klockan är inte tillgänglig")
        action = str(payload.get("action") or "")
        try:
            if action == "start":
                if payload.get("speed") is not None:
                    self.operations_store.set_speed(float(payload["speed"]))
                return self.operations_store.start_clock(time_value=payload.get("time"))
            if action == "stop":
                return self.operations_store.stop_clock(str(payload.get("reason") or "") or None)
            if action == "speed":
                return self.operations_store.set_speed(float(payload.get("speed", 1)))
        except (TypeError, ValueError) as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_clock", str(error)) from error
        raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_clock_action", "Okänt klockkommando")

    def restart_required(self) -> bool:
        if self.runtime_store is None:
            return False
        publication = self.runtime_store.active()
        return publication is not None and publication.session_config() != self.engine.config

    def restart_server(self, client: PairedClient) -> dict[str, Any]:
        self._require_admin(client)
        if not self.config.allow_restart:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "restart_unavailable",
                "Serveromstart är inte tillgänglig i det här körläget",
            )
        return {
            "status": "restarting",
            "message": "TrainMeet Server startar om. Sidan ansluter igen automatiskt.",
        }

    def reset_operational_data(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        if not self.config.allow_restart:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "operational_reset_unavailable",
                "Nollställning är inte tillgänglig i det här körläget",
            )
        if str(payload.get("confirmation", "")).strip().upper() != "NOLLSTÄLL":
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "operational_reset_not_confirmed",
                "Skriv NOLLSTÄLL för att bekräfta",
            )
        return {
            "status": "resetting",
            "mode": "operational",
            "message": (
                "Träffdata och anslutningar nollställs. Administratören, "
                "servernamnet och din inloggning behålls."
            ),
        }

    def factory_reset_server(
        self,
        client: PairedClient,
        payload: dict[str, Any],
        *,
        local_access: bool,
    ) -> dict[str, Any]:
        self._require_admin(client)
        if not local_access:
            raise HTTPAPIError(
                HTTPStatus.FORBIDDEN,
                "factory_reset_requires_local_access",
                "Full fabriksåterställning kan bara göras direkt på serverns lokala adress",
            )
        if not self.config.allow_restart:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "factory_reset_unavailable",
                "Nollställning är inte tillgänglig i det här körläget",
            )
        if str(payload.get("confirmation", "")).strip().upper() != "NOLLSTÄLL":
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "factory_reset_not_confirmed",
                "Skriv NOLLSTÄLL för att bekräfta",
            )
        return {
            "status": "resetting",
            "message": "TrainMeet Server nollställs och öppnar första installationen igen.",
        }

    def software_update_status(self, client: PairedClient) -> dict[str, Any]:
        self._require_admin(client)
        result: dict[str, Any] = {
            "supported": self.config.allow_software_update,
            "installed_version": installed_version(),
            "installed_build": installed_build(),
            **read_update_status(Path(self.config.state_dir)),
        }
        if self.config.allow_software_update:
            try:
                latest = latest_version()
                result["latest_version"] = latest["version"]
                result["latest_build"] = latest["build"]
                result["published_at"] = latest["published_at"]
                # The build decides, not the version: a version number can
                # stay put across several fixes and an operator still wants
                # to be able to take them.
                result["update_available"] = latest["build"] != result["installed_build"]
            except SoftwareUpdateError as error:
                result["check_error"] = str(error)
        return result

    def update_software(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        if not self.config.allow_software_update:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "update_unavailable", "Programuppdatering hanteras av Docker eller driftmiljön")
        try:
            start_update()
        except SoftwareUpdateError as error:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "update_failed", str(error)) from error
        return {"status": "started", "message": "Uppdateringen har startat i bakgrunden."}

    def local_configuration(self, client: PairedClient) -> dict[str, Any]:
        self._require_admin(client)
        if self.local_configuration_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "local_configuration_unavailable",
                "Lokal konfigurationslagring saknas",
            )
        return self.local_configuration_store.current()

    def build_topology(self, client: PairedClient) -> dict[str, Any]:
        """Stations, connections and A-D panels for BYGG step 2.

        One shape whichever side the data comes from, so the view has a single
        renderer and the difference between a Cloud package and a local draft
        is one boolean rather than two code paths.

        `locked` is decided here, never in the browser. A page that could work
        out for itself whether editing is open would eventually get it wrong
        and offer to edit a package Cloud owns.
        """
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "runtime_unavailable",
                "Lokal lagring saknas",
            )
        locked = not self.runtime_store.editing_is_open()
        if locked:
            publication = self.runtime_store.active()
            payload = publication.payload if publication is not None else {}
            revision = publication.publication_id if publication is not None else None
            # Cloud decides the order of the line; the server only reads it.
            stations = sorted(
                payload.get("stations") or [],
                key=lambda station: int(station.get("diagram_order") or 0),
            )
        else:
            draft: dict[str, Any] = {}
            if self.local_configuration_store is not None:
                draft = self.local_configuration_store.current().get("draft") or {}
            payload = draft
            revision = draft.get("revision")
            # A draft has no diagram_order - the list *is* the order, which is
            # what makes reordering rows mean something.
            stations = list(payload.get("stations") or [])

        return {
            "locked": locked,
            "source": "cloud" if locked else "lokal",
            "revision": revision,
            "stations": [
                {
                    "id": str(station["id"]),
                    "code": str(station.get("code") or station["id"]),
                    "name": str(station.get("name") or station["id"]),
                    "order": index,
                }
                for index, station in enumerate(stations, start=1)
            ],
            "connections": [
                {
                    "id": str(connection["id"]),
                    "station_a_id": str(connection.get("station_a_id") or ""),
                    "station_b_id": str(connection.get("station_b_id") or ""),
                    "track_type": str(
                        connection.get("track_type") or TrackType.SINGLE.value
                    ),
                    "dispatch_mode_override": connection.get("dispatch_mode_override")
                    or None,
                }
                for connection in payload.get("connections") or []
            ],
            "panels": [
                {
                    "id": str(panel["id"]),
                    "station_id": str(panel.get("station_id") or ""),
                    "name": str(panel.get("name") or ""),
                    "slots": {
                        key: (panel.get("slots") or {}).get(key) or None
                        for key in ("A", "B", "C", "D")
                    },
                }
                for panel in payload.get("panels") or []
            ],
        }

    def save_local_configuration(
        self,
        client: PairedClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_admin(client)
        self._require_editing_open()
        if self.local_configuration_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "local_configuration_unavailable",
                "Lokal konfigurationslagring saknas",
            )
        draft = payload.get("draft", payload)
        if not isinstance(draft, dict):
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_local_configuration",
                "Konfigurationen måste vara ett objekt",
            )
        expected_revision = payload.get("expected_revision")
        try:
            revision = int(expected_revision) if expected_revision is not None else None
            saved = self.local_configuration_store.save(
                draft,
                expected_revision=revision,
            )
        except ConfigurationRevisionConflict as error:
            raise HTTPAPIError(HTTPStatus.CONFLICT, "configuration_conflict", str(error)) from error
        except (LocalConfigurationError, TypeError, ValueError) as error:
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_local_configuration",
                str(error),
            ) from error
        return saved

    def _require_editing_open(self) -> None:
        """D3: while Cloud is the editor, the server refuses local changes.

        A rule, not a recommendation. The mode is persistent state a person
        set, never a reading of whether Cloud answered this second - a network
        blip must not unlock editing, and a network recovering must not lock
        it in the middle of somebody's work.
        """
        if self.runtime_store is None or self.runtime_store.editing_is_open():
            return
        raise HTTPAPIError(
            HTTPStatus.CONFLICT,
            "cloud_linked",
            "Servern är kopplad till TrainMeet Cloud, som är redaktör. "
            "Ta träffen offline för att redigera lokalt.",
        )

    def operating_mode_state(self, client: PairedClient) -> dict[str, Any]:
        """What mode we are in, and what going back to Cloud would cost."""
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal lagring saknas"
            )
        revisions = self.runtime_store.local_revisions()
        return {
            "mode": self.runtime_store.operating_mode(),
            "editing_open": self.runtime_store.editing_is_open(),
            "linked": bool(self.runtime_store.link_token()),
            "local_revisions": revisions,
            "discards_on_return": self._discard_preview(revisions),
        }

    def _discard_preview(self, revisions: list[str]) -> dict[str, Any]:
        """Exactly what going back to Cloud throws away (D4).

        It must never happen silently. Counting revisions is not enough - an
        operator needs to see which rows changed before agreeing to lose them.
        """
        if not revisions or self.local_configuration_store is None or self.runtime_store is None:
            return {"revisions": 0, "rows": []}
        draft = self.local_configuration_store.current().get("draft") or {}

        # Compare against the Cloud publication the revisions were built on,
        # not against whatever is active - once a local revision is activated,
        # the active package *is* the edit, and comparing the draft to itself
        # finds nothing. The base is what going back to Cloud restores.
        base_id = draft.get("base_publication_id")
        base = self.runtime_store.publication(base_id) if base_id else None
        if base is None:
            base = self.runtime_store.active()
        rows: list[dict[str, Any]] = []
        if base is not None:
            published = {str(row["id"]): row for row in base.payload.get("trains") or []}
            for row in draft.get("trains") or []:
                before = published.get(str(row["id"]))
                if before is None:
                    rows.append({
                        "id": row["id"], "train_number": row.get("train_number"),
                        "change": "tillagd",
                    })
                    continue
                for field, label in (
                    ("arrival_time", "ankomst"),
                    ("departure_time", "avgång"),
                    ("track_id", "spår"),
                ):
                    if (before.get(field) or None) != (row.get(field) or None):
                        rows.append({
                            "id": row["id"], "train_number": row.get("train_number"),
                            "change": f"{label} {before.get(field) or '–'} → {row.get(field) or '–'}",
                        })
            local_ids = {str(row["id"]) for row in draft.get("trains") or []}
            for identifier, row in published.items():
                if identifier not in local_ids:
                    rows.append({
                        "id": identifier, "train_number": row.get("train_number"),
                        "change": "borttagen",
                    })
        return {"revisions": len(revisions), "rows": rows}

    def set_operating_mode(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        """Move between modes. Explicit, and sticky once set."""
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal lagring saknas"
            )
        mode = str(payload.get("mode") or "")
        if mode not in SQLiteRuntimeStore.OPERATING_MODES:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "unknown_mode", "Okänt driftläge")

        # Going back to Cloud discards the local revisions, so it needs the
        # operator to have seen what goes and said yes.
        if mode == SQLiteRuntimeStore.CLOUD_LINKED:
            revisions = self.runtime_store.local_revisions()
            if revisions and payload.get("discard_local_revisions") is not True:
                raise HTTPAPIError(
                    HTTPStatus.CONFLICT,
                    "confirm_discard",
                    f"{len(revisions)} lokala revisioner kastas när Clouds version gäller igen. "
                    "Bekräfta för att fortsätta.",
                )
        self.runtime_store.set_operating_mode(mode)
        return self.operating_mode_state(client)

    def seed_local_configuration(self, client: PairedClient) -> dict[str, Any]:
        """Open the active Cloud package as an editable working copy (D2).

        The path that was missing: a server could always build a configuration
        of its own, but never edit the one Cloud published - which is the only
        thing worth correcting during a meet.
        """
        self._require_admin(client)
        self._require_editing_open()
        if self.local_configuration_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE, "local_configuration_unavailable",
                "Lokal konfiguration är inte tillgänglig",
            )
        active = self.runtime_store.active() if self.runtime_store is not None else None
        if active is None:
            raise HTTPAPIError(
                HTTPStatus.CONFLICT, "no_active_publication",
                "Det finns ingen aktiv version att öppna. Hämta en från TrainMeet Cloud först.",
            )
        try:
            return self.local_configuration_store.seed_from_publication(active.payload)
        except LocalConfigurationError as error:
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST, "invalid_local_configuration", str(error)
            ) from error

    def configure_cloud_auto_sync(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal lagring saknas")
        enabled = payload.get("enabled") is True
        if enabled and not self.runtime_store.link_token():
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "runtime_not_linked", "Koppla servern till TrainMeet Cloud först")
        self.runtime_store.set_cloud_auto_sync(enabled)
        return {
            "enabled": enabled,
            "message": "Automatisk Cloud-synk är aktiverad." if enabled else "Automatisk Cloud-synk är avstängd.",
        }

    def auto_sync_cloud_runtime(self) -> dict[str, Any]:
        if self.runtime_store is None or not self.runtime_store.cloud_auto_sync_enabled():
            return {"checked": False, "updated": False}
        token = self.runtime_store.link_token()
        if not token:
            return {"checked": False, "updated": False}
        central_url = canonical_runtime_url(self.runtime_store.central_url() or self.config.central_runtime_url)
        manifest = self.linked_runtime_fetcher(token, central_url, True)
        if not isinstance(manifest, CentralRuntimeManifest):
            raise CentralSyncError("TrainMeet Cloud skickade inget versionsbesked")
        active = self.runtime_store.active()
        if active is not None and active.publication_id == manifest.publication_id:
            return {"checked": True, "updated": False, "publication_id": manifest.publication_id}
        download = self.linked_runtime_fetcher(token, central_url, False)
        if not isinstance(download, CentralRuntimeDownload):
            raise CentralSyncError("TrainMeet Cloud skickade inget driftpaket")
        publication = self.runtime_store.install(download.package)
        if self.operations_store is not None:
            self.operations_store.ensure_publication(publication)
        return {
            "checked": True,
            "updated": True,
            "publication_id": publication.publication_id,
            "restart_required": publication.session_config() != self.engine.config,
        }

    def activate_local_configuration(
        self,
        client: PairedClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_admin(client)
        self._require_editing_open()
        if self.local_configuration_store is None or self.runtime_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "local_configuration_unavailable",
                "Lokal konfigurationslagring saknas",
            )
        expected_revision = payload.get("expected_revision")
        try:
            revision = int(expected_revision) if expected_revision is not None else None
            package = self.local_configuration_store.runtime_package(
                expected_revision=revision,
            )
            publication = self.runtime_store.install(package)
        except ConfigurationRevisionConflict as error:
            raise HTTPAPIError(HTTPStatus.CONFLICT, "configuration_conflict", str(error)) from error
        except (LocalConfigurationError, RuntimePublicationError, TypeError, ValueError) as error:
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_local_configuration",
                str(error),
            ) from error

        if self.operations_store is not None:
            self.operations_store.ensure_publication(publication)
        restart_required = publication.session_config() != self.engine.config
        return {
            **self.runtime_store.summary(),
            "source": "local",
            "configuration_revision": revision,
            "restart_required": restart_required,
            "message": (
                "Konfigurationen är aktiverad. Starta om TrainMeet Server för att börja köra den nya stationsplanen."
                if restart_required
                else "Konfigurationen är aktiverad och används redan av servern."
            ),
        }

    def timetable(self, client: PairedClient, station_id: str | None) -> dict[str, Any]:
        del client
        if self.runtime_store is None:
            raise HTTPAPIError(
                HTTPStatus.NOT_FOUND,
                "runtime_not_configured",
                "Ingen tidtabell är publicerad",
            )
        publication = self.runtime_store.active()
        active_day = self.runtime_store.active_day()
        if publication is None or active_day is None:
            raise HTTPAPIError(
                HTTPStatus.NOT_FOUND,
                "runtime_not_configured",
                "Ingen tidtabell är publicerad",
            )
        if station_id is not None and station_id not in {
            str(station["id"]) for station in publication.payload["stations"]
        }:
            raise HTTPAPIError(HTTPStatus.NOT_FOUND, "unknown_station", "Stationen finns inte")
        return publication.timetable(active_day=active_day, station_id=station_id)

    def install_runtime(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "runtime_unavailable",
                "Lokal tidtabellslagring saknas",
            )
        package = payload.get("package", payload)
        if not isinstance(package, dict):
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_runtime",
                "Driftpaketet måste vara ett objekt",
            )
        try:
            publication = self.runtime_store.install(package)
        except RuntimePublicationError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_runtime", str(error)) from error
        if self.operations_store is not None:
            self.operations_store.ensure_publication(publication)
        restart_required = publication.session_config() != self.engine.config
        return {
            **self.runtime_store.summary(),
            "restart_required": restart_required,
            "message": (
                "Driftpaketet är sparat. Starta om Raspberry Pi-servern för att aktivera stationer och paneler."
                if restart_required
                else "Driftpaketet är aktivt."
            ),
        }

    def validate_runtime(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a complete runtime package without changing server state."""
        self._require_admin(client)
        package = payload.get("package", payload)
        if not isinstance(package, dict):
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_runtime",
                "Driftpaketet måste vara ett objekt",
            )
        try:
            publication = RuntimePublication.parse(package)
        except RuntimePublicationError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_runtime", str(error)) from error

        station_rows = {str(station["id"]): 0 for station in package["stations"]}
        station_tracks: dict[str, set[tuple[int, str]]] = {
            station_id: set() for station_id in station_rows
        }
        operating_point_rows: dict[str, int] = {}
        operating_point_tracks: dict[str, set[tuple[int, str]]] = {}
        for station in package["stations"]:
            for operating_point in station.get("operating_points", []):
                operating_point_id = str(operating_point["id"])
                operating_point_rows[operating_point_id] = 0
                operating_point_tracks[operating_point_id] = set()
        # The catalogue is the only track list. Deriving a second one from the
        # timetable rows is what let Cloud's tracks and the server's drift
        # apart without anyone noticing.
        for track in package["tracks"]:
            if not bool(track.get("active", True)):
                continue
            entry = (int(track["sort_order"]), str(track["display_label"]))
            station_tracks[str(track["station_id"])].add(entry)
            track_operating_point_id = track.get("operating_point_id")
            if track_operating_point_id:
                operating_point_tracks[str(track_operating_point_id)].add(entry)
        for movement in package["trains"]:
            station_id = str(movement["station_id"])
            station_rows[station_id] += 1
            operating_point_id = movement.get("operating_point_id")
            if operating_point_id:
                operating_point_rows[str(operating_point_id)] += 1

        connection_counts = {station_id: 0 for station_id in station_rows}
        for connection in package["connections"]:
            connection_counts[str(connection["station_a_id"])] += 1
            connection_counts[str(connection["station_b_id"])] += 1

        panels_by_station = {station_id: 0 for station_id in station_rows}
        for panel in package["panels"]:
            panels_by_station[str(panel["station_id"])] += 1

        warnings: list[str] = []
        stations = []
        for station in sorted(
            package["stations"],
            key=lambda value: int(value.get("diagram_order", 0)),
        ):
            station_id = str(station["id"])
            if station_rows[station_id] == 0:
                warnings.append(f"{station['name']} saknar tågrörelser")
            if connection_counts[station_id] == 0:
                warnings.append(f"{station['name']} saknar anslutande sträcka")
            if panels_by_station[station_id] == 0:
                warnings.append(f"{station['name']} saknar TMBox-panel")
            if station_rows[station_id] > 0 and not station_tracks[station_id]:
                warnings.append(f"{station['name']} saknar spårkatalog")
            operating_points = []
            for operating_point in station.get("operating_points", []):
                operating_point_id = str(operating_point["id"])
                if operating_point_rows[operating_point_id] == 0:
                    warnings.append(
                        f"{station['name']} · {operating_point['name']} saknar tågrörelser"
                    )
                operating_points.append(
                    {
                        "id": operating_point_id,
                        "code": str(operating_point["code"]),
                        "name": str(operating_point["name"]),
                        "aliases": list(operating_point.get("aliases", [])),
                        "timetable_rows": operating_point_rows[operating_point_id],
                        "tracks": [
                            label
                            for _, label in sorted(operating_point_tracks[operating_point_id])
                        ],
                    }
                )
            stations.append(
                {
                    "id": station_id,
                    "code": str(station["code"]),
                    "name": str(station["name"]),
                    "timetable_rows": station_rows[station_id],
                    "track_count": len(station_tracks[station_id]),
                    "connection_count": connection_counts[station_id],
                    "panel_count": panels_by_station[station_id],
                    "operating_points": operating_points,
                }
            )

        short_services = [
            service for service in package["services"] if len(service.get("stops", [])) < 2
        ]
        if short_services:
            warnings.append(
                f"{len(short_services)} tågturer har bara ett känt stopp och visas utan full rutt"
            )

        return {
            "valid": True,
            "schema_version": publication.schema_version,
            "publication_id": publication.publication_id,
            "published_at": publication.published_at,
            "checksum": publication.checksum,
            "meet": {
                "id": publication.meet_id,
                "name": publication.meet_name,
                "active_day": publication.active_day,
                "timezone": publication.timezone,
            },
            "counts": {
                "stations": len(package["stations"]),
                "operating_points": sum(
                    len(station.get("operating_points", []))
                    for station in package["stations"]
                ),
                "connections": len(package["connections"]),
                "panels": len(package["panels"]),
                "services": len(package["services"]),
                "timetable_rows": len(package["trains"]),
                "route_stops": len(package["routes"]),
            },
            "stations": stations,
            "warnings": warnings,
        }

    def set_active_day(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        if self.runtime_store is None or self.runtime_store.active() is None:
            raise HTTPAPIError(
                HTTPStatus.NOT_FOUND,
                "runtime_not_configured",
                "Ingen tidtabell är publicerad",
            )
        try:
            active_day = self.runtime_store.set_active_day(str(payload.get("active_day", "")))
        except RuntimePublicationError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_active_day", str(error)) from error
        return {"active_day": active_day}

    def sync_runtime(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        code = str(payload.get("sync_code", ""))
        central_url = str(payload.get("central_url", "")).strip()
        if self.runtime_store is not None:
            central_url = central_url or self.runtime_store.central_url() or self.config.central_runtime_url
        else:
            central_url = central_url or self.config.central_runtime_url
        central_url = canonical_runtime_url(central_url)
        parsed_url = urlparse(central_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_central_url",
                "Ange en fullständig http- eller https-adress till centrala TrainMeet",
            )
        try:
            result = self.runtime_fetcher(code, central_url)
        except CentralSyncError as error:
            raise HTTPAPIError(HTTPStatus.BAD_GATEWAY, "central_sync_failed", str(error)) from error
        if isinstance(result, CentralRuntimeDownload):
            package = result.package
            if self.runtime_store is not None and result.link_token:
                self.runtime_store.save_link_token(result.link_token)
        elif isinstance(result, dict):
            package = result
        else:
            raise HTTPAPIError(
                HTTPStatus.BAD_GATEWAY,
                "central_sync_failed",
                "TrainMeet skickade inget driftpaket",
            )
        if self.runtime_store is not None:
            try:
                self.runtime_store.save_central_url(central_url)
            except RuntimePublicationError as error:
                raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_central_url", str(error)) from error
        response = self.install_runtime(client, {"package": package})
        response["linked"] = self.runtime_store.link_token() is not None if self.runtime_store else False
        return response

    def check_runtime_update(self, client: PairedClient) -> dict[str, Any]:
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal tidtabellslagring saknas")
        token = self.runtime_store.link_token()
        if not token:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "runtime_not_linked", "Koppla först servern med en sexsiffrig träffkod")
        try:
            central_url = canonical_runtime_url(self.runtime_store.central_url() or self.config.central_runtime_url)
            result = self.linked_runtime_fetcher(token, central_url, True)
        except CentralSyncError as error:
            raise HTTPAPIError(HTTPStatus.BAD_GATEWAY, "central_sync_failed", str(error)) from error
        if not isinstance(result, CentralRuntimeManifest):
            raise HTTPAPIError(HTTPStatus.BAD_GATEWAY, "central_sync_failed", "TrainMeet skickade inget versionsbesked")
        active = self.runtime_store.active()
        return {
            "linked": True,
            "update_available": active is None or active.publication_id != result.publication_id,
            "current_publication_id": active.publication_id if active else None,
            "publication_id": result.publication_id,
            "published_at": result.published_at,
            "package_checksum": result.package_checksum,
        }

    def download_runtime_update(self, client: PairedClient) -> dict[str, Any]:
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal tidtabellslagring saknas")
        token = self.runtime_store.link_token()
        if not token:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "runtime_not_linked", "Koppla först servern med en sexsiffrig träffkod")
        try:
            central_url = canonical_runtime_url(self.runtime_store.central_url() or self.config.central_runtime_url)
            result = self.linked_runtime_fetcher(token, central_url, False)
        except CentralSyncError as error:
            raise HTTPAPIError(HTTPStatus.BAD_GATEWAY, "central_sync_failed", str(error)) from error
        if not isinstance(result, CentralRuntimeDownload):
            raise HTTPAPIError(HTTPStatus.BAD_GATEWAY, "central_sync_failed", "TrainMeet skickade inget driftpaket")
        try:
            publication = self.runtime_store.install(result.package, activate=False)
        except RuntimePublicationError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_runtime", str(error)) from error
        active = self.runtime_store.active()
        return {
            **self.runtime_store.summary(),
            "downloaded_publication_id": publication.publication_id,
            "update_available": active is None or active.publication_id != publication.publication_id,
            "message": "Den nya versionen är hämtad och väntar på aktivering.",
        }

    def activate_runtime_update(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal tidtabellslagring saknas")
        publication_id = str(payload.get("publication_id") or "")
        if not publication_id:
            staged = self.runtime_store.latest_staged()
            publication_id = staged.publication_id if staged else ""
        try:
            publication = self.runtime_store.activate(publication_id)
        except RuntimePublicationError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_runtime", str(error)) from error
        if self.operations_store is not None:
            self.operations_store.ensure_publication(publication)
        restart_required = publication.session_config() != self.engine.config
        return {
            **self.runtime_store.summary(),
            "restart_required": restart_required,
            "message": (
                "Versionen är aktiverad. Starta om TrainMeet Server för att börja använda den nya stationsplanen."
                if restart_required else "Versionen är aktiverad."
            ),
        }

    def assign_device(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        """Assign a discovered box to a station.

        A box is assigned one station, not a panel with fixed A-D slots
        against individual connections; what it shows is built from the
        station's topology. panel_id is still accepted for the v1 clients that
        have not moved yet.
        """
        self._require_admin(client)
        station_id = str(payload.get("station_id", "")).strip()
        panel_id = str(payload.get("panel_id", "")).strip()
        if not station_id and not panel_id:
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "station_required",
                "Ange stationen boxen ska tilldelas",
            )
        if station_id and station_id not in self.engine.config.stations:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "unknown_station", "Stationen finns inte")
        if panel_id and panel_id not in self.engine.config.panels:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "unknown_panel", "Panelen finns inte")
        if panel_id and not station_id:
            station_id = self.engine.config.panels[panel_id].station_id
        try:
            assigned = self.identities.assign_discovered_device(
                str(payload.get("device_code", "")),
                (panel_id,) if panel_id else (),
                station_id=station_id or None,
            )
        except PairingError as error:
            raise HTTPAPIError(HTTPStatus.NOT_FOUND, error.code, str(error)) from error
        return {
            "device_id": assigned.client_id,
            "station_id": assigned.station_id,
            "assigned_panel_ids": list(assigned.panel_ids),
        }

    @staticmethod
    def _require_admin(client: PairedClient) -> None:
        if client.kind not in {DeviceKind.WEB_ADMIN, DeviceKind.SWIFT_ADMIN}:
            raise HTTPAPIError(
                HTTPStatus.FORBIDDEN,
                "admin_required",
                "Administratörsbehörighet krävs",
            )

    def static_asset(self, path: str) -> tuple[bytes, str] | None:
        if path.startswith("/tkl/"):
            relative_tkl = path.removeprefix("/tkl/") or "index.html"
            if ".." in Path(relative_tkl).parts:
                return None
            asset = self.tkl_web_root.joinpath(relative_tkl)
            try:
                data = asset.read_bytes()
            except (FileNotFoundError, IsADirectoryError):
                return None
            mime_type = mimetypes.guess_type(relative_tkl)[0] or "application/octet-stream"
            return data, mime_type
        relative = {
            "/": "index.html",
            "/index.html": "index.html",
            "/assets/app.css": "app.css",
            "/assets/app.js": "app.js",
            "/assets/tmbox-render.js": "tmbox-render.js",
            "/assets/tmbox-nav.js": "tmbox-nav.js",
            "/assets/tmbox-attention.js": "tmbox-attention.js",
            "/assets/ikon/trainmeet-ikon.svg": "ikon/trainmeet-ikon.svg",
            "/assets/ikon/trainmeet-ikon-mork.svg": "ikon/trainmeet-ikon-mork.svg",
            "/assets/ikon/trainmeet-ikon-enfargad.svg": "ikon/trainmeet-ikon-enfargad.svg",
            "/assets/ikon/png/trainmeet-ikon-16.png": "ikon/png/trainmeet-ikon-16.png",
            "/assets/ikon/png/trainmeet-ikon-32.png": "ikon/png/trainmeet-ikon-32.png",
            "/assets/ikon/png/trainmeet-ikon-64.png": "ikon/png/trainmeet-ikon-64.png",
            "/assets/ikon/png/trainmeet-ikon-128.png": "ikon/png/trainmeet-ikon-128.png",
            "/assets/ikon/png/trainmeet-ikon-256.png": "ikon/png/trainmeet-ikon-256.png",
            "/assets/ikon/png/trainmeet-ikon-512.png": "ikon/png/trainmeet-ikon-512.png",
            "/assets/fonts/inter-400.woff2": "fonts/inter-400.woff2",
            "/assets/fonts/inter-500.woff2": "fonts/inter-500.woff2",
            "/assets/fonts/inter-600.woff2": "fonts/inter-600.woff2",
            "/assets/fonts/inter-700.woff2": "fonts/inter-700.woff2",
            "/trainmeet-logo.png": "trainmeet-logo.png",
        }.get(path)
        if relative is None and path in {
            "/display",
            "/display/topology",
            "/display/graph",
            "/display/clock",
            "/display/dashboard",
        }:
            relative = "index.html"
        if relative is None:
            return None
        asset = self.web_root.joinpath(relative)
        try:
            data = asset.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            return None
        mime_type = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        return data, mime_type


class HTTPAPIError(RuntimeError):
    def __init__(self, status: HTTPStatus, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


class TrainMeetRequestHandler(BaseHTTPRequestHandler):
    server: "TrainMeetHTTPServer"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/tkl":
                self.send_response(HTTPStatus.PERMANENT_REDIRECT)
                self.send_header("Location", "/tkl/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/v1/display":
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.display_snapshot(self.headers.get("Host", "")),
                )
                return
            if path == "/v1/tkl/context":
                client = self._authenticated_client()
                station_id = parse_qs(parsed.query).get("station_id", [""])[0]
                self._send_json(HTTPStatus.OK, self.server.application.tkl_context(client, station_id))
                return
            if path == "/healthz":
                # Unauthenticated by necessity: an updater checking whether
                # the service came back cannot log in first. It therefore says
                # only what a health check needs - that the process is up and
                # which build it is - and nothing about who administers it.
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "version": installed_version(),
                        "build": installed_build(),
                    },
                )
                return
            if path == "/v1/operating-mode":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.operating_mode_state(client),
                )
                return
            if path == "/v1/auth/status":
                client = self._optional_authenticated_client()
                access = self.server.application.identities.admin_access_summary()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "authenticated": client is not None,
                        "access_mode": "local" if self._has_automatic_local_admin() else "external",
                        # No username. It used to be here, which both
                        # prefilled the login field and told any
                        # unauthenticated caller who the administrator is.
                        "password_configured": access["password_configured"],
                        "must_change_password": access["must_change_password"],
                    },
                )
                return
            if path == "/v1/setup":
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.installation_status(),
                )
                return
            if path == "/v1/admin/access":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.admin_access(client),
                )
                return
            if path == "/v1/info":
                client = self._optional_authenticated_client()
                if client is None:
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "protocol_version": 1,
                            "gateway_id": (
                                self.server.application.runtime_store.server_name()
                                if self.server.application.runtime_store is not None
                                else None
                            ) or self.server.application.config.gateway_id,
                            "authentication_required": True,
                        },
                    )
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "protocol_version": 1,
                        "gateway_id": (
                            self.server.application.runtime_store.server_name()
                            if self.server.application.runtime_store is not None
                            else None
                        ) or self.server.application.config.gateway_id,
                        "traffic_session_id": self.server.application.engine.config.id,
                        "traffic_session_name": self.server.application.engine.config.name,
                        "local_development": self.server.application.config.local_development,
                        "runtime": (
                            self.server.application.runtime_store.summary()
                            if self.server.application.runtime_store is not None
                            else {"configured": False}
                        ),
                        "local_configuration": (
                            self.server.application.local_configuration_store.current()
                            if self.server.application.local_configuration_store is not None
                            else {"configured": False}
                        ),
                        "restart_required": self.server.application.restart_required(),
                    },
                )
                return
            if path == "/v1/server/update":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.software_update_status(client))
                return
            if path == "/v1/snapshots":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.snapshots(client))
                return
            if path == "/v1/devices":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.devices(client))
                return
            if path == "/v1/tmbox-v2/stations":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.tmbox_v2_stations(client))
                return
            if path == "/v1/tmbox-v2/assignment":
                client = self._authenticated_client()
                device_id = parse_qs(parsed.query).get("device_id", [""])[0]
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.tmbox_v2_assignment(client, device_id),
                )
                return
            if path == "/v1/tmbox-v2/config":
                client = self._authenticated_client()
                station_id = parse_qs(parsed.query).get("station_id", [""])[0]
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.tmbox_v2_config(client, station_id),
                )
                return
            if path == "/v1/tmbox-v2/snapshot":
                client = self._authenticated_client()
                station_id = parse_qs(parsed.query).get("station_id", [""])[0]
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.tmbox_v2_snapshot(client, station_id),
                )
                return
            if path == "/v1/runtime":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.runtime_summary(client))
                return
            if path == "/v1/runtime/update":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.check_runtime_update(client))
                return
            if path == "/v1/local-configuration":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.local_configuration(client),
                )
                return
            if path == "/v1/build/topology":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.build_topology(client))
                return
            if path == "/v1/timetable":
                client = self._authenticated_client()
                station_id = parse_qs(parsed.query).get("station_id", [None])[0]
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.timetable(client, station_id),
                )
                return
            asset = self.server.application.static_asset(path)
            if asset is not None:
                self._send_bytes(HTTPStatus.OK, *asset)
                return
            raise HTTPAPIError(HTTPStatus.NOT_FOUND, "not_found", "Sidan finns inte")
        except HTTPAPIError as error:
            self._send_api_error(error)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/v1/setup/admin":
                if not self._client_address_is_private():
                    raise HTTPAPIError(
                        HTTPStatus.FORBIDDEN,
                        "local_setup_required",
                        "Den första administratören måste skapas från servern eller dess lokala nätverk",
                    )
                configured = self.server.application.create_initial_admin(payload)
                password = str(payload.get("password", ""))
                token = self.server.application.identities.create_admin_session(
                    str(configured["username"]),
                    password,
                )
                if token is None:
                    raise HTTPAPIError(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        "admin_session_failed",
                        "Administratören skapades men inloggningen kunde inte startas",
                    )
                self._send_json(
                    HTTPStatus.CREATED,
                    {"authenticated": True, "installation": self.server.application.installation_status()},
                    headers={"Set-Cookie": self._admin_cookie(token)},
                )
                return
            if path == "/v1/auth/login":
                access = self.server.application.identities.admin_access_summary()
                if not access["password_configured"]:
                    raise HTTPAPIError(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "admin_password_not_configured",
                        "Extern inloggning är inte konfigurerad. Öppna servern lokalt och välj ett lösenord först.",
                    )
                token = self.server.application.identities.create_admin_session(
                    str(payload.get("username", "")),
                    str(payload.get("password", "")),
                )
                if token is None:
                    raise HTTPAPIError(
                        HTTPStatus.UNAUTHORIZED,
                        "invalid_login",
                        "Fel användarnamn eller lösenord",
                    )
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "authenticated": True,
                        "access_mode": "external",
                        "must_change_password": access["must_change_password"],
                    },
                    headers={"Set-Cookie": self._admin_cookie(token)},
                )
                return
            if path == "/v1/auth/logout":
                token = self._admin_session_token()
                if token:
                    self.server.application.identities.revoke_admin_session(token)
                self._send_json(
                    HTTPStatus.OK,
                    {"authenticated": False},
                    headers={"Set-Cookie": self._admin_cookie("", max_age=0)},
                )
                return
            if path == "/v1/admin/access":
                client = self._authenticated_client()
                configured = self.server.application.configure_admin_access(client, payload)
                response_headers: dict[str, str] = {}
                password = str(payload.get("password", ""))
                if password and not self._is_direct_local_request():
                    token = self.server.application.identities.create_admin_session(
                        str(configured["username"]),
                        password,
                    )
                    if token:
                        response_headers["Set-Cookie"] = self._admin_cookie(token)
                self._send_json(
                    HTTPStatus.OK,
                    configured,
                    headers=response_headers,
                )
                return
            if path == "/v1/setup/server":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.save_initial_server_name(client, payload),
                )
                return
            if path == "/v1/setup/complete":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.complete_installation(client, payload),
                )
                return
            if path == "/v1/pair":
                response = self.server.application.pair(payload, self.headers.get("Host", "localhost"))
                self._send_json(HTTPStatus.CREATED, response)
                return
            if path == "/v1/command":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.command(client, payload))
                return
            if path == "/v1/tkl/shift/start":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.start_tkl_shift(client, payload))
                return
            if path == "/v1/tkl/shift/finish":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.finish_tkl_shift(client, payload))
                return
            if path == "/v1/tkl/movement":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.update_tkl_movement(client, payload))
                return
            if path in ("/v1/tkl/clearance", "/v1/tkl/line"):
                # /v1/tkl/line is the old name for this, kept until the
                # terminals have moved. It never meant line-available.
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.tkl_clearance_action(client, payload),
                )
                return
            if path == "/v1/tkl/line-available":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.tkl_line_available(client, payload),
                )
                return
            if path == "/v1/devices/assign":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.assign_device(client, payload))
                return
            if path == "/v1/runtime/install":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.CREATED,
                    self.server.application.install_runtime(client, payload),
                )
                return
            if path == "/v1/runtime/validate":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.validate_runtime(client, payload),
                )
                return
            if path == "/v1/runtime/active-day":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.set_active_day(client, payload),
                )
                return
            if path == "/v1/runtime/sync":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.CREATED,
                    self.server.application.sync_runtime(client, payload),
                )
                return
            if path == "/v1/runtime/update":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.CREATED,
                    self.server.application.download_runtime_update(client),
                )
                return
            if path == "/v1/tmbox-v2/command":
                client = self._authenticated_client()
                # A rejected command is a valid answer carrying its reason, the
                # same one a box would render. Only transport faults are errors.
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.tmbox_v2_command(client, payload),
                )
                return
            if path == "/v1/runtime/activate":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.CREATED,
                    self.server.application.activate_runtime_update(client, payload),
                )
                return
            if path == "/v1/local-configuration":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.save_local_configuration(client, payload),
                )
                return
            if path == "/v1/operating-mode":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.set_operating_mode(client, payload),
                )
                return
            if path == "/v1/local-configuration/seed":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.seed_local_configuration(client),
                )
                return
            if path == "/v1/local-configuration/activate":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.CREATED,
                    self.server.application.activate_local_configuration(client, payload),
                )
                return
            if path == "/v1/cloud/auto-sync":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.configure_cloud_auto_sync(client, payload),
                )
                return
            if path == "/v1/server/restart":
                client = self._authenticated_client()
                response = self.server.application.restart_server(client)
                # Record the intent before answering. The shutdown itself is
                # deferred, so the client still gets its response first, but
                # the server can never report "restarting" without having
                # decided to restart.
                self.server.request_restart()
                self._send_json(HTTPStatus.ACCEPTED, response)
                return
            if path == "/v1/server/operational-reset":
                client = self._authenticated_client()
                response = self.server.application.reset_operational_data(client, payload)
                self.server.request_operational_reset()
                self._send_json(HTTPStatus.ACCEPTED, response)
                return
            if path == "/v1/server/factory-reset":
                client = self._authenticated_client()
                response = self.server.application.factory_reset_server(
                    client,
                    payload,
                    local_access=self._has_automatic_local_admin(),
                )
                self.server.request_factory_reset()
                self._send_json(HTTPStatus.ACCEPTED, response)
                return
            if path == "/v1/server/update":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.ACCEPTED, self.server.application.update_software(client, payload))
                return
            if path == "/v1/display/connection":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.configure_connection_badge(client, payload),
                )
                return
            if path == "/v1/clock":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.control_clock(client, payload),
                )
                return
            raise HTTPAPIError(HTTPStatus.NOT_FOUND, "not_found", "Sidan finns inte")
        except HTTPAPIError as error:
            self._send_api_error(error)

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_body", "Felaktig datalängd") from error
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_body", "Tom eller för stor begäran")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_json", "Begäran är inte giltig JSON") from error
        if not isinstance(payload, dict):
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_json", "JSON-värdet måste vara ett objekt")
        return payload

    def _authenticated_client(self) -> PairedClient:
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if authorization.startswith(prefix):
            client = self.server.application.identities.authenticate(authorization[len(prefix) :])
            if client is None:
                raise HTTPAPIError(
                    HTTPStatus.UNAUTHORIZED,
                    "invalid_credential",
                    "Parkopplingen gäller inte längre",
                )
            return client
        client = self._optional_authenticated_client()
        if client is None:
            raise HTTPAPIError(
                HTTPStatus.UNAUTHORIZED,
                "authentication_required",
                "Administratörsinloggning krävs",
            )
        return client

    def _optional_authenticated_client(self) -> PairedClient | None:
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if authorization.startswith(prefix):
            return self.server.application.identities.authenticate(authorization[len(prefix) :])
        if self._has_automatic_local_admin():
            return self.server.application.local_admin()
        token = self._admin_session_token()
        if token and self.server.application.identities.authenticate_admin_session(token):
            return self.server.application.local_admin()
        return None

    def _has_automatic_local_admin(self) -> bool:
        if self.server.application.config.force_external_auth:
            return False
        if self._is_direct_local_request():
            return True
        access = self.server.application.identities.admin_access_summary()
        return not access["password_configured"] and self._client_address_is_private()

    def _is_direct_local_request(self) -> bool:
        if self._client_address_is_loopback():
            return True
        host = _hostname_without_port(self.headers.get("Host", "")).strip("[]").lower()
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def _client_address_is_loopback(self) -> bool:
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return address.is_loopback

    def _client_address_is_private(self) -> bool:
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return address.is_loopback or address.is_private or address.is_link_local

    def _admin_session_token(self) -> str | None:
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        cookies = SimpleCookie()
        try:
            cookies.load(cookie_header)
        except Exception:
            return None
        morsel = cookies.get(ADMIN_COOKIE_NAME)
        return morsel.value if morsel is not None else None

    def _admin_cookie(self, token: str, *, max_age: int = ADMIN_COOKIE_MAX_AGE) -> str:
        cookie = SimpleCookie()
        cookie[ADMIN_COOKIE_NAME] = token
        cookie[ADMIN_COOKIE_NAME]["path"] = "/"
        cookie[ADMIN_COOKIE_NAME]["httponly"] = True
        cookie[ADMIN_COOKIE_NAME]["samesite"] = "Strict"
        cookie[ADMIN_COOKIE_NAME]["max-age"] = max_age
        if self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower() == "https":
            cookie[ADMIN_COOKIE_NAME]["secure"] = True
        return cookie.output(header="").strip()

    def _send_api_error(self, error: HTTPAPIError) -> None:
        self._send_json(error.status, {"error": error.code, "message": str(error)})

    def _send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, data, "application/json; charset=utf-8", headers=headers)

    def _send_bytes(
        self,
        status: HTTPStatus,
        data: bytes,
        content_type: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'",
        )
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)


class TrainMeetHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        application: TrainMeetHTTPApplication,
    ):
        self.application = application
        self.restart_requested = False
        self.operational_reset_requested = False
        self.factory_reset_requested = False
        super().__init__(address, TrainMeetRequestHandler)

    def request_restart(self) -> None:
        # The flag is what makes the supervising process act, so it is set
        # first and the shutdown is deferred. A quarter of a second is far
        # more than a local response needs, and it keeps the ordering honest:
        # decided, answered, then stopped.
        self.restart_requested = True
        timer = threading.Timer(0.25, self.shutdown)
        timer.daemon = True
        timer.start()

    def request_factory_reset(self) -> None:
        self.factory_reset_requested = True
        self.request_restart()

    def request_operational_reset(self) -> None:
        self.operational_reset_requested = True
        self.request_restart()


def _is_loopback_address(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _hostname_without_port(host_header: str) -> str:
    if host_header.startswith("["):
        return host_header.split("]", 1)[0] + "]"
    return host_header.rsplit(":", 1)[0] if ":" in host_header else host_header
