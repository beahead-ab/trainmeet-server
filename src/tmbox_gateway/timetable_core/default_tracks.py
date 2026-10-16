"""Standardspår: det spår ett tåg tar på en station när tidtabellen inte säger något.

Stationen har spår per tågsort och färdriktning. Tågsorterna är persontåg,
godståg (arbetståg räknas dit) och tåg som passerar utan uppehåll.
Färdriktningen är åt vänster eller åt höger på banskissen i Cloud: ett tåg
som kommer in från en sträcka på stationens vänstra sida kör åt höger.
Varje ruta kan ha flera spår, till exempel godsbangårdens ankomst- och
avgångsspår 11, 12 och 13, och ordningen är stationens förstahandsval:
på Charlottendal tar ett persontåg från höger 2b och står 2b upptaget 2a
bakom. Reservspåren gäller alla tåg åt båda hållen och tas när spåren i
tågets ruta är upptagna.

Tidtabellens spår går alltid före. Standardspåren används bara när raden
saknar spår, och de skrivs aldrig in i tidtabellen.

    default_tracks = {"person": {"left": [track_id, ...], "right": [...]},
                      "goods": {...}, "pass": {...}, "reserve": [track_id, ...]}

Paketet bär också stationens sidor som de såg ut i Cloud, så att servern
läser riktningen som den var tänkt även om en box vänds på plats:

    default_track_sides = {"left": [neighbor_station_id, ...], "right": [...]}
"""
from __future__ import annotations

from typing import Any, Iterable

CATEGORIES = ("person", "goods", "pass")
DIRECTIONS = ("left", "right")
RESERVE = "reserve"


def station_track_ids(station: dict[str, Any]) -> list[str]:
    """Stationens aktiva spår i stationens egen ordning, driftplatsernas sist."""
    owners = [station, *(station.get("operating_points") or [])]
    ids: list[str] = []
    for owner in owners:
        entries = [entry for entry in owner.get("tracks") or [] if isinstance(entry, dict)]
        for entry in sorted(entries, key=lambda item: item.get("sort_order") or 0):
            track_id = entry.get("id")
            if isinstance(track_id, str) and entry.get("active", True) is not False and track_id not in ids:
                ids.append(track_id)
    return ids


def clean_default_tracks(value: Any, station: dict[str, Any]) -> dict[str, Any] | None:
    """Bara kända tågsorter, riktningar och stationens egna aktiva spår, utan dubbletter.

    Ordningen behålls: den är stationens förstahandsval. Ett spår som tagits
    bort eller stängts faller bort tyst; en tom ruta betyder att tågsorten
    inte har något standardspår åt det hållet.
    """
    if not isinstance(value, dict):
        return None
    known = set(station_track_ids(station))

    def kept(chosen: Any) -> list[str]:
        found: list[str] = []
        for track_id in chosen if isinstance(chosen, list) else []:
            if isinstance(track_id, str) and track_id in known and track_id not in found:
                found.append(track_id)
        return found

    cleaned: dict[str, Any] = {}
    for category in CATEGORIES:
        entry = value.get(category)
        if not isinstance(entry, dict):
            continue
        sides = {direction: ids for direction in DIRECTIONS if (ids := kept(entry.get(direction)))}
        if sides:
            cleaned[category] = sides
    if reserve := kept(value.get(RESERVE)):
        cleaned[RESERVE] = reserve
    return cleaned or None


def train_category(row: dict[str, Any]) -> str:
    """Passerar utan uppehåll, annars godståg (arbetståg räknas dit), annars persontåg."""
    if row.get("no_stop"):
        return "pass"
    return "goods" if row.get("train_type") in {"goods", "work"} else "person"


def travel_direction(sides: dict[str, Any] | None, previous_station_id: str | None,
                     next_station_id: str | None) -> str | None:
    """Åt vänster eller åt höger, ur grannstationen tåget kör mot eller kommer ifrån.

    Nästa station avgör först; ett tåg som slutar här räknas efter den
    station det kom ifrån. None när ingen av dem är en granne på någon sida.
    """
    sides = sides if isinstance(sides, dict) else {}
    left, right = set(sides.get("left") or []), set(sides.get("right") or [])
    if next_station_id in right:
        return "right"
    if next_station_id in left:
        return "left"
    if previous_station_id in left:
        return "right"
    if previous_station_id in right:
        return "left"
    return None


def default_track_ids(default_tracks: dict[str, Any] | None, category: str, direction: str | None,
                      train_type: str | None = None) -> list[str]:
    """Standardspåren för tågsorten åt hållet, i stationens valda ordning, och sist reservspåren.

    Ett passerande tåg utan egna standardspår tar sin tågsorts. Okänd
    riktning ger båda riktningarnas spår, vänster först.
    """
    tracks = default_tracks if isinstance(default_tracks, dict) else {}

    def chosen(name: str) -> list[str]:
        entry = tracks.get(name) if isinstance(tracks.get(name), dict) else {}
        directions: Iterable[str] = (direction,) if direction in DIRECTIONS else DIRECTIONS
        found: list[str] = []
        for side in directions:
            for track_id in entry.get(side) or []:
                if track_id not in found:
                    found.append(track_id)
        return found

    found = chosen(category)
    if not found and category == "pass":
        found = chosen("goods" if train_type in {"goods", "work"} else "person")
    reserve = tracks.get(RESERVE)
    for track_id in reserve if isinstance(reserve, list) else []:
        if track_id not in found:
            found.append(track_id)
    return found


def passing_tracks(main_tracks: Iterable[dict[str, Any]], sides: dict[str, Any] | None) -> dict[str, list[str]]:
    """Passerande tågs spår ur huvudspårens riktning, som planeringsprogrammet anger den.

    Ett huvudspår som går mot en granne till höger, eller kommer från en
    granne till vänster, är spåret åt höger. Ett huvudspår utan riktning som
    gäller åt båda hållen hamnar i båda; ett med riktning i sin egen.
    """
    found: dict[str, list[str]] = {}
    for track in main_tracks:
        track_id = track.get("track_id")
        if not isinstance(track_id, str):
            continue
        direction = travel_direction(sides, track.get("from_station_id"), track.get("to_station_id"))
        directions = [direction] if direction else list(DIRECTIONS) if track.get("both") else []
        for direction in directions:
            if track_id not in found.setdefault(direction, []):
                found[direction].append(track_id)
    return {direction: ids for direction, ids in found.items() if ids}
