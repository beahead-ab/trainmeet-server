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


def train_live(services: list[dict], trains: list[dict], movement_live: dict[str, dict], now_seconds: float,
               on_line: set[str] | frozenset[str] = frozenset()) -> dict[str, dict]:
    """Tågnummer → {state, delay_minutes, late, estimated, early_minutes, early_kind, train_type}."""
    by_number: dict[str, dict] = {}
    for service in services:
        number = str(service.get("train_number") or "").strip()
        if number and (number not in by_number or len(service.get("stops") or []) > len(by_number[number].get("stops") or [])):
            by_number[number] = service
    index: dict[tuple, str | None] = {}
    for row in trains:
        key = (row.get("service_id"), row.get("station_id"), _hhmm(row.get("arrival_time")), _hhmm(row.get("departure_time")))
        index[key] = None if key in index else str(row.get("id"))
    now = now_seconds / 60
    result: dict[str, dict] = {}
    for number, service in by_number.items():
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
        if number in on_line or (last_index >= 0 and stops[last_index]["departed"] and last_index < len(stops) - 1):
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
        result[number] = {
            "state": state, "delay_minutes": max(0, delay), "late": delay >= LATE_MINUTES, "estimated": estimated,
            "early_minutes": early, "early_kind": measured if early else None,
            "train_type": str(service.get("train_type") or "person").lower(),
        }
    return result


def expected_time(planned: str, delay_minutes: int) -> str | None:
    """Den nya tiden för en planerad tid och en försening, eller None."""
    minute = _minutes(planned)
    if minute is None or delay_minutes < 1:
        return None
    value = int((minute + delay_minutes) % 1440)
    return f"{value // 60:02d}:{value % 60:02d}"
