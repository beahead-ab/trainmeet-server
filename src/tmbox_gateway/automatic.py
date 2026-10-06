"""Automatic stations in normal operation (issue #115).

A station nobody operates still has to answer and send trains, or a manned
neighbour waits for an answer that never comes. While the meet clock runs,
every station without a TMBox or TKL in use is automatic and follows the
simulator's rules on the real traffic: it clears a request when the planned
track is free, asks for and reports the departure of its own trains when they
are ready, and reports an arrival once the timetable's running time has
passed since the departure.

The first box or TKL that works a station makes it manual. A manual station
whose operator loses contact waits for that operator; the automation never
takes a station back by itself. An administrator can hand it back.

This is not the simulator: no separate database, no invented delays, no
yard work. The automation acts through the same station commands as a box,
under its own actor name, and the station service refuses it on a manned
station.
"""
from __future__ import annotations

import json
import logging
import math
from time import monotonic

from .operations import _time_to_seconds
from .protocol_v2 import CommandRejected
from .simulation import PRESENCE_SECONDS, plan_legs

LOGGER = logging.getLogger("tmbox_gateway.automatic")

ACTOR = "automatik"
ENABLED_SETTING = "automatic_stations"
STATE_SETTING = "automatic_stations_state"
# A box and a TKL name their station by being assigned to it; an
# administrator or a TKL terminal names it in the request.
NAMED_STATION_KINDS = {"web_admin", "swift_admin", "tkl_terminal"}
REQUEST_LEAD_SECONDS = 120


