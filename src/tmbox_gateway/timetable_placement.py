"""Var tågen står enligt tidtabellen vid en viss klocktid (#136).

Efter en nollställning, vid träffens start och när admin ställer klockan står
varje tåg där tidtabellen säger: på den station det senast skulle ha kommit
till, på stoppets planerade spår. Ett tåg som enligt tidtabellen är ute på
linjen har ingen klarering och står därför kvar på avgångsstationen. Det som
tåget redan skulle ha gjort före den stationen räknas som gjort, så att
automatiken och "tåget hoppar fram" inte går tillbaka genom historiken.

Här räknas bara lägena. Operations skriver dem, och rör aldrig ett tåg som
har verkliga händelser.
"""
from __future__ import annotations

from typing import Any

from .runtime import matches_active_day
from .train_routes import _order, _visits

#: Den som står som uppdaterare för ett läge som tidtabellen gav.
PLACED_BY = "tidtabell"


def _seconds(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + (int(parts[2]) if len(parts) > 2 else 0)
    except (IndexError, ValueError):
        return None


def _journey(payload: dict[str, Any], service: dict[str, Any], day: str) -> list[dict[str, Any]] | None:
    """Tjänstens stopp i ordning med absoluta tider och rörelsen för varje stopp.

    Samma dygnsräkning som automatiken (simulation.plan_legs). En tjänst där
    ett stopp inte går att para ihop med exakt en rörelse hoppas över.
    """
    stops = sorted(service.get("stops", []), key=lambda stop: _order(stop.get("stop_order")))
    rows = [row for row in payload.get("trains", [])
            if row.get("service_id") == service.get("id") and matches_active_day(str(row.get("days", "")), day)]
    result = []
    for index, stop in enumerate(stops):
        matches = [row for row in rows if index in _visits(stops, row)]
        if len(matches) != 1:
            return None
        offset = int(stop.get("service_day_offset") or 0) * 86400
        arrival = _seconds(stop.get("arrival_time"))
        departure = _seconds(stop.get("departure_time"))
        arrival = arrival + offset if arrival is not None else None
        departure = departure + offset if departure is not None else None
        if arrival is not None and departure is not None and departure < arrival:
            departure += 86400
        result.append({"movement_id": str(matches[0]["id"]), "station_id": str(stop["station_id"]),
                       "track_id": matches[0].get("track_id") or None, "arrival": arrival, "departure": departure})
    return result if result else None


def timetable_positions(payload: dict[str, Any], day: str, clock_seconds: float) -> dict[str, dict[str, Any]]:
    """{tågnummer: {"station_id", "track_id", "states": {rörelse-id: läge}}}.

    Läget för en rörelse är {"station_id", "arrival", "departure", "track_id"}
    med TKL:s värden. Rörelser som tåget ännu inte nått har inget läge. Ett tåg
    som inte står någonstans än (se nedan) har station_id None.
    """
    journeys: dict[str, list[list[dict[str, Any]]]] = {}
    for service in payload.get("services", []):
        if not matches_active_day(str(service.get("days", "")), day):
            continue
        journey = _journey(payload, service, day)
        if journey and any(stop["arrival"] is not None or stop["departure"] is not None for stop in journey):
            journeys.setdefault(str(service.get("train_number")), []).append(journey)
    arrivals: dict[tuple[str, str], list[int]] = {}
    for runs in journeys.values():
        for journey in runs:
            for index, stop in enumerate(journey):
                if index and stop["arrival"] is not None and stop["track_id"]:
                    arrivals.setdefault((stop["station_id"], str(stop["track_id"])), []).append(stop["arrival"])

    def first_time(journey):
        return next(t for stop in journey for t in (stop["departure"], stop["arrival"]) if t is not None)

    def last_time(journey):
        return next(t for stop in reversed(journey) for t in (stop["arrival"], stop["departure"]) if t is not None)

    def reached(journey):
        """Det sista stopp tåget skulle ha kommit till vid klockan, -1 om inget.

        Ett första stopp utan ankomsttid är där tåget börjar; har det en
        ankomsttid kommer tåget utifrån och är där först vid den tiden.
        """
        return max((index for index, stop in enumerate(journey)
                    if (index == 0 and stop["arrival"] is None)
                    or (stop["arrival"] is not None and stop["arrival"] <= clock_seconds)), default=-1)

    result = {}
    for number, runs in journeys.items():
        runs.sort(key=first_time)
        # Samma tågnummer flera gånger samma dag: den körning som har börjat
        # senast gäller, annars den första.
        started = [journey for journey in runs if first_time(journey) <= clock_seconds]
        current_run = started[-1] if started else runs[0]
        states: dict[str, dict[str, Any]] = {}
        for journey in runs:
            if journey is not current_run and last_time(journey) > clock_seconds:
                continue  # en senare körning: inget har hänt än
            # En tidigare körning är helt avklarad.
            last = reached(journey) if journey is current_run else len(journey)
            for index, stop in enumerate(journey[:last + 1]):
                done = index < last
                states[stop["movement_id"]] = {
                    "station_id": stop["station_id"], "track_id": stop["track_id"],
                    "arrival": "arrived" if stop["arrival"] is not None else "none",
                    "departure": ("departed" if done else "positioned") if stop["departure"] is not None else "none",
                }
        if reached(current_run) < 0:
            result[number] = {"station_id": None, "track_id": None, "states": states}
            continue
        here = current_run[reached(current_run)]
        if (here is current_run[0] and here["arrival"] is None and here["departure"] is not None
                and here["departure"] > clock_seconds and here["track_id"]):
            # Ett tåg som börjar här står inte där om ett annat tåg enligt
            # tidtabellen kommer in på samma spår innan det avgår: oftast är det
            # samma tågsätt som vänder och byter nummer. Det ställs upp när det
            # tåget har kommit, av automatiken eller av tågklareraren.
            if any(clock_seconds < arrival <= here["departure"]
                   for arrival in arrivals.get((here["station_id"], str(here["track_id"])), [])):
                for stop in current_run:
                    states.pop(stop["movement_id"], None)
                result[number] = {"station_id": None, "track_id": None, "states": states}
                continue
        result[number] = {"station_id": here["station_id"], "track_id": here["track_id"], "states": states}
    return result
