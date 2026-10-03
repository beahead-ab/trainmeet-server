"""Shared server-owned 16x2 views, key meanings and input contexts.

The isolated lab uses an ephemeral TrafficEngine. terminal16_runtime reuses
these views with the persistent station service; the datasets never mix.
Clients render frames and buffer digits according to `entry`, without a
movement state machine. The lab refuses a persistent/production engine.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from threading import RLock
from time import monotonic
from uuid import uuid4

from .engine import TrafficEngine
from .models import ConnectionState as State, DispatchMode
from .train_routes import resolve_departure, RouteResolutionError
from .terminal16_glyphs import text_cells, encode_lcd
from .terminal16_i18n import text as translated, notice as translated_notice
from .display_placement import effective_sides


# Det command() hanterar utan _traffic: de ändrar vad boxen visar, aldrig
# trafiken. Varje annan åtgärd utför något och märks "acts" i bilden. Efter ett
# skärmbyte spärrar klienten bara de märkta en kort stund, så att ett tryck
# avsett för förra bilden inte utför något på den nya - medan bläddring svarar
# direkt.
NAVIGATION_ACTIONS = frozenset({
    "back", "home",
    "requests", "next_request", "previous_request", "active", "next_active", "previous_active",
    "browse", "next", "previous", "filter", "select", "cancel_view", "reject_view",
    "tracks", "next_track", "previous_track",
})

# The few words a phone keypad prints under a key (iPhone TMBox). Keyed by the
# Swedish source label of the button, so two buttons with the same action but
# different meanings (Tillbaka / Stäng meddelande) keep their own word. The
# word is translated like every other text; a label without one gets none.
SHORT_LABELS = {
    "Stäng meddelande": "STÄNG", "Tillbaka": "TILLBAKA", "OK": "OK",
    "Visa väntande förfrågningar": "VISA KÖ", "Visa kommande tåg": "KOMMANDE",
    "Föregående aktiva tåg": "FÖREG", "Föregående tåg": "FÖREG",
    "Föregående förfrågan": "FÖREG", "Föregående spår": "FÖREG",
    "Nästa aktiva tåg": "NÄSTA", "Nästa tåg": "NÄSTA", "Nästa förfrågan": "NÄSTA", "Nästa spår": "NÄSTA",
    "Översikt utan trafikändring": "ÖVERSIKT", "Ge klart": "GE KLART", "Neka begäran…": "NEKA",
    "Visa ankomster": "FILTER", "Visa avgångar": "FILTER", "Visa alla tåg": "FILTER",
    "Välj tåg": "VÄLJ", "Behåll begäran eller klartecken": "BEHÅLL", "Bekräfta återtagning": "ÅTERTA",
    "Tillbaka utan att neka": "TILLBAKA", "Bekräfta neka": "NEKA", "Ankommit på valt spår": "INNE",
    "Reservera": "RESERVERA", "Begär klartecken": "BEGÄR", "Återta begäran…": "ÅTERTA",
    "Rapportera avgång": "AVGÅTT", "Återta klartecken…": "ÅTERTA", "Rapportera ankomst": "INNE",
    "Annat ankomstspår": "SPÅR", "Placera på spår": "PLACERA", "Placera på spår…": "PLACERA",
}

#: How long a notice stays before the box goes back to its start screen by
#: itself. Nothing on the box waits for #OK (Casper, 2026-10-03).
NOTICE_SECONDS = 3


@dataclass
class Terminal:
    station: str
    # both, left or right: which lines this box handles at its station.
    side: str = "both"
    language: str = "sv"
    selected: str | None = None
    screen: str = "overview"
    track: int = 0
    revision: int = 0
    notice: str = ""
    # The other station of the notice (its code), on the second row.
    notice_hint: str = ""
    browse_filter: str = "all"
    return_screen: str = "detail"
    receipts: list[tuple[str, str]] = field(default_factory=list)
    receipt_until: float | None = None
    notice_until: float | None = None


def row(left="", right=""):
    left, right = text_cells(left), text_cells(right)
    if len(left) + len(right) > 16:
        raise ValueError("A display row must not truncate an identity")
    return left + " " * (16 - len(left) - len(right)) + right


class Terminal16Lab:
    def __init__(self, engine: TrafficEngine, publication: dict, assignments: dict[str, str], *, now=monotonic):
        if engine.state_store is not None:
            raise ValueError("This pilot is isolated and in-memory only")
        self.engine = engine
        self.now = now
        self.publication = deepcopy(publication)
        self.day = publication["meet"]["active_day"]
        self.epoch = uuid4().hex
        self.lock = RLock()
        self.terminals = {key: Terminal(station) for key, station in assignments.items()}
        self.display_sides = {station: effective_sides(engine.config, {}, station) for station in engine.config.stations}
        if any(t.station not in engine.config.stations for t in self.terminals.values()):
            raise ValueError("Unknown assigned station")
        self.legs = {}
        for movement in publication["trains"]:
            if movement.get("departure_time"):
                try:
                    leg = resolve_departure(publication, self.day, movement["station_id"], movement["id"])
                except RouteResolutionError:
                    continue
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
            active = self._is_active(leg)
            # Every arrival still to come can be chosen here, sent or not: a
            # train nobody sent can be placed on a track afterwards (2.1.0).
            # A departed train is no longer an upcoming departure at its sender.
            if own and active and self._line(leg).state == State.OCCUPIED:
                continue
            if (own or incoming) and self._on_side(terminal, leg):
                if filtered and terminal.browse_filter not in {"all", self._schedule(terminal, leg)["kind"]}:
                    continue
                result.append(key)
        return sorted(result, key=lambda key: (self._schedule(terminal, self.legs[key])["order"],
                                              self.legs[key]["train_number"], key))

    def _lookup(self, terminal, number):
        """The legs a typed number means here, one wherever it can be told.

        A through train has two at its station: in from MUN and on to MUN.
        The one with a case wins (a request to answer, a train to receive or
        an own request); with both under way the arrival comes first, and
        the departure is under B; with neither, the departure. Until 2.0.2
        such a number matched twice and the box got no answer at all.
        """
        matches = [key for key in self._candidates(terminal) if self.legs[key]["train_number"] == number]
        if len(matches) > 1:
            active = [key for key in matches if self._is_active(self.legs[key])]
            if len(active) == 1:
                return active
            if active:
                # Both under way: the arrival first, the departure under B.
                arriving = {self.legs[key]["to_movement_id"] for key in active
                            if self.legs[key]["to_station_id"] == terminal.station}
                return [key for key in active if self.legs[key]["from_movement_id"] not in arriving]
            # Nothing under way: the station's own departure, which 93# asks
            # for at once; an arrival only when nothing leaves from here. An
            # arrival is still in the timetable, to be placed (2.1.0).
            own = [key for key in matches if self.legs[key]["from_station_id"] == terminal.station]
            matches = own or matches
        return matches

    @staticmethod
    def _short(t, key, action, label, requests, active):
        """The word under a key on a phone keypad; counts follow the long label."""
        if key == "A":
            return t("KÖ") + (f" {requests}" if requests else "")
        if action == "active":
            return t("AKTIVA") + (f" {active}" if active else "")
        word = SHORT_LABELS.get(label)
        return t(word) if word else ""

    def station_timetable(self, device):
        """The assigned station's trains today and where each one is now.

        Read-only, for a client that shows the timetable beside the box (the
        iPhone TMBox). The rows follow the station and side the box has, in
        timetable order, and say only what the views already know: planned,
        requested, cleared, departed or arrived. Nothing here can select or
        change a train; the box's own keys still do that.
        """
        with self.lock:
            terminal = self.terminals[device]
            self._advance_receipts(terminal)
            station = self.engine.config.stations[terminal.station]
            labels = {track.id: track.display_label for track in self._tracks(terminal)}
            movements = {item["id"]: item for item in self.publication["trains"]}
            rows = []
            for key, leg in self.legs.items():
                if terminal.station not in {leg["from_station_id"], leg["to_station_id"]} or not self._on_side(terminal, leg):
                    continue
                schedule = self._schedule(terminal, leg)
                outgoing = schedule["kind"] == "departure"
                other = self.engine.config.stations[schedule["other"]]
                movement = movements.get(leg["from_movement_id"] if outgoing else leg["to_movement_id"], {})
                line = self._line(leg)
                active = self._is_active(leg)
                state = ("arrived" if key in self.completed else
                         "requested" if active and line.state == State.REQUESTED else
                         "cleared" if active and line.state == State.RESERVED else
                         "departed" if active and line.state == State.OCCUPIED else "planned")
                rows.append((schedule["order"], leg["train_number"], key, {
                    "movement_id": key, "train_number": leg["train_number"],
                    "kind": schedule["kind"], "time": schedule["time"],
                    "station": {"code": other.code, "name": other.name},
                    "side": self._side(terminal.station, other.id, leg["connection_id"]),
                    "track": labels.get(movement.get("track_id")),
                    "state": state, "selected": terminal.selected == key,
                }))
            clock = self.engine.meeting_clock()["time"]
            return {"station": {"code": station.code, "name": station.name}, "side": terminal.side,
                    "clock": clock if re.fullmatch(r"\d{2}:\d{2}", str(clock)) else None,
                    "revision": self.engine.revision,
                    "rows": [entry for _, _, _, entry in sorted(rows)]}

    def timetable(self, device):
        """Read-only test aid: keep the full schedule, including completed trains."""
        with self.lock:
            terminal = self.terminals[device]
            entries = []
            for key, leg in self.legs.items():
                if terminal.station not in {leg["from_station_id"], leg["to_station_id"]} or not self._on_side(terminal, leg):
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

    def _requests(self, terminal):
        # Bindings retain request order. Only exact, still-unanswered incoming legs.
        return [key for key in self.bindings.values()
                if self.legs[key]["to_station_id"] == terminal.station
                and self._on_side(terminal, self.legs[key])
                and self._line(self.legs[key]).state == State.REQUESTED]

    def _open_requests(self, terminal, direction=0):
        choices = self._requests(terminal)
        current = choices.index(terminal.selected) if terminal.selected in choices else None
        index = (current + direction) % len(choices) if current is not None else 0
        terminal.selected = choices[index] if choices else None
        terminal.screen, terminal.notice = "requests", ""

    def _active_trains(self, terminal):
        """Ongoing traffic, not future timetable entries or unanswered arrivals."""
        choices = []
        for key in self.bindings.values():
            leg = self.legs[key]
            own = leg["from_station_id"] == terminal.station
            if key in self.completed or not self._is_active(leg):
                continue
            if not own and leg["to_station_id"] != terminal.station:
                continue
            if not self._on_side(terminal, leg):
                continue
            state = self._line(leg).state
            if state == State.FREE or (not own and state == State.REQUESTED):
                continue  # Unanswered incoming requests live under A.
            priority = (0 if own and state == State.RESERVED else
                        1 if not own and state == State.OCCUPIED else
                        2 if state == State.REQUESTED else 3 if state == State.RESERVED else 4)
            choices.append((priority, self._schedule(terminal, leg)["order"], key))
        return [key for _, _, key in sorted(choices)]

    def _open_active(self, terminal, direction=0):
        choices = self._active_trains(terminal)
        current = choices.index(terminal.selected) if terminal.screen == "active" and terminal.selected in choices else None
        index = ((current + direction) % len(choices) if current is not None else
                 len(choices) - 1 if direction < 0 else 0)
        terminal.selected = choices[index] if choices else None
        terminal.screen, terminal.notice = "active", ""

    def _notify_request(self, leg):
        for terminal in self.terminals.values():
            # An empty queue's INGA FRÅGOR gives way to the request at once.
            if (terminal.station == leg["to_station_id"] and self._on_side(terminal, leg)
                    and terminal.notice in ("", "INGA FRÅGOR")
                    and (terminal.screen == "overview"
                         or (terminal.screen == "requests" and terminal.selected is None))):
                self._open_requests(terminal)
                terminal.revision += 1

    def _notify_arrival(self, leg):
        for terminal in self.terminals.values():
            if terminal.station != leg["from_station_id"] or not self._on_side(terminal, leg):
                continue
            # Clear the exact completed leg, never a different train being handled.
            if terminal.selected == leg["from_movement_id"]:
                terminal.selected, terminal.screen, terminal.notice = None, "overview", ""
            terminal.receipts.append((leg["train_number"], leg["to_station_id"]))
            terminal.revision += 1

    def _notify_withdrawn(self, leg, key):
        """The receiver is told when the sender takes a train back, also one it
        had cleared: "3510 ÅTERTAGET" with the sender's code, then the start
        screen. A box busy with another train is left alone."""
        sender = self.engine.config.stations[leg["from_station_id"]].code
        for terminal in self.terminals.values():
            if terminal.station != leg["to_station_id"] or not self._on_side(terminal, leg):
                continue
            if terminal.selected not in (None, key) and terminal.screen != "overview":
                continue
            terminal.notice, terminal.notice_hint = f"{leg['train_number']} ÅTERTAGET", sender
            terminal.notice_until = self.now() + NOTICE_SECONDS
            terminal.revision += 1

    def _dismiss_receipt(self, terminal):
        if terminal.receipt_until is not None:
            terminal.receipts.pop(0)
            terminal.receipt_until = None
            terminal.revision += 1

    def _advance_receipts(self, terminal):
        # An empty request queue is said and then goes away, it does not stay
        # on the box until someone presses a key (Benny, 2026-10-03).
        if (terminal.screen == "requests" and terminal.selected is None and not terminal.notice
                and not self._requests(terminal)):
            terminal.notice, terminal.notice_hint = "INGA FRÅGOR", ""
            terminal.revision += 1
        # A notice replaced by something else takes its time limit with it, or
        # the box would be sent home in the middle of what replaced it.
        if not terminal.notice and terminal.notice_until is not None:
            terminal.notice_until, terminal.notice_hint = None, ""
        # Every notice clears itself; the clock starts when it is first shown.
        if terminal.notice and terminal.notice_until is None:
            terminal.notice_until = self.now() + NOTICE_SECONDS
        if terminal.notice_until is not None and self.now() >= terminal.notice_until:
            terminal.notice, terminal.notice_until, terminal.notice_hint = "", None, ""
            terminal.selected, terminal.screen = None, "overview"
            terminal.revision += 1
        # Real elapsed time, independent of a paused/accelerated meeting clock.
        if terminal.receipt_until is not None and self.now() >= terminal.receipt_until:
            self._dismiss_receipt(terminal)
        if (terminal.receipts and terminal.receipt_until is None
                and terminal.screen == "overview" and not terminal.notice):
            terminal.receipt_until = self.now() + 5
            terminal.revision += 1

    def _finish_leg(self, device, key):
        """Bring the lab engine's line for this leg to its end, whatever it
        last knew: answered, departed and arrived, so the line is free."""
        leg = self.legs[key]
        if self._is_active(leg):
            steps = {State.REQUESTED: ("accept", "depart", "arrive"), State.RESERVED: ("depart", "arrive"),
                     State.OCCUPIED: ("arrive",)}.get(self._line(leg).state, ())
            for step in steps:
                station = leg["from_station_id"] if step == "depart" else leg["to_station_id"]
                accepted, reason = self.engine.perform(station_id=station, connection_id=leg["connection_id"], action=step,
                                                       train_number=leg["train_number"], client_id=device)
                if not accepted:
                    return reason
            self.bindings.pop(leg["connection_id"], None)
        self.completed.add(key)
        return None

    def _can_take_in(self, leg):
        """An arrival can be reported unless it waits for an answer: sent,
        cleared but not reported departed, or never sent at all (2.1.0)."""
        return not self._is_active(leg) or self._line(leg).state in {State.RESERVED, State.OCCUPIED}

    def _line(self, leg):
        return self.engine.connections[leg["connection_id"]]

    def _is_active(self, leg):
        return self.bindings.get(leg["connection_id"]) == leg["from_movement_id"]

    def _on_side(self, terminal, leg):
        """A box set to one side handles only the trains on lines to that side."""
        if terminal.side == "both":
            return True
        own = leg["from_station_id"] == terminal.station
        other = leg["to_station_id"] if own else leg["from_station_id"]
        return self._side(terminal.station, other, leg["connection_id"]) == terminal.side

    def _side(self, station, other, connection_id=None):
        if connection_id is None:
            connection_id = next(c.id for c in self.engine.config.connections.values()
                                 if {c.station_a_id, c.station_b_id} == {station, other})
        return self.display_sides[station][connection_id]

    def update_display_placement(self, config, overrides):
        """Refresh presentation only, retaining selection, queues and entry context."""
        with self.lock:
            sides = {station: effective_sides(config, overrides, station) for station in config.stations}
            for terminal in self.terminals.values():
                if sides.get(terminal.station) != self.display_sides.get(terminal.station):
                    terminal.revision += 1
            self.display_sides = sides

    def _label(self, station, leg):
        own = leg["from_station_id"] == station
        other = leg["to_station_id"] if own else leg["from_station_id"]
        code = self.engine.config.stations[other].code
        side = self._side(station, other, leg["connection_id"])
        line = self._line(leg)
        active = self._is_active(leg)
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
        for key in self.bindings.values():
            leg = self.legs[key]
            if terminal.station in {leg["from_station_id"], leg["to_station_id"]} and self._on_side(terminal, leg):
                items.append(self._label(terminal.station, leg))
        left = [text for text, side in items if side == "left"]
        right = [text for text, side in items if side == "right"]
        a = left[0] if left else ""
        b = right[0] if right else ""
        if a and b and len(a) + len(b) >= 16:
            # Preserve full identities. B opens the counted active list; C/D
            # reveals every train without the old unlabelled overview paging.
            return row(a)
        return row(a, b)

    def _tracks(self, terminal):
        return self.engine.config.tracks_for_station(terminal.station)

    def _planned_track(self, leg):
        return next(r.get("track_id") for r in self.publication["trains"] if r["id"] == leg["to_movement_id"])

    def _buttons(self, terminal):
        buttons = self._view_buttons(terminal)
        buttons["A"] = ("requests", f"Förfrågningskö ({len(self._requests(terminal))} väntar)")
        return buttons

    def _view_buttons(self, terminal):
        if terminal.receipt_until is not None:
            return {"#": ("home", "Stäng meddelande"), "*": ("home", "Tillbaka")}
        if terminal.notice:
            return {"#": ("back", "OK"), "*": ("back", "Tillbaka")}
        if terminal.screen == "overview":
            primary = ("requests", "Visa väntande förfrågningar") if self._requests(terminal) else ("browse", "Visa kommande tåg")
            active = self._active_trains(terminal)
            # No language menu on the box: the administrator sets each box's
            # language in Server, so the start screen stays simple.
            return {"#": primary,
                    "C": ("previous_active", "Föregående aktiva tåg") if active else ("previous", "Föregående tåg"),
                    "D": ("next_active", "Nästa aktiva tåg") if active else ("next", "Nästa tåg"),
                    "B": ("active", "Visa aktiva tåg")}
        buttons = {"*": ("back", "Tillbaka")}
        if terminal.screen == "requests":
            buttons.update(B=("home", "Översikt utan trafikändring"))
            if self._requests(terminal):
                buttons.update(C=("previous_request", "Föregående förfrågan"), D=("next_request", "Nästa förfrågan"))
            if terminal.selected in self._requests(terminal):
                buttons.update({"#": ("accept", "Ge klart"), "*": ("reject_view", "Neka begäran…")})
            return buttons
        if terminal.screen == "browse":
            next_filter = {"all": "ankomster", "arrival": "avgångar", "departure": "alla tåg"}[terminal.browse_filter]
            buttons.update(B=("filter", "Visa " + next_filter), C=("previous", "Föregående tåg"), D=("next", "Nästa tåg"))
            if terminal.selected in self._candidates(terminal, filtered=True):
                buttons["#"] = ("select", "Välj tåg")
            return buttons
        active_view = terminal.screen == "active"
        if active_view:
            buttons.update(B=("home", "Översikt utan trafikändring"),
                           C=("previous_active", "Föregående aktiva tåg"), D=("next_active", "Nästa aktiva tåg"))
            if terminal.selected not in self._active_trains(terminal):
                return buttons  # Never silently select/confirm another train.
        leg = self.legs.get(terminal.selected)
        if not leg or terminal.selected in self.completed:
            return buttons
        line = self._line(leg)
        active = self._is_active(leg)
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
            if not own and self._can_take_in(leg):
                buttons.update({"#": ("arrive_track", "Ankommit på valt spår"),
                                "C": ("previous_track", "Föregående spår"), "D": ("next_track", "Nästa spår")})
            return buttons
        if not active_view:
            buttons.update(C=("previous", "Föregående tåg"), D=("next", "Nästa tåg"))
        # A through train's next leg can be requested while it is still on
        # its way in, and sent on once cleared: if the system has not seen it
        # come, it jumps here with the departure (Casper, 2026-10-02).
        if own and line.state == State.FREE:
            direct = (self.engine.config.connections[leg["connection_id"]].dispatch_mode_override
                      or self.engine.config.default_dispatch_mode) == DispatchMode.DIRECT
            buttons["#"] = ("request", "Reservera" if direct else "Begär klartecken")
        elif active and line.state == State.REQUESTED:
            buttons["B"] = ("home", "Översikt utan trafikändring")
            if own:
                buttons["*"] = ("cancel_view", "Återta begäran…")
            else:
                buttons.update({"#": ("accept", "Ge klart"), "*": ("reject_view", "Neka begäran…")})
        elif active and line.state == State.RESERVED and own:
            buttons.update({"#": ("depart", "Rapportera avgång"), "*": ("cancel_view", "Återta klartecken…"),
                            "B": ("home", "Översikt utan trafikändring")})
        elif not own and active and line.state == State.OCCUPIED:
            buttons.update({"#": ("arrive", "Rapportera ankomst"), "B": ("tracks", "Annat ankomstspår")})
        elif not own and not active:
            # Never sent in the system: 93# shows it, # places it (2.1.0).
            buttons.update({"#": ("arrive", "Placera på spår"), "B": ("tracks", "Annat ankomstspår")})
        elif not own and line.state == State.RESERVED:
            # Cleared here but never reported departed: placed through the
            # track picker only, so the # after #Ja never takes a train in.
            buttons["B"] = ("tracks", "Placera på spår…")
        return buttons

    def _frame(self, device):
        terminal = self.terminals[device]
        t = lambda key, **values: translated(terminal.language, key, **values)
        self._advance_receipts(terminal)
        buttons = self._buttons(terminal)
        clock = self.engine.meeting_clock()["time"]
        clock = clock if re.fullmatch(r"\d{2}:\d{2}", clock) else "--:--"
        hint = t("Nr# A:Kö")
        first = self._overview(terminal)
        selected = self.legs.get(terminal.selected)
        requests = self._requests(terminal)
        active = self._active_trains(terminal)
        active_position = active.index(terminal.selected) + 1 if terminal.selected in active else 0
        compact = lambda count: str(count) if count < 100 else "99+"
        if terminal.screen == "overview" and active:
            hint = t("B:Akt{count} C/D", count=compact(len(active)))
            if len(text_cells(hint)) > 11:
                hint = t("B:Akt{count}", count=compact(len(active)))
        position = requests.index(terminal.selected) + 1 if terminal.selected in requests else 0
        if terminal.screen == "overview" and requests:
            hint = t("A:K{count} B:Akt", count=compact(len(requests))) if active else "A:Kö #Visa"
            if len(text_cells(hint)) > 11:
                hint = t("A{count} B:Akt", count=compact(len(requests)))
        if terminal.notice:
            first, hint = row(translated_notice(terminal.language, terminal.notice)), terminal.notice_hint
        elif terminal.screen == "requests":
            if position:
                label, side = self._label(terminal.station, selected)
                counter = f"{position}/{len(requests)}"
                if len(label) + len(counter) < 16:
                    first = row(label, counter) if side == "left" else row(counter, label)
                    hint = "#Ja *Nej"
                else:
                    # Keep long train identities intact; put queue count in the hint.
                    first = row(label) if side == "left" else row("", label)
                    hint = t("#Ja {count}", count=counter)
            else:
                first = row(t("INGA FRÅGOR" if not requests else "FRÅGAN ÄNDRAD"))
                hint = "A:Kö *=Bak"
        elif terminal.screen == "browse":
            choices = self._candidates(terminal, filtered=True)
            if terminal.selected in choices:
                schedule = self._schedule(terminal, selected)
                first = row(f"{selected['train_number']} {t(schedule['label'])}", schedule["time"])
                hint = "#Välj A:Kö"
            else:
                first = row(t("VÄLJ NÄSTA TÅG" if choices else
                            {"all": "INGA TÅG", "arrival": "INGA ANKOMSTER", "departure": "INGA AVGÅNGAR"}[terminal.browse_filter]))
                hint = "C/D A:Kö" if choices else "B:Fil A:Kö"
        elif terminal.screen == "cancel":
            first, hint = ((row(t("ÅTER {number}?", number=selected['train_number'])), "#Ja *Nej") if "#" in buttons
                           else (row(t("LÄGET ÄNDRAT")), "*=Bak"))
        elif terminal.screen == "reject":
            first, hint = ((row(t("NEKA {number}?", number=selected['train_number'])), "#Ja *Nej") if "#" in buttons
                           else (row(t("LÄGET ÄNDRAT")), "*=Bak"))
        elif terminal.screen == "tracks":
            tracks = self._tracks(terminal)
            label = tracks[terminal.track % len(tracks)].display_label
            first, hint = row(t("{number} SPÅR {track}", number=selected['train_number'], track=label)), "#In C/D:Sp"
        elif terminal.screen == "active" and not active_position:
            first = row(t("LÄGET ÄNDRAT" if active else "INGA AKTIVA TÅG"))
            hint = "C/D B:Öv"
        elif terminal.screen in {"detail", "active"} and selected:
            label, side = self._label(terminal.station, selected)
            first = row(label) if side == "left" else row("", label)
            action = buttons.get("#", ("", ""))[0]
            hint = {"request": "#Beg A:Kö", "accept": "#Ja *Nej", "depart": "#Avg *Åter",
                    "arrive": "#In B:Sp"}.get(action, "*Åter B:Öv" if buttons.get("B", (None,))[0] == "home" else
                                               "C/D A:Kö" if "C" in buttons else "A:Kö *=Bak")
            if action == "request" and buttons["#"][1] == "Reservera":
                hint = "#Sändklar"
            if terminal.screen == "active":
                counter = f"{compact(active_position)}/{compact(len(active))}"
                hint = {"depart": "#Avg C/D", "arrive": "#In B:Sp"}.get(action, "C/D B:Öv")
                if len(text_cells(label)) + len(counter) < 16:
                    first = row(label, counter) if side == "left" else row(counter, label)
                else:
                    # Long identities stay intact. Count moves beside the
                    # action; exceptionally large lists show the total here.
                    prefix = t({"depart": "#Avg", "arrive": "#In"}.get(action, "C/D"))
                    hint = prefix + " " + counter
                    if len(text_cells(hint)) > 11:
                        hint = prefix + " " + compact(len(active))
        status = t("Skriv tågnummer direkt, eller bläddra med C/D")
        upcoming = None
        if selected and terminal.screen != "overview":
            schedule = self._schedule(terminal, selected)
            other = self.engine.config.stations[schedule["other"]].name
            direction = "Avgång till" if schedule["kind"] == "departure" else "Ankomst från"
            status = t("Tåg {number} · {direction} {station} · planerat {time}", number=selected['train_number'], direction=t(direction), station=other, time=schedule['time'])
            if terminal.screen == "browse":
                choices = self._candidates(terminal, filtered=True)
                position = choices.index(terminal.selected) + 1 if terminal.selected in choices else 0
                label = {"all": "alla tåg", "arrival": "ankomster", "departure": "avgångar"}[terminal.browse_filter]
                status = f"{position}/{len(choices)} · {t(label)} · " + status
                upcoming = {"position": position, "count": len(choices), "filter": terminal.browse_filter,
                            "kind": schedule["kind"], "time": schedule["time"], "movement_id": terminal.selected}
        if terminal.screen == "requests":
            status = (t("Förfrågan {position}/{count} · ", position=requests.index(terminal.selected)+1, count=len(requests)) + status
                      if terminal.selected in requests else t("Ingen vald förfrågan. A öppnar kön; B visar översikten."))
        if terminal.screen == "active":
            status = (t("Aktivt tåg {position}/{count}", position=active_position, count=len(active)) + " · " + status
                      if active_position else t("Ingen vald aktiv rörelse. C/D väljer; B visar översikten."))
        hint = t(hint)
        if terminal.receipt_until is not None:
            number, destination = terminal.receipts[0]
            station = self.engine.config.stations[destination]
            first, hint = row(number + " " + t("MOTTAGET")), station.code
            if len(text_cells(hint)) > 11:
                first, hint = row(number, station.code), t("MOTTAGET")
            status = t("Tåg {number} mottaget i {station}. Meddelandet försvinner automatiskt.", number=number, station=station.name)
        lines = [first, row(hint, clock)]
        entry_lines = [row(t("TÅG: _____")), row(t("#Sök B:Del"), clock)]
        return {
            "profile": "server-16x2-pilot", "device_id": device,
            "station": self.engine.config.stations[terminal.station].name,
            "station_code": self.engine.config.stations[terminal.station].code,
            "rows": 2, "cols": 16, "lines": lines, "lcd": encode_lcd(lines),
            "view_token": self._token(device, terminal),
            "revision": self.engine.revision, "view_revision": terminal.revision,
            "input_guard_ms": 500,
            "language": terminal.language,
            "keys": {key: {"label": t("Förfrågningskö ({count} väntar)", count=len(requests)) if key == "A" else
                      t("Aktiva tåg ({count})", count=len(active)) if action == "active" else t(label),
                      "short": self._short(t, key, action, label, len(requests), len(active)),
                      "acts": action not in NAVIGATION_ACTIONS} for key, (action, label) in buttons.items()},
            "entry": {"context": f"{self.epoch}:{device}:{terminal.station}", "max_length": 5,
                      "lines": entry_lines, "lcd": encode_lcd(entry_lines),
                      "row": 0, "column": 5, "commit": "#", "cancel": "*", "erase": "B",
                      "shortcut": "A",
                      "labels": {"#": t("Sök tåg (begär direkt)"), "*": t("Avbryt inmatning"), "B": t("Sudda siffra"),
                                 "A": t("Förfrågningskö (avbryt inmatning)")},
                      "short": {"#": t("SÖK"), "*": t("AVBRYT"), "B": t("SUDDA"), "A": t("KÖ")}},
            "requests": {"count": len(requests), "position": requests.index(terminal.selected) + 1 if terminal.selected in requests else 0,
                         "label": "A · " + t("Förfrågningskö ({count} väntar)", count=len(requests))},
            "active": {"count": len(active), "position": active_position if terminal.screen == "active" else 0,
                       "movement_id": terminal.selected if terminal.screen == "active" and active_position else None},
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
            self._advance_receipts(terminal)
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
            self._dismiss_receipt(terminal)
            matches = self._lookup(terminal, number)
            if len(matches) != 1:
                here = [leg for key, leg in self.legs.items() if leg["train_number"] == number and key not in self.completed
                        and terminal.station in {leg["from_station_id"], leg["to_station_id"]}]
                # The train is here, but on the lines the station's other box handles.
                other_side = here and not any(self._on_side(terminal, leg) for leg in here)
                terminal.notice = ("ANNAN SIDA" if other_side else "INGET TÅG") if not matches else "FLERA TÅG ADMIN"
            else:
                terminal.selected, terminal.screen, terminal.notice = matches[0], "detail", ""
                terminal.browse_filter = "all"
                # 93# is enough: a departure that can be asked for is asked for
                # at once, and * takes it back until the receiver has answered
                # (Casper, 2026-10-02). Anything else waits for its own key.
                if self._buttons(terminal).get("#", (None,))[0] == "request":
                    message = self._traffic(device, terminal, "request")
                    if message:
                        terminal.revision += 1
                        return self._answer(device, False, message)
            terminal.revision += 1
            return self._answer(device, True)
        if "train_number" in body:
            return self._answer(device, False, "Servern bestämmer funktion och mottagare")
        button = self._buttons(terminal).get(key)
        if not button:
            return self._answer(device, False, "Tangenten är inte tillgänglig i det här läget")
        self._dismiss_receipt(terminal)
        action = button[0]
        terminal.notice, terminal.notice_hint = "", ""
        terminal.notice_until = None
        if action == "back":
            terminal.screen = terminal.return_screen if terminal.screen in {"tracks", "cancel", "reject"} else "overview"
        elif action == "home":
            terminal.screen = "overview"
        elif action in {"requests", "next_request", "previous_request"}:
            self._open_requests(terminal, {"requests": 0, "next_request": 1, "previous_request": -1}[action])
        elif action in {"active", "next_active", "previous_active"}:
            self._open_active(terminal, {"active": 0, "next_active": 1, "previous_active": -1}[action])
        elif action in {"browse", "next", "previous"}:
            self._browse(terminal, {"browse": 0, "next": 1, "previous": -1}[action])
        elif action == "filter":
            terminal.browse_filter = {"all": "arrival", "arrival": "departure", "departure": "all"}[terminal.browse_filter]
            terminal.selected = None
            self._browse(terminal)
        elif action == "select":
            terminal.screen = "detail"
        elif action == "cancel_view":
            terminal.return_screen = terminal.screen
            terminal.screen = "cancel"
        elif action == "reject_view":
            terminal.return_screen = terminal.screen
            terminal.screen = "reject"
        elif action == "tracks":
            terminal.return_screen = terminal.screen
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
        arrival_track, occupied = None, False
        refused = lambda reason: {"connection_busy": "Sträckan är upptagen", "departure_not_reserved": "Klartecken saknas",
                                  "train_not_departed": "Tåget har inte avgått"}.get(reason, "Läget har ändrats. Välj tåget igen.")
        if action in {"arrive", "arrive_track"}:
            arrival_track = (self._tracks(terminal)[terminal.track % len(self._tracks(terminal))].id
                             if action == "arrive_track" else self._planned_track(leg))
            track = self.engine.config.tracks.get(arrival_track)
            if not track or not track.active or track.station_id != terminal.station:
                return "Ankomstspåret är inte giltigt"
            # Another train on the track does not stop the arrival (2.1.0).
            occupied = any(a["station"] == terminal.station and a["track"] == arrival_track for a in self.arrivals.values())
            reason = self._finish_leg(device, key)
            if reason:
                return refused(reason)
        else:
            if action == "depart" and self._is_active(leg) and self._line(leg).state == State.RESERVED:
                # Not seen to come in: it jumps here with the departure.
                for other, incoming in self.legs.items():
                    if incoming["to_movement_id"] == key and other not in self.completed:
                        reason = self._finish_leg(device, other)
                        if reason:
                            return refused(reason)
            accepted, reason = self.engine.perform(
                station_id=terminal.station, connection_id=leg["connection_id"], action=action,
                train_number=leg["train_number"], client_id=device)
            if not accepted:
                return refused(reason)
        if action == "request":
            self.bindings[leg["connection_id"]] = key
            if self._line(leg).state == State.REQUESTED:
                self._notify_request(leg)
        if action == "accept" and terminal.screen == "requests":
            # Stay with the accepted train, never repurpose a second # for another request.
            terminal.screen = "detail"
        if action in {"arrive", "arrive_track"}:
            self.arrivals[key] = {"station": terminal.station, "track": arrival_track,
                                  "planned_track": self._planned_track(leg), "movement_id": leg["to_movement_id"]}
            self.completed.add(key)
            terminal.notice = (f"{leg['train_number']} UPPT SPÅR" if occupied else
                               f"{leg['train_number']} ANK SP{self.engine.config.tracks[arrival_track].display_label}")
            terminal.notice_hint = self.engine.config.stations[leg["from_station_id"]].code
            terminal.notice_until = self.now() + NOTICE_SECONDS
            self._notify_arrival(leg)
        if action in {"cancel", "reject", "arrive", "arrive_track"}:
            self.bindings.pop(leg["connection_id"], None)
        if action == "cancel":
            terminal.notice = f"{leg['train_number']} ÅTERTAGET"
            terminal.notice_hint = self.engine.config.stations[leg["to_station_id"]].code
            terminal.notice_until = self.now() + NOTICE_SECONDS
            self._notify_withdrawn(leg, key)
        if action == "reject":
            terminal.notice = f"{leg['train_number']} NEKAT"
            terminal.notice_hint = self.engine.config.stations[leg["from_station_id"]].code
            terminal.notice_until = self.now() + NOTICE_SECONDS
            for other in self.terminals.values():
                if other.station == leg["from_station_id"] and other.selected == key:
                    other.notice = f"{leg['train_number']} NEKAT"
                    other.notice_hint = self.engine.config.stations[leg["to_station_id"]].code
                    other.notice_until = self.now() + NOTICE_SECONDS
                    other.revision += 1
        return ""
