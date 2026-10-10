"""Automatic stations in normal operation (issue #115).

A station nobody operates still has to answer and send trains, or a manned
neighbour waits for an answer that never comes. While the meet clock runs,
every station without a TMBox or TKL in use is automatic: it clears a
request when the planned track is free, asks for and reports the departure of
its own trains when they are ready, and reports an arrival once the
timetable's running time has passed since the departure.

The first box or TKL that works a station makes it manual. A manual station
whose operator loses contact waits for that operator, unless the
administrator has chosen that the automation takes over after some minutes
without contact. The administrator, and the station's own box, web TMBox,
iPhone or TKL, can leave the station to the automation ("going to the
toilet") and take it back; that choice stands until someone changes it, also
over a new traffic day. While the automation works a station, a traffic key
on its box or TKL first asks to take the station back (Casper, 2026-10-10).

Disturbances (extra station work, longer running times) and stabling at a
terminus are the administrator's choice under Settings. There is no separate
database: the automation acts on the meet's own traffic through the same
station commands as a box, under its own actor name, and the station service
refuses it on a manned station. (The traffic simulation, with its own run
database, was removed in Server 4.0; this is what remains of it.)
"""
from __future__ import annotations

import json
import logging
import math
from hashlib import sha256
from time import monotonic

from .operations import _time_to_seconds
from .protocol_v2 import CommandRejected
from .legs import PRESENCE_SECONDS, plan_legs

LOGGER = logging.getLogger("tmbox_gateway.automatic")

