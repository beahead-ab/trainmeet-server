"""Standardspår på servern: spåret ett tåg tar när tidtabellen inte anger något.

Cloud skickar stationens standardspår per tågsort och färdriktning
(`stations[].default_tracks`) och stationens sidor som de ser ut i Cloud
(`default_track_sides`). Reglerna är timetable_core.default_tracks, samma
kod som Cloud. Tidtabellens spår går alltid före, och ett standardspår
skrivs aldrig in i tidtabellen: det blir tågets verkliga spår först när
tåget tas emot. Det första lediga spåret i rutan vinner; är alla upptagna
blir det det första, som när ett planerat spår är upptaget.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from .timetable_core.default_tracks import default_track_ids, train_category, travel_direction


def _station(payload: dict[str, Any], station_id: Any) -> dict[str, Any] | None:
    return next((item for item in payload.get("stations") or [] if str(item.get("id")) == str(station_id)), None)


def _stops(payload: dict[str, Any], service_id: Any) -> list[dict[str, Any]]:
    service = next((item for item in payload.get("services") or [] if item.get("id") == service_id), None)
    return sorted(service.get("stops") or [], key=lambda stop: int(stop.get("stop_order") or 0)) if service else []


def neighbours(payload: dict[str, Any], row: dict[str, Any],
               stops: list[dict[str, Any]] | None = None) -> tuple[str | None, str | None]:
    """Stationen tåget kommer ifrån och stationen det kör mot, ur tjänstens stopp."""
    stops = _stops(payload, row.get("service_id")) if stops is None else stops
    here = [index for index, stop in enumerate(stops) if str(stop.get("station_id")) == str(row.get("station_id"))]
    if not here:
        return None, None
    # Ett tåg som besöker stationen två gånger: besöket med radens tider.
    index = next((index for index in here if any(
        row.get(key) and stops[index].get(key) == row.get(key) for key in ("arrival_time", "departure_time"))), here[0])
    previous = str(stops[index - 1]["station_id"]) if index > 0 else None
    following = str(stops[index + 1]["station_id"]) if index + 1 < len(stops) else None
    return previous, following


def candidates(payload: dict[str, Any], row: dict[str, Any], stops: list[dict[str, Any]] | None = None) -> list[str]:
    """Radens standardspår i stationens ordning: bara stationens egna aktiva spår."""
    station = _station(payload, row.get("station_id"))
    if not station or not station.get("default_tracks"):
        return []
    previous, following = neighbours(payload, row, stops)
    direction = travel_direction(station.get("default_track_sides"), previous, following)
    usable = {str(track["id"]) for track in payload.get("tracks") or []
              if track.get("active", True) is not False and str(track.get("station_id")) == str(row.get("station_id"))}
    return [track_id for track_id in default_track_ids(station["default_tracks"], train_category(row), direction,
                                                       row.get("train_type")) if track_id in usable]


_ALL: dict[int, tuple[dict[str, Any], dict[str, list[str]]]] = {}


def all_candidates(payload: dict[str, Any]) -> dict[str, list[str]]:
    """{rad-id: standardspår} för paketets alla rader, räknat en gång per paket.

    Tidtabellen som klienterna läser byggs om ofta; paketet ändras inte.
    """
    cached = _ALL.get(id(payload))
    if cached is not None and cached[0] is payload:
        return cached[1]
    result: dict[str, list[str]] = {}
    stations = {str(item.get("id")) for item in payload.get("stations") or [] if item.get("default_tracks")}
    if stations:
        services = {item.get("id"): sorted(item.get("stops") or [], key=lambda stop: int(stop.get("stop_order") or 0))
                    for item in payload.get("services") or []}
        for row in payload.get("trains") or []:
            if str(row.get("station_id")) in stations:
                found = candidates(payload, row, services.get(row.get("service_id"), []))
                if found:
                    result[str(row["id"])] = found
    if len(_ALL) > 4:
        _ALL.clear()
    _ALL[id(payload)] = (payload, result)
    return result


def choose(payload: dict[str, Any], row: dict[str, Any], occupied: Callable[[str], Any]) -> str | None:
    """Det första lediga standardspåret, annars det första; None utan standardspår."""
    found = candidates(payload, row)
    return next((track_id for track_id in found if not occupied(track_id)), found[0] if found else None)


def ordered(first: str | None, defaults: Iterable[str], tracks: Iterable[str]) -> list[str]:
    """Spårvalets ordning: tågets spår först, sedan standardspåren, sedan resten."""
    result: list[str] = []
    for track_id in [first, *defaults, *tracks]:
        if track_id and track_id not in result:
            result.append(track_id)
    return result
