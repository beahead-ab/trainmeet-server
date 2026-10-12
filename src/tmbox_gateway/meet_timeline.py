"""Träffens tid på Drift: dygnen på rad och vad ett hopp gör (Casper 2026-10-12).

Tidslinjen under klockan visar träffens dygn, hur mycket trafik varje dygn
har och var träffen står nu. Admin drar i den för att välja en dag och en tid
och ser, innan något ändras, hur tågen kommer att stå där. Hoppet görs sedan
av tidsmaskinen som förut.

Här räknas bara. Lägena räknas med samma regler som tidsmaskinen ställer
tågen med (timetable_placement), så att förhandsvisningen säger det som
sedan händer. Ett tåg som enligt tidtabellen är ute på linjen ställs på sin
avgångsstation, utan klarering; det räknas här som ute på linjen.

Sekunderna gäller den valda dagen och kan gå förbi midnatt (nattåg hör till
dagen de började på), som träffklockans löpande tid.
"""
from __future__ import annotations

from typing import Any

from .runtime import matches_active_day
from .timetable_placement import _journey, timetable_positions

#: Staplarna i tidslinjen: en per halvtimme.
SLOT_SECONDS = 1800


def _journeys(payload: dict[str, Any], day: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """(tågnummer, körning) för dagens körningar, i tidsordning."""
    out = []
    for service in payload.get("services", []):
        if not matches_active_day(str(service.get("days", "")), day):
            continue
        journey = _journey(payload, service, day)
        if journey and _first(journey) is not None:
            out.append((str(service.get("train_number")), journey))
    out.sort(key=lambda item: _first(item[1]))
    return out


def _first(journey: list[dict[str, Any]]) -> int | None:
    return next((t for stop in journey for t in (stop["departure"], stop["arrival"]) if t is not None), None)


def _last(journey: list[dict[str, Any]]) -> int | None:
    return next((t for stop in reversed(journey) for t in (stop["arrival"], stop["departure"]) if t is not None), None)


def traffic_density(payload: dict[str, Any], day: str, start: float, end: float) -> list[int]:
    """Hur många tåg som är i gång under varje halvtimme från `start` till `end`.

    Ett tåg är i gång från sin första tid till sin sista. Sekunderna är
    dagens, och `end` kan ligga efter midnatt.
    """
    slots = max(1, int(-(-(end - start) // SLOT_SECONDS)))
    counts = [0] * slots
    for _number, journey in _journeys(payload, day):
        first, last = _first(journey), _last(journey)
        if first is None or last is None:
            continue
        lo = max(0, int((first - start) // SLOT_SECONDS))
        hi = min(slots - 1, int((last - start) // SLOT_SECONDS))
        for index in range(lo, hi + 1):
            counts[index] += 1
    return counts


def first_and_last(payload: dict[str, Any], day: str) -> tuple[int | None, int | None]:
    """Dagens första och sista tågtid, i sekunder på dagen."""
    times = [(_first(journey), _last(journey)) for _number, journey in _journeys(payload, day)]
    firsts = [first for first, _last_time in times if first is not None]
    lasts = [last for _first_time, last in times if last is not None]
    return (min(firsts) if firsts else None, max(lasts) if lasts else None)


def _hhmm(seconds: float) -> str:
    minutes = int(seconds // 60) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def preview(payload: dict[str, Any], day: str, seconds: float) -> dict[str, Any]:
    """Hur tågen står på dagen `day` vid `seconds` om träffen hoppar dit.

    {"on_line", "at_station", "not_started", "finished", "trains", "next"}:
    tåg som enligt tidtabellen är ute på linjen (de ställs på sin
    avgångsstation), som står på en station, som inte har börjat och som är
    klara för dagen, alla tåg som går den dagen och nästa avgång efter tiden.
    """
    names = {str(station.get("id")): str(station.get("name") or station.get("code") or station.get("id"))
             for station in payload.get("stations", [])}
    positions = timetable_positions(payload, day, seconds)
    runs: dict[str, list[list[dict[str, Any]]]] = {}
    for number, journey in _journeys(payload, day):
        runs.setdefault(number, []).append(journey)
    counts = {"on_line": 0, "at_station": 0, "not_started": 0, "finished": 0}
    next_departure = None
    for number, journeys in runs.items():
        if all((_last(journey) or 0) <= seconds for journey in journeys):
            counts["finished"] += 1
        elif positions.get(number, {}).get("station_id") is None:
            counts["not_started"] += 1
        elif _between_stations(journeys, seconds):
            counts["on_line"] += 1
        else:
            counts["at_station"] += 1
        for journey in journeys:
            for index, stop in enumerate(journey[:-1]):
                departure = stop["departure"]
                if departure is not None and departure > seconds and (next_departure is None or departure < next_departure[0]):
                    next_departure = (departure, number, stop["station_id"], journey[-1]["station_id"])
    result: dict[str, Any] = {**counts, "trains": len(runs), "next": None}
    if next_departure is not None:
        at, number, origin, destination = next_departure
        result["next"] = {"train_number": number, "time": _hhmm(at), "from": names.get(origin, origin),
                          "to": names.get(destination, destination)}
    return result


def _between_stations(journeys: list[list[dict[str, Any]]], seconds: float) -> bool:
    """Har tåget enligt tidtabellen lämnat ett stopp men inte kommit till nästa?"""
    for journey in journeys:
        for here, after in zip(journey, journey[1:]):
            if (here["departure"] is not None and after["arrival"] is not None
                    and here["departure"] <= seconds < after["arrival"]):
                return True
    return False