ACTOR = "automatik"
ENABLED_SETTING = "automatic_stations"
STATE_SETTING = "automatic_stations_state"
# A box and a TKL name their station by being assigned to it; an
# administrator or a TKL terminal names it in the request.
NAMED_STATION_KINDS = {"web_admin", "swift_admin", "tkl_terminal"}
REQUEST_LEAD_SECONDS = 120
# Störningar i automatiken (Casper 2026-10-08: förseningsdelarna ur
# simuleringen, som en del av automatiken, med en inställning i admin).
# (andel av tågen i procent, längsta extra minuter) som simuleringens profiler.
DISTURBANCE_SETTING = "automatic_disturbance"
DISTURBANCE_PROFILES = {"off": (0, 0), "normal": (25, 4), "disrupted": (65, 12)}
DISTURBANCE_PLACES = ("both", "station", "line")
DEFAULT_DISTURBANCE = {"profile": "off", "where": "both", "seed": "", "stabling": True, "stabling_minutes": 5}
# Automatiken tar över en bemannad station som varit utan kontakt så här många
# minuter (väggtid). 0 betyder aldrig: stationen väntar på sin operatör.
LOST_CONTACT_SETTING = "automatic_lost_contact_minutes"
LOST_CONTACT_CHOICES = (0, 2, 5, 10)
# Who left a station to the automation: an administrator, the station's own
# operator, or the automation itself after lost contact.
RELEASED_BY = ("admin", "operator", "lost_contact")


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
        # Contact is counted from when this server started: after a restart
        # nobody has been heard yet, and that is not minutes of silence.
        self.started = now()
        self.listeners: list = []
        self._ensure_events()
        service.automatic = self

    # ------------------------------------------------------------- settings

    def enabled(self) -> bool:
        return self.runtime._setting(ENABLED_SETTING) != "false"

    def set_enabled(self, enabled: bool) -> None:
        with self.store.command_lock:
            self.runtime._save_setting(ENABLED_SETTING, "true" if enabled else "false")

    def disturbance(self) -> dict:
        """Störningar och undanställning: admins val, annars inga störningar
        och undanställning efter fem spelminuter."""
        try:
            saved = json.loads(self.runtime._setting(DISTURBANCE_SETTING) or "{}")
        except ValueError:
            saved = {}
        return {**DEFAULT_DISTURBANCE, **{key: value for key, value in saved.items() if key in DEFAULT_DISTURBANCE}}

    def set_disturbance(self, values: dict) -> dict:
        settings = {**self.disturbance(), **{key: value for key, value in values.items() if key in DEFAULT_DISTURBANCE}}
        if settings["profile"] not in DISTURBANCE_PROFILES:
            raise ValueError("Välj Av, Normal eller Störd trafik.")
        if settings["where"] not in DISTURBANCE_PLACES:
            raise ValueError("Välj var störningarna sker.")
        if not isinstance(settings["seed"], str) or len(settings["seed"]) > 40:
            raise ValueError("Scenarionyckeln får vara högst 40 tecken.")
        if not isinstance(settings["stabling"], bool):
            raise ValueError("Undanställningen är på eller av.")
        if type(settings["stabling_minutes"]) is not int or not 1 <= settings["stabling_minutes"] <= 60:
            raise ValueError("Undanställningen sker efter 1–60 spelminuter.")
        with self.store.command_lock:
            self.runtime._save_setting(DISTURBANCE_SETTING, json.dumps(settings, ensure_ascii=False, sort_keys=True))
        return settings

    def lost_contact_minutes(self) -> int:
        try:
            minutes = int(self.runtime._setting(LOST_CONTACT_SETTING) or 0)
        except ValueError:
            return 0
        return minutes if minutes in LOST_CONTACT_CHOICES else 0

    def set_lost_contact_minutes(self, minutes) -> int:
        if type(minutes) is not int or minutes not in LOST_CONTACT_CHOICES:
            raise ValueError("Välj av, 2, 5 eller 10 minuter.")
        with self.store.command_lock:
            self.runtime._save_setting(LOST_CONTACT_SETTING, str(minutes))
        return minutes

    def _delay(self, kind: str, key: str) -> float:
        """Extra spelsekunder för ett tåg: stationsarbete före avgången
        ("station") eller längre gångtid före ankomsten ("line"). Samma nyckel
        ger samma störningar, som i simuleringen."""
        settings = getattr(self, "_settings", None) or DEFAULT_DISTURBANCE
        chance, limit = DISTURBANCE_PROFILES.get(settings["profile"], (0, 0))
        if not limit or settings["where"] not in (kind, "both"):
            return 0.0
        value = int(sha256(f"{getattr(self, '_seed', '')}:{kind}:{key}".encode()).hexdigest()[:12], 16)
        return float((1 + value // 100 % limit) * 60) if value % 100 < chance else 0.0

    def stabled_movements(self, publication, day) -> set:
        """Tåg som ställts undan vid en automatisk slutstation: de håller
        inte längre sitt spår."""
        return set(self._state(publication, day).get("stabled", {}))

    def running(self) -> bool:
        return self.enabled()

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
        # Stations left to the automation, with who did it. Older states
        # had none: a handed-back station was only an owner removed.
        state.setdefault("automatic", {})
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
            if device in state["suppressed"] or assigned in state["stations"] or assigned in state["automatic"]:
                return
            state["stations"][assigned] = device
            self._save(state)

    def mode(self, station, state=None) -> str:
        if state is None:
            publication, day = self._context()
            if publication is None:
                return "automatic"
            state = self._state(publication, day)
        if station in state.get("automatic", {}):
            return "automatic"
        owner = state["stations"].get(station)
        if not owner:
            return "automatic"
        # The owner is the first client that worked the station. Another box
        # or TKL there still works it when the owner is gone.
        return "manual" if self.now() - self._last_heard(station) <= PRESENCE_SECONDS else "disconnected"

    def _last_heard(self, station) -> float:
        return max((seen for assigned, seen in self.seen.values() if assigned == station), default=-math.inf)

    def automatic_here(self, station) -> bool:
        """What a box or TKL at the station goes by: the automation is on and
        someone left this station to it. A station nobody has worked yet is
        automatic too, but the first box or TKL there simply mans it."""
        if not self.enabled():
            return False
        publication, day = self._context()
        return publication is not None and station in self._state(publication, day)["automatic"]

    def hand_back(self, station, *, by="admin", device=None) -> None:
        """The station is automatic until someone takes it back: the
        administrator, its own operator ("going to the toilet") or, after
        lost contact, the automation itself.

        Checked now, written after the caller's traffic transaction: a box's
        key press runs inside one on the operations database, and this state
        lives in the runtime database, a second connection to the same file.
        """
        if by not in RELEASED_BY:
            raise ValueError("Okänt skäl")
        with self.store.command_lock:
            self._require_station(station)
            if by == "operator":
                if not self.enabled():
                    raise ValueError("Automatiken är avstängd av administratören.")
                self._require_present(station, device)
        self.store.after_commit(lambda: self._release(station, by, device))

    def _release(self, station, by, device) -> None:
        with self.store.command_lock:
            publication, day = self._context()
            if publication is None:
                return
            state = self._state(publication, day)
            if station in state["automatic"]:
                return
            owner = state["stations"].pop(station, None)
            for seen_device, (assigned, _) in self.seen.items():
                if assigned == station and seen_device not in state["suppressed"]:
                    state["suppressed"].append(seen_device)
            if owner and owner not in state["suppressed"]:
                state["suppressed"].append(owner)
            state["automatic"][station] = {"by": by, "device": device or owner}
            self._save(state)
            self._audit(publication, "automatic.station_released", station, device or owner or by, {"by": by})
        self._changed()

    def take_over(self, station, device, *, by="admin") -> None:
        """A connected box or TKL at the station works it again: chosen by
        the administrator, or the operator there taking it back. Written
        after the caller's transaction, like hand_back."""
        with self.store.command_lock:
            self._require_station(station)
            self._require_present(station, device)
        self.store.after_commit(lambda: self._take(station, device, by))

    def _take(self, station, device, by) -> None:
        with self.store.command_lock:
            publication, day = self._context()
            if publication is None:
                return
            state = self._state(publication, day)
            # Every client at the station works it normally again.
            state["suppressed"] = [other for other in state["suppressed"]
                                   if other != device and self.seen.get(other, (None, 0))[0] != station]
            state["automatic"].pop(station, None)
            state["stations"][station] = device
            self._save(state)
            self._audit(publication, "automatic.station_taken_back", station, device, {"by": by})
        self._changed()

    def _require_present(self, station, device) -> None:
        assigned, seen = self.seen.get(device, (None, -math.inf))
        if not device or assigned != station or self.now() - seen > PRESENCE_SECONDS:
            raise ValueError("Klienten måste vara ansluten till stationen.")

    def _audit(self, publication, action, station, actor, detail) -> None:
        self.store.record_audit_event(correlation_id=f"automatic-{publication.publication_id}", source="automatic",
            actor=str(actor), action=action, outcome="accepted", station_id=station, detail=detail)

    def _changed(self) -> None:
        """Boxes redraw (AUTOMATIK on the start screen) and the pages hear it."""
        self.service.notify_changed()
        for listener in self.listeners:
            try:
                listener()
            except Exception:
                LOGGER.exception("Kunde inte meddela ändrat automatikläge")

    def _take_lost_stations(self, state) -> list:
        """Stations whose operator has been out of contact longer than the
        administrator allows go to the automation. Called under the lock."""
        minutes = self.lost_contact_minutes()
        if not minutes:
            return []
        taken = []
        for station, owner in sorted(state["stations"].items()):
            if station in state["automatic"]:
                continue
            if self.now() - max(self._last_heard(station), self.started) >= minutes * 60:
                taken.append((station, owner))
        return taken

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

    def new_day(self, publication, day: str, start_seconds: float) -> None:
        """En ny trafikdag (midnatt eller tidsmaskinen): automatiken börjar om
        från `start_seconds` för den nya dagens tåg. Vem som arbetar var står
        kvar. Dagens tider tas bort av operations.start_traffic_day. Körs mitt
        i övergången, när ingen träff räknas som vald: därför anges den här."""
        try:
            saved = json.loads(self.runtime._setting(STATE_SETTING) or "{}")
        except ValueError:
            saved = {}
        # A station left to the automation stays so over the new day, until
        # someone takes it back; before, a box still there took it again.
        state = {"key": [publication.publication_id, day], "stations": saved.get("stations", {}),
                 "suppressed": [], "start": float(start_seconds), "automatic": saved.get("automatic", {})}
        self._save(state)
        self.blocked.clear()

    # ------------------------------------------------------------- traffic

    def guard(self, actor, station) -> None:
        if actor == ACTOR:
            if self.mode(station) != "automatic":
                raise CommandRejected("automatic_station_manned", "Stationen är bemannad")
            return
        # A box or TKL at a station the automation works asks first: the
        # operator takes the station back, then acts (Casper, 2026-10-10).
        client = self.service.identities.client(actor)
        if client is not None and client.kind.value not in {"web_admin", "swift_admin"} and self.automatic_here(station):
            raise CommandRejected("station_automatic", "Automatiken sköter stationen. Ta tillbaka den först.")

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
            for station, owner in self._take_lost_stations(self._state(publication, day)):
                self.hand_back(station, by="lost_contact", device=owner)
            state = self._state(publication, day)
            if state["start"] is None:
                # Trains planned before the automation first ran are history,
                # not a queue to send all at once.
                state["start"] = seconds
                self._save(state)
            legs = self.legs(publication, day)
            if not legs:
                return
            self._settings = self.disturbance()
            self._seed = self._settings["seed"] or f"{publication.publication_id}:{day}"
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
            if self._settings["stabling"]:
                self._stable(publication, day, legs, live, times, seconds)

    def _stable(self, publication, day, legs, live, times, seconds) -> None:
        """Undanställning vid en automatisk slutstation: ett tåg som har
        kommit fram och slutar där rangeras bort efter några spelminuter, så
        att spåret blir fritt för nästa tåg. Utan det håller tåget sitt spår
        resten av dagen, och mottagaren säger "Mottagningsspåret är upptaget".
        Ingen avgång hittas på; ankomsten står kvar i historiken."""
        rows = {str(row["id"]): row for row in publication.payload["trains"]}
        state = self._state(publication, day)
        stabled = state.setdefault("stabled", {})
        wait = self._settings["stabling_minutes"] * 60
        changed = False
        for leg in legs.values():
            movement, station = leg["to_movement_id"], leg["to_station_id"]
            if movement in stabled or rows.get(movement, {}).get("departure_time"):
                continue
            if self.mode(station, state) != "automatic" or live[station].get(movement, {}).get("arrival") != "arrived":
                continue
            arrived = times.get(("train.arrived", movement), leg["arrival"])
            if seconds < arrived + wait:
                continue
            stabled[movement] = seconds
            changed = True
            self.store.record_audit_event(correlation_id=f"automatic-{publication.publication_id}", source="automatic",
                actor=ACTOR, action="automatic.stabled", outcome="accepted", station_id=station, movement_id=movement,
                detail={"game_seconds": seconds, "description": "Rangerat till uppställning utanför trafikspåren"})
        if changed:
            self._save(state)
            self.store.after_commit(self.service.notify_changed)

    def _step(self, key, leg, legs, seconds, state, times, live, cases, publication, day):
        sender, receiver = leg["from_station_id"], leg["to_station_id"]
        if live[receiver].get(leg["to_movement_id"], {}).get("arrival") == "arrived":
            return ""
        if live[sender].get(key, {}).get("departure") == "departed":
            if self.mode(receiver, state) != "automatic":
                return "Väntar på mottagarens ankomst"
            departed = times.get(("train.departed", key), leg["departure"])
            if seconds < departed + leg["duration"] + self._delay("line", key):
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
            # it has actually come in here, or stands here already: after a
            # reset or a new clock time the timetable puts it here (#136).
            if (leg["previous"] is None and leg["departure"] < state["start"]
                    and live[sender].get(key, {}).get("departure", "none") == "none"):
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
        if automatic_sender and case and case["status"] == "approved" and seconds >= leg["departure"]:
            return "Stationsarbete pågår"
        if case and case["status"] == "waiting":
            return "Väntar på klartecken"
        return ""

    def _ready_at(self, key, leg, legs, live, times):
        previous = leg["previous"]
        if previous is None:
            return leg["departure"] + self._delay("station", key)
        prior = legs[previous]
        if live[prior["to_station_id"]].get(prior["to_movement_id"], {}).get("arrival") != "arrived":
            return math.inf
        arrived = times.get(("train.arrived", prior["to_movement_id"]), prior["arrival"])
        return max(leg["departure"], arrived + leg["dwell"]) + self._delay("station", key)

    def _act(self, station, action, **body):
        self._acted = True
        return self.service.execute_station_command(ACTOR, station, action, body)

    # ------------------------------------------------------------- status

    def status(self) -> dict:
        with self.store.command_lock:
            publication, day = self._context()
            result = {"enabled": self.enabled(), "lost_contact_minutes": self.lost_contact_minutes(),
                      "stations": [], "trains": [], "plan_errors": [], "disturbance": self.disturbance()}
            if publication is None:
                return result
            state = self._state(publication, day)
            legs = self.legs(publication, day)
            result["plan_errors"] = list(self._plan_errors[:12])
            result["stations"] = [{
                "id": station.id, "name": station.name, "code": station.code,
                "mode": self.mode(station.id, state),
                "released_by": state["automatic"].get(station.id, {}).get("by"),
                "operator": state["stations"].get(station.id),
                "available_operators": sorted(device for device, (assigned, seen) in self.seen.items()
                                              if assigned == station.id and self.now() - seen <= PRESENCE_SECONDS),
            } for station in publication.session_config().stations.values()]
            result["trains"] = [{"movement_id": key, "train_number": legs[key]["train_number"],
                                 "from_station_id": legs[key]["from_station_id"],
                                 "to_station_id": legs[key]["to_station_id"], "reason": reason}
                                for key, reason in sorted(self.blocked.items()) if key in legs]
            return result
