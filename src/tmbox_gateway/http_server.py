from __future__ import annotations

import json
import ipaddress
import logging
import mimetypes
import re
import secrets
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from functools import wraps
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from . import backup
from .engine import TrafficEngine
from .cloud_config import CloudConfiguration
from .lifecycle import SQLiteMeetLifecycle, MeetLifecycleError
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
    build_from_station_order,
    local_configuration_from_publication,
)
from .models import Command, ConnectionState, InteractionMode, TrackConfig, TrackType, UnknownTrackError, resolve_track_id
from .observability import log_event, use_correlation
from .operations import SQLiteOperationsStore
from .us import USStore, USError
from .us_clock import clock_settings as validate_us_clock_settings
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


def runtime_command(region=None):
    """Serialize operational HTTP commands with config activation and MQTT."""
    def decorate(method):
        @wraps(method)
        def guarded(self, *args, **kwargs):
            with self.engine._lock:
                if self.lifecycle:
                    selected = self.lifecycle.selected()
                    self.lifecycle.assert_selected(region or (selected or {}).get("region", ""))
                    payload = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
                    generation = payload.get("meet_generation")
                    scoped_required = bool(self.runtime_store and self.runtime_store._setting("require_scoped_commands") == "true")
                    # US warrants already carry mandatory session ID+revision.
                    if len(args) > 1 and isinstance(args[1], dict) and region != "us" and ((generation is not None and (type(generation) is not int or generation != selected["generation"]))
                                           or (scoped_required and generation is None)):
                        raise HTTPAPIError(HTTPStatus.CONFLICT, "stale_meet_context", "Träffen eller configen har ändrats. Läs in arbetsytan igen innan du fortsätter.")
                return method(self, *args, **kwargs)
        return guarded
    return decorate