class AutomaticStations:
    def __init__(self, service, *, now=monotonic):
        self.service = service
        self.store = service.operations_store
        self.runtime = service.runtime_store
        self.now = now
        self.seen: dict[str, tuple[str, float]] = {}
        self.blocked: dict[str, str] = {}
        self._plan_key = None
        self._legs: dict[str, dict] = {}
        self._plan_errors: list[str] = []
        self._acted = False
        self._ensure_events()
        service.automatic = self

    # ------------------------------------------------------------- settings

    def enabled(self) -> bool:
        return self.runtime._setting(ENABLED_SETTING) != "false"

    def set_enabled(self, enabled: bool) -> None:
        with self.store.command_lock:
            self.runtime._save_setting(ENABLED_SETTING, "true" if enabled else "false")

    def running(self) -> bool:
        """Automatic stations act only in normal operation, never in a simulation."""
        simulation = self.service.simulation
        return self.enabled() and not (simulation and simulation.active)

    def _context(self):
        publication = self.service.publication()
        if publication is None:
            return None, None
        return publication, self.runtime.active_day() or publication.active_day

    def _state(self, publication, day) -> dict:
        """Who works which station, kept per publication and traffic day."""
        try:
            state = json.loads(self.runtime._setting(STATE_SETTING) or "{}")
        except ValueError:
            state = {}
        key = [publication.publication_id, day]
        if state.get("key") != key:
            state = {"key": key, "stations": {}, "suppressed": [], "start": None}
        return state

    def _save(self, state) -> None:
        self.runtime._save_setting(STATE_SETTING, json.dumps(state, ensure_ascii=False, sort_keys=True))

    # ------------------------------------------------------------- manning

    def observe(self, device, station=None) -> None:
        """A box or TKL at work: its station is manned from now on."""
        client = self.service.identities.client(device)
        if client is None:
            return
        if client.kind.value in NAMED_STATION_KINDS:
            assigned = station
        else:
            assigned = self.service.identities.station_for_client(device)
            if station is not None and assigned != station:
                return
        if not assigned:
            return
        with self.store.command_lock:
            self.seen[device] = (assigned, self.now())
            publication, day = self._context()
            if publication is None or assigned not in publication.session_config().stations:
                return
            state = self._state(publication, day)
            if device in state["suppressed"] or assigned in state["stations"]:
                return
            state["stations"][assigned] = device
            self._save(state)

    def mode(self, station, state=None) -> str:
        if state is None:
            publication, day = self._context()
            if publication is None:
                return "automatic"
            state = self._state(publication, day)
        owner = state["stations"].get(station)
        if not owner:
            return "automatic"
        assigned, seen = self.seen.get(owner, (None, -math.inf))
        return "manual" if assigned == station and self.now() - seen <= PRESENCE_SECONDS else "disconnected"

    def hand_back(self, station) -> None:
        """Admin: the station is automatic again until someone takes it over."""
        with self.store.command_lock:
            publication, day = self._require_station(station)
            state = self._state(publication, day)
            owner = state["stations"].pop(station, None)
            for device, (assigned, _) in self.seen.items():
                if assigned == station and device not in state["suppressed"]:
                    state["suppressed"].append(device)
            if owner and owner not in state["suppressed"]:
                state["suppressed"].append(owner)
            self._save(state)

    def take_over(self, station, device) -> None:
        """Admin: a connected box or TKL at the station works it again."""
        with self.store.command_lock:
            publication, day = self._require_station(station)
            assigned, seen = self.seen.get(device, (None, -math.inf))
            if assigned != station or self.now() - seen > PRESENCE_SECONDS:
                raise ValueError("Klienten måste vara ansluten till stationen.")
            state = self._state(publication, day)
            if device in state["suppressed"]:
                state["suppressed"].remove(device)
            state["stations"][station] = device
            self._save(state)

    def _require_station(self, station):
        publication, day = self._context()
        if publication is None:
            raise ValueError("Ingen träff är vald.")
        if station not in publication.session_config().stations:
            raise ValueError("Okänd station")
        return publication, day

    def forget_meet(self) -> None:
        """Träffen nollställdes: vem som arbetar var och när automatiken
        började räknas om från början. Händelserna tas bort av
        operations.reset_meet i samma databas."""
        self.runtime._save_setting(STATE_SETTING, "{}")
        self.blocked.clear()
        self._acted = False

    # ------------------------------------------------------------- traffic

    def guard(self, actor, station) -> None:
        if actor == ACTOR and self.mode(station) != "automatic":
            raise CommandRejected("automatic_station_manned", "Stationen är bemannad")

    def _ensure_events(self) -> None:
        self.store._connection.execute(
            "CREATE TABLE IF NOT EXISTS automatic_events(publication_id TEXT NOT NULL, day TEXT NOT NULL, "
            "action TEXT NOT NULL, movement_id TEXT NOT NULL, seconds REAL NOT NULL, "
            "PRIMARY KEY(publication_id, day, action, movement_id))")

    def record_action(self, action, movement_id) -> None:
        """The game time of a departure or an arrival, whoever reported it.

        Written in the same transaction as the traffic act. An arrival is due
        a running time after the departure, so the time has to be known.
        """
        if action not in {"train.departed", "train.arrived"} or not self.running():
            return
        publication, day = self._context()
        if publication is None:
            return
        self._ensure_events()
        self.store._connection.execute(
            "INSERT OR IGNORE INTO automatic_events VALUES(?,?,?,?,?)",
            (publication.publication_id, day, action, movement_id, self._seconds(self.store.clock_status())))

    @staticmethod
    def _seconds(clock) -> float:
        value = clock.get("elapsed_seconds")
        return float(value) if value is not None else float(_time_to_seconds(str(clock.get("time") or "00:00:00")))

    def legs(self, publication, day):
        key = (publication.publication_id, day, self.service.config_version())
        if key != self._plan_key:
            self._legs, self._plan_errors = plan_legs(publication, day)
            self._plan_key = key
        return self._legs

    def tick(self) -> None:
        with self.store.command_lock:
            if not self.running():
                return
            publication, day = self._context()
            if publication is None:
                return
            clock = self.store.clock_status()
            if not clock.get("running"):
                return
            seconds = self._seconds(clock)
            state = self._state(publication, day)
            if state["start"] is None:
                # Trains planned before the automation first ran are history,
                # not a queue to send all at once.
                state["start"] = seconds
                self._save(state)
            legs = self.legs(publication, day)
            if not legs:
                return
            self._ensure_events()
            times = {(action, movement): when for action, movement, when in self.store._connection.execute(
                "SELECT action, movement_id, seconds FROM automatic_events WHERE publication_id=? AND day=?",
                (publication.publication_id, day))}
            def read():
                live = {station: self.store.tkl_station_state(publication.publication_id, day, station)["movements"]
                        for station in publication.session_config().stations}
                cases = {}
                for case in self.service.open_cases(None):
                    cases.setdefault(case["movement_id"], case)
                return live, cases
            live, cases = read()
            blocked = {}
            for key, leg in sorted(legs.items(), key=lambda item: (item[1]["departure"], item[0])):
                self._acted = False
                try:
                    with self.store.atomic_command():
                        reason = self._step(key, leg, legs, seconds, state, times, live, cases, publication, day)
                    if reason:
                        blocked[key] = reason
                except CommandRejected as error:
                    blocked[key] = error.reason
                if self._acted:
                    # What one train did changes what the next one sees.
                    live, cases = read()
            self.blocked = blocked

    def _step(self, key, leg, legs, seconds, state, times, live, cases, publication, day):
        sender, receiver = leg["from_station_id"], leg["to_station_id"]
        if live[receiver].get(leg["to_movement_id"], {}).get("arrival") == "arrived":
            return ""
        if live[sender].get(key, {}).get("departure") == "departed":
            if self.mode(receiver, state) != "automatic":
                return "Väntar på mottagarens ankomst"
            departed = times.get(("train.departed", key), leg["departure"])
            if seconds < departed + leg["duration"]:
                return "På väg"
            self._act(receiver, "train.arrived", movement_id=leg["to_movement_id"], track_id=leg["track_id"])
            return ""
        case = cases.get(key)
        if case is not None and case["from_station_id"] != sender:
            case = None
        ready = self._ready_at(key, leg, legs, live, times)
        automatic_sender = self.mode(sender, state) == "automatic"
        if case is None and automatic_sender:
            if math.isinf(ready):
                return "Väntar på föregående ankomst"
            # A train planned before the automation started is sent only once
            # it has actually come in here.
            if leg["previous"] is None and leg["departure"] < state["start"]:
                return ""
            if seconds < ready - REQUEST_LEAD_SECONDS:
                return ""
            # A refusal is a decision, not an invitation to ask again.
            refused = self.store._connection.execute(
                "SELECT 1 FROM clearances WHERE movement_id=? AND status='rejected'", (key,)).fetchone()
            if refused:
                return "Begäran nekad – kräver operatörsåtgärd"
            if live[sender].get(key, {}).get("departure", "none") == "none":
                self._act(sender, "train.position.set", movement_id=key)
            result = self._act(sender, "clearance.request", movement_id=key, connection_id=leg["connection_id"])
            case = self.store.clearance(result["revision"]["key"])
        if case and case["status"] == "waiting" and self.mode(receiver, state) == "automatic":
            if self.service.track_conflict(publication, day, receiver, leg["to_movement_id"], leg["track_id"], actual=True):
                return "Mottagningsspåret är upptaget"
            self._act(receiver, "clearance.response", clearance_id=case["clearance_id"], approved=True)
            case = self.store.clearance(case["clearance_id"])
        if automatic_sender and case and case["status"] == "approved" and seconds >= ready:
            self._act(sender, "train.departed", movement_id=key)
            return ""
        if case and case["status"] == "waiting":
            return "Väntar på klartecken"
        return ""

    def _ready_at(self, key, leg, legs, live, times):
        previous = leg["previous"]
        if previous is None:
            return leg["departure"]
        prior = legs[previous]
        if live[prior["to_station_id"]].get(prior["to_movement_id"], {}).get("arrival") != "arrived":
            return math.inf
        arrived = times.get(("train.arrived", prior["to_movement_id"]), prior["arrival"])
        return max(leg["departure"], arrived + leg["dwell"])

    def _act(self, station, action, **body):
        self._acted = True
        return self.service.execute_station_command(ACTOR, station, action, body)

    # ------------------------------------------------------------- status

    def status(self) -> dict:
        with self.store.command_lock:
            publication, day = self._context()
            simulation = self.service.simulation
            result = {"enabled": self.enabled(), "simulation": bool(simulation and simulation.active),
                      "stations": [], "trains": [], "plan_errors": []}
            if publication is None:
                return result
            state = self._state(publication, day)
            legs = self.legs(publication, day)
            result["plan_errors"] = list(self._plan_errors[:12])
            result["stations"] = [{
                "id": station.id, "name": station.name, "code": station.code,
                "mode": self.mode(station.id, state),
                "operator": state["stations"].get(station.id),
                "available_operators": sorted(device for device, (assigned, seen) in self.seen.items()
                                              if assigned == station.id and self.now() - seen <= PRESENCE_SECONDS),
            } for station in publication.session_config().stations.values()]
            result["trains"] = [{"movement_id": key, "train_number": legs[key]["train_number"],
                                 "from_station_id": legs[key]["from_station_id"],
                                 "to_station_id": legs[key]["to_station_id"], "reason": reason}
                                for key, reason in sorted(self.blocked.items()) if key in legs]
            return result
