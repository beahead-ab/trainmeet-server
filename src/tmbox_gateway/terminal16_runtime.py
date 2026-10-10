"""The 16x2 interaction profile over the persistent, shared station service.

Only selection and presentation are ephemeral. Cases, arrival tracks, train
positions and duplicate receipts live in the same transaction/store as TKL.
The isolated lab and real terminals share the view/key state machine, never
each other's data. No device is allowed to choose its station.
"""
from copy import deepcopy
from hashlib import sha256
import json
import logging
from time import monotonic

from .engine import TrafficEngine
from .models import ConnectionRuntime, ConnectionState
from .protocol_v2 import CommandRejected
from .terminal16 import NOTICE_SECONDS, Terminal, Terminal16Lab, row
from .terminal16_glyphs import encode_lcd
from .terminal16_i18n import text

LOGGER = logging.getLogger("tmbox_gateway.terminal16")


class RuntimeViews(Terminal16Lab):
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

    def _automatic_here(self, terminal):
        automatic = self.service.automatic
        return bool(automatic and automatic.automatic_here(terminal.station))

    def _automatic_offered(self, terminal):
        automatic = self.service.automatic
        return bool(automatic and automatic.enabled())

    def _token(self, device, terminal):
        # Another box or TKL at the station switching to the automation
        # changes what # and * mean here: a press for the old picture is
        # refused instead of acting on the new one.
        mode = "automatic" if self._automatic_here(terminal) else "manned"
        return sha256(f"{super()._token(device, terminal)}:{mode}".encode()).hexdigest()[:24]

    def refresh(self):
        service = self.service
        publication = service.publication()
        self.update_display_placement(publication.session_config(),
            service.runtime_store.display_placement_overrides(publication.meet_id))
        states = {station: service.operations_store.tkl_station_state(
            publication.publication_id, self.day, station)["movements"] for station in self.engine.config.stations}
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
                            terminal.notice_hint = self.engine.config.stations[self.legs[key]["to_station_id"]].code
                            terminal.notice_until = self.now() + NOTICE_SECONDS
                            terminal.revision += 1
                elif closed and closed["status"] == "cancelled":
                    # Taken back by the sender, by its box or by TKL; an arrival
                    # also closes its case but is in self.completed.
                    self._notify_withdrawn(self.legs[key], key)
        self.engine.revision += 1
        self.fingerprint = fingerprint

    def _traffic(self, device, terminal, action):
        automatic = self.service.automatic
        if action in {"automatic_on", "take_back"}:
            try:
                if action == "automatic_on":
                    automatic.hand_back(terminal.station, by="operator", device=device)
                else:
                    automatic.take_over(terminal.station, device, by="operator")
            except ValueError as error:
                return str(error)
            terminal.screen = (terminal.return_screen if action == "take_back" and terminal.selected in self.legs
                               else "overview")
            return ""
        if automatic and automatic.automatic_here(terminal.station):
            # Ask first: the operator takes the station back, then acts.
            terminal.return_screen, terminal.screen = terminal.screen, "takeback"
            return ""
        leg = self.legs[terminal.selected]
        case = self.cases.get(terminal.selected)
        body = {}
        if action == "request":
            operation = "clearance.request"
            body = {"movement_id": leg["from_movement_id"], "connection_id": leg["connection_id"]}
        elif action in {"arrive", "arrive_track"}:
            # Also a train never sent in the system: it is placed here (2.1.0).
            tracks = self._tracks(terminal)
            track = tracks[terminal.track % len(tracks)].id if action == "arrive_track" and tracks else self._planned_track(leg)
            operation, body = "train.arrived", {"movement_id": leg["to_movement_id"], "track_id": track}
        elif case is None:
            return "Läget ändrades. Välj tåget igen."
        elif action in {"accept", "reject", "cancel"}:
            operation = "clearance.cancel" if action == "cancel" else "clearance.response"
            body = {"clearance_id": case["clearance_id"], "approved": action == "accept"}
        elif action == "depart":
            operation, body = "train.departed", {"movement_id": leg["from_movement_id"]}
        else:
            return "Åtgärden finns inte"
        try:
            result = self.service.execute_station_command(device, terminal.station, operation, body) or {}
        except CommandRejected as error:
            return {"track_occupied": "Spåret är upptaget", "unknown_track": "Ankomstspåret är inte giltigt",
                    "channel_occupied": "Sträckan är upptagen", "departure_not_reserved": "Klartecken saknas",
                    "train_not_departed": "Tåget har inte avgått"}.get(error.reason, "Läget ändrades. Välj tåget igen.")
        self.refresh()
        if action == "accept":
            terminal.screen = "detail"
        if action in {"arrive", "arrive_track", "cancel", "reject"}:
            terminal.notice = f"{leg['train_number']} " + (("UPPT SPÅR" if result.get("track_occupied_by") is not None else "MOTTAGET")
                                                          if action.startswith("arrive") else "ÅTERTAGET" if action == "cancel" else "NEKAT")
            other = leg["to_station_id"] if action == "cancel" else leg["from_station_id"]
            terminal.notice_hint = self.engine.config.stations[other].code
            terminal.notice_until = self.now() + NOTICE_SECONDS
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
                 self.service.runtime_scope().get("meet_generation"))
        if scope != self.scope:
            self.views, self.scope = RuntimeViews(self.service, now=self.now), scope
        views = self.views
        station = self.service.identities.station_for_client(device)
        if station not in views.engine.config.stations:
            views.terminals.pop(device, None)
            return None
        side = self.service.identities.station_side_for_client(device)
        terminal = views.terminals.get(device)
        new_assignment = terminal is None or terminal.station != station or terminal.side != side
        if new_assignment:
            views.terminals[device] = Terminal(station, side=side)
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
                # The iPhone shows AUTOMATIK beside the box; older apps ignore it.
                frame.update(profile="server-16x2", **self.service.runtime_scope(),
                             station_mode="automatic" if views._automatic_here(views.terminals[device]) else "manned")
                return frame
            info = self.service.identities.discovered_device_or_none(device)
            language = self.service.device_ui(device)["language"]
            lines = [row(text(language, "VÄNTAR PÅ ADMIN")), row(info.device_code[:16] if info else text(language, "ANSLUTER"))]
            return {"profile": "server-16x2", "device_id": device, "rows": 2, "cols": 16,
                    "lines": lines, "lcd": encode_lcd(lines), "keys": {}, "entry": None,
                    "view_token": "", "status": text(language, "Administratören tilldelar station i Inställningar.")}

    def timetable(self, device):
        """The station timetable beside the box; empty until a station is assigned."""
        with self.service.operations_store.command_lock:
            views = self._views(device)
            if views is None:
                return {"station": None, "side": None, "clock": None, "revision": 0, "rows": []}
            return views.station_timetable(device)

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
            except Exception:
                views.terminals, views.processed = saved
                views.fingerprint = None
                views.refresh()
                # A box that gets no answer waits, then gives up after 30 s
                # (INGET SVAR). A no with the current screen is the honest
                # answer; nothing was changed. Until 2.0.2 a too long notice
                # (FLERA TÅG - ADMIN) left the box waiting like this.
                LOGGER.exception("TMBox-kommandot kunde inte hanteras: %s", device)
                return {"status": "rejected", "message": "Serverfel. Försök igen.", "frame": self.frame(device)}
            except BaseException:
                views.terminals, views.processed = saved
                views.fingerprint = None
                views.refresh()
                raise
            result["frame"] = self.frame(device)
            return result
