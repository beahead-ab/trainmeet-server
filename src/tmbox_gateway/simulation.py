"""One isolated EU operating run, with automatic operators, not another engine.

All mutations run under the station command lock. The normal operations
connection stays open and untouched while adapters use a separate run database.
Assignment/Cloud configuration remain server-owned; a device name grants nothing.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
import sqlite3
from time import monotonic
from uuid import uuid4

from .operations import SQLiteOperationsStore, _time_to_seconds
from .protocol_v2 import CommandRejected
from .runtime import matches_active_day
from .train_routes import resolve_departure, RouteResolutionError


class SimulationError(ValueError):
    pass


PROFILES = {"timetable": (0, 0), "normal": (25, 4), "disrupted": (65, 12)}
PRESENCE_SECONDS = 45


def build_plan(publication, day):
    """Resolve visits first. Never infer a route from a train number alone."""
    payload = publication.payload
    movements = {str(m["id"]): m for m in payload["trains"] if matches_active_day(m["days"], day)}
    times = {}
    errors = []
    for service in payload.get("services", []):
        if not matches_active_day(service["days"], day):
            continue
        previous = -1
        for stop in sorted(service["stops"], key=lambda s: s["stop_order"]):
            offset = int(stop.get("service_day_offset") or 0) * 86400
            arrival = _time_to_seconds(stop["arrival_time"]) + offset if stop.get("arrival_time") else None
            departure = _time_to_seconds(stop["departure_time"]) + offset if stop.get("departure_time") else None
            # A visit spanning midnight has one day offset, but two clock times.
            if arrival is not None and departure is not None and departure < arrival:
                departure += 86400
            first = arrival if arrival is not None else departure
            if first is None or first < previous:
                errors.append(f"Tåg {service['train_number']}: otydligt dygn eller besöksordning.")
            previous = departure if departure is not None else (arrival or 0)
            times[(service["id"], int(stop["stop_order"]))] = (arrival, departure)
    legs = {}
    for key, movement in movements.items():
        if not movement.get("departure_time"):
            continue
        try:
            leg = resolve_departure(payload, day, movement["station_id"], key)
            _, departure = times[(leg["service_id"], leg["from_stop_order"])]
            arrival, _ = times[(leg["service_id"], leg["to_stop_order"])]
            if departure is None or arrival is None or not 0 < arrival - departure <= 86400:
                raise ValueError("Restid saknas eller är orimlig")
            target = movements[leg["to_movement_id"]]
            for row in (movement, target):
                track = publication.track_catalogue().get(row.get("track_id"))
                if not track or not track.active or track.station_id != row["station_id"]:
                    raise ValueError("Aktivt planerat spår saknas")
            legs[key] = {**leg, "departure": departure, "arrival": arrival,
                         "duration": arrival - departure, "track_id": target["track_id"]}
        except (RouteResolutionError, KeyError, ValueError) as error:
            errors.append(f"Tåg {movement['train_number']} ({key}): {error}")
    incoming = {leg["to_movement_id"]: key for key, leg in legs.items()}
    for key, movement in movements.items():
        if movement.get("arrival_time") and key not in incoming:
            errors.append(f"Tåg {movement['train_number']}: ankomsten vid {movement['station_id']} saknar föregående avgång.")
    for key, leg in legs.items():
        prior = legs.get(incoming.get(key))
        leg["previous"] = incoming.get(key)
        leg["dwell"] = max(0, leg["departure"] - prior["arrival"]) if prior else 0
    journeys = {}
    for leg in legs.values():
        journey = journeys.setdefault((leg["train_number"], leg["service_id"]), [leg["departure"], leg["arrival"]])
        journey[0] = min(journey[0], leg["departure"])
        journey[1] = max(journey[1], leg["arrival"])
    for (number, service), (start, end) in journeys.items():
        if any(other_number == number and other_service != service and start < other_end and other_start < end
               for (other_number, other_service), (other_start, other_end) in journeys.items()):
            errors.append(f"Tågnummer {number} används i överlappande tåglopp.")
    if not legs:
        errors.append("Ingen komplett tågväg finns för den valda trafikdagen.")
    if errors:
        raise SimulationError("Kan inte starta simuleringen: " + " ".join(errors[:12]))
    return legs


class TrafficSimulation:
    def __init__(self, service, *, now=monotonic):
        self.service, self.store, self.now = service, service.operations_store, now
        self.normal_connection = self.store._connection
        self.normal_external_source = self.store.external_clock_source
        self.run = None
        self.legs = {}
        self.seen = {}
        self.suppressed = set()
        self._initializing = False
        self._closed = False
        self.store.simulation_controller = self
        self.control = sqlite3.connect(self.store.path.with_suffix(".simulation-control.sqlite3"),
                                      isolation_level=None, check_same_thread=False)
        self.control.execute("PRAGMA synchronous=FULL")
        self.control.execute("CREATE TABLE IF NOT EXISTS selection (id INTEGER PRIMARY KEY CHECK(id=1), run_id TEXT)")
        service.simulation = self
        selected = self.control.execute("SELECT run_id FROM selection WHERE id=1").fetchone()
        if selected and selected[0]:
            # Fail closed if the selected run was lost, never silently use live data.
            path = self._path(selected[0])
            if not path.is_file():
                raise SimulationError("Simuleringsdata saknas. Vanlig drift får inte återupptas automatiskt.")
            candidate = SQLiteOperationsStore(path)
            row = candidate._connection.execute("SELECT state FROM simulation_state WHERE id=1").fetchone()
            self.run = json.loads(row[0])
            self.suppressed = set(self.run.get("suppressed", []))
            if not service.publication() or self.run["publication_id"] != service.publication().publication_id:
                candidate.close()
                raise SimulationError("Simuleringens config stämmer inte med vald träff.")
            self.legs = build_plan(service.publication(), self.run["day"])
            self._use(candidate._connection)
            self._capture(self.run["seconds"])
            events = self.store._connection.execute("SELECT MAX(seconds) FROM simulation_events").fetchone()[0]
            self.run["seconds"] = max(self.run["seconds"], events or 0)
            # Restart resumes at the last committed game time, always paused.
            self._set_seconds(self.run["seconds"], running=False)
            self.run["notice"] = "Servern startades om. Simuleringen är pausad."
            self._save()
            self._fence()

    @property
    def active(self):
        return self.run is not None

    def _path(self, run_id):
        if not isinstance(run_id, str) or len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
            raise SimulationError("Ogiltigt simulerings-ID")
        return self.store.path.parent / "simulations" / (run_id + ".sqlite3")

    def _use(self, connection):
        if self.store._command_depth:
            raise SimulationError("En trafikåtgärd pågår. Försök igen.")
        self.store._connection = connection
        self.store.external_clock_source = None if connection is not self.normal_connection else self.normal_external_source

    def _fence(self):
        lifecycle = self.service.lifecycle
        if lifecycle:
            selected = lifecycle.selected()
            lifecycle.select(selected["region"], selected["meet_id"], selected["publication_id"], meet_name=selected["meet_name"])
            self.service.runtime_store._save_setting("require_scoped_commands", "true")

    def _save(self):
        self.run["suppressed"] = sorted(self.suppressed)
        self.store._connection.execute("INSERT INTO simulation_state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state",
                                       (json.dumps(self.run, ensure_ascii=False),))

    def _set_seconds(self, seconds, *, running=False):
        self.store.configure_clock(running=running)
        self.store._connection.execute("UPDATE runtime_clock SET base_seconds=? WHERE singleton=1", (seconds,))

    def _clock_seconds(self):
        return self.store.clock_status()["elapsed_seconds"]

    def start(self, options):
        with self.store.command_lock:
            if self.active:
                raise SimulationError("En simulering finns redan. Pausa eller återställ den.")
            if options.get("confirmed") is not True:
                raise SimulationError("Bekräfta att det vanliga spelet pausas och att simuleringen tar över alla anslutna klienter.")
            publication = self.service.publication()
            if publication is None:
                raise SimulationError("Välj en EU-träff från Cloud först.")
            external = self.normal_external_source() if self.normal_external_source else None
            if external is not None:
                if external.get("available") is False:
                    raise SimulationError("Den externa träffklockan kan inte läsas. Kontrollera anslutningen och stoppa den före simulering.")
                if external.get("running"):
                    raise SimulationError("Stoppa den externa träffklockan först. Simulatorn kan inte pausa den åt dig.")
            day = self.service.runtime_store.active_day() or publication.active_day
            if options.get("day", day) != day:
                raise SimulationError("Simuleringen använder träffens valda trafikdag.")
            profile = options.get("profile", "normal")
            if profile not in PROFILES:
                raise SimulationError("Välj en giltig störningsnivå.")
            speed = float(options.get("speed", self.store.clock_status().get("speed", 1)))
            if not math.isfinite(speed) or not 0 < speed <= 60:
                raise SimulationError("Välj en klockhastighet mellan 0 och 60.")
            seconds = _time_to_seconds(str(options.get("time") or self.store.clock_status()["time"]))
            seed = str(options.get("seed") or uuid4().hex[:8])
            if len(seed) > 64:
                raise SimulationError("Scenarionyckeln får ha högst 64 tecken.")
            stabling = float(options.get("stabling_minutes", 5))
            if not math.isfinite(stabling) or not 1 <= stabling <= 60:
                raise SimulationError("Undanställning ska ta mellan 1 och 60 spelminuter.")
            # Preserve live operator ownership before the first automatic tick;
            # no reconnect, re-enrollment or new station assignment is needed.
            stations = {}
            for device, (station, seen) in self.seen.items():
                client = self.service.identities.client(device)
                authorized = client and (client.kind.value in {"web_admin", "swift_admin"}
                    or self.service.identities.station_for_client(device) == station)
                if authorized and self.now() - seen <= PRESENCE_SECONDS and station in publication.session_config().stations:
                    stations.setdefault(station, device)
            self._new_run(publication, day, seconds, speed, profile, seed, stations,
                          stabling_seconds=stabling * 60, takeover=True)
            return self.status()

    def _new_run(self, publication, day, seconds, speed, profile, seed, stations=None, stabling_seconds=300, *, takeover=False):
        legs = build_plan(publication, day)
        run_id = uuid4().hex
        candidate = SQLiteOperationsStore(self._path(run_id))
        from .storage import SQLiteStateStore
        SQLiteStateStore(self._path(run_id)).close()
        candidate._connection.execute("CREATE TABLE simulation_state(id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)")
        candidate._connection.execute("CREATE TABLE simulation_events(action TEXT NOT NULL, movement_id TEXT NOT NULL, seconds REAL NOT NULL, PRIMARY KEY(action,movement_id))")
        candidate.ensure_publication(publication)
        candidate.configure_clock(speed=speed, running=False)
        old_connection, old_run, old_legs = self.store._connection, self.run, self.legs
        self._use(candidate._connection)
        self.run = {"id": run_id, "publication_id": publication.publication_id, "day": day,
                    "profile": profile, "seed": seed, "seconds": seconds, "notice": "",
                    "stations": deepcopy(stations or {}), "departed": {}, "arrived": {}, "blocked": {}, "requests": {},
                    "stabled": {}, "stabling_seconds": stabling_seconds}
        self.legs = legs
        self._initializing = True
        try:
            self._set_seconds(seconds)
            with self.store.atomic_command():
                self._seed(seconds)
                self.run["baseline_departed"] = dict(self.run["departed"])
                self.run["baseline_arrived"] = dict(self.run["arrived"])
                if takeover:
                    self.store.configure_clock(running=True)
                self._save()
            if takeover:
                # Only pause normal operation after the candidate is valid.
                # A later failure leaves it safely paused, never clears traffic.
                self._use(old_connection)
                try:
                    if external_clock := self.store.external_clock_source:
                        external = external_clock()
                        if external is not None and (external.get("running") or external.get("available") is False):
                            raise SimulationError("Den externa träffklockan har ändrats. Stoppa den och försök igen.")
                    if self.store.clock_status().get("running"):
                        self.store.stop_clock("Pausat för simulering")
                finally:
                    self._use(candidate._connection)
            self._fence()
            self.control.execute("INSERT INTO selection VALUES(1,?) ON CONFLICT(id) DO UPDATE SET run_id=excluded.run_id", (run_id,))
        except BaseException:
            self.run, self.legs = old_run, old_legs
            self._use(old_connection)
            candidate.close()
            raise
        finally:
            self._initializing = False
        if old_connection is not self.normal_connection:
            old_connection.close()
        self.service.notify_changed()

    def _seed(self, seconds):
        """Replay past timetable transitions through the same command validator.

        This is a new scenario, not a replay of manual decisions/disturbances.
        In-flight trains retain their original planned departure and remaining time.
        Any inconsistent track/channel occupation aborts the whole new run.
        """
        events = []
        for key, leg in self.legs.items():
            if leg["departure"] < seconds:
                events.append((leg["departure"], 1, key))
            if leg["arrival"] <= seconds:
                events.append((leg["arrival"], 0, key))
            if leg["to_movement_id"] not in self.legs and leg["arrival"] + self.run["stabling_seconds"] <= seconds:
                events.append((leg["arrival"] + self.run["stabling_seconds"], -1, key))
        for when, kind, key in sorted(events):
            leg = self.legs[key]
            try:
                if kind == -1:
                    self._stable(key, when)
                elif kind == 1:
                    result = self._act(leg["from_station_id"], "clearance.request", movement_id=key, connection_id=leg["connection_id"])
                    case = self.store.clearance(result["revision"]["key"])
                    if case["status"] == "waiting":
                        self._act(leg["to_station_id"], "clearance.response", clearance_id=case["clearance_id"], approved=True)
                    self._act(leg["from_station_id"], "train.departed", movement_id=key)
                    self.run["departed"][key] = when
                else:
                    self._act(leg["to_station_id"], "train.arrived", movement_id=leg["to_movement_id"], track_id=leg["track_id"])
                    self.run["arrived"][key] = when
            except CommandRejected as error:
                raise SimulationError(f"Startläget för tåg {leg['train_number']} är inte möjligt: {error.reason}") from error

    def reset(self):
        with self.store.command_lock:
            self._require_run()
            seconds = self._clock_seconds()
            self.store.stop_clock("Återställning")
            self._new_run(self.service.publication(), self.run["day"], seconds,
                          self.store.clock_status()["speed"], self.run["profile"], self.run["seed"], self.run["stations"], self.run["stabling_seconds"])
            return self.status()

    def finish(self):
        with self.store.command_lock:
            self._require_run()
            self.pause()
            external = self.normal_external_source() if self.normal_external_source else None
            if external and external.get("available") is False:
                raise SimulationError("Den externa träffklockan kan inte läsas. Kontrollera att den är stoppad innan du återgår till vanlig drift.")
            if external and external.get("running"):
                raise SimulationError("Stoppa den externa träffklockan innan du återgår till vanlig drift.")
            self._fence()
            self.control.execute("UPDATE selection SET run_id=NULL WHERE id=1")
            simulation_connection = self.store._connection
            self._use(self.normal_connection)
            self.run, self.legs = None, {}
            self.suppressed.clear()
            simulation_connection.close()
            self.service.notify_changed()
            return self.status()

    def _require_run(self):
        if not self.active:
            raise SimulationError("Ingen simulering är aktiv.")

    def pause(self):
        with self.store.command_lock:
            self._require_run()
            self.store.stop_clock("Simuleringen är pausad")
            self.run["seconds"] = self._clock_seconds()
            self._save()
            return self.status()

    def resume(self):
        with self.store.command_lock:
            self._require_run()
            self.store.configure_clock(running=True)
            self.run["notice"] = ""
            self._save()
            return self.status()

    def observe(self, device, station=None):
        """Only invoked by inbound, authenticated/operator requests, never ticks."""
        with self.store.command_lock:
            assigned = self.service.identities.station_for_client(device)
            client = self.service.identities.client(device)
            if station is not None and client and client.kind.value in {"web_admin", "swift_admin"}:
                assigned = station
            if station is not None and assigned != station:
                return
            if not assigned:
                return
            self.seen[device] = (assigned, self.now())
            if not self.active or device in self.suppressed or assigned not in self.service.session_config().stations:
                return
            owner = self.run["stations"].get(assigned)
            if owner is None:
                self.run["stations"][assigned] = device
                self._save()

    def hand_back(self, station):
        with self.store.command_lock:
            self._require_run()
            if station not in self.service.session_config().stations:
                raise SimulationError("Okänd station")
            for device, (assigned, _) in self.seen.items():
                if assigned == station:
                    self.suppressed.add(device)
            self.run["stations"].pop(station, None)
            self._save()
            return self.status()

    def take_over(self, station, device):
        with self.store.command_lock:
            self._require_run()
            assigned, seen = self.seen.get(device, (None, -1e9))
            client = self.service.identities.client(device)
            if (assigned != station or self.now() - seen > PRESENCE_SECONDS or not client
                    or (client.kind.value not in {"web_admin", "swift_admin"} and client.station_id != station)):
                raise SimulationError("Klienten måste vara ansluten och tilldelad stationen.")
            self.suppressed.discard(device)
            self.run["stations"][station] = device
            self._save()
            return self.status()

    def _mode(self, station):
        owner = self.run["stations"].get(station)
        if not owner:
            return "automatic"
        assigned, seen = self.seen.get(owner, (None, -1e9))
        client = self.service.identities.client(owner)
        authorized = client and (client.kind.value in {"web_admin", "swift_admin"} or self.service.identities.station_for_client(owner) == station)
        return "manual" if (assigned == station and authorized
                            and self.now() - seen <= PRESENCE_SECONDS) else "disconnected"

    def _delay(self, key):
        chance, limit = PROFILES[self.run["profile"]]
        value = int(sha256((self.run["seed"] + ":" + key).encode()).hexdigest()[:12], 16)
        return (1 + value // 100 % limit) * 60 if limit and value % 100 < chance else 0

    def _ready_at(self, key):
        leg = self.legs[key]
        prior = leg["previous"]
        if prior and prior not in self.run["arrived"]:
            return math.inf
        available = self.run["arrived"].get(prior, leg["departure"] - leg["dwell"])
        return max(leg["departure"], available + leg["dwell"]) + self._delay(key)

    def guard(self, actor, station, action, body):
        if not self.active or self._initializing or action in {"train.lookup", "device.config.ack"}:
            return
        if not self.store.clock_status()["running"]:
            raise CommandRejected("simulation_paused", "Simuleringen är pausad")
        automatic = actor == "simulator:" + self.run["id"]
        if (automatic and self._mode(station) != "automatic") or (not automatic and self.run["stations"].get(station) != actor):
            raise CommandRejected("simulation_station_control", "Stationen styrs av en annan operatör")
        key = str(body.get("movement_id") or "")
        seconds = self._clock_seconds()
        self._capture(seconds)
        if action == "train.departed":
            if key not in self.legs or seconds < self._ready_at(key):
                raise CommandRejected("simulation_train_not_ready", "Tåget är inte färdigt för avgång")
        if action in {"clearance.request", "train.position.set"}:
            if key not in self.legs or (self.legs[key]["previous"] and self.legs[key]["previous"] not in self.run["arrived"]):
                raise CommandRejected("simulation_train_not_present", "Tåget har inte ankommit till stationen")
            movement = next(m for m in self.service.publication().payload["trains"] if m["id"] == key)
            live = self.store.tkl_station_state(self.run["publication_id"], self.run["day"], station)["movements"].get(key, {})
            if self.service.track_conflict(self.service.publication(), self.run["day"], station, key, live.get("actualTrack") or movement.get("track_id")):
                raise CommandRejected("track_occupied", "Avgångsspåret är upptaget")
        if action == "train.arrived":
            incoming = next((k for k, l in self.legs.items() if l["to_movement_id"] == key), None)
            if incoming not in self.run["departed"] or seconds < self.run["departed"][incoming] + self.legs[incoming]["duration"]:
                raise CommandRejected("simulation_train_in_transit", "Tåget har inte nått stationen ännu")

    def _act(self, station, action, **body):
        result = self.service.execute_station_command("simulator:" + self.run["id"], station, action, body)
        if not self._initializing and action in {"train.departed", "train.arrived"}:
            self._capture(self._clock_seconds())
        return result

    def record_action(self, action, movement_id):
        # Same SQLite transaction as the traffic act, so a lost ack or process
        # crash cannot lose the departure time or advance a rollback's clock.
        if self.active and not self._initializing and action in {"train.departed", "train.arrived"}:
            self.store._connection.execute("INSERT OR IGNORE INTO simulation_events VALUES(?,?,?)",
                                           (action, movement_id, self._clock_seconds()))

    def _capture(self, seconds):
        # Rebuild from committed events rather than retaining values observed
        # inside a compound TKL command that might subsequently roll back.
        self.run["departed"] = dict(self.run.get("baseline_departed", {}))
        self.run["arrived"] = dict(self.run.get("baseline_arrived", {}))
        incoming = {leg["to_movement_id"]: key for key, leg in self.legs.items()}
        for action, movement_id, when in self.store._connection.execute("SELECT action,movement_id,seconds FROM simulation_events"):
            if action == "train.departed" and movement_id in self.legs:
                self.run["departed"].setdefault(movement_id, when)
            elif action == "train.arrived" and movement_id in incoming:
                self.run["arrived"].setdefault(incoming[movement_id], when)

    def tick(self):
        with self.store.command_lock:
            if not self.active:
                return
            # Existing online operators are picked up before the first auto act.
            for device, (_, seen) in list(self.seen.items()):
                if self.now() - seen <= PRESENCE_SECONDS:
                    station = self.service.identities.station_for_client(device)
                    if station and device not in self.suppressed and station not in self.run["stations"]:
                        self.run["stations"][station] = device
            if not self.store.clock_status()["running"]:
                return
            seconds = self._clock_seconds()
            if seconds < self.run["seconds"]:
                self.pause()
                raise SimulationError("Klockan har flyttats bakåt. Återställ simuleringen vid önskad tid.")
            previous = deepcopy(self.run)
            try:
                with self.store.atomic_command():
                    self._capture(seconds)
                    for case in self.service.open_cases(None):
                        if case["status"] == "waiting":
                            since = self.run.setdefault("requests", {}).setdefault(case["clearance_id"], seconds)
                            if seconds - since >= 300:
                                self.store.settle_clearance(case["clearance_id"], "expired", "simulator:" + self.run["id"])
                    self.run["blocked"] = {}
                    for key, leg in sorted(self.legs.items(), key=lambda item: (item[1]["departure"], item[0])):
                        if key in self.run["arrived"]:
                            if (key not in self.run["stabled"] and leg["to_movement_id"] not in self.legs
                                    and self._mode(leg["to_station_id"]) == "automatic"
                                    and seconds >= self.run["arrived"][key] + self.run["stabling_seconds"]):
                                self._stable(key, seconds)
                            continue
                        try:
                            self._step(key, leg, seconds)
                        except CommandRejected as error:
                            self.run["blocked"][key] = error.reason
                    self.run["seconds"] = seconds
                    self._save()
            except BaseException:
                self.run = previous
                self.store.stop_clock("Simulatorfel – kontrollera och fortsätt")
                raise

    def _step(self, key, leg, seconds):
        if key in self.run["departed"]:
            if seconds >= self.run["departed"][key] + leg["duration"]:
                if self._mode(leg["to_station_id"]) == "automatic":
                    self._act(leg["to_station_id"], "train.arrived", movement_id=leg["to_movement_id"], track_id=leg["track_id"])
                else:
                    self.run["blocked"][key] = "Väntar på operatörens ankomstbekräftelse"
            return
        cases = [c for c in self.service.open_cases(leg["from_station_id"]) if c["movement_id"] == key]
        case = cases[0] if cases else None
        ready = self._ready_at(key)
        if not case and self._mode(leg["from_station_id"]) == "automatic" and seconds >= ready - 120:
            # A rejection is a decision, not an invitation to spam the receiver.
            rejected = self.store._connection.execute("SELECT 1 FROM clearances WHERE movement_id=? AND status='rejected'", (key,)).fetchone()
            if rejected:
                self.run["blocked"][key] = "Begäran nekad – kräver operatörsåtgärd"
                return
            state = self.store.tkl_station_state(self.run["publication_id"], self.run["day"], leg["from_station_id"])["movements"].get(key, {})
            if state.get("departure", "none") == "none":
                self._act(leg["from_station_id"], "train.position.set", movement_id=key)
            result = self._act(leg["from_station_id"], "clearance.request", movement_id=key, connection_id=leg["connection_id"])
            case = self.store.clearance(result["revision"]["key"])
        if case and case["status"] == "waiting" and self._mode(leg["to_station_id"]) == "automatic":
            station = leg["to_station_id"]
            if self.service.track_conflict(self.service.publication(), self.run["day"], station, leg["to_movement_id"], leg["track_id"]):
                raise CommandRejected("track_occupied")
            self._act(station, "clearance.response", clearance_id=case["clearance_id"], approved=True)
            case = self.store.clearance(case["clearance_id"])
        if self._mode(leg["from_station_id"]) == "automatic" and seconds >= ready and case and case["status"] == "approved":
            self._act(leg["from_station_id"], "train.departed", movement_id=key)
        elif math.isinf(ready):
            self.run["blocked"][key] = "Väntar på föregående ankomst"
        elif seconds >= leg["departure"] and seconds < ready:
            self.run["blocked"][key] = "Stationsarbete pågår"
        elif case and case["status"] == "waiting":
            self.run["blocked"][key] = "Väntar på klartecken"

    def _stable(self, key, seconds):
        # Simulation-only yard work. Do not invent a departure/clearance on a
        # real line. The completed arrival remains in the run's event history.
        self.run["stabled"][key] = seconds
        self.store.record_audit_event(correlation_id=self.run["id"], source="simulation",
            actor="simulator:" + self.run["id"], action="simulation.stabled", outcome="accepted",
            station_id=self.legs[key]["to_station_id"], movement_id=self.legs[key]["to_movement_id"],
            detail={"game_seconds": seconds, "description": "Rangerat till simulerad uppställning utanför trafikspåren"})

    def status(self):
        with self.store.command_lock:
            if not self.active:
                return {"active": False}
            return {"active": True, "run_id": self.run["id"], "day": self.run["day"],
                    "publication_id": self.run["publication_id"], "profile": self.run["profile"], "seed": self.run["seed"],
                    "clock": self.store.clock_status(), "notice": self.run["notice"],
                    "stabling_minutes": self.run["stabling_seconds"] / 60,
                    "stations": [{"id": s.id, "name": s.name, "code": s.code, "mode": self._mode(s.id),
                                  "operator": self.run["stations"].get(s.id),
                                  "available_operators": [device for device, (station, seen) in self.seen.items()
                                      if station == s.id and self.now() - seen <= PRESENCE_SECONDS and self.service.identities.client(device)]}
                                 for s in self.service.session_config().stations.values()],
                    "trains": [{"movement_id": key, "train_number": l["train_number"], "from_station_id": l["from_station_id"],
                                "to_station_id": l["to_station_id"], "departure": l["departure"],
                                "status": "stabled" if key in self.run["stabled"] else "arrived" if key in self.run["arrived"] else "in_transit" if key in self.run["departed"] else "waiting",
                                "reason": self.run["blocked"].get(key, ""), "delay_seconds": self._delay(key)} for key, l in self.legs.items()]}

    def close(self):
        with self.store.command_lock:
            if self._closed:
                return
            if self.active:
                self.store.stop_clock("Servern stoppas")
                self.run["seconds"] = self._clock_seconds()
                self._save()
                connection = self.store._connection
                self._use(self.normal_connection)
                connection.close()
            self.control.close()
            self._closed = True
