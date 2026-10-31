"""Livetrafik från klockan via TrainMeet Cloud: det andra undantaget från D1.

Apple Watch kan inte nå träffens lokala nät, och en Pi bakom träffens router
kan inte nås från internet. Casper beslutade den 29 oktober om ett smalt
undantag från D1 (#14): en egen kanal **bara för livetrafik från klockan**.

- **Reläet (#205).** Pi:n håller själv en utgående, autentiserad förbindelse
  till Cloud (`POST /api/server-relay`). Cloud ger servern en HTTPS-adress,
  `/relay/<id>`, och lämnar det klockan skickar dit till Pi:n i nästa utbyte.
  Bara TMBox-anropen i `RELAY_ROUTES` körs, med samma box-nyckel, samma
  behörighet och samma kod som när boxen ringer direkt. Pi:n förblir
  trafikauktoritet. Ingen port öppnas på träffen.
- **Push (#206).** De händelser som redan finns i boxens `attention` - begäran
  hit, nekad, klart och tåg på väg - skickas till Cloud (`POST
  /api/server-push`) med bara fälten i `EVENT_FIELDS`. Cloud gör notiser av
  dem. Ge klart och Neka från notisen kommer tillbaka som `POST
  /v1/tmbox/answer` med förfrågans id och ett kortlivat engångsbevis, och
  servern avgör bara den förfrågan, bara medan den väntar.

Ingen konfiguration, ingen träffdata och ingen historik går upp. Allt är
avstängt tills administratören slår på det (`cloud_live_enabled`). Nycklar,
bevis och push-token loggas aldrig. Kontraktet står i
docs/apple-watch-relay-push.md, och gränsen vaktas i test_product_boundaries.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from .central_sync import canonical_runtime_url

LOGGER = logging.getLogger("tmbox_gateway.cloud_live")

PROTOCOL = 1

#: Det enda klockan når genom reläet: boxens egna anrop. Inte administrationen,
#: inte registreringen av nya klienter och inte POST /v1/clock.
RELAY_ROUTES = frozenset({
    ("GET", "/v1/tmbox/terminal"),
    ("POST", "/v1/tmbox/terminal"),
    ("GET", "/v1/tmbox/terminal/timetable"),
    ("GET", "/v1/clock"),
    ("POST", "/v1/tmbox/push"),
    ("POST", "/v1/tmbox/answer"),
})
#: Det enda en händelse får bära upp till Cloud.
EVENT_FIELDS = frozenset({"event_id", "kind", "train_number", "from_station", "to_station",
                          "request_id", "proof", "language", "expires_in", "recipients"})
RECIPIENT_FIELDS = frozenset({"device", "token", "environment"})
#: Samma ordning som boxens ljud (terminal16.ATTENTION_KINDS).
PUSH_KINDS = ("incoming_request", "request_denied", "request_approved", "incoming_train")
DEVICES = ("phone", "watch")
ENVIRONMENTS = ("production", "sandbox")

SETTING_ENABLED = "cloud_live_enabled"
SETTING_REGISTRATIONS = "cloud_live_push"

MAX_RELAY_BODY = 4096
EXCHANGE_WAIT_SECONDS = 25
#: Så länge APNs får försöka leverera en notis.
EVENT_EXPIRES_SECONDS = 300
#: En händelse som inte kommit iväg på en minut skickas inte alls: efter ett
#: internetavbrott blir det ingen storm av gamla notiser.
EVENT_MAX_AGE_SECONDS = 60
#: Engångsbeviset för Ge klart/Neka gäller så här länge, och bara en gång.
PROOF_SECONDS = 600
MAX_QUEUED_EVENTS = 100
MAX_REGISTERED_BOXES = 200
OBSERVE_SECONDS = 1.0


class LiveError(RuntimeError):
    """Cloud svarade inte, eller svarade nej. Meddelandet nämner aldrig en nyckel."""

    def __init__(self, message: str, *, status: int = 0, code: str = "", payload: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.payload = status, code, payload or {}


def cloud_base(endpoint_url: str) -> str:
    """Clouds rot, på samma värd som driftpaketet: https://cloud.trainmeet.app."""
    parsed = urlparse(canonical_runtime_url(endpoint_url))
    path = parsed.path.rstrip("/")
    for suffix in ("/config", "/konfig"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


class HTTPTransport:
    """Det enda anropet som skriver till Cloud härifrån. Nyckeln i Authorization."""

    def post(self, url: str, token: str, payload: dict[str, Any], *, timeout: float) -> tuple[int, dict[str, Any]]:
        request = Request(url, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"), method="POST",
                          headers={"Accept": "application/json", "Content-Type": "application/json",
                                   "Authorization": f"Bearer {token}", "User-Agent": "TrainMeet-Server"})
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, _json(response.read())
        except HTTPError as error:
            return error.code, _json(error.read())
        except (URLError, TimeoutError, OSError) as error:
            raise LiveError("TrainMeet Cloud kunde inte nås") from error


def _json(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8")) if data else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class _Proof:
    device: str
    request_id: str
    station: str
    side: str
    expires_at: float


class LiveChannel:
    """Reläet och pushen för en server. Ett objekt per TrainMeetHTTPApplication."""

    def __init__(self, application, *, transport: Any = None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time):
        self.app = application
        self.transport = transport if transport is not None else HTTPTransport()
        self.clock, self.wall = clock, wall
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._relay_wake = threading.Event()
        self._push_wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._seen_requests: OrderedDict[str, None] = OrderedDict()
        self._proofs: dict[str, _Proof] = {}
        self._events: deque[tuple[float, str, dict[str, Any]]] = deque(maxlen=MAX_QUEUED_EVENTS)
        self._last_observe = float("-inf")
        self.relay = {"state": "off", "relay_id": None, "since": None, "error": None, "served": 0}
        self.push = {"configured": None, "sent": 0, "failed": 0, "dropped": 0, "error": None}

    # ------------------------------------------------------------ inställning

    def enabled(self) -> bool:
        store = self.app.runtime_store
        return bool(store is not None and store._setting(SETTING_ENABLED) == "1")

    def set_enabled(self, enabled: bool) -> None:
        self.app.runtime_store._save_setting(SETTING_ENABLED, "1" if enabled else "0")
        if not enabled:
            with self._lock:
                self._events.clear()
                self._proofs.clear()
                self.relay.update(state="off", relay_id=None, since=None, error=None)
        self._relay_wake.set()
        self._push_wake.set()

    def _link(self) -> tuple[str, str] | None:
        store = self.app.runtime_store
        token = store.link_token() if store is not None else None
        if not token:
            return None
        return token, cloud_base(store.central_url() or self.app.config.central_runtime_url)

    def relay_url(self) -> str | None:
        link = self._link()
        with self._lock:
            relay_id = self.relay["relay_id"] if self.relay["state"] == "connected" else None
        return f"{link[1]}/relay/{relay_id}" if link and relay_id else None

    def _relay_state(self) -> str:
        if not self.enabled():
            return "off"
        if self._link() is None:
            return "unlinked"
        with self._lock:
            return "connecting" if self.relay["state"] in {"off", "unlinked"} else self.relay["state"]

    def _push_state(self) -> str:
        if not self.enabled():
            return "off"
        if self._link() is None:
            return "unlinked"
        configured = self.push["configured"]
        return "unknown" if configured is None else "on" if configured else "cloud_not_configured"

    def status(self) -> dict[str, Any]:
        """Administratörens bild, utan en enda nyckel eller token."""
        with self._lock:
            relay, push = dict(self.relay), dict(self.push)
            boxes = len(self._registrations())
        return {
            "enabled": self.enabled(), "linked": self._link() is not None,
            "relay": {"state": self._relay_state(), "url": self.relay_url(), "since": relay["since"],
                      "error": relay["error"], "served": relay["served"]},
            "push": {"state": self._push_state(), "registered_boxes": boxes, "sent": push["sent"],
                     "failed": push["failed"], "dropped": push["dropped"], "error": push["error"]},
        }

    def admin_status(self, client) -> dict[str, Any]:
        """GET /v1/cloud/live, för Inställningar → Träff och Cloud."""
        self.app._require_admin(client)
        return self.status()

    def configure(self, client, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/cloud/live {"enabled": bool}: slå på eller av kanalen."""
        from .http_server import HTTPAPIError
        self.app._require_admin(client)
        if self.app.runtime_store is None:
            raise HTTPAPIError(HTTPStatus.SERVICE_UNAVAILABLE, "runtime_unavailable", "Lokal lagring saknas")
        if set(payload) != {"enabled"} or not isinstance(payload["enabled"], bool):
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_setting", "Ange enabled som true eller false")
        if payload["enabled"] and self._link() is None:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "runtime_not_linked", "Koppla servern till TrainMeet Cloud först")
        self.set_enabled(payload["enabled"])
        return self.status()

    # ------------------------------------------------------------- boxsidan

    def _require_box(self, client) -> None:
        from .http_server import HTTPAPIError
        from .identity import DeviceKind
        if client.kind != DeviceKind.ESP32_PANEL:
            raise HTTPAPIError(HTTPStatus.FORBIDDEN, "box_required", "Använd din egen TMBox")
        self.app._require_box_access(client, client.client_id)

    def box_status(self, client) -> dict[str, Any]:
        """GET /v1/tmbox/watch: det telefonen lämnar över till klockan."""
        self._require_box(client)
        mine = self._registrations().get(client.client_id, {})
        return {
            "protocol": PROTOCOL,
            "relay": {"state": self._relay_state(), "url": self.relay_url()},
            "push": {"state": self._push_state(), "registered": {device: device in mine for device in DEVICES}},
            "answer_path": "/v1/tmbox/answer",
        }

    def _registrations(self) -> dict[str, dict[str, Any]]:
        store = self.app.runtime_store
        raw = store._setting(SETTING_REGISTRATIONS) if store is not None else None
        try:
            value = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            value = {}
        return value if isinstance(value, dict) else {}

    def _save_registrations(self, registrations: dict[str, dict[str, Any]]) -> None:
        self.app.runtime_store._save_setting(SETTING_REGISTRATIONS, json.dumps(registrations, sort_keys=True))

    def register(self, client, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/tmbox/push: telefonens eller klockans push-token för boxen."""
        from .http_server import HTTPAPIError
        self._require_box(client)
        if not isinstance(payload, dict) or set(payload) - {"device", "token", "environment"}:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_push_registration", "Ange device, token och environment")
        device, token = payload.get("device"), payload.get("token")
        environment = payload.get("environment", "production")
        if device not in DEVICES:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_push_registration", "device ska vara phone eller watch")
        if token is not None and (not isinstance(token, str) or not 64 <= len(token) <= 200
                                  or any(character not in "0123456789abcdefABCDEF" for character in token)):
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_push_token", "Push-token ska vara hexadecimal")
        if environment not in ENVIRONMENTS:
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_push_registration", "environment ska vara production eller sandbox")
        with self._lock:
            registrations = self._registrations()
            mine = registrations.pop(client.client_id, {})
            if token is None:
                mine.pop(device, None)
            else:
                # En token hör till en enhet: flyttad till en annan box gäller den bara där.
                for other in registrations.values():
                    for kind in list(other):
                        if other[kind].get("token", "").lower() == token.lower():
                            del other[kind]
                mine[device] = {"token": token.lower(), "environment": environment, "registered_at": _now_iso()}
            registrations = {key: value for key, value in registrations.items() if value}
            if mine:
                registrations[client.client_id] = mine
            while len(registrations) > MAX_REGISTERED_BOXES:
                oldest = min(registrations, key=lambda key: max(item["registered_at"] for item in registrations[key].values()))
                del registrations[oldest]
            self._save_registrations(registrations)
        return {"registered": token is not None, "device": device, **self.box_status(client)}

    def _forget_token_refs(self, refs: set[str]) -> None:
        with self._lock:
            registrations = self._registrations()
            changed = False
            for box in registrations.values():
                for device in list(box):
                    if hashlib.sha256(box[device]["token"].encode()).hexdigest()[:16] in refs:
                        del box[device]
                        changed = True
            if changed:
                self._save_registrations({key: value for key, value in registrations.items() if value})

    def answer(self, client, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/tmbox/answer: Ge klart eller Neka direkt från notisen.

        Gäller bara förfrågan i notisen, bara medan den väntar och bara med
        notisens engångsbevis. Servern avgör, med samma kommando som boxens #.
        Ett andra svar med samma bevis ger samma besked och ingen ny åtgärd.
        """
        from .http_server import HTTPAPIError
        from .protocol_v2 import CommandRejected
        self._require_box(client)
        if (not isinstance(payload, dict) or set(payload) != {"request_id", "proof", "answer"}
                or payload["answer"] not in {"approve", "deny"} or not isinstance(payload["request_id"], str)
                or not isinstance(payload["proof"], str) or not 22 <= len(payload["proof"]) <= 100
                or not 1 <= len(payload["request_id"]) <= 100):
            raise HTTPAPIError(HTTPStatus.BAD_REQUEST, "invalid_answer", "Ange request_id, proof och answer (approve eller deny)")
        device, request_id = client.client_id, payload["request_id"]
        approve = payload["answer"] == "approve"
        digest = hashlib.sha256(payload["proof"].encode()).hexdigest()
        receipt_id = "notification:" + digest[:40]
        operations = self.app.operations_store
        self.app.station_service.observe_operator(device)
        with self.app.engine._lock, operations.command_lock:
            receipt = operations.device_command_response(device, receipt_id)
            if receipt:
                return {"status": "duplicate", "decision": receipt.get("decision"), "request_id": request_id,
                        "message": "Redan besvarad från en notis."}
            with self._lock:
                proof = self._proofs.get(digest)
            if proof is None:
                raise HTTPAPIError(HTTPStatus.GONE, "proof_expired", "Notisen gäller inte längre. Öppna TMBox.")
            if proof.device != device or proof.request_id != request_id:
                raise HTTPAPIError(HTTPStatus.FORBIDDEN, "proof_invalid", "Notisen hör inte till den här boxen eller förfrågan")
            if proof.expires_at < self.wall():
                with self._lock:
                    self._proofs.pop(digest, None)
                raise HTTPAPIError(HTTPStatus.GONE, "proof_expired", "Notisen har gått ut. Öppna TMBox.")
            station = self.app.identities.station_for_client(device)
            if station != proof.station or self.app.identities.station_side_for_client(device) != proof.side:
                raise HTTPAPIError(HTTPStatus.CONFLICT, "assignment_changed", "Boxen har fått en annan station sedan notisen kom")
            case = operations.clearance(request_id)
            if case is None or case.get("status") != "waiting" or case.get("to_station_id") != station:
                raise HTTPAPIError(HTTPStatus.CONFLICT, "request_not_waiting", "Förfrågan är redan avgjord eller återtagen")
            try:
                with operations.atomic_command():
                    self.app.station_service.execute_station_command(
                        device, station, "clearance.response", {"clearance_id": request_id, "approved": approve})
                    operations.remember_device_command(device, receipt_id, {
                        "decision": "approved" if approve else "denied", "request_id": request_id})
            except CommandRejected as error:
                if error.reason == "clearance_not_pending":
                    raise HTTPAPIError(HTTPStatus.CONFLICT, "request_not_waiting", "Förfrågan är redan avgjord eller återtagen") from None
                raise HTTPAPIError(HTTPStatus.CONFLICT, "answer_rejected", "Servern tog inte emot svaret. Öppna TMBox.") from None
            with self._lock:
                self._proofs.pop(digest, None)
        return {"status": "accepted", "decision": "approved" if approve else "denied", "request_id": request_id,
                "message": "Klart givet." if approve else "Begäran nekad."}

    # -------------------------------------------------------------- reläet

    def handle_relay_request(self, request: Any) -> dict[str, Any] | None:
        """En förfrågan från klockan, lämnad av Cloud. Svaret, eller inget alls.

        Allt kontrolleras här igen: vägen, nyckeln och boxen. En förfrågan med
        ett id som redan setts, eller som klockan redan gett upp, körs aldrig.
        """
        if not isinstance(request, dict):
            return None
        request_id = request.get("id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
            return None
        with self._lock:
            if request_id in self._seen_requests:
                return None
            self._seen_requests[request_id] = None
            while len(self._seen_requests) > 1024:
                self._seen_requests.popitem(last=False)
        expires = request.get("expires_in_ms")
        if not isinstance(expires, int) or expires <= 0:
            return None
        status, body = self._relay_dispatch(request)
        with self._lock:
            self.relay["served"] += 1
        return {"id": request_id, "status": int(status), "body": json.dumps(body, ensure_ascii=False, separators=(",", ":"))}

    def _relay_dispatch(self, request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        from .http_server import HTTPAPIError
        from .identity import DeviceKind
        from .lifecycle import MeetLifecycleError
        from .us import USError
        method, path = request.get("method"), request.get("path")
        if (method, path) not in RELAY_ROUTES:
            return 404, {"error": "not_found", "message": "Sidan finns inte"}
        authorization = request.get("authorization")
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return 401, {"error": "authentication_required", "message": "Boxens nyckel saknas"}
        client = self.app.identities.authenticate(authorization[len("Bearer "):])
        if client is None:
            return 401, {"error": "invalid_credential", "message": "Parkopplingen gäller inte längre"}
        if client.kind != DeviceKind.ESP32_PANEL:
            return 403, {"error": "box_required", "message": "Reläet är bara för TMBox"}
        body = request.get("body")
        payload: dict[str, Any] = {}
        if method == "POST":
            try:
                payload = json.loads(body) if isinstance(body, str) and len(body.encode("utf-8")) <= MAX_RELAY_BODY else None
            except json.JSONDecodeError:
                payload = None
            if not isinstance(payload, dict):
                return 400, {"error": "invalid_json", "message": "JSON-värdet måste vara ett objekt"}
        elif body is not None:
            return 400, {"error": "invalid_body", "message": "En läsning har ingen kropp"}
        app = self.app
        routes = {
            ("GET", "/v1/tmbox/terminal"): lambda: app.terminal16_frame(client),
            ("POST", "/v1/tmbox/terminal"): lambda: app.terminal16_command(client, payload),
            ("GET", "/v1/tmbox/terminal/timetable"): lambda: app.terminal16_timetable(client),
            ("GET", "/v1/clock"): lambda: app.clock_status(client),
            ("POST", "/v1/tmbox/push"): lambda: self.register(client, payload),
            ("POST", "/v1/tmbox/answer"): lambda: self.answer(client, payload),
        }
        try:
            return 200, routes[(method, path)]()
        except HTTPAPIError as error:
            return int(error.status), {"error": error.code, "message": str(error), **error.details}
        except MeetLifecycleError as error:
            return 409, {"error": "meet_context_conflict", "message": str(error)}
        except USError as error:
            return int(error.status), {"error": "us_error", "message": str(error)}
        except Exception:  # noqa: BLE001 - klockan ska få ett svar, som från HTTP-servern
            LOGGER.exception("Reläanropet kunde inte hanteras: %s %s", method, path)
            return 500, {"error": "internal_error", "message": "Något gick fel på servern. Försök igen."}

    def exchange_once(self, responses: list[dict[str, Any]], *, wait: int = EXCHANGE_WAIT_SECONDS) -> list[dict[str, Any]]:
        """Ett utbyte med Cloud. Ger svaren som ska med i nästa."""
        link = self._link()
        if not self.enabled() or link is None:
            with self._lock:
                self.relay.update(state="off" if not self.enabled() else "unlinked", relay_id=None, since=None)
            return []
        token, base = link
        try:
            status, payload = self.transport.post(f"{base}/api/server-relay", token,
                                                  {"protocol": PROTOCOL, "wait": wait, "responses": responses},
                                                  timeout=wait + 10)
        except LiveError as error:
            with self._lock:
                self.relay.update(state="error", error=str(error))
            raise
        if isinstance(payload.get("push"), dict) and isinstance(payload["push"].get("configured"), bool):
            with self._lock:
                self.push["configured"] = payload["push"]["configured"]
        if status != 200:
            code = str(payload.get("code") or "")
            # En äldre Cloud utan reläet svarar 401 eller 404 utan kod.
            state = ("link_rejected" if status == 401 and code == "link_invalid"
                     else "cloud_disabled" if code == "relay_disabled" or status in {401, 404, 405} else "error")
            message = str(payload.get("error") or f"Cloud svarade {status}")
            with self._lock:
                self.relay.update(state=state, relay_id=None, since=None, error=message)
            raise LiveError(message, status=status, code="relay_disabled" if state == "cloud_disabled" else code or state)
        relay_id = payload.get("relay_id")
        with self._lock:
            if self.relay["state"] != "connected" or self.relay["relay_id"] != relay_id:
                self.relay.update(state="connected", relay_id=relay_id if isinstance(relay_id, str) else None,
                                  since=_now_iso(), error=None)
        requests = payload.get("requests") if isinstance(payload.get("requests"), list) else []
        if self._stop.is_set():
            return []  # Servern stängs: ingenting körs efter att lagren stängts.
        answers = []
        for request in requests[:16]:
            answer = self.handle_relay_request(request)
            if answer is not None:
                answers.append(answer)
        return answers

    def _relay_loop(self) -> None:
        responses: list[dict[str, Any]] = []
        failures = 0
        while not self._stop.is_set():
            if not self.enabled() or self._link() is None:
                self.exchange_once([])
                self._relay_wake.wait(30)
                self._relay_wake.clear()
                failures = 0
                continue
            try:
                responses = self.exchange_once(responses)
                failures = 0
            except LiveError as error:
                responses = []  # Klockan har redan fått vänta; den läser om.
                failures += 1
                pause = 300 if error.code in {"relay_disabled", "link_rejected"} else min(60, 2 ** min(failures, 6))
                LOGGER.warning("Reläet till TrainMeet Cloud: %s. Nytt försök om %s s.", error, pause)
                self._relay_wake.wait(pause)
                self._relay_wake.clear()
            except Exception:  # noqa: BLE001 - tråden får inte dö
                responses = []
                LOGGER.exception("Reläet till TrainMeet Cloud stannade; försöker igen")
                self._stop.wait(5)

    # ---------------------------------------------------------------- push

    def on_attention(self, device: str, kind: str, info: dict[str, Any]) -> None:
        """Boxen fick något att låta för. Körs under trafiklåset: bara minnet här."""
        if kind not in PUSH_KINDS or not self.enabled():
            return
        registration = self._registrations().get(device)
        if not registration:
            return
        event = {
            "event_id": "e-" + secrets.token_hex(12), "kind": kind,
            "train_number": str(info.get("train_number", "")), "from_station": str(info.get("from_station", "")),
            "to_station": str(info.get("to_station", "")), "language": str(info.get("language") or "sv"),
            "expires_in": EVENT_EXPIRES_SECONDS,
        }
        if info.get("request_id"):
            event["request_id"] = str(info["request_id"])
        with self._lock:
            if kind == "incoming_request" and info.get("request_id"):
                proof = secrets.token_urlsafe(24)
                self._proofs[hashlib.sha256(proof.encode()).hexdigest()] = _Proof(
                    device, str(info["request_id"]), str(info.get("station")), str(info.get("side") or "both"),
                    self.wall() + PROOF_SECONDS)
                event["proof"] = proof
            if len(self._events) == self._events.maxlen:
                self.push["dropped"] += 1
            self._events.append((self.clock(), device, event))
        self._push_wake.set()

    def flush_push(self) -> int:
        """Skicka det som väntar, en gång. Ger antalet skickade händelser."""
        link = self._link()
        with self._lock:
            items = list(self._events)
            self._events.clear()
        if not items or not self.enabled() or link is None:
            return 0
        now = self.clock()
        registrations = self._registrations()
        identities = self.app.identities
        events = []  # (skapad, händelse med mottagare)
        for created, device, event in items:
            if now - created > EVENT_MAX_AGE_SECONDS:
                with self._lock:
                    self.push["dropped"] += 1
                continue
            current = identities.client(device)
            if current is None or current.kind.value != "esp32_panel":
                continue  # Boxen är borttagen: ingen notis till dess telefon.
            recipients = [{"device": kind, "token": item["token"], "environment": item["environment"]}
                          for kind, item in sorted(registrations.get(device, {}).items())]
            if recipients:
                events.append((created, device, {**event, "recipients": recipients}))
        sent = 0
        token, base = link
        for start in range(0, len(events), 10):
            batch = [event for _, _, event in events[start:start + 10]]
            try:
                status, payload = self.transport.post(f"{base}/api/server-push", token,
                                                      {"protocol": PROTOCOL, "events": batch}, timeout=30)
            except LiveError as error:
                # Nytt försök vid nästa tömning, men bara inom händelsens minut.
                with self._lock:
                    self._events.extendleft(reversed([(created, device, {key: value for key, value in event.items()
                                                                         if key != "recipients"})
                                                      for created, device, event in events[start:start + 10]]))
                    self.push.update(error=str(error))
                LOGGER.warning("Notiserna kunde inte lämnas till TrainMeet Cloud: %s", error)
                continue
            if isinstance(payload.get("push"), dict) and isinstance(payload["push"].get("configured"), bool):
                with self._lock:
                    self.push["configured"] = payload["push"]["configured"]
            if status != 200:
                with self._lock:
                    self.push.update(failed=self.push["failed"] + len(batch),
                                     error=str(payload.get("error") or f"Cloud svarade {status}"))
                continue
            invalid = set()
            for result in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
                if isinstance(result, dict):
                    invalid.update(str(ref) for ref in result.get("invalid", []) if isinstance(ref, str))
            if invalid:
                self._forget_token_refs(invalid)
            sent += len(batch)
            with self._lock:
                self.push.update(sent=self.push["sent"] + len(batch), error=None)
        return sent

    def _push_loop(self) -> None:
        while not self._stop.is_set():
            self._push_wake.wait(5)
            self._push_wake.clear()
            try:
                self.flush_push()
            except Exception:  # noqa: BLE001 - tråden får inte dö
                LOGGER.exception("Notiserna kunde inte skickas; försöker igen")

    def tick(self) -> None:
        """Från klocktråden: håll de push-registrerade boxarna med i bilden.

        En box vars app är stängd frågar inte servern, så ingen märker att
        något hänt den. Här tittar servern åt den, högst en gång i sekunden,
        och bara när kanalen är på och någon box har registrerat push.
        """
        now = self.clock()
        if now - self._last_observe < OBSERVE_SECONDS or not self.enabled():
            return
        self._last_observe = now
        wall = self.wall()
        with self._lock:
            for digest in [key for key, proof in self._proofs.items() if proof.expires_at < wall]:
                del self._proofs[digest]
        devices = sorted(self._registrations())
        if devices and self.app.operations_store is not None:
            self.app.terminal16.observe(devices)

    # ---------------------------------------------------------------- trådar

    def start(self) -> None:
        if self._threads:
            return
        for name, target in (("trainmeet-cloud-relay", self._relay_loop), ("trainmeet-cloud-push", self._push_loop)):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 2) -> None:
        self._stop.set()
        self._relay_wake.set()
        self._push_wake.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads = []
