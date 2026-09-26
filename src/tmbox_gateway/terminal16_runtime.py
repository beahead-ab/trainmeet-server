"""The 16x2 interaction profile over the persistent, shared station service.

Only selection and presentation are ephemeral. Cases, arrival tracks, train
positions and duplicate receipts live in the same transaction/store as TKL.
The isolated lab and real terminals share the view/key state machine, never
each other's data. No device is allowed to choose its station.
"""
from copy import deepcopy
from hashlib import sha256
import json
from time import monotonic

from .engine import TrafficEngine
from .models import ConnectionRuntime, ConnectionState
from .protocol_v2 import CommandRejected
from .terminal16 import Terminal, Terminal16Lab, row
from .terminal16_glyphs import encode_lcd
from .terminal16_i18n import text


class RuntimeViews(Terminal16Lab):
    def set_language(self, device, language):
        # Identity preferences use their own connection. Write after the traffic
        # transaction releases SQLite; language selection never changes traffic.
        self.service.operations_store.after_commit(lambda: self.service.identities.set_device_language(device, language))
        super().set_language(device, language)

    def __init__(self, service, *, now=monotonic):
        self.service = service
        publication = deepcopy(service.publication().payload)
        publication["meet"]["active_day"] = service.runtime_store.active_day() or service.publication().active_day
        # A read projection, NOT another traffic authority or persistent engine.
        engine = TrafficEngine(service.session_config())
        engine.set_clock_source(service.clock_source)
        super().__init__(engine, publication, {}, now=now)
        self.lock = service.operations_store.command_lock
        self.cases = {}
        self.fingerprint = None
        self.refresh()

    def _side(self, station, other, connection_id=None):
        config = self.engine.config
        connection = config.connections[connection_id]
        sides = {p.slot_position(key)[1] for p in config.panels.values() if p.station_id == station
                 for key, value in p.slots.items() if value == connection.id}
        if len(sides) == 1:
            return next(iter(sides))
        # Unmapped links are still usable: stable presentation, no A-D routing.
        return "right" if station == connection.station_a_id else "left"

    def _line(self, leg):
        case = self.cases.get(leg["from_movement_id"])
        if case is None:
            return ConnectionRuntime()
        return ConnectionRuntime(state=(ConnectionState.REQUESTED if case["status"] == "waiting" else
            ConnectionState.OCCUPIED if self.service.case_departed(case) else ConnectionState.RESERVED),
            from_station_id=case["from_station_id"], to_station_id=case["to_station_id"],
            train_number=leg["train_number"], request_id=case["clearance_id"])

    def _is_active(self, leg):
        return leg["from_movement_id"] in self.cases

    def refresh(self):
        service = self.service
        states = {station: service.operations_store.tkl_station_state(
            service.publication().publication_id, self.day, station)["movements"] for station in self.engine.config.stations}
        cases = {case["movement_id"]: case for case in service.open_cases(None)
                 if case["movement_id"] in self.legs
                 and case["connection_id"] == self.legs[case["movement_id"]]["connection_id"]
                 and case["to_station_id"] == self.legs[case["movement_id"]]["to_station_id"]}
        fingerprint = sha256(json.dumps([states, cases], sort_keys=True).encode()).hexdigest()
        if fingerprint == self.fingerprint:
            return
        old_cases, old_completed = self.cases, self.completed
        self.cases = cases
        self.bindings = {case["clearance_id"]: key for key, case in cases.items()}
        self.completed = {key for key, leg in self.legs.items()
            if states[leg["from_station_id"]].get(key, {}).get("departure") == "departed"
            and states[leg["to_station_id"]].get(leg["to_movement_id"], {}).get("arrival") == "arrived"}
        if self.fingerprint is not None:
            for key in self.completed - old_completed:
                self._notify_arrival(self.legs[key])
            for key in cases.keys() - old_cases.keys():
                if cases[key]["status"] == "waiting":
                    self._notify_request(self.legs[key])
            for key in old_cases.keys() - cases.keys() - self.completed:
                closed = service.operations_store.clearance(old_cases[key]["clearance_id"])
                if closed and closed["status"] == "rejected":
                    for terminal in self.terminals.values():
                        if terminal.selected == key and terminal.station == self.legs[key]["from_station_id"]:
                            terminal.notice = self.legs[key]["train_number"] + " NEKAT"
                            terminal.notice_until = self.now() + 3
                            terminal.revision += 1
        self.engine.revision += 1
        self.fingerprint = fingerprint

    def _traffic(self, device, terminal, action):
        leg = self.legs[terminal.selected]
        case = self.cases.get(terminal.selected)
        body = {}
        if action == "request":
            operation = "clearance.request"
            body = {"movement_id": leg["from_movement_id"], "connection_id": leg["connection_id"]}
        elif case is None:
            return "Läget ändrades. Välj tåget igen."
        elif action in {"accept", "reject", "cancel"}:
            operation = "clearance.cancel" if action == "cancel" else "clearance.response"
            body = {"clearance_id": case["clearance_id"], "approved": action == "accept"}
        elif action == "depart":
            operation, body = "train.departed", {"movement_id": leg["from_movement_id"]}
        elif action in {"arrive", "arrive_track"}:
            tracks = self._tracks(terminal)
            track = tracks[terminal.track % len(tracks)].id if action == "arrive_track" and tracks else self._planned_track(leg)
            operation, body = "train.arrived", {"movement_id": leg["to_movement_id"], "track_id": track}
        else:
            return "Åtgärden finns inte"
        try:
            self.service.execute_station_command(device, terminal.station, operation, body)
        except CommandRejected as error:
            return {"track_occupied": "Spåret är upptaget", "unknown_track": "Ankomstspåret är inte giltigt",
                    "channel_occupied": "Sträckan är upptagen", "departure_not_reserved": "Klartecken saknas",
                    "train_not_departed": "Tåget har inte avgått"}.get(error.reason, str(error) if error.reason.startswith("simulation_") else "Läget ändrades. Välj tåget igen.")
        self.refresh()
        if action == "accept":
            terminal.screen = "detail"
        if action in {"arrive", "arrive_track", "cancel", "reject"}:
            terminal.notice = f"{leg['train_number']} " + ("MOTTAGET" if action.startswith("arrive") else "ÅTERTAGET" if action == "cancel" else "NEKAT")
            terminal.notice_until = self.now() + 3
        return ""


class Terminal16Service:
    def __init__(self, service, *, now=monotonic):
        self.service, self.now = service, now
        self.views = None
        self.scope = None

    def _views(self, device):
        publication = self.service.publication()
        if publication is None:
            return None
        scope = (publication.publication_id, self.service.runtime_store.active_day(),
                 self.service.runtime_scope().get("meet_generation"),
                 self.service.simulation.run["id"] if self.service.simulation and self.service.simulation.active else None)
        if scope != self.scope:
            self.views, self.scope = RuntimeViews(self.service, now=self.now), scope
        views = self.views
        station = self.service.identities.station_for_client(device)
        if station not in views.engine.config.stations:
            views.terminals.pop(device, None)
            return None
        terminal = views.terminals.get(device)
        new_assignment = terminal is None or terminal.station != station
        if new_assignment:
            views.terminals[device] = Terminal(station)
            # A fresh assignment cannot accept a command from the old station.
            views.terminals[device].revision = (terminal.revision + 1) if terminal else 0
        views.refresh()
        terminal = views.terminals[device]
        language = self.service.device_ui(device)["language"]
        if terminal.language != language:
            terminal.language = language
            terminal.revision += 1
        if new_assignment and views._requests(terminal) and not terminal.notice:
            views._open_requests(terminal)
            terminal.revision += 1
        return views

    def frame(self, device):
        with self.service.operations_store.command_lock:
            views = self._views(device)
            if views:
                frame = views.frame(device)
                frame.update(profile="server-16x2", **self.service.runtime_scope())
                if self.service.simulation and self.service.simulation.active:
                    frame["simulation"] = True
                    frame["status"] = "SIMULERING · " + frame.get("status", "")
                return frame
            info = self.service.identities.discovered_device_or_none(device)
            language = self.service.device_ui(device)["language"]
            lines = [row(text(language, "VÄNTAR PÅ ADMIN")), row(info.device_code[:16] if info else text(language, "ANSLUTER"))]
            return {"profile": "server-16x2", "device_id": device, "rows": 2, "cols": 16,
                    "lines": lines, "lcd": encode_lcd(lines), "keys": {}, "entry": None,
                    "view_token": "", "status": "Administratören tilldelar station i Inställningar."}

    def command(self, device, body):
        with self.service.operations_store.command_lock:
            views = self._views(device)
            if views is None:
                return {"status": "rejected", "message": "Station är inte tilldelad", "frame": self.frame(device)}
            # SQL acts + persisted receipt in one commit. Rejects after a restart
            # use the new view epoch; lost responses can never perform twice.
            command_id = body.get("command_id")
            if not isinstance(command_id, str) or not 1 <= len(command_id) <= 80:
                return {"status": "rejected", "message": "Ogiltigt kommando-ID", "frame": self.frame(device)}
            cache_id = "terminal16:" + command_id
            signature = sha256(json.dumps([self.scope, views.terminals[device].station, body], sort_keys=True).encode()).hexdigest()
            cached = self.service.operations_store.device_command_response(device, cache_id)
            if cached:
                if cached.get("signature") != signature:
                    return {"status": "rejected", "message": "Kommando-ID har redan använts", "frame": self.frame(device)}
                return {"status": "duplicate", "message": "Redan behandlat", "frame": self.frame(device)}
            saved = deepcopy(views.terminals), deepcopy(views.processed)
            try:
                with self.service.operations_store.atomic_command():
                    result = views.command(device, body)
                    if result["status"] == "accepted":
                        self.service.operations_store.remember_device_command(device, cache_id, {"status": "accepted", "signature": signature})
            except BaseException:
                views.terminals, views.processed = saved
                views.fingerprint = None
                views.refresh()
                raise
            result["frame"] = self.frame(device)
            return result
