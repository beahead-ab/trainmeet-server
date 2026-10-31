"""Fejkad Tellurian Dispatch-server för TrainMeets tester. Binds bara till 127.0.0.1.

Stefans server (`Tellurian.Trains.Dispatch.Server`) finns inte publikt. Den här
simulatorn följer hans SPECIFICATION.md (REST enligt HATEOAS under /api/v1 och
Server-Sent Events) och reglerna i bibliotekets ActionStateMachine, läst på
`0a0f42a` (Release 1.1.0). Den länkar inte in och kör inte Tellurians kod.

Det som specifikationen inte bestämmer är antaganden och står i
docs/tellurian-dispatch.md: sektionernas fält `from`, `to`, `trainId`,
`departureTime` och `arrivalTime`, händelsernas JSON, felkoder (409/404),
index i `pass/{index}` och var kapaciteten kontrolleras. Varje svar bär
rubriken `X-Tellurian-Simulator`, så att en rapport aldrig kan tas för en
verifiering mot Stefans server.

Felinjektion finns bara som Python-metoder för testerna, aldrig som HTTP-vägar.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import re
import socket
from threading import Event, RLock, Thread
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

DISPATCH_STATES = ("None", "Requested", "Accepted", "Rejected", "Revoked", "Departed", "Arrived", "Canceled")
TRAIN_STATES = ("Planned", "Manned", "Running", "Canceled", "Aborted", "Completed")
SIMULATOR_HEADER = "X-Tellurian-Simulator"
_ACTION_PATH = re.compile(r"^/api/v1/train-sections/(\d+)/actions/([A-Za-z]+)(?:/(\d+))?$")
_DISPATCHER_PATH = re.compile(r"^/api/v1/dispatchers/(\d+)$")


class SimRefused(Exception):
    """Åtgärden är inte tillåten just nu (som bibliotekets tillståndsmaskin)."""


@dataclass
class _Place:
    id: int
    name: str
    signature: str
    kind: str  # station | signal
    controlled_by: str | None = None


@dataclass
class _TrackStretch:
    id: int
    start: str
    end: str
    # (nummer, riktning): both | forward | backward | closed. Riktningen gäller
    # sträckans definierade håll start → end.
    tracks: list[tuple[str, str]]
    occupied: dict[str, int] = field(default_factory=dict)  # spårnummer → sektion


@dataclass
class _DispatchStretch:
    id: int
    from_sig: str
    to_sig: str
    track_stretches: list[int]  # i definierad riktning från from_sig


@dataclass
class _Train:
    id: int
    company: str
    prefix: str
    number: int
    state: str = "Planned"
    previous: str | None = None

    @property
    def label(self) -> str:
        return f"{self.company} {self.prefix} {self.number}"

    def set_state(self, value: str) -> None:
        self.previous, self.state = self.state, value


@dataclass
class _Section:
    id: int
    train_id: int
    stretch_id: int
    forward: bool
    from_sig: str
    to_sig: str
    departure_time: str
    arrival_time: str
    previous: int | None
    is_last: bool
    state: str = "None"
    index: int = 0
    occupying: tuple[int, str] | None = None


def default_scenario() -> dict[str, Any]:
    """Tre stationer: enkelspår med blocksignal CDA–VST, dubbelspår VST–LEK."""
    return {
        "stations": [
            {"signature": "CDA", "name": "Charlottendal"},
            {"signature": "VST", "name": "Västerhamn"},
            {"signature": "LEK", "name": "Lekeberg"},
        ],
        "signals": [{"signature": "SÖB", "name": "Sörby", "controlled_by": "CDA"}],
        "track_stretches": [
            {"from": "CDA", "to": "SÖB", "tracks": [["1", "both"]]},
            {"from": "SÖB", "to": "VST", "tracks": [["1", "both"]]},
            {"from": "VST", "to": "LEK", "tracks": [["U", "forward"], ["N", "backward"]]},
        ],
        "dispatch_stretches": [{"from": "CDA", "to": "VST"}, {"from": "VST", "to": "LEK"}],
        "trains": [
            {"company": "SJ", "prefix": "RC", "number": 421,
             "calls": [["CDA", None, "09:20"], ["VST", "09:41", "09:45"], ["LEK", "10:05", None]]},
            {"company": "GC", "prefix": "G", "number": 4711,
             "calls": [["LEK", None, "09:30"], ["VST", "09:50", "09:55"], ["CDA", "10:20", None]]},
            {"company": "TAB", "prefix": "RC", "number": 100,
             "calls": [["VST", None, "11:00"], ["LEK", "11:20", None]]},
            {"company": "SJ", "prefix": "RC", "number": 100,
             "calls": [["LEK", None, "11:10"], ["VST", "11:30", None]]},
        ],
    }


class TellurianModel:
    """Brokerns tillstånd och ActionStateMachines regler, utan HTTP."""

    def __init__(self, scenario: dict[str, Any], *, max_items: int = 10):
        self.lock = RLock()
        self.max_items = max_items
        self.places: dict[str, _Place] = {}
        next_id = 1
        for station in scenario["stations"]:
            self.places[station["signature"]] = _Place(next_id, station["name"], station["signature"], "station")
            next_id += 1
        for signal in scenario.get("signals", []):
            self.places[signal["signature"]] = _Place(
                next_id, signal["name"], signal["signature"], "signal", signal["controlled_by"])
            next_id += 1
        self.track_stretches: dict[int, _TrackStretch] = {}
        for index, item in enumerate(scenario["track_stretches"], start=1):
            tracks = [tuple(track) for track in item.get("tracks") or [["1", "both"]]]
            self.track_stretches[index] = _TrackStretch(index, item["from"], item["to"], tracks)
        self.dispatch_stretches: dict[int, _DispatchStretch] = {}
        for index, item in enumerate(scenario["dispatch_stretches"], start=1):
            path = self._path(item["from"], item["to"])
            if not path:
                raise ValueError(f"Ingen spårväg {item['from']}–{item['to']}")
            self.dispatch_stretches[index] = _DispatchStretch(index, item["from"], item["to"], path)
        self.trains: dict[int, _Train] = {}
        self.sections: dict[int, _Section] = {}
        section_id = 101
        for train_id, item in enumerate(scenario["trains"], start=1):
            self.trains[train_id] = _Train(train_id, item["company"], item["prefix"], int(item["number"]))
            calls = [call for call in item["calls"] if self.places[call[0]].kind == "station"]
            previous = None
            for position, (departure, arrival) in enumerate(zip(calls, calls[1:])):
                stretch, forward = self._stretch_between(departure[0], arrival[0])
                self.sections[section_id] = _Section(
                    section_id, train_id, stretch.id, forward, departure[0], arrival[0],
                    departure[2] or "", arrival[1] or "", previous, position == len(calls) - 2)
                previous = section_id
                section_id += 1
        self.events: list[dict[str, Any]] = []
        self._listeners: list[Any] = []

    # ── Infrastruktur ────────────────────────────────────────────────────
    def _path(self, start: str, goal: str) -> list[int]:
        """Kortaste väg längs spårsträckornas definierade riktning (FindPathBetween)."""
        frontier, came = [start], {start: None}
        while frontier:
            current = frontier.pop(0)
            if current == goal:
                path, node = [], goal
                while came[node] is not None:
                    previous, stretch_id = came[node]
                    path.append(stretch_id)
                    node = previous
                return list(reversed(path))
            for stretch in self.track_stretches.values():
                if stretch.start == current and stretch.end not in came:
                    came[stretch.end] = (current, stretch.id)
                    frontier.append(stretch.end)
        return []

    def _stretch_between(self, a: str, b: str) -> tuple[_DispatchStretch, bool]:
        for stretch in self.dispatch_stretches.values():
            if (stretch.from_sig, stretch.to_sig) == (a, b):
                return stretch, True
            if (stretch.from_sig, stretch.to_sig) == (b, a):
                return stretch, False
        raise ValueError(f"Ingen tågledningssträcka {a}–{b}")

    def _in_order(self, section: _Section) -> list[_TrackStretch]:
        ids = self.dispatch_stretches[section.stretch_id].track_stretches
        return [self.track_stretches[i] for i in (ids if section.forward else list(reversed(ids)))]

    def _total(self, section: _Section) -> int:
        return len(self.dispatch_stretches[section.stretch_id].track_stretches)

    def _next_control_point(self, section: _Section) -> _Place | None:
        """Platsen vid slutet av aktuell spårsträcka i färdriktningen.

        Bibliotekets NextControlPoint använder TrackStretch.End oavsett
        riktning, vilket i 1.1.0 ser ut att peka bakåt vid motriktad trafik.
        Simulatorn följer specifikationens avsikt (se docs, fråga till Stefan).
        """
        if section.index >= self._total(section) - 1:
            return None
        stretch = self._in_order(section)[section.index]
        return self.places[stretch.end if section.forward else stretch.start]

    def _free_track(self, stretch: _TrackStretch, forward: bool) -> str | None:
        """HasAvailableTrack: ett tåg per spår; enkelspår spärrar mötande."""
        for number, direction in stretch.tracks:
            if direction == "closed" or number in stretch.occupied:
                continue
            if direction == "both" or (direction == "forward") == forward:
                return number
        return None

    def _can_enter(self, section: _Section, index: int) -> bool:
        # Spårväg följer spårsträckornas definierade riktning, så sektionens
        # riktning är också färdriktningen på varje enskild spårsträcka.
        stretch = self._in_order(section)[index]
        return self._free_track(stretch, section.forward) is not None

    def _opposing_on_single_track(self, section: _Section) -> bool:
        """Ett mötande tåg på ett enkelspårsblock av samma tågledningssträcka.

        Antagande i simulatorn: avgång spärras då för hela sträckan, inte bara
        för första blocket (bibliotekets ActionStateMachine 1.1.0 filtrerar
        inte på kapacitet alls; se docs/tellurian-dispatch.md).
        """
        for stretch_id in self.dispatch_stretches[section.stretch_id].track_stretches:
            stretch = self.track_stretches[stretch_id]
            for number, holder in stretch.occupied.items():
                other = self.sections[holder]
                if other.forward != section.forward and dict(stretch.tracks).get(number) == "both":
                    return True
        return False

    def _occupy(self, section: _Section, index: int) -> None:
        self._release(section)
        stretch = self._in_order(section)[index]
        number = self._free_track(stretch, section.forward)
        if number is None:
            raise SimRefused("Spårsträckan är upptagen")
        stretch.occupied[number] = section.id
        section.occupying = (stretch.id, number)

    def _release(self, section: _Section) -> None:
        if section.occupying:
            stretch_id, number = section.occupying
            self.track_stretches[stretch_id].occupied.pop(number, None)
            section.occupying = None

    # ── ActionStateMachine ───────────────────────────────────────────────
    @staticmethod
    def _rank(state: str) -> int:
        return DISPATCH_STATES.index(state)

    def _previous_departed(self, section: _Section) -> bool:
        return section.previous is None or self._rank(self.sections[section.previous].state) >= self._rank("Departed")

    def _dispatcher_sig(self, dispatcher_id: int) -> str:
        for place in self.places.values():
            if place.id == dispatcher_id and place.kind == "station":
                return place.signature
        raise KeyError(dispatcher_id)

    def available(self, section: _Section, dispatcher: str) -> list[tuple[str, int | None, str]]:
        """(namn, pass-index, visningsnamn) för en dispatcher, i bibliotekets ordning."""
        train = self.trains[section.train_id]
        dispatchable = train.state in ("Manned", "Running")
        ready = dispatchable and self._previous_departed(section)
        actions: list[tuple[str, int | None, str]] = []
        if section.from_sig == dispatcher and ready:
            if section.state in ("None", "Rejected", "Revoked"):
                actions.append(("request", None, "Request"))
            elif section.state == "Requested":
                actions.append(("revoke", None, "Revoke"))
            elif section.state == "Accepted":
                # Kapacitet: avgång bara när första spårsträckan har ett fritt spår.
                if self._can_enter(section, 0) and not self._opposing_on_single_track(section):
                    actions.append(("depart", None, "Depart"))
                actions.append(("revoke", None, "Revoke"))
        if section.to_sig == dispatcher and ready:
            if section.state == "Requested":
                actions += [("accept", None, "Accept"), ("reject", None, "Reject")]
            elif section.state == "Accepted":
                actions.append(("revoke", None, "Revoke"))
            elif section.state == "Departed" and section.index >= self._total(section) - 1:
                actions.append(("arrive", None, "Arrive"))
        if ready and section.state == "Departed":
            point = self._next_control_point(section)
            if (point is not None and point.kind == "signal" and point.controlled_by == dispatcher
                    and self._can_enter(section, section.index + 1)):
                actions.append(("pass", section.index + 1, f"Pass {point.name}"))
        if section.previous is None:
            if train.state == "Planned":
                actions += [("manned", None, "Manned"), ("canceled", None, "Canceled")]
            elif train.state == "Manned":
                actions.append(("canceled", None, "Canceled"))
        elif train.state == "Running":
            actions.append(("aborted", None, "Aborted"))
        if train.state in ("Manned", "Canceled", "Aborted") and train.previous is not None:
            actions.append(("undoTrainState", None, f"Undo {train.state}"))
        if train.state in ("Canceled", "Aborted") and section.state == "Departed":
            actions.append(("clear", None, "Clear"))
        return actions

    def all_available(self, section: _Section) -> list[tuple[str, int | None, str]]:
        seen, result = set(), []
        for dispatcher in (section.from_sig, section.to_sig):
            for action in self.available(section, dispatcher):
                if action[:2] not in seen:
                    seen.add(action[:2])
                    result.append(action)
        return result

    def execute(self, section_id: int, name: str, index: int | None = None) -> _Section:
        with self.lock:
            section = self.sections.get(section_id)
            if section is None:
                raise KeyError(section_id)
            if (name, index) not in {(a, i) for a, i, _ in self.all_available(section)}:
                raise SimRefused(f"{name} är inte tillgänglig")
            train = self.trains[section.train_id]
            before_state, before_train = section.state, train.state
            affected = self._affected(section)
            if name in ("request", "accept", "reject", "revoke"):
                section.state = {"request": "Requested", "accept": "Accepted",
                                 "reject": "Rejected", "revoke": "Revoked"}[name]
            elif name == "depart":
                self._occupy(section, 0)
                section.state, section.index = "Departed", 0
                if train.state == "Manned":
                    train.set_state("Running")
            elif name == "pass":
                point = self._next_control_point(section)
                self._occupy(section, section.index + 1)
                section.index += 1
                self._emit("ControlPointPassed", section, affected, controlPoint=point.signature,
                           controlPointIndex=index)
            elif name == "arrive":
                self._release(section)
                section.state = "Arrived"
                if section.is_last:
                    train.set_state("Completed")
            elif name == "clear":
                self._release(section)
                section.state = "Canceled"
                self._emit("TrainSectionCleared", section, affected)
            elif name in ("manned", "canceled", "aborted"):
                train.set_state({"manned": "Manned", "canceled": "Canceled", "aborted": "Aborted"}[name])
            elif name == "undoTrainState":
                train.state, train.previous = train.previous, None
            if section.state != before_state:
                self._emit("DispatchStateChanged", section, affected,
                           previousState=before_state, newState=section.state)
            if train.state != before_train:
                self._emit("TrainStateChanged", section, self._train_dispatchers(train.id),
                           previousState=before_train, newState=train.state)
            return section

    def _affected(self, section: _Section) -> list[int]:
        sigs = {section.from_sig, section.to_sig}
        for stretch_id in self.dispatch_stretches[section.stretch_id].track_stretches:
            stretch = self.track_stretches[stretch_id]
            for sig in (stretch.start, stretch.end):
                if self.places[sig].controlled_by:
                    sigs.add(self.places[sig].controlled_by)
        return sorted(self.places[sig].id for sig in sigs)

    def _train_dispatchers(self, train_id: int) -> list[int]:
        sigs = {sig for s in self.sections.values() if s.train_id == train_id for sig in (s.from_sig, s.to_sig)}
        return sorted(self.places[sig].id for sig in sigs)

    def _emit(self, kind: str, section: _Section, dispatchers: list[int], **data: Any) -> None:
        event = {"type": kind, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "sectionId": section.id, "trainId": section.train_id,
                 "train": self.trains[section.train_id].label, "dispatcherIds": dispatchers, **data}
        self.events.append(event)
        for listener in list(self._listeners):
            listener(event)

    # ── DTO enligt specifikationen ───────────────────────────────────────
    def dispatcher_list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [{"id": p.id, "signature": p.signature, "name": p.name,
                     "href": f"/api/v1/dispatchers/{p.id}"}
                    for p in self.places.values() if p.kind == "station"]

    def section_dto(self, section: _Section, actions: list[tuple[str, int | None, str]]) -> dict[str, Any]:
        train = self.trains[section.train_id]
        return {
            "id": section.id, "trainId": train.id, "train": train.label,
            "trainState": train.state, "state": section.state,
            "currentTrackStretch": section.index, "totalTrackStretches": self._total(section),
            "from": section.from_sig, "to": section.to_sig,
            "departureTime": section.departure_time, "arrivalTime": section.arrival_time,
            "actions": [{"name": name, "displayName": display,
                         "href": f"/api/v1/train-sections/{section.id}/actions/{name}"
                                 + (f"/{index}" if index is not None else "")}
                        for name, index, display in actions],
        }

    def dispatcher_view(self, dispatcher_id: int) -> dict[str, Any]:
        with self.lock:
            sig = self._dispatcher_sig(dispatcher_id)
            place = self.places[sig]
            visible = [s for s in self.sections.values() if s.state != "Arrived"]
            arrivals = sorted((s for s in visible if s.to_sig == sig), key=lambda s: (s.arrival_time, s.id))
            departures = sorted((s for s in visible if s.from_sig == sig), key=lambda s: (s.departure_time, s.id))
            return {
                "id": place.id, "signature": sig, "stationName": place.name, "preferredLanguage": None,
                "arrivals": [self.section_dto(s, self.available(s, sig)) for s in arrivals[:self.max_items]],
                "departures": [self.section_dto(s, self.available(s, sig)) for s in departures[:self.max_items]],
            }

    # ── Hjälp för testerna ───────────────────────────────────────────────
    def section_for(self, train_number: int, from_sig: str) -> _Section:
        with self.lock:
            found = [s for s in self.sections.values()
                     if self.trains[s.train_id].number == train_number and s.from_sig == from_sig]
            if len(found) != 1:
                raise LookupError(f"{len(found)} sektioner för {train_number} från {from_sig}")
            return found[0]

    def man_all(self) -> None:
        with self.lock:
            for train in self.trains.values():
                if train.state == "Planned":
                    train.set_state("Manned")

    def dispatcher_id(self, signature: str) -> int:
        return self.places[signature].id


class _Subscription:
    def __init__(self, dispatcher_id: int | None):
        self.dispatcher_id = dispatcher_id
        self.queue: queue.Queue = queue.Queue()
        self.closed = Event()


class TellurianSimulator(AbstractContextManager):
    """HTTP- och SSE-värd för TellurianModel på en tillfällig loopback-port."""

    def __init__(self, scenario: dict[str, Any] | None = None, *, max_items: int = 10,
                 heartbeat: float = 0.5):
        self.model = TellurianModel(scenario or default_scenario(), max_items=max_items)
        self.heartbeat = heartbeat
        self._lock = RLock()
        self._subscriptions: list[_Subscription] = []
        self._lose_next_response = False
        self._read_faults: list[str] = []
        self._refuse_streams = False
        self._offline = False
        self._stopping = Event()
        self.executed: list[tuple[int, str]] = []
        self.requests: list[tuple[str, str]] = []
        self.model._listeners.append(self._publish)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                pass

            def _send(self, status: int, data: Any, *, headers: dict[str, str] | None = None) -> None:
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header(SIMULATOR_HEADER, "trainmeet-test")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _drop(self) -> None:
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()

            def _fault(self) -> bool:
                with owner._lock:
                    if owner._offline:
                        self._drop()
                        return True
                    fault = owner._read_faults.pop(0) if owner._read_faults else None
                if fault is None:
                    return False
                if fault == "timeout":
                    time.sleep(1.5)
                    self._drop()
                elif fault == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://example.invalid/api/v1/dispatchers")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif fault == "oversize":
                    body = b"[" + b" " * (3 * 1024 * 1024) + b"]"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                elif fault == "invalid_json":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "1")
                    self.end_headers()
                    self.wfile.write(b"{")
                elif fault == "server_error":
                    self._send(500, {"title": "Internal Server Error", "detail": "secret stack trace"})
                return True

            def do_GET(self):
                url = urlsplit(self.path)
                with owner._lock:
                    owner.requests.append(("GET", url.path))
                if url.path == "/api/v1/events":
                    return self._events(url)
                if self._fault():
                    return
                if url.path == "/api/v1/dispatchers":
                    return self._send(200, owner.model.dispatcher_list())
                match = _DISPATCHER_PATH.match(url.path)
                if match:
                    try:
                        return self._send(200, owner.model.dispatcher_view(int(match.group(1))))
                    except KeyError:
                        return self._send(404, {"title": "Not Found", "status": 404})
                self._send(404, {"title": "Not Found", "status": 404})

            def do_POST(self):
                url = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(min(length, 8192))
                with owner._lock:
                    owner.requests.append(("POST", url.path))
                    if owner._offline:
                        return self._drop()
                match = _ACTION_PATH.match(url.path)
                if not match:
                    return self._send(404, {"title": "Not Found", "status": 404})
                section_id, name = int(match.group(1)), match.group(2)
                index = int(match.group(3)) if match.group(3) else None
                try:
                    with owner.model.lock:
                        section = owner.model.execute(section_id, name, index)
                        dto = owner.model.section_dto(section, owner.model.all_available(section))
                    with owner._lock:
                        owner.executed.append((section_id, name if index is None else f"{name}/{index}"))
                        lose = owner._lose_next_response
                        owner._lose_next_response = False
                except KeyError:
                    return self._send(404, {"title": "Not Found", "status": 404})
                except SimRefused as error:
                    return self._send(409, {"title": "Conflict", "status": 409, "detail": str(error)})
                if lose:
                    return self._drop()
                self._send(200, dto)

            def _events(self, url):
                query = parse_qs(url.query)
                with owner._lock:
                    if owner._offline:
                        return self._drop()
                    if owner._refuse_streams:
                        return self._send(503, {"title": "Unavailable", "status": 503})
                    try:
                        dispatcher = int(query["dispatcherId"][0]) if "dispatcherId" in query else None
                    except ValueError:
                        return self._send(400, {"title": "Bad Request", "status": 400})
                    subscription = _Subscription(dispatcher)
                    owner._subscriptions.append(subscription)
                self.close_connection = True
                self.send_response(200)
                self.send_header("Connection", "close")
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header(SIMULATOR_HEADER, "trainmeet-test")
                self.end_headers()
                last = time.monotonic()
                try:
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    while not subscription.closed.is_set() and not owner._stopping.is_set():
                        try:
                            event = subscription.queue.get(timeout=0.05)
                        except queue.Empty:
                            if time.monotonic() - last >= owner.heartbeat:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                                last = time.monotonic()
                            continue
                        payload = json.dumps(event, ensure_ascii=False)
                        self.wfile.write(f"event: {event['type']}\ndata: {payload}\n\n".encode())
                        self.wfile.flush()
                        last = time.monotonic()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with owner._lock:
                        if subscription in owner._subscriptions:
                            owner._subscriptions.remove(subscription)
                    self._drop()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02},
                              name="tellurian-sim", daemon=True)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stopping.set()
        self.drop_event_streams()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3)

    def _publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            for subscription in self._subscriptions:
                if subscription.dispatcher_id is None or subscription.dispatcher_id in event["dispatcherIds"]:
                    subscription.queue.put(event)

    # ── Felinjektion och hjälp, bara för tester ──────────────────────────
    def act(self, train_number: int, from_sig: str, name: str) -> None:
        """En annan klient (t.ex. Tellurians egen vy) gör en åtgärd direkt."""
        section = self.model.section_for(train_number, from_sig)
        index = next((i for a, i, _ in self.model.all_available(section) if a == name), None)
        self.model.execute(section.id, name, index)

    def lose_next_response(self) -> None:
        with self._lock:
            self._lose_next_response = True

    def fail_next_read(self, kind: str) -> None:
        if kind not in {"timeout", "redirect", "oversize", "invalid_json", "server_error"}:
            raise ValueError("Okänt fel")
        with self._lock:
            self._read_faults.append(kind)

    def drop_event_streams(self) -> None:
        with self._lock:
            for subscription in self._subscriptions:
                subscription.closed.set()

    def refuse_event_streams(self, refuse: bool) -> None:
        with self._lock:
            self._refuse_streams = refuse
        if refuse:
            self.drop_event_streams()

    def set_offline(self, offline: bool) -> None:
        with self._lock:
            self._offline = offline
        if offline:
            self.drop_event_streams()

    def stream_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    def posts(self) -> int:
        with self._lock:
            return sum(1 for method, _ in self.requests if method == "POST")


def main() -> None:  # pragma: no cover - manuell provkörning
    """Starta simulatorn för att prova Inställningar → Klarering för hand."""
    with TellurianSimulator() as simulator:
        print(f"Tellurian-simulator: {simulator.base_url} (välj 'testinstans' i Inställningar)")
        simulator.model.man_all()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":  # pragma: no cover
    main()
