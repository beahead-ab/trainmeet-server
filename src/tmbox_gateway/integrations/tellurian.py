"""Tellurian Dispatch som klareringskälla (#222): adress, läsning, spegling och kommandon.

Principen är "Tellurian bestämmer, TrainMeet kompletterar". Adaptern
- pratar med Stefans server över HTTP enligt hans SPECIFICATION.md (HATEOAS
  under /api/v1 och SSE på /api/v1/events) och länkar aldrig in GPL-biblioteket;
- räknar aldrig själv fram vilka handlingar som är tillåtna: de kommer bara ur
  `actions` som Tellurian lämnar för stationens dispatcher;
- bygger aldrig egna adresser för åtgärder: en POST går bara till den `href`
  som senast lästes, och bara till samma ursprung som den konfigurerade adressen;
- reserverar varje kommando i kommandologgen från #221 före sändning, räknar
  det som `confirmed` först efter återläsning och skickar aldrig om ett okänt
  utfall automatiskt.

Bara lokala adresser (privat IPv4, link-local, `.local`) godtas, utom när
administratören uttryckligen valt "testinstans". Inga omdirigeringar följs,
varje anrop har timeout och storleksgräns, och Tellurians svar eller
felmeddelanden hamnar aldrig i loggar eller användartexter: bara felkoder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import http.client
import ipaddress
import json
import logging
import re
import socket
import ssl
from threading import Event, Lock, RLock, Thread
import time
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

from .framework import Command, IntegrationError, Outcome, Scope, ScopeMismatch, Unsupported

LOGGER = logging.getLogger("tmbox_gateway.integrations.tellurian")

PROVIDER_ID = "tellurian"
API_PREFIX = "/api/v1/"
SIMULATOR_HEADER = "x-tellurian-simulator"
DISPATCH_STATES = ("None", "Requested", "Accepted", "Rejected", "Revoked", "Departed", "Arrived", "Canceled")
TRAIN_STATES = ("Planned", "Manned", "Running", "Canceled", "Aborted", "Completed")
#: Sektioner som håller en sträcka: dessa jämförs med TrainMeets egna ärenden.
ACTIVE_STATES = {"Requested": "requested", "Accepted": "accepted", "Departed": "departed"}

MAX_RESPONSE_BYTES = 1_048_576
MAX_EVENT_BYTES = 16_384
MAX_DISPATCHERS = 200
MAX_SECTIONS = 500
MAX_ACTIONS = 30
_PRIVATE_V4 = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_LINK_LOCAL_V4 = ipaddress.ip_network("169.254.0.0/16")
_SAFE_PATH = re.compile(r"^/api/v1/[A-Za-z0-9_\-/]+$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,39}$")
_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


# ── Fel och adresser ─────────────────────────────────────────────────────
class TellurianError(IntegrationError):
    """Ett anrop misslyckades. `code` är det enda som visas eller loggas.

    `sent` är False när begäran bevisligen aldrig lämnade servern (anslutningen
    gick inte att öppna), True när den kan ha kommit fram och None för läsningar.
    """

    def __init__(self, code: str, *, sent: bool | None = None):
        super().__init__(code)
        self.code = code
        self.sent = sent


class AddressRejected(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Endpoint:
    scheme: str
    host: str
    port: int
    kind: str  # private | link_local | mdns | loopback | public
    test_instance: bool

    @property
    def origin(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}"

    @property
    def local(self) -> bool:
        return self.kind in ("private", "link_local", "mdns")


def _is_local_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (address.version == 4
            and (address in _LINK_LOCAL_V4 or any(address in network for network in _PRIVATE_V4)))


def classify_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if host == "localhost" or host.endswith(".localhost"):
            return "loopback"
        return "mdns" if host.endswith(".local") and len(host) > len(".local") else "public"
    if address.version == 4:
        if address.is_loopback:
            return "loopback"
        if address in _LINK_LOCAL_V4:
            return "link_local"
        if any(address in network for network in _PRIVATE_V4):
            return "private"
    return "public"


def check_address(raw: Any, *, test_instance: bool = False) -> Endpoint:
    """Administratörens adress till Tellurian. Bara ursprung, aldrig sökväg.

    Lokal adress: privat IPv4, link-local (169.254/16) eller ett `.local`-namn.
    Allt annat, också loopback och IPv6, kräver valet "testinstans". TrainMeet
    Cloud godtas aldrig: klareringstrafik ska inte gå dit.
    """
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        raise AddressRejected("address_required")
    if len(text) > 255 or any(ord(c) < 33 for c in text):
        raise AddressRejected("address_invalid")
    if "://" not in text:
        text = "http://" + text
    try:
        url = urlsplit(text)
        port = url.port
    except ValueError:
        raise AddressRejected("address_invalid") from None
    if url.scheme not in ("http", "https"):
        raise AddressRejected("address_scheme")
    if url.username is not None or url.password is not None:
        raise AddressRejected("address_credentials")
    if url.query or url.fragment or url.path not in ("", "/"):
        raise AddressRejected("address_path")
    host = (url.hostname or "").rstrip(".").lower()
    if not host:
        raise AddressRejected("address_required")
    if host == "trainmeet.app" or host.endswith(".trainmeet.app"):
        raise AddressRejected("address_trainmeet_cloud")
    kind = classify_host(host)
    if kind not in ("private", "link_local", "mdns") and not test_instance:
        raise AddressRejected("address_not_local")
    return Endpoint(url.scheme, host, port or (443 if url.scheme == "https" else 80), kind, bool(test_instance))


def safe_href(value: Any, endpoint: Endpoint) -> str | None:
    """En serverlämnad `href` som sökväg på samma ursprung, annars None."""
    if not isinstance(value, str) or not value or len(value) > 300:
        return None
    path = value
    if "://" in value or value.startswith("//"):
        try:
            url = urlsplit(value)
            port = url.port or (443 if url.scheme == "https" else 80)
        except ValueError:
            return None
        if (url.scheme, (url.hostname or "").lower(), port) != (endpoint.scheme, endpoint.host, endpoint.port):
            return None
        if url.query or url.fragment or url.username or url.password:
            return None
        path = url.path
    if not _SAFE_PATH.match(path) or "//" in path:
        return None
    return path


# ── Transport ────────────────────────────────────────────────────────────
class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class Transport:
    """http.client utan proxy, cookies eller omdirigeringar; en anslutning per anrop."""

    def __init__(self, endpoint: Endpoint, *, timeout: float = 3.0, max_bytes: int = MAX_RESPONSE_BYTES,
                 resolver: Callable[..., list] = socket.getaddrinfo):
        if not (0 < timeout <= 30):
            raise ValueError("timeout")
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._resolver = resolver
        self.simulated = False
        self._streams: set = set()
        self._streams_lock = Lock()

    def _address(self) -> str:
        """Slå upp namnet och kontrollera att adressen fortfarande är lokal.

        Ett `.local`-namn som pekar på en publik adress, eller ett namn som
        byter adress mellan kontroll och anrop, ska inte kunna leda ut.
        """
        try:
            infos = self._resolver(self.endpoint.host, self.endpoint.port, socket.AF_INET, socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            raise TellurianError("unreachable", sent=False) from None
        addresses = [info[4][0] for info in infos]
        if self.endpoint.local:
            addresses = [a for a in addresses if _is_local_ip(a)]
            if not addresses:
                raise TellurianError("address_not_local", sent=False)
        if not addresses:
            raise TellurianError("unreachable", sent=False)
        return addresses[0]

    def _connection(self, timeout: float):
        address = self._address()
        kind = _PinnedHTTPS if self.endpoint.scheme == "https" else _PinnedHTTP
        return kind(self.endpoint.host, self.endpoint.port, address, timeout)

    def request(self, method: str, path: str) -> Any:
        if method not in ("GET", "POST") or not _SAFE_PATH.match(path):
            raise ValueError("path")
        connection = self._connection(self.timeout)
        sent: bool | None = None if method == "GET" else False
        try:
            try:
                connection.connect()
            except (OSError, ssl.SSLError):
                raise TellurianError("unreachable", sent=sent) from None
            if method == "POST":
                sent = True
            headers = {"Accept": "application/json", "Connection": "close", "User-Agent": "TrainMeet-Server"}
            if method == "POST":
                headers["Content-Length"] = "0"
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            if response.getheader(SIMULATOR_HEADER):
                self.simulated = True
            status = response.status
            if 300 <= status < 400:
                raise TellurianError("redirect", sent=sent)
            body = response.read(self.max_bytes + 1)
            if len(body) > self.max_bytes:
                raise TellurianError("too_large", sent=sent)
            if status == 404:
                raise TellurianError("not_found", sent=sent)
            if status in (400, 409, 422):
                raise TellurianError("refused", sent=sent)
            if status >= 500:
                raise TellurianError("server_error", sent=sent)
            if not 200 <= status < 300:
                raise TellurianError("bad_status", sent=sent)
            if not body.strip():
                return None
            try:
                return json.loads(body)
            except (ValueError, UnicodeError):
                raise TellurianError("bad_json", sent=sent) from None
        except TellurianError:
            raise
        except TimeoutError:
            raise TellurianError("timeout", sent=sent) from None
        except (http.client.HTTPException, OSError, ssl.SSLError):
            raise TellurianError("connection_lost", sent=sent) from None
        finally:
            connection.close()

    def stream(self, path: str, stop: Event, *, read_timeout: float = 30.0) -> Iterator[tuple[str, str]]:
        """Server-Sent Events. Ger (händelse, data); tom händelse = livstecken."""
        if not _SAFE_PATH.match(path.split("?")[0]):
            raise ValueError("path")
        connection = self._connection(min(self.timeout, read_timeout))
        with self._streams_lock:
            self._streams.add(connection)
        try:
            try:
                connection.connect()
                connection.request("GET", path, headers={"Accept": "text/event-stream", "Cache-Control": "no-cache",
                                                         "User-Agent": "TrainMeet-Server"})
                response = connection.getresponse()
            except TimeoutError:
                raise TellurianError("timeout") from None
            except (http.client.HTTPException, OSError, ssl.SSLError):
                raise TellurianError("unreachable") from None
            if response.getheader(SIMULATOR_HEADER):
                self.simulated = True
            if response.status != 200 or not (response.getheader("Content-Type") or "").startswith("text/event-stream"):
                raise TellurianError("stream_refused")
            if connection.sock is not None:
                connection.sock.settimeout(read_timeout)
            event, data, size = "", [], 0
            while not stop.is_set():
                try:
                    line = response.readline(MAX_EVENT_BYTES + 1)
                except TimeoutError:
                    raise TellurianError("stream_quiet") from None
                except (http.client.HTTPException, OSError, ValueError):
                    raise TellurianError("stream_lost") from None
                if not line:
                    raise TellurianError("stream_lost")
                if len(line) > MAX_EVENT_BYTES:
                    raise TellurianError("too_large")
                text = line.rstrip(b"\r\n").decode("utf-8", "replace")
                if not text:
                    if data:
                        yield event or "message", "\n".join(data)
                    else:
                        yield "", ""
                    event, data, size = "", [], 0
                    continue
                if text.startswith(":"):
                    yield "", ""
                    continue
                name, _, value = text.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if name == "event":
                    event = value[:60]
                elif name == "data":
                    size += len(value)
                    if size > MAX_EVENT_BYTES:
                        raise TellurianError("too_large")
                    data.append(value)
        finally:
            with self._streams_lock:
                self._streams.discard(connection)
            connection.close()

    def abort_streams(self) -> None:
        """Avsluta pågående händelseströmmar direkt, utan att vänta på nästa rad."""
        with self._streams_lock:
            streams = list(self._streams)
        for connection in streams:
            try:
                if connection.sock is not None:
                    connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


# ── Svar från Tellurian ──────────────────────────────────────────────────
@dataclass(frozen=True)
class Action:
    name: str
    href: str
    display_name: str = ""


@dataclass(frozen=True)
class Section:
    id: str
    train: str
    train_id: str | None
    train_state: str
    state: str
    current: int
    total: int
    from_sig: str | None
    to_sig: str | None
    departure_time: str = ""
    arrival_time: str = ""
    actions: tuple[Action, ...] = ()


@dataclass(frozen=True)
class Dispatcher:
    id: str
    signature: str
    name: str
    href: str


@dataclass(frozen=True)
class DispatcherView:
    dispatcher: Dispatcher
    arrivals: tuple[Section, ...]
    departures: tuple[Section, ...]
    unknown_actions: tuple[str, ...] = ()

    def sections(self) -> tuple[Section, ...]:
        return self.arrivals + self.departures

    def section(self, section_id: str) -> Section | None:
        return next((s for s in self.sections() if s.id == section_id), None)


def _text(value: Any, limit: int = 120) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text[:limit] if text and not any(ord(c) < 32 for c in text) else None


def _ident(value: Any) -> str | None:
    text = _text(value, 64)
    return text if text and _ID.match(text) else None


def _int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 1000 else default


def _signature(item: dict, *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("signature") or value.get("station")
        text = _text(value, 20)
        if text:
            return text
    return None


def parse_dispatchers(data: Any, endpoint: Endpoint) -> tuple[Dispatcher, ...]:
    rows = data.get("dispatchers", data.get("items")) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise TellurianError("bad_shape")
    result, seen = [], set()
    for row in rows[:MAX_DISPATCHERS]:
        if not isinstance(row, dict):
            continue
        ident, signature = _ident(row.get("id")), _text(row.get("signature"), 20)
        if not ident or not signature or ident in seen:
            continue
        seen.add(ident)
        link = row.get("href")
        if link is None and isinstance(row.get("links"), list):
            link = next((l.get("href") for l in row["links"] if isinstance(l, dict)
                         and l.get("rel") in ("self", "dispatcher")), None)
        # GET /api/v1/dispatchers/{id} är den dokumenterade läsningen; den
        # serverlämnade länken går före när den finns.
        href = safe_href(link, endpoint) or f"/api/v1/dispatchers/{ident}"
        name = _text(row.get("name") or row.get("stationName"), 80) or signature
        result.append(Dispatcher(ident, signature, name, href))
    return tuple(result)


def _parse_section(row: Any, endpoint: Endpoint, side: str, own: str, unknown: set[str]) -> Section | None:
    if not isinstance(row, dict):
        return None
    ident, train = _ident(row.get("id")), _text(row.get("train"), 80)
    if not ident or not train:
        return None
    state = row.get("state") if row.get("state") in DISPATCH_STATES else "Unknown"
    train_state = row.get("trainState") if row.get("trainState") in TRAIN_STATES else "Unknown"
    actions = []
    for item in (row.get("actions") if isinstance(row.get("actions"), list) else [])[:MAX_ACTIONS]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not _NAME.match(item["name"]):
            continue
        href = safe_href(item.get("href"), endpoint)
        if href is None:
            unknown.add(item["name"] + " (href)")
            continue
        actions.append(Action(item["name"], href, _text(item.get("displayName"), 80) or item["name"]))
    from_sig = _signature(row, "from", "fromSignature", "departure")
    to_sig = _signature(row, "to", "toSignature", "arrival")
    # En avgång i en dispatchers lista går från dess station, en ankomst dit.
    if side == "departures":
        from_sig = from_sig or own
    else:
        to_sig = to_sig or own
    return Section(ident, train, _ident(row.get("trainId")), train_state, state,
                   _int(row.get("currentTrackStretch"), 0), max(1, _int(row.get("totalTrackStretches"), 1)),
                   from_sig, to_sig, _text(row.get("departureTime"), 20) or "",
                   _text(row.get("arrivalTime"), 20) or "", tuple(actions))


def parse_dispatcher_view(data: Any, dispatcher: Dispatcher, endpoint: Endpoint) -> DispatcherView:
    if not isinstance(data, dict):
        raise TellurianError("bad_shape")
    returned = _ident(data.get("id"))
    if returned is not None and returned != dispatcher.id:
        raise TellurianError("bad_shape")
    unknown: set[str] = set()
    lists = {}
    for side in ("arrivals", "departures"):
        rows = data.get(side)
        rows = rows if isinstance(rows, list) else []
        lists[side] = tuple(s for s in (_parse_section(r, endpoint, side, dispatcher.signature, unknown)
                                        for r in rows[:MAX_SECTIONS]) if s)
    return DispatcherView(dispatcher, lists["arrivals"], lists["departures"], tuple(sorted(unknown)))


def parse_train_label(label: str) -> tuple[str, str, str | None]:
    """"SJ IC 456" → ("SJ", "IC", "456"). Utan heltal sist: nummer None."""
    parts = label.split()
    if not parts or not parts[-1].isdigit():
        return "", "", None
    number = str(int(parts[-1]))
    if len(parts) >= 3:
        return parts[0], " ".join(parts[1:-1]), number
    if len(parts) == 2:
        return "", parts[0], number
    return "", "", number


def _number(value: Any) -> str | None:
    text = str(value or "").strip()
    return str(int(text)) if text.isdigit() else (text or None)


# ── Klient ───────────────────────────────────────────────────────────────
class TellurianClient:
    def __init__(self, endpoint: Endpoint, *, timeout: float = 3.0, transport: Transport | None = None):
        self.endpoint = endpoint
        self.transport = transport or Transport(endpoint, timeout=timeout)

    @property
    def simulated(self) -> bool:
        return self.transport.simulated

    def dispatchers(self) -> tuple[Dispatcher, ...]:
        return parse_dispatchers(self.transport.request("GET", "/api/v1/dispatchers"), self.endpoint)

    def view(self, dispatcher: Dispatcher) -> DispatcherView:
        return parse_dispatcher_view(self.transport.request("GET", dispatcher.href), dispatcher, self.endpoint)

    def post(self, href: str) -> Any:
        path = safe_href(href, self.endpoint)
        if path is None:
            raise TellurianError("href_rejected", sent=False)
        return self.transport.request("POST", path)

    def events(self, stop: Event, *, dispatcher_id: str | None = None,
               read_timeout: float = 30.0) -> Iterator[tuple[str, str]]:
        path = "/api/v1/events" + (f"?dispatcherId={dispatcher_id}" if dispatcher_id else "")
        return self.transport.stream(path, stop, read_timeout=read_timeout)


# ── Spegling ─────────────────────────────────────────────────────────────
class Mirror:
    """Skrivskyddad kopia av det som Tellurian senast visade, per dispatcher."""

    def __init__(self):
        self._lock = RLock()
        self.dispatchers: dict[str, Dispatcher] = {}
        self.views: dict[str, DispatcherView] = {}
        self.read_at: float | None = None

    def set_dispatchers(self, dispatchers: tuple[Dispatcher, ...]) -> None:
        with self._lock:
            self.dispatchers = {d.id: d for d in dispatchers}
            self.views = {k: v for k, v in self.views.items() if k in self.dispatchers}

    def put(self, view: DispatcherView, now: float) -> None:
        with self._lock:
            self.views[view.dispatcher.id] = view
            self.read_at = now

    def refresh(self, client: TellurianClient, dispatcher_ids: set[str] | None, now: float) -> None:
        with self._lock:
            targets = [d for i, d in self.dispatchers.items() if dispatcher_ids is None or i in dispatcher_ids]
        for dispatcher in targets:
            self.put(client.view(dispatcher), now)

    def view(self, dispatcher_id: str) -> DispatcherView | None:
        with self._lock:
            return self.views.get(dispatcher_id)

    def sections(self) -> dict[str, Section]:
        """En sektion kan synas hos två dispatchers; slå ihop det de vet."""
        with self._lock:
            merged: dict[str, Section] = {}
            for view in self.views.values():
                for section in view.sections():
                    known = merged.get(section.id)
                    if known is None:
                        merged[section.id] = section
                    else:
                        merged[section.id] = Section(
                            section.id, section.train, section.train_id or known.train_id,
                            section.train_state, section.state, section.current, section.total,
                            known.from_sig or section.from_sig, known.to_sig or section.to_sig,
                            known.departure_time or section.departure_time,
                            known.arrival_time or section.arrival_time, ())
            return merged

    def unknown_actions(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted({name for view in self.views.values() for name in view.unknown_actions}))


# ── Mappning till TrainMeet ──────────────────────────────────────────────
def match_stations(stations: list[dict], dispatchers: tuple[Dispatcher, ...] | list[Dispatcher]) -> dict[str, Any]:
    """Exakt matchning av stationskod mot Tellurians signatur. Dubbletter matchas aldrig."""
    by_code: dict[str, list[dict]] = {}
    for station in stations:
        by_code.setdefault(str(station.get("code") or ""), []).append(station)
    by_sig: dict[str, list[Dispatcher]] = {}
    for dispatcher in dispatchers:
        by_sig.setdefault(dispatcher.signature, []).append(dispatcher)
    matched, unmatched, duplicates = [], [], []
    for station in stations:
        code = str(station.get("code") or "")
        candidates = by_sig.get(code, [])
        if len(by_code[code]) > 1 or len(candidates) > 1:
            duplicates.append(code)
            unmatched.append({"id": station["id"], "code": code, "name": station.get("name", ""), "reason": "duplicate"})
        elif candidates:
            matched.append({"id": station["id"], "code": code, "name": station.get("name", ""),
                            "dispatcher_id": candidates[0].id, "dispatcher_name": candidates[0].name})
        else:
            hint = next((d.signature for d in dispatchers if d.signature.casefold() == code.casefold()), None)
            unmatched.append({"id": station["id"], "code": code, "name": station.get("name", ""),
                              "reason": "case" if hint else "missing", **({"hint": hint} if hint else {})})
    used = {m["dispatcher_id"] for m in matched}
    extra = [{"id": d.id, "signature": d.signature, "name": d.name} for d in dispatchers if d.id not in used]
    return {"matched": matched, "unmatched_trainmeet": unmatched, "unmatched_tellurian": extra}


def station_map(match: dict[str, Any]) -> dict[str, str]:
    """TrainMeets station-id → Tellurians dispatcher-id, bara entydiga par."""
    return {m["id"]: m["dispatcher_id"] for m in match["matched"]}


def match_stretches(connections: list[dict], stations: list[dict], match: dict[str, Any],
                    sections: dict[str, Section]) -> dict[str, Any]:
    code_of = {s["id"]: str(s.get("code") or "") for s in stations}
    mapped = {m["code"] for m in match["matched"]}
    seen: dict[frozenset, int] = {}
    for section in sections.values():
        if section.from_sig and section.to_sig:
            pair = frozenset((section.from_sig, section.to_sig))
            seen[pair] = max(seen.get(pair, 0), section.total - 1)
    matched, unseen, unmapped = [], [], []
    for connection in connections:
        a, b = code_of.get(connection.get("station_a_id"), ""), code_of.get(connection.get("station_b_id"), "")
        row = {"id": connection.get("id"), "from": a, "to": b}
        if a not in mapped or b not in mapped:
            unmapped.append(row)
        elif frozenset((a, b)) in seen:
            matched.append({**row, "passes": seen[frozenset((a, b))]})
        else:
            unseen.append(row)
    known = {frozenset((code_of.get(c.get("station_a_id"), ""), code_of.get(c.get("station_b_id"), "")))
             for c in connections}
    extra = sorted([sorted(pair) for pair in seen if pair not in known])
    return {"matched": matched, "unseen": unseen, "unmapped": unmapped,
            "unmatched_tellurian": [{"from": p[0], "to": p[-1]} for p in extra]}


def _service_source_id(service: dict) -> str | None:
    """Källans tåg-id, om Cloud har fört det vidare (fältnamnet är ett antagande, se docs)."""
    return _ident(service.get("source_train_id"))


def match_trains(services: list[dict], stations: list[dict], sections: dict[str, Section]) -> dict[str, Any]:
    """Tåg: källans id om båda har det, annars nummer (+ bolag + första avgång).

    TrainMeets driftpaket har i dag inget bolag, så nummer räcker bara när det
    är entydigt hos båda. Annars avgör första avgångsstationen, och om inte
    heller den skiljer dem åt är tåget omatchat: aldrig gissat.
    """
    code_of = {s["id"]: str(s.get("code") or "") for s in stations}
    trains: dict[str, dict] = {}
    for section in sections.values():
        key = section.train_id or section.train
        info = trains.setdefault(key, {"label": section.train, "train_id": section.train_id,
                                       "number": parse_train_label(section.train)[2], "sections": []})
        info["sections"].append(section)
    for info in trains.values():
        froms = {s.from_sig for s in info["sections"]}
        tos = {s.to_sig for s in info["sections"]}
        starts = sorted(s for s in froms - tos if s)
        info["first"] = starts[0] if len(starts) == 1 else None
    matched, unmatched, ambiguous, used = [], [], [], set()
    for service in services:
        number = _number(service.get("train_number"))
        stops = sorted(service.get("stops") or [], key=lambda stop: stop.get("stop_order", 0))
        first = code_of.get(stops[0].get("station_id")) if stops else None
        row = {"service_id": service.get("id"), "number": number or ""}
        source = _service_source_id(service)
        candidates = [k for k, t in trains.items() if source and t["train_id"] == source] if source else []
        if not candidates:
            candidates = [k for k, t in trains.items() if number and t["number"] == number]
        if len(candidates) > 1 and first:
            narrowed = [k for k in candidates if trains[k]["first"] == first]
            candidates = narrowed or candidates
        same_number = [s for s in services if _number(s.get("train_number")) == number and s is not service]
        if len(candidates) == 1 and not same_number:
            used.add(candidates[0])
            matched.append({**row, "tellurian": trains[candidates[0]]["label"]})
        elif candidates:
            ambiguous.append({**row, "candidates": sorted(trains[k]["label"] for k in candidates)})
        else:
            unmatched.append(row)
    extra = sorted(t["label"] for k, t in trains.items() if k not in used)
    return {"matched": matched, "unmatched_trainmeet": unmatched, "ambiguous": ambiguous,
            "unmatched_tellurian": extra}


# ── Handlingar: TMBox ↔ Tellurian ────────────────────────────────────────
@dataclass(frozen=True)
class HandlingSpec:
    action: str
    label: str
    key: str | None = None
    admin: bool = False
    when: str | None = None  # sektionens tillstånd när samma åtgärd har två betydelser
    tmbox: str = ""


#: Tabellen i #222. TMBox visar TrainMeets ord och tangenter; vilka som visas
#: bestäms enbart av Tellurians `actions` för stationens dispatcher.
HANDLINGS: dict[str, HandlingSpec] = {
    "request": HandlingSpec("request", "Begär klart", "#", tmbox="clearance.request"),
    "give_clear": HandlingSpec("accept", "Ge klart", "#", tmbox="clearance.response (approved)"),
    "refuse": HandlingSpec("reject", "Neka", "*", tmbox="clearance.response (rejected)"),
    "cancel_request": HandlingSpec("revoke", "Avbryt egen begäran", "*", when="Requested", tmbox="clearance.cancel"),
    "withdraw_clear": HandlingSpec("revoke", "Återta klart", "*", when="Accepted"),
    "departed": HandlingSpec("depart", "Avgått", "#", tmbox="train.departed"),
    "arrived": HandlingSpec("arrive", "Ankommit", "#", tmbox="train.arrived"),
    "manned": HandlingSpec("manned", "Bemanna", tmbox="train.crew_ready.set"),
    "pass": HandlingSpec("pass", "Passage"),
    "cancel_train": HandlingSpec("canceled", "Inställt", admin=True),
    "abort_train": HandlingSpec("aborted", "Avbrutet", admin=True),
    "undo_train_state": HandlingSpec("undoTrainState", "Ångra tågläge", admin=True),
    # `clear` står inte i tabellen i #222. Den ändrar trafikläget, så den
    # behandlas tills vidare som administratörshandling (öppen fråga).
    "clear_stretch": HandlingSpec("clear", "Rensa sträckan", admin=True),
}
#: Handlingar utan Tellurian-åtgärd. Kö skrivs aldrig till Tellurian;
#: annat spår döljs tills Tellurian exponerar en åtgärd för det.
LOCAL_ONLY = {"queue": "A"}
HIDDEN = {"other_track": "B"}


def handling_for(action: Action, section: Section) -> str | None:
    for name, spec in HANDLINGS.items():
        if spec.action == action.name and (spec.when is None or spec.when == section.state):
            return name
    return None


@dataclass(frozen=True)
class Offered:
    section_id: str
    train: str
    handling: str
    label: str
    key: str | None
    admin: bool
    href: str


def offered(view: DispatcherView | None, *, admin: bool = False) -> list[Offered]:
    """Handlingarna för en dispatcher, bara ur Tellurians egna `actions`."""
    result = []
    for section in view.sections() if view else ():
        for action in section.actions:
            name = handling_for(action, section)
            if name is None:
                continue
            spec = HANDLINGS[name]
            if spec.admin and not admin:
                continue
            result.append(Offered(section.id, section.train, name, spec.label, spec.key, spec.admin, action.href))
    return result


# ── Kommandon (för auktoritetsläget, etapp 4; används inte i drift ännu) ──
@dataclass(frozen=True)
class CommandOutcome:
    command_id: str
    outcome: Outcome
    detail: str  # confirmed | pending | unknown | refused | not_sent | duplicate


class ActionNotOffered(IntegrationError):
    """Tellurian erbjuder inte handlingen för den här stationens dispatcher."""


def _effect_visible(action: str, section: Section | None, receipt_state: str | None) -> bool:
    """Visar återläsningen att kommandots effekt finns?

    Tellurian har inga kvitton per kommando. Ett tappat svar avstäms därför
    mot sektionens tillstånd: det bekräftar effekten, inte vem som gjorde den.
    En sektion i läget Arrived syns inte längre i dispatcherns vy (biblioteket
    döljer den), så `arrive` bekräftas av POST-svaret plus att sektionen är borta.
    """
    name, _, argument = action.partition(":")
    if section is None:
        return name == "arrive" and receipt_state == "Arrived"
    state, train = section.state, section.train_state
    return {
        "request": state in ("Requested", "Accepted", "Departed", "Arrived"),
        "accept": state in ("Accepted", "Departed", "Arrived"),
        "reject": state == "Rejected",
        "revoke": state == "Revoked",
        "depart": state in ("Departed", "Arrived"),
        "pass": state == "Arrived" or (state == "Departed" and argument.isdigit() and section.current > int(argument)),
        "arrive": state == "Arrived",
        "clear": state == "Canceled",
        "manned": train in ("Manned", "Running", "Completed"),
        "canceled": train == "Canceled",
        "aborted": train == "Aborted",
        "undoTrainState": bool(argument) and train not in (argument, "Unknown"),
    }.get(name, False)


class TellurianCommands:
    """Skrivvägen. `station_id` ska komma från serverns inloggning, aldrig från klienten.

    En box får bara agera för sin tilldelade stations dispatcher. Kontrollen sker
    här, i serverns inkörsport: Tellurians `href` bär ingen dispatcher, så
    Tellurian själv kan inte skilja stationer åt.
    """

    def __init__(self, client: TellurianClient, mirror: Mirror, journal, *, meet_id: str,
                 connection_id: str, stations: dict[str, str], clock: Callable[[], float] = time.time):
        self.client, self.mirror, self.journal = client, mirror, journal
        self.meet_id, self.connection_id = meet_id, connection_id
        self.stations = dict(stations)
        self.clock = clock
        self._locks: dict[str, Lock] = {}
        self._registry = Lock()

    def scope(self, station_id: str) -> Scope:
        if station_id not in self.stations:
            raise ScopeMismatch("station_not_mapped")
        return Scope(self.meet_id, PROVIDER_ID, self.connection_id, station_id)

    def _lock(self, station_id: str) -> Lock:
        with self._registry:
            return self._locks.setdefault(station_id, Lock())

    def _read(self, station_id: str) -> DispatcherView:
        dispatcher = self.mirror.dispatchers.get(self.stations[station_id])
        if dispatcher is None:
            self.mirror.set_dispatchers(self.client.dispatchers())
            dispatcher = self.mirror.dispatchers.get(self.stations[station_id])
        if dispatcher is None:
            raise ScopeMismatch("dispatcher_missing")
        view = self.client.view(dispatcher)
        self.mirror.put(view, self.clock())
        return view

    def submit(self, *, station_id: str, section_id: str, handling: str, command_id: str,
               admin: bool = False) -> CommandOutcome:
        if not isinstance(command_id, str) or not _ID.match(command_id):
            raise ValueError("command_id")
        spec = HANDLINGS.get(handling)
        if spec is None:
            raise Unsupported("handling_not_mapped")
        if spec.admin and not admin:
            raise Unsupported("admin_only")
        scope = self.scope(station_id)
        with self._lock(station_id):
            previous = self.journal.get(scope, command_id)
            if previous is not None:
                command, result = previous
                if command.train_id != f"section:{section_id}" or command.action.partition(":")[0] != spec.action:
                    raise IntegrationError("command_id_reused")
                return CommandOutcome(command_id, result.outcome, "duplicate")
            # Senast lästa href för just det här tåget och den här åtgärden.
            view = self._read(station_id)
            section = view.section(section_id)
            action = next((a for a in (section.actions if section else ())
                           if handling_for(a, section) == handling), None)
            if section is None or action is None:
                raise ActionNotOffered("not_offered")
            encoded = action.name
            if action.name == "pass":
                encoded = f"pass:{section.current}"
            elif action.name == "undoTrainState":
                encoded = f"undoTrainState:{section.train_state}"
            command = Command(command_id, f"section:{section.id}", encoded)
            claimed, result = self.journal.claim(scope, command)
            if not claimed:
                return CommandOutcome(command_id, result.outcome, "duplicate")
            try:
                response = self.client.post(action.href)
            except TellurianError as error:
                # Ett avbrutet anrop kan ha gått igenom. Aldrig automatisk omsändning.
                detail = "refused" if error.code == "refused" else "not_sent" if error.sent is False else "unknown"
                log = LOGGER.info if detail == "refused" else LOGGER.warning
                log("Tellurian-kommando utan bekräftelse (%s)", error.code)
                return CommandOutcome(command_id, self.journal.record(scope, command, Outcome.UNKNOWN).outcome, detail)
            receipt = (response.get("state") if isinstance(response, dict)
                       and str(response.get("id")) == section.id else None)
            self.journal.record(scope, command, Outcome.PENDING)
            try:
                after = self._read(station_id).section(section.id)
            except TellurianError:
                return CommandOutcome(command_id, Outcome.PENDING, "pending")
            if _effect_visible(encoded, after, receipt):
                return CommandOutcome(command_id, self.journal.record(scope, command, Outcome.CONFIRMED).outcome,
                                      "confirmed")
            return CommandOutcome(command_id, Outcome.PENDING, "pending")

    def reconcile(self, station_id: str, command_id: str) -> CommandOutcome:
        """Bara läsning: stäm av ett osäkert kommando utan att skicka det igen."""
        scope = self.scope(station_id)
        with self._lock(station_id):
            previous = self.journal.get(scope, command_id)
            if previous is None:
                raise IntegrationError("unknown_command")
            command, result = previous
            if result.outcome == Outcome.CONFIRMED:
                return CommandOutcome(command_id, result.outcome, "confirmed")
            try:
                section = self._read(station_id).section(command.train_id.removeprefix("section:"))
            except TellurianError:
                return CommandOutcome(command_id, result.outcome, result.outcome.value)
            if _effect_visible(command.action, section, None):
                return CommandOutcome(command_id, self.journal.record(scope, command, Outcome.CONFIRMED).outcome,
                                      "confirmed")
            return CommandOutcome(command_id, result.outcome, result.outcome.value)

    def reconcile_pending(self, station_id: str) -> tuple[CommandOutcome, ...]:
        return tuple(self.reconcile(station_id, command.command_id)
                     for command in self.journal.unresolved(self.scope(station_id)))


# ── Avvikelser mot TrainMeets egen klarering (skuggläge) ─────────────────
def deviations(sections: dict[str, Section], stations: list[dict], trainmeet_cases: list[dict]) -> dict[str, Any]:
    """Jämför Tellurians aktiva sektioner med TrainMeets öppna klareringsärenden.

    Ett ärende är {from_station_id, to_station_id, train_number, state} där
    state är requested | accepted | departed. Inget av det ändrar något.
    """
    code_of = {s["id"]: str(s.get("code") or "") for s in stations}
    codes = set(code_of.values())
    remaining = {sid: s for sid, s in sections.items() if s.state in ACTIVE_STATES}
    result, unmapped = [], 0
    for case in trainmeet_cases:
        a, b = code_of.get(case.get("from_station_id"), "?"), code_of.get(case.get("to_station_id"), "?")
        number = _number(case.get("train_number"))
        hit = next((sid for sid, s in remaining.items() if s.from_sig == a and s.to_sig == b
                    and parse_train_label(s.train)[2] == number), None)
        row = {"train": number or "", "from": a, "to": b, "trainmeet": case.get("state")}
        if hit is None:
            result.append({**row, "kind": "only_trainmeet", "tellurian": None})
            continue
        section = remaining.pop(hit)
        if ACTIVE_STATES[section.state] != case.get("state"):
            result.append({**row, "kind": "state_differs", "tellurian": ACTIVE_STATES[section.state],
                           "tellurian_train": section.train})
    for section in remaining.values():
        if section.from_sig not in codes or section.to_sig not in codes:
            unmapped += 1
            continue
        result.append({"kind": "only_tellurian", "train": parse_train_label(section.train)[2] or section.train,
                       "tellurian_train": section.train, "from": section.from_sig, "to": section.to_sig,
                       "trainmeet": None, "tellurian": ACTIVE_STATES[section.state]})
    result.sort(key=lambda row: (row["from"], row["to"], row["train"]))
    return {"items": result[:100], "count": len(result), "unmapped": unmapped}


# ── Skuggläge: läs och spegla bredvid TrainMeets egen klarering ──────────
@dataclass
class ShadowState:
    started_at: float
    last_contact: float | None = None
    sse: bool = False
    error: str | None = None
    simulated: bool = False
    deviations: dict[str, Any] = field(default_factory=lambda: {"items": [], "count": 0, "unmapped": 0})


class ShadowRunner:
    """Läser och speglar utan att skriva. Allt i den här klassen är GET.

    SSE väcker en ny läsning av berörda dispatchers (Stefans mönster). När
    strömmen bryts läses alla regelbundet tills den är tillbaka. Utan lyckad
    kontakt på `contact_timeout` sekunder visas "ingen kontakt".
    """

    def __init__(self, client: TellurianClient, *, stations: Callable[[], list[dict]],
                 trainmeet_cases: Callable[[], list[dict]], poll_interval: float = 3.0,
                 liveness_interval: float = 5.0, contact_timeout: float = 15.0,
                 sse_read_timeout: float = 30.0, clock: Callable[[], float] = time.time):
        self.client = client
        self.mirror = Mirror()
        self._stations, self._cases = stations, trainmeet_cases
        self.poll_interval, self.liveness_interval = poll_interval, liveness_interval
        self.contact_timeout, self.sse_read_timeout = contact_timeout, sse_read_timeout
        self.clock = clock
        self.state = ShadowState(clock())
        self._lock = Lock()
        self._stop, self._wake = Event(), Event()
        self._pending: set[str] | None = None  # None = läs alla
        self._threads: list[Thread] = []

    def start(self) -> None:
        for target, name in ((self._loop, "tellurian-shadow"), (self._events, "tellurian-sse")):
            thread = Thread(target=target, name=name, daemon=True)
            self._threads.append(thread)
            thread.start()

    def stop(self, *, wait: bool = True) -> None:
        """Stoppa läsningen. Utan `wait` avslutas trådarna själva strax efter.

        Ett träffbyte sker under trafiklåset; där får stoppet inte vänta på en
        läsning som i sin tur väntar på samma lås.
        """
        self._stop.set()
        self._wake.set()
        self.client.transport.abort_streams()
        if wait:
            for thread in self._threads:
                thread.join(timeout=self.client.transport.timeout + 1)

    def _request_refresh(self, dispatcher_ids: set[str] | None) -> None:
        with self._lock:
            if dispatcher_ids is None or self._pending is None:
                self._pending = None
            else:
                self._pending |= dispatcher_ids
        self._wake.set()

    def _contact(self) -> None:
        with self._lock:
            self.state.last_contact = self.clock()
            self.state.error = None
            self.state.simulated = self.client.simulated

    def _loop(self) -> None:
        last_full = last_liveness = float("-inf")
        need_list = True
        while not self._stop.is_set():
            now = self.clock()
            with self._lock:
                pending, self._pending = self._pending, set()
                sse = self.state.sse
            try:
                if need_list or pending is None or (not sse and now - last_full >= self.poll_interval):
                    self.mirror.set_dispatchers(self.client.dispatchers())
                    self.mirror.refresh(self.client, None, now)
                    need_list, last_full = False, now
                    self._contact()
                    self._compare()
                elif pending:
                    self.mirror.refresh(self.client, pending, now)
                    self._contact()
                    self._compare()
                elif now - last_liveness >= self.liveness_interval:
                    known = set(self.mirror.dispatchers)
                    listed = self.client.dispatchers()
                    last_liveness = now
                    self._contact()
                    if {d.id for d in listed} != known:
                        need_list = True
                        continue
            except Exception as error:  # noqa: BLE001 - skuggan får aldrig dö tyst
                code = error.code if isinstance(error, TellurianError) else "internal"
                with self._lock:
                    self.state.error = code
                    if pending:
                        self._pending = None
                need_list = True
                # Bara koden: Tellurians svar hamnar aldrig i loggen.
                LOGGER.info("Tellurian (skugga) kunde inte läsas: %s (%s)", code, type(error).__name__)
            self._wake.wait(0.2 if not sse else min(self.liveness_interval, 1.0))
            self._wake.clear()

    def _events(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                for kind, data in self.client.events(self._stop, read_timeout=self.sse_read_timeout):
                    if not self.state.sse:
                        with self._lock:
                            self.state.sse = True
                        # Händelser kan ha missats medan strömmen var nere.
                        self._request_refresh(None)
                    backoff = 0.5
                    self._contact()
                    if kind:
                        self._request_refresh(self._affected(data))
            except Exception:  # noqa: BLE001 - en bruten ström ger regelbunden läsning
                pass
            with self._lock:
                self.state.sse = False
            self._wake.set()
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 10.0)

    def _affected(self, data: str) -> set[str] | None:
        try:
            payload = json.loads(data)
            ids = payload.get("dispatcherIds") or payload.get("affectedDispatchers")
            result = {_ident(i) for i in ids} if isinstance(ids, list) else None
        except (ValueError, AttributeError, TypeError):
            return None
        return {i for i in result if i} if result else None

    def _compare(self) -> None:
        try:
            stations, cases = self._stations(), self._cases()
        except Exception:
            LOGGER.warning("TrainMeets klareringsläge kunde inte läsas för jämförelse")
            return
        result = deviations(self.mirror.sections(), stations, cases)
        with self._lock:
            self.state.deviations = result

    def status(self) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            state = self.state
            contact = state.last_contact
            if contact is None:
                summary = "starting" if now - state.started_at < self.contact_timeout else "no_contact"
            elif now - contact > self.contact_timeout:
                summary = "no_contact"
            else:
                summary = "connected" if state.sse else "polling"
            return {"state": summary, "sse": state.sse, "last_contact": contact, "error": state.error,
                    "simulated": state.simulated, "dispatchers": len(self.mirror.dispatchers),
                    "sections": len(self.mirror.sections()), "deviations": dict(state.deviations)}


# ── Förhandsrapport ("Testa anslutning") ─────────────────────────────────
def preflight(client: TellurianClient, *, stations: list[dict], connections: list[dict],
              services: list[dict], clock_source: dict[str, Any], dispatch_mode: str,
              region: str, budget: float = 12.0, clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Läser Tellurian en gång och jämför med träffens publicerade config. Bara GET."""
    started = clock()
    report: dict[str, Any] = {
        "api": {"status": "failed", "version": "v1"},
        "clock": {"trainmeet": clock_source.get("source", "internal"),
                  "name": clock_source.get("clock_name") or "", "tellurian": "unknown",
                  "warning": clock_source.get("source") != "fastclock"},
        "dispatch_mode": {"value": dispatch_mode, "region": region,
                          "ok": region == "eu" and dispatch_mode == "clearance"},
    }
    try:
        dispatchers = client.dispatchers()
    except TellurianError as error:
        report["api"]["error"] = error.code
        report["simulated"] = client.simulated
        report["activation"] = {"shadow": False, "authority": False, "blockers": ["api"]}
        return report
    mirror = Mirror()
    mirror.set_dispatchers(dispatchers)
    errors = []
    for dispatcher in dispatchers:
        if clock() - started > budget:
            errors.append("budget")
            break
        try:
            mirror.put(client.view(dispatcher), time.time())
        except TellurianError as error:
            errors.append(error.code)
    report["api"] = {"status": "passed" if not errors else "partial", "version": "v1",
                     "dispatchers": len(dispatchers), "read": len(mirror.views),
                     **({"error": errors[0]} if errors else {})}
    sections = mirror.sections()
    report["simulated"] = client.simulated
    report["stations"] = match_stations(stations, dispatchers)
    report["stretches"] = match_stretches(connections, stations, report["stations"], sections)
    report["trains"] = match_trains(services, stations, sections)
    report["unknown_actions"] = list(mirror.unknown_actions())
    blockers = []
    if report["stations"]["unmatched_trainmeet"]:
        blockers.append("stations")
    if not report["dispatch_mode"]["ok"]:
        blockers.append("dispatch_mode")
    report["activation"] = {"shadow": report["api"]["status"] != "failed" and region == "eu",
                            "authority": False, "blockers": blockers,
                            "warnings": (["trains"] if report["trains"]["unmatched_trainmeet"]
                                         or report["trains"]["ambiguous"] else [])
                                        + (["clock"] if report["clock"]["warning"] else [])}
    return report
