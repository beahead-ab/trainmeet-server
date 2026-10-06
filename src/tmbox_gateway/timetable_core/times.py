"""Namn, id, trafikdagar och klockslag, som Cloud och servern skriver dem."""
from __future__ import annotations

import re
import uuid
from typing import Any


def normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def safe_id(prefix: str, value: str) -> str:
    text = normalized(value).replace("å", "a").replace("ä", "a").replace("ö", "o")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-") or str(uuid.uuid4())[:8]
    return f"{prefix}-{text}"


def canonical_days(value: str) -> str:
    text = str(value or "Dagl").strip()
    if not text or re.match(r"^dagl(igen)?$", text, re.I) or normalized(text) in {"-", "—", "(ej specificerat)", "ej specificerat", "okant", "okänt"}:
        return "Dagl"
    aliases = {"M": "Mån", "Ti": "Tis", "O": "Ons", "On": "Ons", "To": "Tor", "F": "Fre", "Fr": "Fre", "L": "Lör", "Lö": "Lör", "S": "Sön", "Sö": "Sön"}
    return ",".join("-".join(aliases.get(day.strip(), day.strip()) for day in part.split("-")) for part in text.split(","))


def parse_clock(value: Any) -> str | None:
    text = str(value or "").strip()
    match = re.match(r"^(\d{1,2})[:.]([0-5]\d)$", text)
    if not match:
        return None
    hour = int(match.group(1))
    return f"{hour:02d}:{match.group(2)}" if hour < 24 else None


def clock_minutes(value: Any) -> int | None:
    clock = parse_clock(value)
    if not clock:
        return None
    hour, minute = (int(part) for part in clock.split(":"))
    return hour * 60 + minute
