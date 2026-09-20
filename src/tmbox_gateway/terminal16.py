"""Isolated 16x2 pilot: the SERVER owns views, key meanings and traffic.

Not wired into production HTTP or MQTT. Uses TrafficEngine.perform rather
than the incomplete V2 movement transitions. A client renders frames and
buffers digits according to `entry`; it has no movement state machine.
Persistent arrival/track transactions and firmware adapters remain release
requirements. This lab deliberately refuses a persistent/production engine.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from uuid import uuid4

from .engine import TrafficEngine
from .models import ConnectionState as State, DispatchMode
from .train_routes import resolve_departure, RouteResolutionError
from .terminal16_glyphs import text_cells, encode_lcd


@dataclass
class Terminal:
    station: str
    selected: str | None = None
    screen: str = "overview"
    track: int = 0
    page: int = 0
    revision: int = 0
    notice: str = ""
    browse_filter: str = "all"


def row(left="", right=""):
    left, right = text_cells(left), text_cells(right)
    if len(left) + len(right) > 16:
        raise ValueError("A display row must not truncate an identity")
    return left + " " * (16 - len(left) - len(right)) + right


class Terminal16Lab:
    def __init__(self, engine: TrafficEngine, publication: dict, assignments: dict[str, str]):
        if engine.state_store is not None:
            raise ValueError("This pilot is isolated and in-memory only")
        self.engine = engine
        self.publication = deepcopy(publication)
        self.day = publication["meet"]["active_day"]
        self.epoch = uuid4().hex
        self.lock = RLock()
        self.terminals = {key: Terminal(station) for key, station in assignments.items()}
        if any(t.station not in engine.config.stations for t in self.terminals.values()):
            raise ValueError("Unknown assigned station")
        self.legs = {}
        for movement in publication["trains"]:
            if movement.get("departure_time"):
                leg = resolve_departure(publication, self.day, movement["station_id"], movement["id"])
                if leg["status"] == "resolved":
                    self.legs[movement["id"]] = leg
        self.bindings = {}  # connection -> exact departure movement, not just train number
        self.completed = set()
        self.arrivals = {}
        self.processed = OrderedDict()

    def _token(self, device, terminal):
        return sha256(f"{self.epoch}:{device}:{terminal.station}:{terminal.revision}:{self.engine.revision}".encode()).hexdigest()[:24]

    def _schedule(self, terminal, leg):
        outgoing = leg["from_station_id"] == terminal.station
        movement_id = leg["from_movement_id"] if outgoing else leg["to_movement_id"]
        movement = next(item for item in self.publication["trains"] if item["id"] == movement_id)
        value = str(movement.get("departure_time" if outgoing else "arrival_time") or "")
        match = re.fullmatch(r"(\d{2}):([0-5]\d)", value)
        minute = int(match[1]) * 60 + int(match[2]) if match else 10**9
        offset = movement.get("service_day_offset", 0)
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            offset = 0
        return {"kind": "departure" if outgoing else "arrival", "label": "AVG" if outgoing else "ANK",
                "time": value if match else "--:--", "order": offset * 1440 + minute,
                "other": leg["to_station_id"] if outgoing else leg["from_station_id"]}

    def _candidates(self, terminal, *, filtered=False):
        result = []
        for key, leg in self.legs.items():
            if key in self.completed:
                continue
            own = leg["from_station_id"] == terminal.station
            incoming = leg["to_station_id"] == terminal.station
            # A departed train is no longer an upcoming departure at its sender.
            if own and self.bindings.get(leg["connection_id"]) == key and self._line(leg).state == State.OCCUPIED:
                continue
            if own or incoming:
                if filtered and terminal.browse_filter not in {"all", self._schedule(terminal, leg)["kind"]}:
                    continue
                result.append(key)
        return sorted(result, key=lambda key: (self._schedule(terminal, self.legs[key])["order"],
                                              self.legs[key]["train_number"], key))

    def timetable(self, device):
        """Read-only test aid: keep the full schedule, including completed trains."""
        with self.lock:
            terminal = self.terminals[device]
            entries = []
            for key, leg in self.legs.items():
                if terminal.station not in {leg["from_station_id"], leg["to_station_id"]}:
                    continue
                schedule = self._schedule(terminal, leg)
                outgoing = schedule["kind"] == "departure"
                other = self.engine.config.stations[schedule["other"]]
                entry = {"train_number": leg["train_number"],
                         "time": ("Avg " if outgoing else "Ank ") + schedule["time"],
                         "route": ("Till " if outgoing else "Från ") + other.name}
                entries.append((schedule["order"], leg["train_number"], key, entry))
            return {"title": "Tidtabell · testdata", "columns": ["Tåg", "Tid", "Från / till"],
                    "rows": [entry for _, _, _, entry in sorted(entries)],
                    "empty": "Inga tåg i testtidtabellen."}

    def _browse(self, terminal, direction=0):
        choices = self._candidates(terminal, filtered=True)
        current = choices.index(terminal.selected) if terminal.selected in choices else None
        if choices:
            index = ((current + direction) % len(choices) if current is not None else
                     len(choices) - 1 if direction < 0 else 0)
            terminal.selected = choices[index]
        else:
            terminal.selected = None
        terminal.screen = "browse"

    def _departure_ready(self, leg):
        # For a through train the preceding arrival must be recorded first.
        return all(key in self.completed for key, incoming in self.legs.items()
                   if incoming["to_movement_id"] == leg["from_movement_id"])

    def _line(self, leg):
        return self.engine.connections[leg["connection_id"]]

    def _side(self, station, other):
        link = next(c for c in self.publication["connections"]
                    if {c["station_a_id"], c["station_b_id"]} == {station, other})
        return link["display_side_a" if link["station_a_id"] == station else "display_side_b"]

    def _label(self, station, leg):
        own = leg["from_station_id"] == station
        other = leg["to_station_id"] if own else leg["from_station_id"]
        code = self.engine.config.stations[other].code
        side = self._side(station, other)
        line = self._line(leg)
        active = self.bindings.get(leg["connection_id"]) == leg["from_movement_id"]
        marker = "-"
        if active:
            points_left = (side == "left") == own
            marker = ("?" if line.state == State.REQUESTED else
                      ("◀" if points_left else "▶") if line.state == State.OCCUPIED else
                      "<" if points_left else ">")
        number = leg["train_number"]
        token = f"{code}{marker}{number}" if side == "left" else f"{number}{marker}{code}"
        if len(token) > 16:
            raise ValueError("Identity requires a longer-identity presentation profile")
        return token, side

    def _overview(self, terminal):
        items = []
        for connection in self.engine.config.connections.values():
            if terminal.station not in {connection.station_a_id, connection.station_b_id}:
                continue
            key = self.bindings.get(connection.id)
            if key:
                items.append(self._label(terminal.station, self.legs[key]))
        left = [text for text, side in items if side == "left"]
        right = [text for text, side in items if side == "right"]
        page = terminal.page
        a = left[page % len(left)] if left else ""
        b = right[page % len(right)] if right else ""
        if a and b and len(a) + len(b) >= 16:
            # One full identity at a time; never silently clip a train number.
            return row(a) if page % 2 == 0 else row("", b)
        return row(a, b)

    def _tracks(self, terminal):
        return self.engine.config.tracks_for_station(terminal.station)

    def _planned_track(self, leg):
        return next(r.get("track_id") for r in self.publication["trains"] if r["id"] == leg["to_movement_id"])

    def _buttons(self, terminal):
        if terminal.notice:
            return {"#": ("back", "OK"), "*": ("back", "Tillbaka")}
        if terminal.screen == "overview":
            return {"#": ("browse", "Visa kommande tåg"), "C": ("previous", "Föregående tåg"),
                    "D": ("next", "Nästa tåg"), "B": ("page", "Nästa översiktssida")}
        buttons = {"*": ("back", "Tillbaka")}
        if terminal.screen == "browse":
            next_filter = {"all": "ankomster", "arrival": "avgångar", "departure": "alla tåg"}[terminal.browse_filter]
            buttons.update(A=("filter", "Visa " + next_filter), C=("previous", "Föregående tåg"), D=("next", "Nästa tåg"))
            if terminal.selected in self._candidates(terminal, filtered=True):
                buttons["#"] = ("select", "Välj tåg")
            return buttons
        leg = self.legs.get(terminal.selected)
        if not leg or terminal.selected in self.completed:
            return buttons
        line = self._line(leg)
        active = self.bindings.get(leg["connection_id"]) == terminal.selected
        own = leg["from_station_id"] == terminal.station
        if terminal.screen == "cancel":
            buttons["*"] = ("back", "Behåll begäran eller klartecken")
            if own and active and line.state in {State.REQUESTED, State.RESERVED}:
                buttons["#"] = ("cancel", "Bekräfta återtagning")
            return buttons
        if terminal.screen == "reject":
            buttons["*"] = ("back", "Tillbaka utan att neka")
            if not own and active and line.state == State.REQUESTED:
                buttons["#"] = ("reject", "Bekräfta neka")
            return buttons
        if terminal.screen == "tracks":
            if not own and active and line.state == State.OCCUPIED:
                buttons.update({"#": ("arrive_track", "Ankommit på valt spår"),
                                "C": ("previous_track", "Föregående spår"), "D": ("next_track", "Nästa spår")})
            return buttons
        buttons.update(C=("previous", "Föregående tåg"), D=("next", "Nästa tåg"))
        if own and line.state == State.FREE and self._departure_ready(leg):
            direct = (self.engine.config.connections[leg["connection_id"]].dispatch_mode_override
                      or self.engine.config.default_dispatch_mode) == DispatchMode.DIRECT
            buttons["#"] = ("request", "Reservera" if direct else "Begär klartecken")
        elif active and line.state == State.REQUESTED:
            buttons["A"] = ("home", "Översikt utan trafikändring")
            if own:
                buttons["*"] = ("cancel_view", "Återta begäran…")
            else:
                buttons.update({"#": ("accept", "Ge klart"), "*": ("reject_view", "Neka begäran…")})
        elif active and line.state == State.RESERVED and own:
            buttons.update({"#": ("depart", "Rapportera avgång"), "*": ("cancel_view", "Återta klartecken…"),
                            "A": ("home", "Översikt utan trafikändring")})
        elif active and line.state == State.OCCUPIED and not own:
            buttons.update({"#": ("arrive", "Rapportera ankomst"), "B": ("tracks", "Annat ankomstspår")})
        return buttons

    def _frame(self, device):
        terminal = self.terminals[device]
        buttons = self._buttons(terminal)
        clock = self.engine.meeting_clock()["time"]
        clock = clock if re.fullmatch(r"\d{2}:\d{2}", clock) else "--:--"
        hint = "Nr# C/D"
        first = self._overview(terminal)
        selected = self.legs.get(terminal.selected)
        if terminal.notice:
            first, hint = row(terminal.notice), "#OK *=Bak"
        elif terminal.screen == "browse":
            choices = self._candidates(terminal, filtered=True)
            if terminal.selected in choices:
                schedule = self._schedule(terminal, selected)
                first = row(f"{selected['train_number']} {schedule['label']}", schedule["time"])
                hint = "#Välj C/D"
            else:
                first = row("VÄLJ NÄSTA TÅG" if choices else
                            {"all": "INGA TÅG", "arrival": "INGA ANKOMSTER", "departure": "INGA AVGÅNGAR"}[terminal.browse_filter])
                hint = "C/D *=Bak" if choices else "A:Filter *"
        elif terminal.screen == "cancel":
            first, hint = ((row(f"ÅTER {selected['train_number']}?"), "#Ja *Nej") if "#" in buttons
                           else (row("LÄGET ÄNDRAT"), "*=Bak"))
        elif terminal.screen == "reject":
            first, hint = ((row(f"NEKA {selected['train_number']}?"), "#Ja *Nej") if "#" in buttons
                           else (row("LÄGET ÄNDRAT"), "*=Bak"))
        elif terminal.screen == "tracks":
            tracks = self._tracks(terminal)
            label = tracks[terminal.track % len(tracks)].display_label
            first, hint = row(f"{selected['train_number']} SPÅR {label}"), "#In C/D:Sp"
        elif terminal.screen == "detail" and selected:
            label, side = self._label(terminal.station, selected)
            first = row(label) if side == "left" else row("", label)
            action = buttons.get("#", ("", ""))[0]
            hint = {"request": "#Begär", "accept": "#Ja *Nej", "depart": "#Avg *Åter",
                    "arrive": "#In B:Sp"}.get(action, "*Åter A:Öv" if "A" in buttons else
                                               "C/D *=Bak" if "C" in buttons else "*=Bak")
            if action == "request" and buttons["#"][1] == "Reservera":
                hint = "#Sändklar"
        status = "Skriv tågnummer direkt, eller bläddra med C/D"
        upcoming = None
        if selected and terminal.screen != "overview":
            schedule = self._schedule(terminal, selected)
            other = self.engine.config.stations[schedule["other"]].name
            direction = "Avgång till" if schedule["kind"] == "departure" else "Ankomst från"
            status = f"Tåg {selected['train_number']} · {direction} {other} · planerat {schedule['time']}"
            if terminal.screen == "browse":
                choices = self._candidates(terminal, filtered=True)
                position = choices.index(terminal.selected) + 1 if terminal.selected in choices else 0
                label = {"all": "alla tåg", "arrival": "ankomster", "departure": "avgångar"}[terminal.browse_filter]
                status = f"{position}/{len(choices)} · {label} · " + status
                upcoming = {"position": position, "count": len(choices), "filter": terminal.browse_filter,
                            "kind": schedule["kind"], "time": schedule["time"], "movement_id": terminal.selected}
        lines = [first, row(hint, clock)]
        entry_lines = [row("TÅG: _____"), row("#Sök B:Del", clock)]
        return {
            "profile": "server-16x2-pilot", "device_id": device,
            "station": self.engine.config.stations[terminal.station].name,
            "station_code": self.engine.config.stations[terminal.station].code,
            "rows": 2, "cols": 16, "lines": lines, "lcd": encode_lcd(lines),
            "view_token": self._token(device, terminal),
            "revision": self.engine.revision, "view_revision": terminal.revision,
            "input_guard_ms": 500,
            "keys": {key: {"label": label} for key, (_, label) in buttons.items()},
            "entry": {"context": f"{self.epoch}:{device}:{terminal.station}", "max_length": 5,
                      "lines": entry_lines, "lcd": encode_lcd(entry_lines),
                      "row": 0, "column": 5, "commit": "#", "cancel": "*", "erase": "B",
                      "labels": {"#": "Sök tåg", "*": "Avbryt inmatning", "B": "Sudda siffra"}},
            "status": status, "upcoming": upcoming,
        }

    def frame(self, device):
        with self.lock:
            return self._frame(device)

    def frames(self):
        with self.lock:
            return [self._frame(device) for device in self.terminals]

    def command(self, device, body):
        with self.lock:
            terminal = self.terminals[device]
            command_id = body.get("command_id")
            if not isinstance(command_id, str) or not 1 <= len(command_id) <= 80:
                return self._answer(device, False, "Ogiltigt kommando-ID")
            signature = json.dumps(body, sort_keys=True, ensure_ascii=False)
            cache_key = (device, command_id)
            cached = self.processed.get(cache_key)
            if cached:
                if cached[0] != signature:
                    return self._answer(device, False, "Kommando-ID har redan använts")
                return self._answer(device, cached[1], cached[2])
            if body.get("view_token") != self._token(device, terminal):
                result = self._answer(device, False, "Läget ändrades. Läs displayen och försök igen.")
            else:
                result = self._dispatch(device, terminal, body)
            self.processed[cache_key] = (signature, result["status"] == "accepted", result["message"])
            if len(self.processed) > 512:
                self.processed.popitem(last=False)
            return result

    def _answer(self, device, accepted, message=""):
        return {"status": "accepted" if accepted else "rejected", "message": message, "frame": self._frame(device)}

    def _dispatch(self, device, terminal, body):
        key = body.get("key")
        if not isinstance(key, str) or len(key) != 1:
            return self._answer(device, False, "Ogiltig tangent")
        if any(field in body for field in ("action", "connection_id", "station_id")):
            return self._answer(device, False, "Servern bestämmer funktion och mottagare")
        if key == "#" and "train_number" in body:
            number = body["train_number"]
            if (body.get("entry_context") != self._frame(device)["entry"]["context"]
                    or not isinstance(number, str) or not re.fullmatch(r"[0-9]{1,5}", number)):
                return self._answer(device, False, "Ogiltig eller gammal inmatning")
            matches = [key for key in self._candidates(terminal) if self.legs[key]["train_number"] == number]
            if len(matches) != 1:
                terminal.notice = "INGET TÅG" if not matches else "FLERA TÅG - ADMIN"
            else:
                terminal.selected, terminal.screen, terminal.notice = matches[0], "detail", ""
                terminal.browse_filter = "all"
            terminal.revision += 1
            return self._answer(device, True)
        if "train_number" in body:
            return self._answer(device, False, "Servern bestämmer funktion och mottagare")
        button = self._buttons(terminal).get(key)
        if not button:
            return self._answer(device, False, "Tangenten är inte tillgänglig i det här läget")
        action = button[0]
        terminal.notice = ""
        if action == "back":
            terminal.screen = "detail" if terminal.screen in {"tracks", "cancel", "reject"} else "overview"
        elif action == "home":
            terminal.screen = "overview"
        elif action == "page":
            terminal.page += 1
        elif action in {"browse", "next", "previous"}:
            self._browse(terminal, {"browse": 0, "next": 1, "previous": -1}[action])
        elif action == "filter":
            terminal.browse_filter = {"all": "arrival", "arrival": "departure", "departure": "all"}[terminal.browse_filter]
            terminal.selected = None
            self._browse(terminal)
        elif action == "select":
            terminal.screen = "detail"
        elif action == "cancel_view":
            terminal.screen = "cancel"
        elif action == "reject_view":
            terminal.screen = "reject"
        elif action == "tracks":
            terminal.track, terminal.screen = 0, "tracks"
        elif action in {"next_track", "previous_track"}:
            terminal.track = (terminal.track + (1 if action == "next_track" else -1)) % len(self._tracks(terminal))
        else:
            message = self._traffic(device, terminal, action)
            if message:
                return self._answer(device, False, message)
        terminal.revision += 1
        return self._answer(device, True)

    def _traffic(self, device, terminal, action):
        key = terminal.selected
        leg = self.legs[key]
        try:
            fresh = resolve_departure(self.publication, self.day, leg["from_station_id"], key)
        except RouteResolutionError:
            return "Tågets rutt kan inte identifieras"
        if fresh != leg:
            return "Tågets rutt har ändrats"
        arrival_track = None
        if action in {"arrive", "arrive_track"}:
            arrival_track = (self._tracks(terminal)[terminal.track % len(self._tracks(terminal))].id
                             if action == "arrive_track" else self._planned_track(leg))
            track = self.engine.config.tracks.get(arrival_track)
            if not track or not track.active or track.station_id != terminal.station:
                return "Ankomstspåret är inte giltigt"
            if any(a["station"] == terminal.station and a["track"] == arrival_track for a in self.arrivals.values()):
                return "Spåret är upptaget i testet"
        accepted, reason = self.engine.perform(
            station_id=terminal.station, connection_id=leg["connection_id"],
            action="arrive" if action == "arrive_track" else action,
            train_number=leg["train_number"], client_id=device)
        if not accepted:
            return {"connection_busy": "Sträckan är upptagen", "departure_not_reserved": "Klartecken saknas",
                    "train_not_departed": "Tåget har inte avgått"}.get(reason, "Läget har ändrats. Välj tåget igen.")
        if action == "request":
            self.bindings[leg["connection_id"]] = key
        if action in {"arrive", "arrive_track"}:
            self.arrivals[key] = {"station": terminal.station, "track": arrival_track,
                                  "planned_track": self._planned_track(leg), "movement_id": leg["to_movement_id"]}
            self.completed.add(key)
            terminal.notice = f"{leg['train_number']} ANK SP{self.engine.config.tracks[arrival_track].display_label}"
        if action in {"cancel", "reject", "arrive", "arrive_track"}:
            self.bindings.pop(leg["connection_id"], None)
        if action == "cancel":
            terminal.notice = f"{leg['train_number']} ÅTERTAGET"
        if action == "reject":
            terminal.notice = f"{leg['train_number']} NEKAT"
            for other in self.terminals.values():
                if other.station == leg["from_station_id"] and other.selected == key:
                    other.notice = f"{leg['train_number']} NEKAT"
                    other.revision += 1
        return ""