def runtime_view(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.engine._lock:
            return method(self, *args, **kwargs)
    return guarded


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


#: How many rows a change list names before it says "och N till". An operator
#: reading a diff needs to recognise the change, not audit every row; a list
#: that fills the screen is one nobody reads to the end of.
CHANGE_SAMPLE = 8


def _named(rows: list[str]) -> dict[str, Any]:
    return {
        "count": len(rows),
        "names": rows[:CHANGE_SAMPLE],
        "more": max(0, len(rows) - CHANGE_SAMPLE),
    }


def _movement_signature(movement: dict[str, Any]) -> tuple:
    """Everything about a movement an operator would call a change.

    Deliberately not the whole row: sort keys and internal ordering fields
    differ between publications without anything visible moving, and a diff
    that cries wolf on those is a diff people stop reading.
    """
    return (
        str(movement.get("train_number") or ""),
        str(movement.get("station_id") or ""),
        str(movement.get("arrival_time") or ""),
        str(movement.get("departure_time") or ""),
        str(movement.get("track_id") or ""),
        str(movement.get("days") or ""),
        bool(movement.get("no_stop")),
    )


def _revision_changes(
    active: RuntimePublication | None,
    pending: RuntimePublication,
) -> dict[str, Any]:
    """What activating `pending` would replace in `active`.

    Written as a comparison of what is on screen, not of JSON: stations by
    name, movements by train and time. A diff nobody can read is the same as
    no diff at all, and this one exists precisely so that somebody reads it
    before agreeing to lose work.
    """
    if active is None:
        return {"first_activation": True}

    before, after = active.payload, pending.payload

    old_stations = {str(s["id"]): str(s.get("name") or s["id"]) for s in before.get("stations") or []}
    new_stations = {str(s["id"]): str(s.get("name") or s["id"]) for s in after.get("stations") or []}
    renamed = [
        f"{old_stations[key]} \u2192 {new_stations[key]}"
        for key in old_stations.keys() & new_stations.keys()
        if old_stations[key] != new_stations[key]
    ]

    old_links = {str(c["id"]) for c in before.get("connections") or []}
    new_links = {str(c["id"]) for c in after.get("connections") or []}

    old_moves = {str(m["id"]): _movement_signature(m) for m in before.get("trains") or []}
    new_moves = {str(m["id"]): _movement_signature(m) for m in after.get("trains") or []}
    by_id = {str(m["id"]): m for m in before.get("trains") or []}
    changed_moves = [
        str(by_id[key].get("train_number") or key)
        for key in sorted(old_moves.keys() & new_moves.keys())
        if old_moves[key] != new_moves[key]
    ]

    return {
        "first_activation": False,
        "stations": {
            "added": _named(sorted(new_stations[k] for k in new_stations.keys() - old_stations.keys())),
            "removed": _named(sorted(old_stations[k] for k in old_stations.keys() - new_stations.keys())),
            "renamed": _named(sorted(renamed)),
        },
        "connections": {
            "added": len(new_links - old_links),
            "removed": len(old_links - new_links),
        },
        "timetable": {
            "added": len(new_moves.keys() - old_moves.keys()),
            "removed": len(old_moves.keys() - new_moves.keys()),
            "changed": _named(sorted(set(changed_moves))),
            "total_before": len(old_moves),
            "total_after": len(new_moves),
        },
    }


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
        us_store: USStore | None = None,
        lifecycle: SQLiteMeetLifecycle | None = None,
    ):
        self.engine = engine
        self.identities = identities
        self.pairing = pairing
        self.config = config
        self.runtime_store = runtime_store
        self.local_configuration_store = local_configuration_store
        self.operations_store = operations_store
        self._station_service = station_service
        self._box_enrollment_lock = threading.Lock()
        self._box_enrollment_attempts: dict[str, list[float]] = {}
        self.us_store = us_store
        self.lifecycle = lifecycle or (SQLiteMeetLifecycle(runtime_store.path) if runtime_store else None)
        self.lifecycle_error = ""
        if self.lifecycle:
            self.engine._lock = self.lifecycle.lock
            try:
                self.lifecycle.bootstrap(runtime_store.active(), us_store.current_session() if us_store else None)
            except MeetLifecycleError as error:
                self.lifecycle_error = str(error)
            self.engine.runtime_guard = self._eu_runtime_guard
            if self._station_service:
                self._station_service.lifecycle = self.lifecycle
        self.cloud_config = CloudConfiguration(self) if self.lifecycle else None
        self.on_config_applied = None
        self.on_device_assignment_changed = None
        if self.lifecycle and us_store and (self.lifecycle.selected() or {}).get("region") == "us":
            old_link = us_store.cloud_link()
            if old_link and not runtime_store.link_token():
                runtime_store.save_central_url(old_link[0])
                runtime_store.save_link_token(old_link[1])
                runtime_store.set_cloud_auto_sync(True)
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
        self.us_web_root = files("tmbox_gateway").joinpath("us_web")

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
                if active is not None and not self._eu_runtime_guard():
                    self.operations_store.ensure_publication(active)

    def _eu_runtime_guard(self):
        if not self.lifecycle:
            return None
        try:
            self.lifecycle.assert_selected("eu", publication_id=self.engine.config.id)
        except MeetLifecycleError:
            return "wrong_session"
        return None

    def refresh_connection_grants(self, *, new_meet=False):
        """Refresh allowed panels after hot activation; keep admin sessions intact."""
        panels = sorted(self.engine.config.panels)
        self.identities.revoke_pairing_codes(label="Lokal enkel parkoppling")
        code = ""
        if panels:
            code = self.config.connection_code if not new_meet else ""
            code = code or f"{secrets.randbelow(1000000):06d}"
            hours = self.runtime_store.connection_code_validity_hours()
            code = self.identities.issue_pairing_code(panels,
                allowed_kinds=[DeviceKind.SWIFT_PANEL, DeviceKind.SWIFT_ADMIN, DeviceKind.WEB_ADMIN,
                               DeviceKind.TKL_TERMINAL, DeviceKind.ESP32_PANEL],
                ttl=timedelta(hours=hours) if hours else None, max_uses=50,
                label="Lokal enkel parkoppling", code=code)
        self.config = replace(self.config, connection_code=code)

    def server_context(self, client: PairedClient) -> dict[str, Any]:
        selected = self.lifecycle.selected() if self.lifecycle else None
        region = selected["region"] if selected else None
        admin = client.kind in {DeviceKind.WEB_ADMIN, DeviceKind.SWIFT_ADMIN}
        workspaces = ["administration"] if admin else []
        if region == "eu" and (admin or client.kind == DeviceKind.TKL_TERMINAL):
            workspaces.append("tkl")
        if region == "eu" and admin:
            workspaces.append("tmbox")
        if region == "us":
            if admin:
                workspaces.append("dispatcher")
            if client.kind == DeviceKind.US_CONDUCTOR:
                workspaces.append("conductor")
        return {
            "selected_meet": ({"id": selected["meet_id"], "name": selected.get("meet_name", ""),
                               "publication_id": selected["publication_id"], "operating_region": region,
                               "generation": selected["generation"]} if selected else None),
            "operating_region": region, "available_workspaces": workspaces,
            "cloud_update": self.cloud_config.status() if self.cloud_config and admin else {},
            "config_authority": "cloud", "local_editing": False,
            "transition_pending": bool(self.lifecycle and self.lifecycle.transition()),
            "error": self.lifecycle_error or None,
        }

    def check_config_update(self, client: PairedClient) -> dict[str, Any]:
        self._require_admin(client)
        if not self.cloud_config:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal lagring saknas")
        try:
            return self.cloud_config.check()
        except (CentralSyncError, RuntimePublicationError, USError) as error:
            raise HTTPAPIError(HTTPStatus.CONFLICT, "config_update_failed", str(error)) from error

    def local_admin(self, user: dict[str, object] | None = None) -> PairedClient:
        return PairedClient(
            client_id="local-web-admin",
            display_name=str(user["username"]) if user else "Lokal administratör",
            kind=DeviceKind.WEB_ADMIN,
            panel_ids=tuple(sorted(self.engine.config.panels)),
            admin_user_id=str(user["user_id"]) if user and user.get("user_id") else None,
            admin_role=str(user["role"]) if user else "owner",
        )

    def admin_access(self, client: PairedClient) -> dict[str, object]:
        self._require_admin(client)
        return self.identities.admin_access_summary()

    # ------------------------------------------------------- användare

    def _require_owner(self, client: PairedClient) -> None:
        """Bara ägaren får ändra vilka som har tillgång.

        En administratör kan allt annat på servern. Skillnaden är avsiktligt
        smal: den som lagts till ska kunna sköta en träff fullt ut, men inte
        kunna ge sig själv sällskap eller ta bort den som bjöd in hen.
        """

        self._require_admin(client)
        if client.admin_role != "owner":
            raise HTTPAPIError(
                HTTPStatus.FORBIDDEN,
                "owner_required",
                "Bara ägaren kan lägga till och ta bort användare",
            )

    def admin_users(self, client: PairedClient) -> dict[str, Any]:
        self._require_admin(client)
        return {"users": self.identities.list_admin_users(), "role": client.admin_role}

    def create_admin_user(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_owner(client)
        try:
            user = self.identities.invite_admin_user(
                str(payload.get("username") or ""),
                str(payload.get("role") or "admin"),
            )
        except AdminAccessError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_admin_user", str(error)) from error
        self._record_user_change(client, "user.invited", str(user.get("username")))
        return {"user": user}

    def reissue_admin_setup(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_owner(client)
        try:
            user = self.identities.reissue_admin_setup(str(payload.get("user_id") or ""))
        except AdminAccessError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_admin_user", str(error)) from error
        self._record_user_change(client, "user.invitation_reissued", str(user.get("username")))
        return {"user": user}

    def redeem_admin_setup(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Utan inloggning, med flit: den inbjudne har inget konto att logga in
        med förrän koden är inlöst. Koden är beviset."""

        try:
            user = self.identities.redeem_admin_setup(
                str(payload.get("username") or ""),
                str(payload.get("code") or ""),
                str(payload.get("password") or ""),
            )
        except AdminAccessError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_setup_code", str(error)) from error
        return {"user": user}

    def _record_user_change(self, client: PairedClient, action: str, target: str) -> None:
        """Vem gjorde vad mot vem. Utan det är en borttagen användare bara
        någon som inte längre finns."""

        if self.operations_store is None:
            return
        self.operations_store.record_audit_event(
            correlation_id=f"admin-users-{target}",
            source="web-admin",
            actor=client.admin_user_id or "konsol",
            action=action,
            outcome="ok",
            detail={"target": target, "by": client.display_name},
        )

    def delete_admin_user(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_owner(client)
        user_id = str(payload.get("user_id") or "")
        # Att ta bort sig själv är inte en behörighetsfråga utan ett misstag.
        # Den som vill sluta ber någon annan ägare ta bort kontot.
        if client.admin_user_id and client.admin_user_id == user_id:
            raise HTTPAPIError(
                HTTPStatus.CONFLICT,
                "cannot_remove_self",
                "Du kan inte ta bort ditt eget konto. Be en annan ägare göra det.",
            )
        target = (self.identities.admin_user(user_id) or {}).get("username", user_id)
        try:
            self.identities.delete_admin_user(user_id)
        except AdminAccessError as error:
            raise HTTPAPIError(HTTPStatus.CONFLICT, "invalid_admin_user", str(error)) from error
        self._record_user_change(client, "user.removed", str(target))
        return {"users": self.identities.list_admin_users()}

    def update_admin_user(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        """Roll och lösenord. Rollen är ägarens ensak; lösenordet får var och
        en byta på sig själv."""

        self._require_admin(client)
        user_id = str(payload.get("user_id") or "")
        try:
            if payload.get("role") is not None:
                self._require_owner(client)
                # Samma skäl som ovan: den som degraderar sig själv gör det av
                # misstag oftare än med avsikt.
                if client.admin_user_id == user_id and str(payload["role"]) != "owner":
                    raise HTTPAPIError(
                        HTTPStatus.CONFLICT,
                        "cannot_demote_self",
                        "Du kan inte ta bort din egen ägarroll. Be en annan ägare göra det.",
                    )
                self.identities.set_admin_user_role(user_id, str(payload["role"]))
                self._record_user_change(client, "user.role_changed", user_id)
            if payload.get("password") is not None:
                # Var och en får byta sitt eget lösenord. Någon annans är
                # ägarens ensak.
                if client.admin_user_id != user_id:
                    self._require_owner(client)
                self.identities.set_admin_user_password(user_id, str(payload["password"]))
        except AdminAccessError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_admin_user", str(error)) from error
        return {"user": self.identities.admin_user(user_id)}

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
        selected = self.lifecycle.selected() if self.lifecycle else None
        if selected:
            runtime.update(configured=True, publication_id=selected["publication_id"],
                           meet_id=selected["meet_id"], meet_name=selected.get("meet_name", ""))
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

    @runtime_view
    def complete_installation(
        self,
        client: PairedClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_admin(client)
        selected = self.lifecycle.selected() if self.lifecycle else None
        if self.runtime_store is None or not selected:
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "runtime_not_configured",
                "Hämta och aktivera en träff innan installationen avslutas",
            )
        self.lifecycle.assert_selected(selected["region"], publication_id=selected["publication_id"])
        if not self.runtime_store.server_name():
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "server_name_missing",
                "Ge servern ett namn innan installationen avslutas",
            )
        # Setup completion is metadata only, even when an old client submits an
        # active_day. Cloud supplies the initial day; later changes must use the
        # guarded runtime-day command, not bypass it through a repeated setup.
        active_day = self.runtime_store.active_day() if selected["region"] == "eu" else None
        self.runtime_store.complete_installation()
        return {
            "completed": True,
            "active_day": active_day,
            "restart_required": False,
            "message": "Grundinstallationen är klar. Servern använder den valda träffen.",
        }

    def pair(self, payload: dict[str, Any], request_host: str) -> dict[str, Any]:
        try:
            kind = DeviceKind(str(payload.get("device_kind", DeviceKind.SWIFT_PANEL.value)))
        except ValueError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_device_kind", "Okänd enhetstyp") from error

        try:
            result = self.pairing.pair(
                pairing_code=str(payload.get("pairing_code", "")),
                client_id=f"us-{uuid4()}" if kind == DeviceKind.US_CONDUCTOR else str(payload.get("client_id", "")),
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
            DeviceKind.US_CONDUCTOR,
        }:
            response["access_token"] = result.access_token
        return response

    def enroll_tmbox(self, payload: dict[str, Any], peer: str) -> dict[str, Any]:
        """Redeem a local connection code, without picking a station for a box."""
        with self._box_enrollment_lock:
            now = time.monotonic()
            self._box_enrollment_attempts = {
                key: [stamp for stamp in stamps if now - stamp < 60]
                for key, stamps in self._box_enrollment_attempts.items()
                if any(now - stamp < 60 for stamp in stamps)
            }
            attempts = self._box_enrollment_attempts.get(peer, [])
            if len(attempts) >= 5 or (peer not in self._box_enrollment_attempts and len(self._box_enrollment_attempts) >= 128):
                raise HTTPAPIError(HTTPStatus.TOO_MANY_REQUESTS, "pairing_rate_limited", "Vänta en minut innan nästa kodförsök.")
            self._box_enrollment_attempts[peer] = [*attempts, now]
            client_id = str(payload.get("client_id") or "")
            if not re.fullmatch(r"esp8266-[0-9a-f]{12}", client_id):
                raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_device_id", "Ogiltigt NodeMCU-ID.")
            grant = None
            try:
                grant = self.identities.reserve_pairing_code(
                    str(payload.get("pairing_code") or ""), DeviceKind.ESP32_PANEL,
                )
                # Legacy name ESP32_PANEL is the existing physical-box kind,
                # shared by MQTT v1 ESP8266 and ESP32 devices.
                client = self.identities.enroll_physical_box(client_id)
            except PairingError as error:
                if grant is not None:
                    self.identities.release_pairing_code(grant.pairing_id)
                raise HTTPAPIError(HTTPStatus.UNAUTHORIZED, error.code, str(error)) from error
            return {"accepted": True, "client_id": client.client_id,
                    "station_id": client.station_id,
                    "awaiting_station_assignment": not bool(client.station_id or client.panel_ids)}

    def us_access(self, client: PairedClient) -> tuple[USStore, str, bool]:
        if self.us_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "us_unavailable", "US runtime is not configured")
        dispatcher = client.kind in {DeviceKind.WEB_ADMIN, DeviceKind.SWIFT_ADMIN}
        if not dispatcher and client.kind != DeviceKind.US_CONDUCTOR:
            raise HTTPAPIError(HTTPStatus.FORBIDDEN, "us_access_required", "US dispatcher or conductor access required")
        actor = f"user:{client.admin_user_id}" if client.admin_user_id else f"client:{client.client_id}"
        return self.us_store, actor, dispatcher

    @runtime_view
    def us_context(self, client: PairedClient) -> dict[str, Any]:
        store, actor, dispatcher = self.us_access(client)
        if self.lifecycle:
            self.lifecycle.assert_selected("us")
        result = store.context(actor, dispatcher)
        if 'clock' not in result:
            result["clock"] = {"time": "12:00:00", "running": False, "configured": False, "scope": "us"}
        if dispatcher:
            result["conductors"] = [{"id": f"client:{c.client_id}", "name": c.display_name} for c in self.identities.enabled_clients() if c.kind == DeviceKind.US_CONDUCTOR]
            selected = self.lifecycle.selected() if self.lifecycle else None
            result['cloud'] = {'linked': bool(self.runtime_store and self.runtime_store.link_token())}
            result['packages'] = [p for p in store.package_catalogue() if not selected or p['publication_id'] == selected['publication_id']]
        return result

    def us_stage_package(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        store, _, dispatcher = self.us_access(client)
        if not dispatcher:
            raise HTTPAPIError(HTTPStatus.FORBIDDEN, 'us_dispatcher_required', 'Dispatcher access required')
        return {'package': store.stage_package(payload.get('package')), 'staged': True, 'restart_required': False}

    def us_cloud_download(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        store, _, dispatcher = self.us_access(client)
        if not dispatcher:
            raise HTTPAPIError(HTTPStatus.FORBIDDEN, 'us_dispatcher_required', 'Dispatcher access required')
        link = store.cloud_link()
        code = str(payload.get('sync_code') or '').strip()
        if code and (len(code) != 6 or not code.isascii() or not code.isdigit()):
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, 'invalid_sync_code', 'Enter a six-digit Cloud code')
        if not code and not link:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, 'us_not_linked', 'Connect with a six-digit Cloud code first')
        url = canonical_runtime_url(str(payload.get('central_url') or (link[0] if link else DEFAULT_RUNTIME_PUBLICATION_URL))) if code else link[0]
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, 'invalid_central_url', 'Enter a full HTTP or HTTPS Config URL without credentials')
        try:
            download = self.runtime_fetcher(code, url) if code else self.linked_runtime_fetcher(link[1], url, False)
        except CentralSyncError as error:
            raise HTTPAPIError(HTTPStatus.BAD_GATEWAY, 'central_sync_failed', str(error)) from error
        if not isinstance(download, CentralRuntimeDownload):
            raise HTTPAPIError(HTTPStatus.BAD_GATEWAY, 'invalid_us_download', 'Cloud returned no US package')
        summary = store.stage_package(download.package, url=url, token=download.link_token,
                                      expected_link=link if not code else None)
        return {'package': summary, 'staged': True, 'linked': bool(store.cloud_link()), 'restart_required': False}

    @runtime_command("us")
    def us_command(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        store, actor, dispatcher = self.us_access(client)
        action = str(payload.get("action", ""))
        if action == "create_session" and self.lifecycle:
            selected = self.lifecycle.assert_selected("us")
            if payload.get("package") is not None or payload.get("publication_id") != selected["publication_id"]:
                raise HTTPAPIError(HTTPStatus.CONFLICT, "cloud_config_required", "Starta serverns valda publicerade Cloud-config.")
        if action == "assign":
            conductor = next((c for c in self.identities.enabled_clients() if c.kind == DeviceKind.US_CONDUCTOR and f"client:{c.client_id}" == payload.get("conductor_id")), None)
            if conductor is None:
                raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "unknown_conductor", "Select a paired conductor")
            payload = {**payload, "conductor_name": conductor.display_name}
        clock = store.context(actor, dispatcher).get("clock", {"time": "12:00"})
        result = store.execute(actor, dispatcher, action, payload, str(clock["time"]))
        if action == 'create_session' and self.runtime_store and self.runtime_store.server_name() and self.runtime_store.installation_required() and self.runtime_store.active() is None:
            # Explicitly starting a US session completes first-time setup too;
            # it does not create/activate an EU publication or set the EU day.
            self.runtime_store.complete_installation()
        return result

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

    @runtime_command("eu")
    def command(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        if client.kind == DeviceKind.ESP32_PANEL:
            current = self.identities.client(client.client_id)
            if current is None:
                raise HTTPAPIError(HTTPStatus.FORBIDDEN, "panel_not_assigned", "Enheten har inte tillgång till den panelen")
            client = current
        if payload.get("traffic_session_id") and payload["traffic_session_id"] != self.engine.config.id:
            raise HTTPAPIError(HTTPStatus.CONFLICT, "wrong_session", "Kommandot tillhör en tidigare config.")
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
                train_number=payload.get("train_number"),
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
        self._station_service.lifecycle = self.lifecycle
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

    @runtime_command("eu")
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

    @runtime_view
    def runtime_summary(self, client: PairedClient) -> dict[str, Any]:
        if self.runtime_store is None:
            return {"configured": False, "central_url": canonical_runtime_url(self.config.central_runtime_url)}
        context = self.server_context(client)
        selected = context["selected_meet"]
        return {
            **self.runtime_store.summary(),
            **({"configured": True, "publication_id": selected["publication_id"],
                "meet_id": selected["id"], "meet_name": selected["name"]} if selected else {}),
            "central_url": canonical_runtime_url(self.runtime_store.central_url() or self.config.central_runtime_url),
            "server_context": context,
        }

    @runtime_view
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
        selected = self.lifecycle.selected() if self.lifecycle else None
        if selected and selected["region"] == "us":
            clock = self.clock_status(self.local_admin())
            meet = {"id": selected["meet_id"], "name": selected.get("meet_name", ""), "operating_region": "us"}
            publication_id = selected["publication_id"]
            positions = []
        clock = self._clock_display(clock)
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

    @runtime_command("eu")
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
            "meet_generation": (self.lifecycle.selected() or {}).get("generation") if self.lifecycle else None,
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

    @runtime_command("eu")
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

    @runtime_command("eu")
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

    @runtime_command("eu")
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
                # Saknas fältet lämnas anteckningen ifred. Ett anrop som bara
                # byter spår ska inte råka radera vad någon annan skrivit.
                operator_note=(
                    str(payload["operator_note"])[:200]
                    if payload.get("operator_note") is not None
                    else None
                ),
            )
        except ValueError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_tkl_movement", str(error)) from error
        return {"movement": result}

    @runtime_command("eu")
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

    @runtime_command("eu")
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
        current = self.identities.client(client.client_id)
        if current is None and client.kind == DeviceKind.ESP32_PANEL:
            raise HTTPAPIError(HTTPStatus.FORBIDDEN, "station_not_assigned", "Terminalen har inte tillgång till stationen")
        if current is not None:
            client = current
        if client.station_id is not None and client.station_id == station_id:
            return
        station_panels = {
            panel.id for panel in self.engine.config.panels.values() if panel.station_id == station_id
        }
        if not station_panels.intersection(client.panel_ids):
            raise HTTPAPIError(HTTPStatus.FORBIDDEN, "station_not_assigned", "Terminalen har inte tillgång till stationen")

    def _clock_scope(self) -> str:
        selected = self.lifecycle.selected() if self.lifecycle else None
        if selected:
            return f"{selected['region']}:{selected['meet_id']}"
        return f"eu:{self.engine.config.id}"

    def _clock_display(self, clock: dict[str, Any]) -> dict[str, Any]:
        settings = self.runtime_store.clock_display_settings(self._clock_scope()) if self.runtime_store else {}
        styles = clock.get("available_styles") or list(AVAILABLE_CLOCK_STYLES)
        style = settings.get("style", styles[0])
        return {**clock, "available_styles": styles, "style": style if style in styles else styles[0],
                "show_seconds": settings.get("show_seconds", clock.get("show_seconds", True))}

    def clock_status(self, client: PairedClient) -> dict[str, Any]:
        selected = self.lifecycle.selected() if self.lifecycle else None
        if selected and selected["region"] == "us":
            result = self.us_context(client)
            return self._clock_display({**result["clock"], "configured": bool(result["session"])})
        return self._clock_display(self.operations_store.clock_status() if self.operations_store else {"configured": False, "running": False})

    @runtime_command()
    def control_clock(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        if payload.get("action") == "appearance":
            if self.runtime_store is None:
                raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "clock_unavailable", "Klockinställningarna kan inte sparas")
            current = self.clock_status(client)
            style, seconds = payload.get("style"), payload.get("show_seconds")
            if style not in current["available_styles"] or not isinstance(seconds, bool):
                raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_clock", "Välj ett giltigt klockutseende och sekundvisning")
            self.runtime_store.save_clock_display_settings(self._clock_scope(), style, seconds)
            return self.clock_status(client)
        selected = self.lifecycle.selected() if self.lifecycle else None
        if selected and selected["region"] == "us":
            store, actor, _ = self.us_access(client)
            context = store.context(actor, True)
            action = payload.get("action")
            if action not in {"start", "stop", "speed", "set"}:
                raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_clock_action", "Okänt klockkommando")
            # Reject malformed edits before creating a first session or touching
            # the current one. Missing fields retain the session/package defaults.
            try:
                validate_us_clock_settings({
                    **({"clock_time": payload["time"]} if "time" in payload else {}),
                    **({"clock_speed": payload["speed"]} if "speed" in payload else {}),
                })
            except ValueError as error:
                raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_clock", str(error)) from error
            if context["session"] is None or context["session"]["status"] == "closed":
                if payload.get("action") not in {"start", "set"}:
                    raise HTTPAPIError(HTTPStatus.CONFLICT, "session_not_started", "Starta US-körningen först.")
                package = next(p for p in store.package_catalogue() if p["publication_id"] == selected["publication_id"])
                self.us_command(client, {"action": "create_session", "command_id": str(uuid4()), "confirmed": True,
                    "publication_id": selected["publication_id"], "package_checksum": package["checksum"]})
                context = store.context(actor, True)
            clock = context["clock"]
            self.us_command(client, {"action": "clock", "command_id": str(uuid4()), "confirmed": True,
                "session_id": context["session"]["id"], "expected_revision": context["session"]["revision"],
                "clock_time": payload.get("time") or clock["time"], "clock_speed": payload.get("speed", clock["speed"]),
                "running": action == "start" or (action in {"speed", "set"} and clock["running"])})
            return self.clock_status(client)
        if self.operations_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "clock_unavailable", "Den lokala klockan är inte tillgänglig")
        action = str(payload.get("action") or "")
        try:
            if action in {"start", "set", "speed"}:
                self.operations_store.configure_clock(time_value=payload.get("time"),
                    speed=float(payload["speed"]) if payload.get("speed") is not None else None,
                    running=True if action == "start" else None)
                return self.clock_status(client)
            if action == "stop":
                self.operations_store.stop_clock(str(payload.get("reason") or "") or None)
                return self.clock_status(client)
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

    # ── Säkerhetskopior ────────────────────────────────────────────────

    def _backup_dir(self) -> Path:
        return Path(self.config.state_dir) / "backups"

    def _current_meet_name(self) -> str:
        """Vad som skrivs över. Träffens namn om det finns en, annars serverns.

        Bekräftelsen ska namnge datan som försvinner, inte vara ett ord man
        skriver av. NOLLSTÄLL duger för fabriksåterställningen, som alltid tar
        allt; här beror det på vad som råkar ligga i servern just nu.
        """

        if self.runtime_store is None:
            return "TrainMeet Server"
        summary = self.runtime_store.summary()
        return str(
            summary.get("meet_name")
            or summary.get("server_name")
            or "TrainMeet Server"
        )

    def server_backups(self, client: PairedClient) -> dict[str, Any]:
        self._require_admin(client)
        return {
            "supported": self.config.allow_restart,
            "overwrites": self._current_meet_name(),
            "backups": backup.available(self._backup_dir()),
        }

    def restore_backup(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        """Återställningen skriver över hela databasen - även användarna.

        Därför ägaren och ingen annan: en administratör kan sköta servern, men
        att byta ut vilka som har tillgång är ägarens ensak, och en
        återställning gör precis det på omvägen.
        """

        self._require_owner(client)
        if not self.config.allow_restart:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "restore_unavailable",
                "Återställning kräver att servern kan startas om, vilket det här körläget inte tillåter",
            )

        try:
            path = backup.resolve(self._backup_dir(), str(payload.get("backup", "")))
        except backup.BackupError as error:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_backup", str(error)) from error

        described = backup.describe(path)
        if not described["usable"]:
            raise HTTPAPIError(
                HTTPStatus.CONFLICT,
                "backup_not_usable",
                f"Säkerhetskopian går inte att återställa: {described['problem']}",
            )

        overwrites = self._current_meet_name()
        given = str(payload.get("confirmation", "")).strip()
        if given.casefold() != overwrites.casefold():
            raise HTTPAPIError(
                HTTPStatus.BAD_REQUEST,
                "restore_not_confirmed",
                f"Skriv {overwrites} för att bekräfta att den datan skrivs över",
            )

        return {
            "status": "restoring",
            "backup": described,
            "overwrote": overwrites,
            "message": (
                f"Servern återställs till kopian från {described['taken_at']} och startar om."
            ),
            # Hela databasen byts ut, inte bara träffen. Den som läser det här
            # ska veta vad som händer med det som var inloggat och parkopplat.
            "consequences": [
                "Träffdata, tidtabell och driftläge blir det som fanns när kopian togs.",
                "Inloggningar och lösenord blir också de som gällde då.",
                "Enheter som parkopplats efter kopian måste parkopplas igen.",
                "Anslutna skärmar och TMBoxar återansluter av sig själva efter omstarten.",
            ],
            "path": str(path),
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

    def build_timetable(self, client: PairedClient) -> dict[str, Any]:
        """Tågrörelserna för BYGG steg 3.

        Samma form oavsett var de kommer ifrån, precis som `build_topology` -
        vyn ska ha en renderare och inte två kodvägar för Cloud och lokalt.

        Till skillnad från steg 2 finns här ingen låsning. Tidtabellen är
        alltid redigerbar lokalt, även när grundrevisionen kommer från Cloud:
        servern är runtime och tidtabellen är det som ändras under en träff.
        Cloud är säkerhetskopian. En ändring här blir en lokal revision som
        någon aktiverar själv - den slår aldrig igenom av sig själv.

        Stationer och spår följer med, för att vyn ska kunna visa namn i
        stället för id och erbjuda de spår som faktiskt finns.
        """
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "runtime_unavailable",
                "Lokal lagring saknas",
            )

        draft: dict[str, Any] = {}
        if self.local_configuration_store is not None:
            draft = self.local_configuration_store.current().get("draft") or {}

        # Ett lokalt utkast vinner när det har rader: det är där någon just
        # har redigerat. Saknas det läser vi den aktiva publikationen.
        if draft.get("trains"):
            payload = draft
            source = "lokal"
            revision = self.local_configuration_store.current().get("revision")
        else:
            publication = self.runtime_store.active()
            payload = publication.payload if publication is not None else {}
            source = "cloud" if publication is not None else "tom"
            revision = self.local_configuration_store.current().get("revision") if self.local_configuration_store else None

        stations = {
            str(station["id"]): str(station.get("name") or station["id"])
            for station in payload.get("stations") or []
        }
        codes = {
            str(station["id"]): str(station.get("code") or "")
            for station in payload.get("stations") or []
        }
        # Spåren ligger olika i de två källorna: runtime-paketet har dem platt
        # på toppnivå med station_id, ett lokalt utkast har dem nästlade under
        # stationen. Den skillnaden hör hemma här och ingen annanstans - vyn
        # ska se en lista.
        catalogue = list(payload.get("tracks") or [])
        for station in payload.get("stations") or []:
            for track in station.get("tracks") or []:
                catalogue.append({**track, "station_id": station["id"]})
        tracks = {
            str(track["id"]): str(track.get("display_label") or track.get("label") or track["id"])
            for track in catalogue
        }

        rows = []
        for row in payload.get("trains") or []:
            station_id = str(row.get("station_id") or "")
            track_id = str(row.get("track_id") or "")
            rows.append(
                {
                    "id": str(row.get("id") or ""),
                    "train_number": str(row.get("train_number") or ""),
                    "train_type": str(row.get("train_type") or "person"),
                    "days": str(row.get("days") or "Dagl"),
                    "station_id": station_id,
                    "station": stations.get(station_id, str(row.get("station") or station_id)),
                    "station_code": codes.get(station_id, ""),
                    "track_id": track_id,
                    "track": tracks.get(track_id, ""),
                    "arrival_time": row.get("arrival_time") or "",
                    "departure_time": row.get("departure_time") or "",
                    "sort_time": row.get("sort_time") or "",
                    "arrival_from": row.get("arrival_from") or "",
                    "departure_to": row.get("departure_to") or "",
                    "no_stop": bool(row.get("no_stop")),
                    "note": str(row.get("note") or ""),
                }
            )

        return {
            # Ingen låsning här, till skillnad från steg 2. Fältet finns ändå
            # så att vyn kan läsa samma nyckel i båda stegen.
            "locked": False,
            "source": source,
            # Vyn ska inte jämföra källsträngar för att veta vad den tittar på.
            # Samma regel som `locked` i build_topology: beslutet fattas här.
            "base_from_cloud": source == "cloud",
            "revision": revision,
            "rows": rows,
            "stations": [
                {"id": str(station["id"]), "name": stations[str(station["id"])], "code": codes[str(station["id"])]}
                for station in payload.get("stations") or []
            ],
            "tracks": [
                {
                    "id": str(track["id"]),
                    "label": tracks[str(track["id"])],
                    "station_id": str(track.get("station_id") or ""),
                }
                for track in catalogue
            ],
        }

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
        self._require_topology_unchanged(draft)
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

    #: Vad i ett utkast som hör till *banan* och inte till dagens trafik.
    #: Cloud äger de här så länge servern är Cloud-kopplad. Tidtabellen ägs
    #: aldrig av Cloud när träffen väl kör.
    TOPOLOGY_SECTIONS = ("stations", "connections", "panels")

    @classmethod
    def _topology_of(cls, configuration: dict[str, Any]) -> dict[str, Any]:
        """The line, in a form two configurations can be compared by.

        Sorted by id, because a publication and a draft can hold the same
        stations in different order without anything having changed - and a
        gate that refuses a save over row order is a gate people route around.
        """
        return {
            section: sorted(
                (configuration.get(section) or []),
                key=lambda row: str(row.get("id", "")),
            )
            for section in cls.TOPOLOGY_SECTIONS
        }

    def _require_topology_unchanged(self, draft: dict[str, Any]) -> None:
        """T3: in Cloud mode the timetable stays editable. The line does not.

        The old rule was one gate over the whole draft, which made the two
        indistinguishable: correcting a departure time was refused for the same
        reason as redrawing the line. But during a meet the server *is* the
        operation - trains run late and movements get cancelled, and there is
        no route through Cloud for that at 13:40 on a Saturday.

        The line is the other way round. It is interpreted from the meet's own
        documents and reviewed in Cloud, it is drawn once, and it sits still.
        """
        if self.runtime_store is None or self.runtime_store.editing_is_open():
            return
        active = self.runtime_store.active()
        if active is None:
            return
        base = self._topology_of(local_configuration_from_publication(active.payload))
        incoming = self._topology_of(draft)
        changed = [
            section for section in self.TOPOLOGY_SECTIONS if incoming[section] != base[section]
        ]
        if not changed:
            return
        names = {"stations": "stationer", "connections": "sträckor", "panels": "paneler"}
        raise HTTPAPIError(
            HTTPStatus.CONFLICT,
            "topology_locked_by_cloud",
            "Banan kommer från TrainMeet Cloud och ändras inte här ("
            + ", ".join(names[section] for section in changed)
            + "). Tidtabellen går att rätta som vanligt.",
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
        # Seeding är en kopia, inte en ändring: den aktiva träffen står orörd.
        # Den måste gå att göra även i Cloud-läge, annars finns inget utkast
        # att rätta tidtabellen i - och tidtabellen ska alltid gå att rätta.
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
        """Same validated, guarded pipeline as a manual config check; no restart."""
        return self.cloud_config.check(automatic=True) if self.cloud_config else {"checked": False}

    # ------------------------------------------------- väntande Cloud-revision

    def pending_revision_state(self, client: PairedClient) -> dict[str, Any]:
        """What is waiting, and exactly what saying yes would cost.

        Counting rows is not enough. "3 stationer ändras" tells an operator
        nothing about whether the change matters; naming them does. This exists
        so that nobody activates a revision without having been shown what it
        replaces.
        """
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal lagring saknas"
            )
        pending = self.runtime_store.pending_publication()
        if pending is None:
            return {"pending": False}
        active = self.runtime_store.active()
        return {
            "pending": True,
            "publication_id": pending.publication_id,
            "meet_name": pending.meet_name,
            "published_at": pending.published_at,
            "active_publication_id": active.publication_id if active else None,
            "local_revisions": self.runtime_store.local_revisions(),
            "changes": _revision_changes(active, pending),
        }

    def activate_pending_revision(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        """Activate the waiting revision, because somebody said so.

        The publication id has to be sent back and has to match. Without it a
        stale button in a tab left open since this morning could activate a
        revision that arrived since - the same silent overwrite, entering
        through the UI instead of through the poller.
        """
        self._require_admin(client)
        if self.runtime_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal lagring saknas"
            )
        pending = self.runtime_store.pending_publication()
        if pending is None:
            raise HTTPAPIError(
                HTTPStatus.CONFLICT,
                "no_pending_revision",
                "Ingen revision väntar på aktivering",
            )
        confirmed = str(payload.get("publication_id") or "")
        if confirmed != pending.publication_id:
            raise HTTPAPIError(
                HTTPStatus.CONFLICT,
                "pending_revision_changed",
                "En annan revision väntar nu. Läs om vad den ändrar innan du aktiverar.",
            )
        publication = self.runtime_store.activate(pending.publication_id)
        if self.operations_store is not None:
            self.operations_store.ensure_publication(publication)
        return {
            "activated": True,
            "publication_id": publication.publication_id,
            "restart_required": publication.session_config() != self.engine.config,
        }

    def build_local_configuration_from_stations(self, client: PairedClient) -> dict[str, Any]:
        """The shortcut: derive connections and A-D panels from the station order.

        Goes through the same save path as any other edit, so the same rules
        apply. In Cloud mode that means it is refused with
        `topology_locked_by_cloud` - which is right: the shortcut builds the
        line, and the line is Cloud's while Cloud is linked.
        """
        self._require_admin(client)
        if self.local_configuration_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "local_configuration_unavailable",
                "Lokal konfigurationslagring saknas",
            )
        current = self.local_configuration_store.current()
        draft = current.get("draft") or {}
        if not (draft.get("stations") or []):
            raise HTTPAPIError(
                HTTPStatus.CONFLICT,
                "no_stations",
                "Lägg till stationer först - genvägen bygger sträckor och paneler ur deras ordning.",
            )
        return self.save_local_configuration(
            client,
            {"draft": build_from_station_order(draft), "expected_revision": current.get("revision")},
        )

    def activate_local_configuration(
        self,
        client: PairedClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_admin(client)
        # Ingen global grind här heller: en rättad tidtabell ska kunna bli en
        # lokal revision även när grundrevisionen kommer från Cloud.
        if self.local_configuration_store is None or self.runtime_store is None:
            raise HTTPAPIError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "local_configuration_unavailable",
                "Lokal konfigurationslagring saknas",
            )
        # Kontrollen görs på det utkast som faktiskt aktiveras, inte på det
        # som råkade sparas sist: spara och aktivera är två anrop och kan komma
        # i vilken ordning som helst.
        self._require_topology_unchanged(
            self.local_configuration_store.current().get("draft") or {}
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

    @runtime_command("eu")
    def set_active_day(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        """Change the operating day, never the published Cloud configuration."""
        self._require_admin(client)
        if self.runtime_store is None or self.runtime_store.active() is None:
            raise HTTPAPIError(
                HTTPStatus.NOT_FOUND,
                "runtime_not_configured",
                "Ingen tidtabell är publicerad",
            )
        publication = self.runtime_store.active()
        if self.lifecycle is None or self.operations_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Driftläget kan inte verifieras.")
        selected = self.lifecycle.assert_selected("eu", publication_id=publication.publication_id)
        if type(payload.get("meet_generation")) is not int or payload["meet_generation"] != selected["generation"]:
            raise HTTPAPIError(HTTPStatus.CONFLICT, "stale_meet_context", "Läs in aktuell träff innan du byter trafikdag.")
        day = payload.get("active_day")
        if not isinstance(day, str) or not day.strip() or len(day.strip()) > 40:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_active_day", "Ange en giltig trafikdag.")
        day = day.strip()
        if day == self.runtime_store.active_day():
            return {"active_day": day, "meet_generation": selected["generation"], "changed": False}
        blockers = self.operations_store.config_update_blockers(publication, publication)
        if self.operations_store.clock_status().get("running"):
            blockers.append("Stoppa klockan innan du byter trafikdag.")
        if any(value.state != ConnectionState.FREE for value in self.engine.connections.values()):
            blockers.append("Avsluta pågående trafik och klareringar först.")
        if any(value.mode != InteractionMode.IDLE for value in self.engine.panels.values()):
            blockers.append("Avsluta pågående TMBox-inmatning först.")
        if blockers:
            raise HTTPAPIError(HTTPStatus.CONFLICT, "active_day_busy", " ".join(dict.fromkeys(blockers)))
        # Day affects timetable/command meaning. Fail closed across the store
        # writes and advance scope so queued commands from yesterday are stale.
        ticket = self.lifecycle.begin_transition("eu", selected["meet_id"], selected["publication_id"],
            meet_name=selected.get("meet_name", ""), expected_generation=selected["generation"])
        self.runtime_store.set_active_day(day)
        self.runtime_store.bump_config_version()
        self.runtime_store._save_setting("require_scoped_commands", "true")
        self.engine.adopt_config(self.engine.config)
        updated = self.lifecycle.complete_transition(ticket)
        if self.on_config_applied:
            try:
                self.on_config_applied()
            except Exception:
                LOGGER.warning("Trafikdagen har ändrats; enheter hämtar den vid nästa kontakt.", exc_info=True)
        return {"active_day": day, "meet_generation": updated["generation"], "changed": True}

    def sync_runtime(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_admin(client)
        if self.cloud_config:
            try:
                return self.cloud_config.connect(payload)
            except (CentralSyncError, RuntimePublicationError, USError) as error:
                raise HTTPAPIError(HTTPStatus.CONFLICT, "cloud_connection_failed", str(error)) from error
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
        elif isinstance(result, dict):
            package = result
        else:
            raise HTTPAPIError(
                HTTPStatus.BAD_GATEWAY,
                "central_sync_failed",
                "TrainMeet skickade inget driftpaket",
            )
        # Route by schema BEFORE touching EU connection tokens or publications.
        # A US package is staged only; starting a session is an explicit US command.
        if isinstance(package, dict) and package.get('schema') == 'trainmeet.us.runtime/1':
            store, _, _ = self.us_access(client)
            staged = store.stage_package(package, url=central_url,
                                         token=result.link_token if isinstance(result, CentralRuntimeDownload) else None)
            return {'operating_region': 'us', 'staged': True, 'package': staged,
                    'linked': bool(store.cloud_link()), 'restart_required': False,
                    'message': 'US-paketet är hämtat. Granska och starta det i US Dispatcher.',
                    'open_url': '/us/dispatcher'}
        if self.runtime_store is not None:
            try:
                self.runtime_store.save_central_url(central_url)
            except RuntimePublicationError as error:
                raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_central_url", str(error)) from error
        response = self.install_runtime(client, {"package": package})
        if self.runtime_store is not None and isinstance(result, CentralRuntimeDownload) and result.link_token:
            self.runtime_store.save_link_token(result.link_token)
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

    @runtime_command("eu")
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
        self._notify_device_assignment(assigned.client_id)
        return {
            "device_id": assigned.client_id,
            "station_id": assigned.station_id,
            "assigned_panel_ids": list(assigned.panel_ids),
        }

    @runtime_view
    def remove_device(self, client: PairedClient, payload: dict[str, Any]) -> dict[str, Any]:
        # Server device management also works without an active meet. Serialize
        # with traffic commands so none can race the revocation of its grants.
        self._require_admin(client)
        device_id = str(payload.get("device_id") or "").strip()
        if not device_id:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "device_required", "Välj en TMBox att ta bort")
        try:
            self.identities.remove_discovered_device(device_id)
        except PairingError as error:
            raise HTTPAPIError(HTTPStatus.NOT_FOUND, error.code, str(error)) from error
        self._notify_device_assignment(device_id)
        return {"device_id": device_id, "removed": True}

    def _notify_device_assignment(self, device_id: str) -> None:
        if self.on_device_assignment_changed:
            try:
                self.on_device_assignment_changed(device_id)
            except Exception:
                # Grants are already committed. An offline broker must not
                # undo them; the next hello republishes the current assignment.
                LOGGER.exception("Could not publish changed TMBox assignment")

    @staticmethod
    def _require_admin(client: PairedClient) -> None:
        if client.kind not in {DeviceKind.WEB_ADMIN, DeviceKind.SWIFT_ADMIN}:
            raise HTTPAPIError(
                HTTPStatus.FORBIDDEN,
                "admin_required",
                "Administratörsbehörighet krävs",
            )

    def static_asset(self, path: str) -> tuple[bytes, str] | None:
        if path in {"/us", "/us/", "/us/dispatcher", "/us/conductor", "/us/app.js", "/us/style.css", "/us/workspace-messages.js"}:
            name = path.rsplit("/", 1)[-1]
            if name not in {"app.js", "style.css", "workspace-messages.js"}:
                name = "index.html"
            return self.us_web_root.joinpath(name).read_bytes(), mimetypes.guess_type(name)[0] or "text/plain"
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
            "/assets/i18n.js": "i18n.js",
            "/assets/i18n-messages.js": "i18n-messages.js",
            "/assets/shell-messages.js": "shell-messages.js",
            "/assets/meet-type-messages.js": "meet-type-messages.js",
            "/assets/us-cloud-messages.js": "us-cloud-messages.js",
            "/assets/meet-type.css": "meet-type.css",
            "/assets/i18n-init.js": "i18n-init.js",
            "/assets/tmbox-fixtures.js": "tmbox-fixtures.js",
            "/assets/tmbox-legacy-catalog.js": "tmbox-legacy-catalog.js",
            "/assets/tmbox-guide.js": "tmbox-guide.js",
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
            if path in {"/v1/operating-mode", "/v1/local-configuration", "/v1/build/timetable", "/v1/build/topology"}:
                self.server.application._require_admin(self._authenticated_client())
                raise HTTPAPIError(HTTPStatus.GONE, "cloud_authoring_only", "Träffen redigeras i Cloud. Befintliga lokala utkast är arkiverade, inte aktiva arbetsytor.")
            if path == "/v1/server-context":
                self._send_json(HTTPStatus.OK, self.server.application.server_context(self._authenticated_client()))
                return
            if path == "/v1/clock":
                self._send_json(HTTPStatus.OK, self.server.application.clock_status(self._authenticated_client()))
                return
            if path == "/v1/us/context":
                self._send_json(HTTPStatus.OK, self.server.application.us_context(self._authenticated_client()))
                return
            if path == '/v1/us/package':
                application = self.server.application
                with application.engine._lock:
                    store, _, dispatcher = application.us_access(self._authenticated_client())
                    if not dispatcher:
                        raise HTTPAPIError(HTTPStatus.FORBIDDEN, 'us_dispatcher_required', 'Dispatcher access required')
                    publication_id = parse_qs(parsed.query).get('publication_id', [''])[0]
                    if application.lifecycle:
                        selected = application.lifecycle.assert_selected("us")
                        if publication_id and publication_id != selected["publication_id"]:
                            raise HTTPAPIError(HTTPStatus.CONFLICT, "stale_meet_context", "Package does not belong to the selected configuration.")
                        publication_id = selected["publication_id"]
                    self._send_json(HTTPStatus.OK, {'package': store.saved_package(publication_id)})
                return
            if path == "/v1/us/command-status":
                application = self.server.application
                with application.engine._lock:
                    store, actor, _ = application.us_access(self._authenticated_client())
                    if application.lifecycle:
                        application.lifecycle.assert_selected("us")
                    command_id = parse_qs(parsed.query).get("command_id", [""])[0]
                    result = store.command_status(actor, command_id)
                    current = store.current_session()
                    if result and (not current or result.get("session_id") != current["id"]):
                        result = None
                    self._send_json(HTTPStatus.OK, {"result": result})
                return
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
                        # Läget svarar på hur man är inne. "local" betyder att
                        # servern släpper in utan inloggning, och det gör den
                        # numera bara innan det finns ett lösenord att logga in
                        # med - alltså under installationen.
                        "access_mode": "local" if self._installation_is_open() else "external",
                        # Var man står är en annan fråga än vem man är. Den
                        # avgör inte längre behörighet, men fabriksåterställning
                        # kräver fortfarande att man står vid maskinen.
                        "at_the_machine": self._at_the_machine(),
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
            if path == "/v1/admin/users":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.admin_users(client))
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
            if path == "/v1/server/backups":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.server_backups(client))
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
            if path == "/v1/runtime/pending":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK, self.server.application.pending_revision_state(client)
                )
                return
            if path == "/v1/build/timetable":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.build_timetable(client))
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
        except MeetLifecycleError as error:
            self._send_api_error(HTTPAPIError(HTTPStatus.CONFLICT, "meet_context_conflict", str(error)))
        except USError as error:
            self._send_api_error(HTTPAPIError(HTTPStatus(error.status), "us_error", str(error)))
        except HTTPAPIError as error:
            self._send_api_error(error)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path in {"/v1/local-configuration", "/v1/local-configuration/seed", "/v1/local-configuration/build",
                        "/v1/local-configuration/activate", "/v1/operating-mode", "/v1/runtime/install", "/v1/us/packages"}:
                self.server.application._require_admin(self._authenticated_client())
                raise HTTPAPIError(HTTPStatus.GONE, "cloud_authoring_only", "Träffens config redigeras och publiceras i TrainMeet Cloud. Lokala utkast finns kvar som historik.")
            if path in {"/v1/config/check", "/v1/runtime/update", "/v1/runtime/activate", "/v1/runtime/pending/activate"}:
                self._send_json(HTTPStatus.OK, self.server.application.check_config_update(self._authenticated_client()))
                return
            if path == "/v1/us/cloud/download":
                client = self._authenticated_client()
                result = self.server.application.sync_runtime(client, payload) if payload.get("sync_code") else self.server.application.check_config_update(client)
                self._send_json(HTTPStatus.OK, result)
                return
            if path == "/v1/us/commands":
                self._send_json(HTTPStatus.OK, self.server.application.us_command(self._authenticated_client(), payload))
                return
            if path == '/v1/us/packages':
                self._send_json(HTTPStatus.CREATED, self.server.application.us_stage_package(self._authenticated_client(), payload))
                return
            if path == '/v1/us/cloud/download':
                self._send_json(HTTPStatus.OK, self.server.application.us_cloud_download(self._authenticated_client(), payload))
                return
            if path == "/v1/us/conductor-code":
                client = self._authenticated_client()
                self.server.application._require_admin(client)
                self.server.application.us_access(client)
                code = self.server.application.identities.issue_pairing_code([], allowed_kinds=(DeviceKind.US_CONDUCTOR,), label="US conductor", max_uses=1)
                self._send_json(HTTPStatus.CREATED, {"code": code, "expires_in_minutes": 15})
                return
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
            if path == "/v1/tmbox/enroll":
                self._send_json(HTTPStatus.CREATED, self.server.application.enroll_tmbox(payload, self.client_address[0]))
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
            if path == "/v1/devices/remove":
                client = self._authenticated_client()
                self._send_json(HTTPStatus.OK, self.server.application.remove_device(client, payload))
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
            if path == "/v1/runtime/pending/activate":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.activate_pending_revision(client, payload),
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
            if path == "/v1/admin/users":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.CREATED,
                    self.server.application.create_admin_user(client, payload),
                )
                return
            if path == "/v1/admin/users/update":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.update_admin_user(client, payload),
                )
                return
            if path == "/v1/admin/users/reissue":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.reissue_admin_setup(client, payload),
                )
                return
            if path == "/v1/admin/users/redeem":
                # Den inbjudne är inte inloggad än. Koden är hela beviset.
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.redeem_admin_setup(payload),
                )
                return
            if path == "/v1/admin/users/delete":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.delete_admin_user(client, payload),
                )
                return
            if path == "/v1/local-configuration/seed":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.seed_local_configuration(client),
                )
                return
            if path == "/v1/local-configuration/build":
                client = self._authenticated_client()
                self._send_json(
                    HTTPStatus.OK,
                    self.server.application.build_local_configuration_from_stations(client),
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
                    local_access=self._at_the_machine(),
                )
                self.server.request_factory_reset()
                self._send_json(HTTPStatus.ACCEPTED, response)
                return
            if path == "/v1/server/restore":
                client = self._authenticated_client()
                response = self.server.application.restore_backup(client, payload)
                # Ordningen är hela poängen: svaret går ut medan servern
                # fortfarande kör, och filen byts först när tillsynsprocessen
                # stängt alla anslutningar. En levande databas rörs aldrig.
                self.server.request_restore(Path(str(response.pop("path"))))
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
        except MeetLifecycleError as error:
            self._send_api_error(HTTPAPIError(HTTPStatus.CONFLICT, "meet_context_conflict", str(error)))
        except USError as error:
            self._send_api_error(HTTPAPIError(HTTPStatus(error.status), "us_error", str(error)))
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
        if self._installation_is_open():
            return self.server.application.local_admin()
        token = self._admin_session_token()
        if token:
            user = self.server.application.identities.admin_session_user(token)
            if user is not None:
                return self.server.application.local_admin(user)
        return None

    def _installation_is_open(self) -> bool:
        """Innan det finns ett lösenord finns det ingen att logga in som.

        Servern gav tidigare full ägarbehörighet till alla som nådde den från
        maskinen själv, alltid. Det var bekvämt och det var fel: när servern
        fick flera användare med olika roller gällde inte rollgränsen vid
        maskinen - vem som helst som kom åt tangentbordet var ägare.

        Kvar är bara det fall där en inloggning inte kan finnas: en
        installation som ännu inte satt sitt lösenord. Den öppningen stänger
        sig själv i samma stund som den första administratören skapas.

        Var du står är fortfarande en giltig fråga - se _is_direct_local_request
        - men den avgör vad du får göra, inte vem du är.
        """

        if self.server.application.config.force_external_auth:
            return False
        access = self.server.application.identities.admin_access_summary()
        return not access["password_configured"] and self._client_address_is_private()

    def _at_the_machine(self) -> bool:
        """Står webbläsaren på serverdatorn själv?

        force_external_auth finns för att kunna köra servern som om den nåddes
        utifrån. Då ska den frågan svara nej, annars vore läget inte det man
        bad om.
        """

        if self.server.application.config.force_external_auth:
            return False
        return self._is_direct_local_request()

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
    # A browser fetches scripts, fonts and snapshots concurrently. The default
    # backlog of five can reset asset connections during a page load on macOS.
    request_queue_size = 64

    def __init__(
        self,
        address: tuple[str, int],
        application: TrainMeetHTTPApplication,
    ):
        self.application = application
        self.restart_requested = False
        self.operational_reset_requested = False
        self.factory_reset_requested = False
        self.restore_requested: Path | None = None
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

    def request_restore(self, backup_path: Path) -> None:
        """Vilken kopia som ska läggas tillbaka när servern har stannat.

        Återställningen får inte ske här: databasen är öppen, WAL-loggen lever,
        och att byta filen under en igång-varande SQLite-anslutning är precis
        det fel den här funktionen finns för att undvika.
        """

        self.restore_requested = backup_path
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
