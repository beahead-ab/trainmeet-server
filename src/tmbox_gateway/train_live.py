"""Var tågen är och hur sent eller för tidigt, som web/drift-model.js trainLive.

Samma regler i Python, för klienter som får tidtabellen färdig från servern
(webb-TMBoxen och iPhone-appen via /v1/tmbox/terminal/timetable):

- förseningen räknas ur den senaste händelsen med en verklig tid (avgång
  eller ankomst), i träffklockans sekunder ur movement_live;
- ett läge som bara tidtabellen gav, eller en övergång utan tid, säger inget
  om förseningen;
- ett tåg som står kvar efter sin avgångstid blir allt senare ("beräknad");
- ett tåg som är ute på linjen enligt sin kanal är på linjen, även utan tider;
- för tidigt är en verklig tid före den planerade. Om det ska synas avgör
  klienten: en för tidig avgång gäller bara persontåg.

Varje tåg räknas för sig (Cloud #100): två tåg som delar nummer samma dag,
som DSB IC 116 och SJ X 116, får var sitt svar under sitt tjänst-id. Under
numret står det tåg numret gäller just nu (det som är på väg, annars nästa,
annars det senast framkomna), så att den som bara vet numret, som en
kanal på linjen, får rätt tåg. Ett nummer som bara ett tåg har ger som förut
det tåget.

tests/test_train_live.py kör samma fall som tests/js/train-live.test.cjs.
"""
from __future__ import annotations

from typing import Any

LATE_MINUTES = 3


def _minutes(value: Any) -> float | None:
    text = str(value or "")
    if not text:
        return None
    parts = text.split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return None


def _between(a: float, b: float) -> float:
    """Minuter från a till b på ett dygn, mellan −12 och +12 timmar."""
    return ((b - a + 720) % 1440 + 1440) % 1440 - 720


def _hhmm(value: Any) -> str:
    return str(value or "")[:5]


def on_line_trains(connection_states: list[dict], positions: list[dict]) -> set[str]:
    """Tågnummer som har avgått ut på en linje, som drift-model.js trains().

    En upptagen kanal säger att tåget är ute. En klarerad (reserverad) kanal
    gör det inte, och då gäller inte heller ett läge på linjen för tåget.
    """
    departed, seen = set(), set()
    for connection in connection_states:
        for channel in connection.get("channels") or []:
            number = str(channel.get("train_number") or "")
            if number and channel.get("state") in {"reserved", "occupied"}:
                seen.add(number)
                if channel.get("state") == "occupied":
                    departed.add(number)
    for position in positions:
        number = str(position.get("train_number") or "")
        if number and number not in seen and position.get("status") == "connection":
            departed.add(number)
    return departed


IN_PROGRESS = ("on_line", "at_station", "waiting")


def _current(candidates: list[dict]) -> dict:
    """Det tåg ett delat nummer gäller nu: på väg, annars nästa, annars det senaste."""
    moving = [item for item in candidates if item["state"] in IN_PROGRESS]
    if moving:
        return moving[0]
    coming = sorted((item for item in candidates if item["state"] == "not_departed"), key=lambda item: item["_start"])
    if coming:
        return coming[0]
    return max(candidates, key=lambda item: item["_start"])


