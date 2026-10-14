"""Om trafikspelet: berättelsen, instruktionerna, programmet och stationernas
instruktioner, som Cloud skickar med paketet (``about`` och
``stations[].instructions``).

Texterna är träffens egna, i en liten delmängd av Markdown som
``web/markdown.js`` ritar. Servern lämnar dem som de är, men bara det som har
rätt form: ett paket från en äldre Cloud saknar fältet, och ett trasigt fält
får inte fälla träffen. ``/v1/about`` är öppen, som ``/v1/display``; texterna
är till för deltagarna.
"""
from __future__ import annotations

from typing import Any

LANGUAGES = ("sv", "da", "nb", "de", "en")
MAX_TEXT = 300_000
MAX_STATION_TEXT = 20_000


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def about_of(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Berättelse, instruktioner per språk och program ur paketet."""
    about = (payload or {}).get("about")
    if not isinstance(about, dict):
        return {"story": "", "instructions": {}, "program": ""}
    instructions = about.get("instructions") if isinstance(about.get("instructions"), dict) else {}
    return {
        "story": _text(about.get("story"), MAX_TEXT),
        "instructions": {language: text for language in LANGUAGES if (text := _text(instructions.get(language), MAX_TEXT))},
        "program": _text(about.get("program"), MAX_TEXT),
    }


def station_texts(payload: dict[str, Any] | None) -> list[dict[str, str]]:
    """Stationerna som har egna instruktioner, i paketets ordning."""
    out = []
    for station in (payload or {}).get("stations") or []:
        if not isinstance(station, dict):
            continue
        text = _text(station.get("instructions"), MAX_STATION_TEXT)
        if text:
            out.append({"id": str(station.get("id") or ""), "name": str(station.get("name") or ""), "code": str(station.get("code") or ""),
                        "instructions": text})
    return out


def summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Vad som finns att läsa, för /v1/display: klienterna visar knappen bara då."""
    about = about_of(payload)
    stations = station_texts(payload)
    return {"story": bool(about["story"]), "instructions": list(about["instructions"]), "program": bool(about["program"]),
            "stations": len(stations), "available": bool(about["story"] or about["instructions"] or about["program"] or stations)}
