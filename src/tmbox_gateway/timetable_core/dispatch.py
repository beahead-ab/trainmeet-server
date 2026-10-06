"""Trafikspelets trafikläge: ett enda val för hela trafikspelet.

- "clearance": grannstationen godkänner varje tåg.
- "direct": sträckan tas direkt om den är ledig; en begäran på en ledig
  sträcka medges genast.

Samma ord används i Clouds utkast, i driftpaketet, på servern och i TKL.
Det finns inget eget läge per sträcka. Utkast från före 2026-10 kan ha det
äldre ordet "automatic", som betyder "direct".
"""
from __future__ import annotations

from typing import Any

CLEARANCE, DIRECT = "clearance", "direct"
MODES = (CLEARANCE, DIRECT)
#: Äldre ord i Clouds utkast.
ALIASES = {"automatic": DIRECT}


def dispatch_mode(value: Any) -> str:
    """Trafikspelets läge. Tomt är "clearance"; ett okänt värde är ett fel."""
    if value in (None, ""):
        return CLEARANCE
    mode = ALIASES.get(value, value)
    if mode not in MODES:
        raise ValueError(f"Okänt trafikläge: {value}")
    return mode