def train_live(services: list[dict], trains: list[dict], movement_live: dict[str, dict], now_seconds: float,
               on_line: set[str] | frozenset[str] = frozenset()) -> dict[str, dict]:
    """Tjänst-id och tågnummer → {state, delay_minutes, late, estimated, early_minutes, early_kind,
    train_type, train_number, service_id}.

    Varje tåg finns under sitt tjänst-id. Numret ger det tåg numret gäller
    nu; delar två tåg nummer avgör det som händer i movement_live vilket.
    """
    services = [service for service in services if str(service.get("train_number") or "").strip()]
    shared: dict[str, int] = {}
    for service in services:
        number = str(service.get("train_number")).strip()
        shared[number] = shared.get(number, 0) + 1
    index: dict[tuple, str | None] = {}
    for row in trains:
        key = (row.get("service_id"), row.get("station_id"), _hhmm(row.get("arrival_time")), _hhmm(row.get("departure_time")))
        index[key] = None if key in index else str(row.get("id"))
    now = now_seconds / 60
    result: dict[str, dict] = {}
    by_number: dict[str, list[dict]] = {}
    for service in services:
        number = str(service.get("train_number")).strip()
        stops = []
        for stop in sorted(service.get("stops") or [], key=lambda item: int(item.get("stop_order") or 0)):
            offset = int(stop.get("service_day_offset") or 0) * 1440
            movement = index.get((service.get("id"), stop.get("station_id"), _hhmm(stop.get("arrival_time")), _hhmm(stop.get("departure_time"))))
            state = movement_live.get(movement or "") or {}
            arrival, departure = _minutes(stop.get("arrival_time")), _minutes(stop.get("departure_time"))
            stops.append({
                "planned_arrival": None if arrival is None else arrival + offset,
                "planned_departure": None if departure is None else departure + offset,
                "arrived": state.get("arrival") == "arrived",
                "departed": state.get("departure") == "departed",
                "actual_arrival": state["arrived_seconds"] / 60 if isinstance(state.get("arrived_seconds"), (int, float)) else None,
                "actual_departure": state["departed_seconds"] / 60 if isinstance(state.get("departed_seconds"), (int, float)) else None,
            })
        if not stops:
            continue
        delay, estimated, measured = 0.0, False, None
        last_index = max((i for i, stop in enumerate(stops) if stop["arrived"] or stop["departed"]), default=-1)
        for stop in reversed(stops):
            if stop["departed"] and stop["actual_departure"] is not None and stop["planned_departure"] is not None:
                delay, measured = _between(stop["planned_departure"], stop["actual_departure"]), "dep"
                break
            if stop["arrived"] and stop["actual_arrival"] is not None and stop["planned_arrival"] is not None:
                delay, measured = _between(stop["planned_arrival"], stop["actual_arrival"]), "arr"
                break
        last = stops[-1]
        # En kanal säger bara numret. Delar två tåg det gäller kanalen det
        # tåg som har avgått men inte kommit fram, aldrig ett som är framme
        # eller ännu inte har gått.
        channel = number in on_line and (shared[number] == 1 or (last_index >= 0 and not last["arrived"]))
        if channel or (last_index >= 0 and stops[last_index]["departed"] and last_index < len(stops) - 1):
            state = "on_line"
        elif last["arrived"]:
            state = "arrived"
        else:
            here = stops[max(0, last_index)]
            overdue = _between(here["planned_departure"], now) if here["planned_departure"] is not None else -1
            state = "not_departed" if last_index < 0 and overdue < 0 else "at_station" if last_index >= 0 and overdue < 0 else "waiting"
            if state == "waiting" and int(overdue // 1) > delay:
                delay, estimated, measured = float(int(overdue // 1)), True, None
        delay = round(delay)
        early = -delay if delay < 0 else 0
        start = next((value for stop in stops for value in (stop["planned_departure"], stop["planned_arrival"]) if value is not None), 0)
        item = {
            "state": state, "delay_minutes": max(0, delay), "late": delay >= LATE_MINUTES, "estimated": estimated,
            "early_minutes": early, "early_kind": measured if early else None,
            "train_type": str(service.get("train_type") or "person").lower(),
            "train_number": number, "service_id": service.get("id"), "_start": start,
        }
        if service.get("id"):
            result[str(service["id"])] = item
        by_number.setdefault(number, []).append(item)
    for number, candidates in by_number.items():
        result[number] = _current(candidates)
    for item in {id(value): value for value in result.values()}.values():
        item.pop("_start", None)
    return result


def live_for_row(lives: dict[str, dict], row: dict, service_ids: dict[str, str]) -> dict | None:
    """Tåget en tidtabellsrad gäller: radens tjänst om den är känd, annars numret."""
    service = service_ids.get(str(row.get("movement_id") or row.get("id") or ""))
    if service and service in lives:
        return lives[service]
    return lives.get(str(row.get("train_number") or ""))


def expected_time(planned: str, delay_minutes: int) -> str | None:
    """Den nya tiden för en planerad tid och en försening, eller None."""
    minute = _minutes(planned)
    if minute is None or delay_minutes < 1:
        return None
    value = int((minute + delay_minutes) % 1440)
    return f"{value // 60:02d}:{value % 60:02d}"
