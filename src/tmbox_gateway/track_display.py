"""Stationens spårvisning: vilka spår TMBox-appens Spår-vy visar, och i vilken ordning.

Detta är en lokal presentationsinställning på träffservern (TMBox iOS #46).
Den sparas per träff och station, utanför trafikmotorn, och ändrar aldrig
Clouds spårkatalog, sort_order, standardspårens prioritet, faktiska
spårplaceringar eller vad boxarnas tangenter erbjuder. Den skickas inte till Cloud.

Grunden är den aktiva publiceringens katalog i den ordning
``tracks_for_station`` ger. En sparad anpassning är en ordning av spår-id:n
och en mängd dolda id:n. När Cloud publicerar på nytt för samma träff
behålls valen för kvarvarande spår, nya spår läggs synliga sist och
borttagna spår försvinner ur listan.
"""
from __future__ import annotations

from typing import Any

from .models import SessionConfig, TrackConfig


def catalogue(config: SessionConfig, station_id: str) -> list[TrackConfig]:
    """Stationens aktiva spår i grundordning, samma lista som boxarnas spårval."""
    return config.tracks_for_station(station_id)


def effective(tracks: list[TrackConfig], saved: dict[str, Any] | None) -> list[tuple[TrackConfig, bool]]:
    """Spåren i visningsordning, vart och ett med om det visas.

    Okända id:n i en sparad anpassning (spår som tagits bort i Cloud) hoppas
    över. Spår som saknas i anpassningen (nya spår) läggs sist i grundordning
    och visas.
    """
    by_id = {track.id: track for track in tracks}
    order: list[str] = []
    for track_id in (saved or {}).get("order") or []:
        if track_id in by_id and track_id not in order:
            order.append(track_id)
    order += [track.id for track in tracks if track.id not in order]
    hidden = set((saved or {}).get("hidden") or []) & by_id.keys()
    return [(by_id[track_id], track_id not in hidden) for track_id in order]


def describe(config: SessionConfig, station_id: str, saved: dict[str, Any] | None,
             points: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    """Det klienter läser: revision, om stationen är anpassad och alla spår."""
    entries = []
    for track, visible in effective(catalogue(config, station_id), saved):
        entries.append({
            "id": track.id,
            "label": track.display_label,
            "visible": visible,
            "operating_point": (points or {}).get(track.operating_point_id) if track.operating_point_id else None,
        })
    return {
        "revision": int((saved or {}).get("revision") or 0),
        "customized": bool((saved or {}).get("order")),
        "tracks": entries,
    }


def visible_labels(config: SessionConfig, station_id: str, saved: dict[str, Any] | None) -> list[str]:
    """Det äldre fältet ``tracks``: synliga spår i visningsordning.

    Utan anpassning är listan exakt densamma som före #46.
    """
    return [track.display_label for track, visible in effective(catalogue(config, station_id), saved) if visible]


def parse_save(tracks: list[TrackConfig], value: Any) -> tuple[list[str], list[str]]:
    """Validera ``tracks`` i en sparning: hela katalogen, varje id exakt en gång.

    Fel form ger ValueError. Ett id som inte finns, eller ett spår som saknas,
    betyder att katalogen ändrats sedan klienten läste: KeyError.
    """
    if not isinstance(value, list) or not value:
        raise ValueError("Ange stationens spår.")
    order: list[str] = []
    hidden: list[str] = []
    seen: set[str] = set()
    for item in value:
        if (not isinstance(item, dict) or set(item) != {"id", "visible"}
                or not isinstance(item["id"], str) or not isinstance(item["visible"], bool)):
            raise ValueError("Varje spår anges med id och visible.")
        if item["id"] in seen:
            raise ValueError("Ett spår förekommer två gånger.")
        seen.add(item["id"])
        order.append(item["id"])
        if not item["visible"]:
            hidden.append(item["id"])
    if seen != {track.id for track in tracks}:
        raise KeyError("tracks_changed")
    return order, hidden


def operating_points(payload: dict[str, Any], station_id: str) -> dict[str, dict[str, str]]:
    """Stationens driftplatser ur publiceringen, så att lika spårnamn går att skilja åt."""
    for station in payload.get("stations") or []:
        if isinstance(station, dict) and station.get("id") == station_id:
            return {
                str(point["id"]): {"id": str(point["id"]), "code": str(point.get("code") or ""),
                                   "name": str(point.get("name") or "")}
                for point in station.get("operating_points") or []
                if isinstance(point, dict) and point.get("id")
            }
    return {}
