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
from .terminal16_glyphs import encode_lcd, GEOMETRIES
from .terminal16_i18n import text

LOGGER = logging.getLogger("tmbox_gateway.terminal16")


class RuntimeViews(Terminal16Lab):
    def __init__(self, service, *, now=monotonic):
        self.service = service
        self._states = {}
        publication = deepcopy(service.publication().payload)
        publication["meet"]["active_day"] = service.runtime_store.active_day() or service.publication().active_day
        # A read projection, NOT another traffic authority or persistent engine.
        engine = TrafficEngine(service.session_config())
        engine.set_clock_source(service.clock_source)
        super().__init__(engine, publication, {}, now=now)
        self.lock = service.operations_store.command_lock
        self.cases = {}
        # Told about every sound a box is given (cloud_live.py: push to the
        # box's phone and watch). Called under the traffic lock.
        self.on_attention = None
        self._previous_cases = {}
        self._departed = set()
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

    def _signal(self, terminal, kind, leg=None):
        super()._signal(terminal, kind, leg)
        listener = self.on_attention
        if listener is None or leg is None:
            return
        device = next((key for key, value in self.terminals.items() if value is terminal), None)
        if device is None:
            return
        case = self.cases.get(leg["from_movement_id"]) or self._previous_cases.get(leg["from_movement_id"])
        stations = self.engine.config.stations
        listener(device, kind, {
            "train_number": leg["train_number"], "from_station": stations[leg["from_station_id"]].code,
            "to_station": stations[leg["to_station_id"]].code,
            "request_id": case["clearance_id"] if case else None,
            "station": terminal.station, "side": terminal.side, "language": terminal.language,
        })

    def _automatic_here(self, terminal):
        automatic = self.service.automatic
        return bool(automatic and automatic.automatic_here(terminal.station))

    def _automatic_offered(self, terminal):
        automatic = self.service.automatic
        return bool(automatic and automatic.enabled())

    def _to_put_away(self, terminal):
        automatic = self.service.automatic
        if automatic is None:
            return []
        publication = self.service.publication()
        ends = automatic.ends_here(publication, self.day)
        stabled = automatic.stabled_movements(publication, self.day)
        return [key for key in self.completed
                if self.legs[key]["to_station_id"] == terminal.station
                and self.legs[key]["to_movement_id"] in ends and self.legs[key]["to_movement_id"] not in stabled
                and self._on_side(terminal, self.legs[key])]

    def _token(self, device, terminal):
        # Another box or TKL at the station switching to the automation
        # changes what # and * mean here: a press for the old picture is
        # refused instead of acting on the new one.
        mode = "automatic" if self._automatic_here(terminal) else "manned"
        return sha256(f"{super()._token(device, terminal)}:{mode}".encode()).hexdigest()[:24]

    def _actual_track(self, station, movement_id):
        """Spåret TKL, en box eller automatiken satt för rörelsen."""
        return (self._states.get(station, {}).get(movement_id) or {}).get("actualTrack")

    def _occupied(self, station, movement_id, track_id):
        """Står ett tåg på spåret på riktigt? Samma fråga som ankomsten ställer."""
        service = self.service
        return service.track_conflict(service.publication(), self.day, station, movement_id, track_id, actual=True)

    def refresh(self):
        service = self.service
        publication = service.publication()
        self.update_display_placement(publication.session_config(),
            service.runtime_store.display_placement_overrides(publication.meet_id))
        states = {station: service.operations_store.tkl_station_state(
            publication.publication_id, self.day, station)["movements"] for station in self.engine.config.stations}
        self._states = states
        cases = {case["movement_id"]: case for case in service.open_cases(None)
                 if case["movement_id"] in self.legs
                 and case["connection_id"] == self.legs[case["movement_id"]]["connection_id"]
                 and case["to_station_id"] == self.legs[case["movement_id"]]["to_station_id"]}
        fingerprint = sha256(json.dumps([states, cases], sort_keys=True).encode()).hexdigest()
        if fingerprint == self.fingerprint:
            return
        old_cases, old_completed = self.cases, self.completed
        old_departed = self._departed
        self.cases = cases
        self._previous_cases = old_cases
        self._departed = {key for key, case in cases.items() if service.case_departed(case)}
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
            # An answer is heard by the one who asked, never by the one who
            # gave it: the direction of the line says who did what.
            for key in cases.keys() & old_cases.keys():
                if old_cases[key]["status"] == "waiting" and cases[key]["status"] != "waiting":
                    self._notify_answer(self.legs[key], True)
            for key in self._departed - old_departed:
                self._notify_departure(self.legs[key])
            for key in old_cases.keys() - cases.keys() - self.completed:
                closed = service.operations_store.clearance(old_cases[key]["clearance_id"])
                if closed and closed["status"] == "rejected":
                    self._notify_answer(self.legs[key], False)
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
        if action == "stable":
            leg = self.legs[terminal.selected]
            try:
                automatic.stable(terminal.station, leg["to_movement_id"], by=device)
            except ValueError as error:
                return str(error)
            terminal.notice, terminal.notice_hint = "UNDANSTÄLLT", leg["train_number"]
            terminal.notice_until = self.now() + NOTICE_SECONDS
            terminal.screen, terminal.selected = "overview", None
            return ""
        leg = self.legs[terminal.selected]
        case = self.cases.get(terminal.selected)
        body = {}
        if action == "request":
            operation = "clearance.request"
            body = {"movement_id": leg["from_movement_id"], "connection_id": leg["connection_id"]}
        elif action in {"arrive", "arrive_track"}:
            # Also a train never sent in the system: it is placed here (2.1.0).
            tracks = self._track_choices(terminal)
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
            # Inne: spåret tåget kom in på, eller UPPT SPÅR om ett annat tåg
            # står där (Benny #197).
            arrived = self.engine.config.tracks.get(result.get("track_id") or "")
            terminal.notice = f"{leg['train_number']} " + (
                ("UPPT SPÅR" if result.get("track_occupied_by") is not None else
                 f"ANK SP{arrived.display_label}" if arrived else "MOTTAGET")
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
        # cloud_live.LiveChannel.on_attention when the watch channel is wired.
        self.on_attention = None

    def _views(self, device, *, refresh=True):
        publication = self.service.publication()
        if publication is None:
            return None
        scope = (publication.publication_id, self.service.runtime_store.active_day(),
                 self.service.runtime_scope().get("meet_generation"))
        if scope != self.scope:
            self.views, self.scope = RuntimeViews(self.service, now=self.now), scope
            self.views.on_attention = self._attention
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
        terminal = views.terminals[device]
        # The size the box announced in its hello: a 20x4 gets pictures for
        # 20x4, everything else the 16x2 it has always had.
        geometry = self._geometry(device)
        if (terminal.rows, terminal.cols) != geometry:
            terminal.rows, terminal.cols = geometry
            terminal.revision += 1
        if refresh:
            views.refresh()
        language = self.service.device_ui(device)["language"]
        if terminal.language != language:
            terminal.language = language
            terminal.revision += 1
        if new_assignment and views._requests(terminal) and not terminal.notice:
            views._open_requests(terminal)
            terminal.revision += 1
        return views

    def _attention(self, device, kind, info):
        if self.on_attention is not None:
            self.on_attention(device, kind, info)

    def observe(self, devices):
        """Keep these boxes in the picture although nobody asks for their frames.

        A phone whose app is closed does not poll, so nothing would notice a
        request for its station. The watch channel calls this about once a
        second for the boxes that registered for push; a new request, answer
        or departure then reaches them as a push, exactly as it would sound.
        """
        with self.service.operations_store.command_lock:
            views = None
            for device in devices:
                views = self._views(device, refresh=False) or views
            if views is not None:
                views.refresh()

    def _geometry(self, device):
        info = self.service.identities.discovered_device_or_none(device)
        geometry = (info.display.rows, info.display.cols) if info else (2, 16)
        return geometry if geometry in GEOMETRIES else (2, 16)

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
            rows, cols = self._geometry(device)
            lines = [row(text(language, "VÄNTAR PÅ ADMIN"), "", cols),
                     row(info.device_code[:cols] if info else text(language, "ANSLUTER"), "", cols)]
            lines += [" " * cols] * (rows - 2)
            return {"profile": "server-16x2", "device_id": device, "rows": rows, "cols": cols,
                    "lines": lines, "lcd": encode_lcd(lines), "keys": {}, "entry": None,
                    "view_token": "", "status": text(language, "Administratören tilldelar station i Inställningar.")}

    def timetable(self, device):
        """The station timetable beside the box; empty until a station is assigned."""
        with self.service.operations_store.command_lock:
            views = self._views(device)
            if views is None:
                return {"station": None, "side": None, "clock": None, "revision": 0, "tracks": [], "rows": []}
            result = views.station_timetable(device)
            # Stationens sparade spårvisning (#46). Bara presentation: tangenternas
            # spårval och trafiken använder fortfarande hela katalogen.
            publication = self.service.publication()
            if publication is not None:
                from . import track_display
                station = views.terminals[device].station
                saved = self.service.runtime_store.track_display(publication.meet_id, station)
                config = views.engine.config
                result["tracks"] = track_display.visible_labels(config, station, saved)
                result["track_display"] = track_display.describe(
                    config, station, saved, track_display.operating_points(publication.payload, station))
            return result

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
