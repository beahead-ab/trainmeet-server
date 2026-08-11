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
    CentralSyncError,
    fetch_runtime_package,
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
from .models import Command
from .runtime import RuntimePublicationError, SQLiteRuntimeStore


LOGGER = logging.getLogger("tambox_gateway.http")
MAX_REQUEST_BYTES = 4 * 1024 * 1024
ADMIN_COOKIE_NAME = "trainmeet_admin"
ADMIN_COOKIE_MAX_AGE = 12 * 60 * 60


@dataclass(frozen=True)
class HTTPServerConfig:
    gateway_id: str = "gateway-local"
    mqtt_port: int = 1883
    advertised_mqtt_host: str | None = None
    local_development: bool = False
    central_runtime_url: str = DEFAULT_RUNTIME_PUBLICATION_URL
    allow_restart: bool = False
    force_external_auth: bool = False


class TamboxHTTPApplication:
    def __init__(
        self,
        engine: TrafficEngine,
        identities: IdentityStore,
        pairing: PairingService,
        config: HTTPServerConfig,
        runtime_store: SQLiteRuntimeStore | None = None,
        local_configuration_store: SQLiteLocalConfigurationStore | None = None,
        runtime_fetcher: Callable[[str, str], dict[str, Any]] | None = None,
    ):
        self.engine = engine
        self.identities = identities
        self.pairing = pairing
        self.config = config
        self.runtime_store = runtime_store
        self.local_configuration_store = local_configuration_store
        self.runtime_fetcher = runtime_fetcher or (
            lambda code, url: fetch_runtime_package(code, url)
        )
        self.web_root = files("tambox_gateway").joinpath("web")

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
        if result.client.kind in {DeviceKind.WEB_ADMIN, DeviceKind.SWIFT_ADMIN}:
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
                }
                for device in self.identities.discovered_devices()
            ]
        }

    def runtime_summary(self, client: PairedClient) -> dict[str, Any]:
        del client
        if self.runtime_store is None:
            return {"configured": False}
        return self.runtime_store.summary()

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

    def local_configuration(self, client: PairedClient) -> dict[str, Any]:
        self._require_admin(client)
        if self.local_configuration_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "local_configuration_unavailable",
                "Lokal konfigurationslagring saknas",
            )
        return self.local_configuration_store.current()

    def save_local_configuration(
        self,
        client: PairedClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_admin(client)
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
            return self.local_configuration_store.save(
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

    def activate_local_configuration(
        self,
        client: PairedClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_admin(client)
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
        try:
            package = self.runtime_fetcher(code, self.config.central_runtime_url)
        except CentralSyncError as error:
            raise HTTPAPIError(HTTPStatus.BAD_GATEWAY, "central_sync_failed", str(error)) from error
        return self.install_runtime(client, {"package": package})

    def assign_device(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        panel_id = str(payload.get("panel_id", ""))
        if panel_id not in self.engine.config.panels:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "unknown_panel", "Panelen finns inte")
        try:
            assigned = self.identities.assign_discovered_device(
                str(payload.get("device_code", "")),
                (panel_id,),
            )
        except PairingError as error:
            raise HTTPAPIError(HTTPStatus.NOT_FOUND, error.code, str(error)) from error
        return {
            "device_id": assigned.client_id,
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
        relative = {
            "/": "index.html",
            "/index.html": "index.html",
            "/assets/app.css": "app.css",
            "/assets/app.js": "app.js",
        }.get(path)
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


class TamboxRequestHandler(BaseHTTPRequestHandler):
    server: "TamboxHTTPServer"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/v1/auth/status":
                client = self._optional_authenticated_client()
                access = self.server.application.identities.admin_access_summary()
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "authenticated": client is not None,
                        "access_mode": "local" if self._has_automatic_local_admin() else "external",
                        "username": access["username"],
                        "password_configured": access["password_configured"],
                    },
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
                            "gateway_id": self.server.application.config.gateway_id,
                            "authentication_required": True,
                        },
                    )
                    return
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "protocol_version": 1,
                        "gateway_id": self.server.application.config.gateway_id,
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
            if path == "/v1/snapshots":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.snapshots(client))
                return
            if path == "/v1/devices":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.devices(client))
                return
            if path == "/v1/runtime":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.runtime_summary(client))
                return
            if path == "/v1/local-configuration":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.local_configuration(client),
                )
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
                    {"authenticated": True, "access_mode": "external"},
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
            if path == "/v1/pair":
                response = self.server.application.pair(payload, self.headers.get("Host", "localhost"))
                self._send_json(HTTPStatus.CREATED, response)
                return
            if path == "/v1/command":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.command(client, payload))
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
            if path == "/v1/local-configuration":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.save_local_configuration(client, payload),
                )
                return
            if path == "/v1/local-configuration/activate":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.CREATED,
                    self.server.application.activate_local_configuration(client, payload),
                )
                return
            if path == "/v1/server/restart":
                client = self._authenticated_client()
                response = self.server.application.restart_server(client)
                self._send_json(HTTPStatus.ACCEPTED, response)
                self.server.request_restart()
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


class TamboxHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        application: TamboxHTTPApplication,
    ):
        self.application = application
        self.restart_requested = False
        super().__init__(address, TamboxRequestHandler)

    def request_restart(self) -> None:
        self.restart_requested = True
        timer = threading.Timer(0.25, self.shutdown)
        timer.daemon = True
        timer.start()


def _hostname_without_port(host_header: str) -> str:
    if host_header.startswith("["):
        return host_header.split("]", 1)[0] + "]"
    return host_header.rsplit(":", 1)[0] if ":" in host_header else host_header
