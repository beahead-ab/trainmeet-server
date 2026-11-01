"""Tågens tjänster och rutter, härledda ur stationernas tidtabellsrader.

Från och Till i en stationstidtabell är tågets ursprung och slutstation, inte
grannstationerna. Vägen tåget tar kommer därför ur samma tåg på stationerna
i tidsordning.
"""
from __future__ import annotations

import re
from typing import Any

from .times import canonical_days, clock_minutes, normalized, safe_id


def merge_text(left: Any, right: Any) -> str:
    values: list[str] = []
    for candidate in (left, right):
        text = str(candidate or "").strip()
        if not text:
            continue
        if any(normalized(text) == normalized(value) for value in values):
            continue
        # OCR often returns one complete note and one shortened copy. Keep the
        # complete text instead of showing both variants to the organizer.
        contained = next((value for value in values if normalized(text) in normalized(value)), None)
        if contained:
            continue
        values = [value for value in values if normalized(value) not in normalized(text)]
        values.append(text)
    return "\n".join(values)


#: Största tillåtna lucka mellan två stopp i samma tåg när dygnen står i
#: raderna. Är luckan större stämmer dygnen inte med tiderna längre (någon
#: har till exempel ändrat en tid i rutnätet) och ordningen räknas fram som
#: för rader utan dygn.
EXPLICIT_DAY_GAP = 12 * 60


def service_key(row: dict[str, Any]) -> str:
    """Vad som skiljer två tåg med samma nummer och dagar åt, t.ex. "SJ X"."""
    return " ".join(str(row.get("service_key") or "").split())


def service_label(number: str, key: str) -> str:
    """Tågets namn i texter: numret, och bolag/förkortning när numret delas."""
    return f"{number} ({key})" if key else number


def service_groups(rows: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Raderna per tåg: (nummer, trafikdagar, nyckel).

    Nyckeln är tom för de flesta tåg. Den finns bara när två tåg delar nummer
    och dagar (SJ X 116 och DSB IC 116). En rad utan nyckel hör till det enda
    tåget med nyckel bland samma nummer och dagar; finns flera sådana tåg kan
    raden inte placeras och blir ett eget tåg utan nyckel.
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    keys: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        base = (str(row.get("train_number") or ""), canonical_days(str(row.get("days") or "Dagl")))
        if service_key(row):
            keys.setdefault(base, set()).add(service_key(row))
    for row in rows:
        number, days = str(row.get("train_number") or ""), canonical_days(str(row.get("days") or "Dagl"))
        key = service_key(row)
        if not key and len(keys.get((number, days), ())) == 1:
            key = next(iter(keys[(number, days)]))
        groups.setdefault((number, days, key), []).append(row)
    return groups


def _absolute_minute(row: dict[str, Any]) -> int | None:
    """Minuten räknad från tågets trafikdag, när raden bär sitt dygn."""
    minute, day = clock_minutes(row.get("sort_time")), row.get("day_offset")
    if minute is None or isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 30:
        return None
    return day * 24 * 60 + minute


def explicit_day_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Raderna i körordning efter dygn och tid, om alla tidsatta rader bär dygn som håller.

    Svarar None när ett dygn saknas eller inte stämmer med tiderna, och då
    gäller den gamla regeln med den längsta luckan.
    """
    timed = [row for row in rows if clock_minutes(row.get("sort_time")) is not None]
    if not timed or any(_absolute_minute(row) is None for row in timed):
        return None
    ordered = sorted(timed, key=lambda row: (_absolute_minute(row), str(row.get("id") or "")))
    minutes = [_absolute_minute(row) for row in ordered]
    if any(later - earlier > EXPLICIT_DAY_GAP for earlier, later in zip(minutes, minutes[1:])):
        return None
    return ordered + [row for row in rows if clock_minutes(row.get("sort_time")) is None]


def order_across_midnight(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    explicit = explicit_day_order(rows)
    if explicit is not None:
        return explicit
    timed = [(row, clock_minutes(row.get("sort_time"))) for row in rows]
    ordered = sorted(timed, key=lambda item: (item[1] if item[1] is not None else 24 * 60, str(item[0].get("id") or "")))
    if len(ordered) < 2:
        return [item[0] for item in ordered]

    largest_gap = -1
    start_index = 0
    for index, (_, minute) in enumerate(ordered):
        current = minute if minute is not None else 24 * 60
        next_minute = ordered[(index + 1) % len(ordered)][1]
        following = next_minute if next_minute is not None else 24 * 60
        if index + 1 == len(ordered):
            following += 24 * 60
        gap = following - current
        if gap > largest_gap:
            largest_gap = gap
            start_index = (index + 1) % len(ordered)
    rotated = ordered[start_index:] + ordered[:start_index]
    return [item[0] for item in rotated]


def service_stops(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return chronological, canonical station stops for one train/day.

    The PDFs describe final origins and destinations in Från/Till. They do not
    describe adjacent infrastructure. Just like TrainMeet's proven Lovable
    route builder, topology must therefore come from the same train appearing
    at consecutive station sheets.
    """
    stops: list[dict[str, Any]] = []
    for row in order_across_midnight(rows):
        if not row.get("station_id") or clock_minutes(row.get("sort_time")) is None:
            continue
        if stops and stops[-1].get("station_id") == row.get("station_id"):
            # Multiple OCR/import rows at the same physical station must not
            # create a loop. Prefer a real dwell/yard movement over a
            # pass-through row at another operating point of the same station.
            current = stops[-1]
            if movement_richness(row) > movement_richness(current):
                replacement = dict(row)
                replacement["note"] = merge_text(current.get("note"), replacement.get("note"))
                stops[-1] = replacement
                continue
            if not current.get("arrival_time") and row.get("arrival_time"):
                current["arrival_time"] = row["arrival_time"]
            if not current.get("departure_time") and row.get("departure_time"):
                current["departure_time"] = row["departure_time"]
            current["note"] = merge_text(current.get("note"), row.get("note"))
            continue
        stops.append(dict(row))
    return stops


def movement_richness(row: dict[str, Any]) -> tuple[int, int, int]:
    arrival = clock_minutes(row.get("arrival_time"))
    departure = clock_minutes(row.get("departure_time"))
    dwell = 0
    if arrival is not None and departure is not None:
        dwell = (departure - arrival) % (24 * 60)
    meaningful_note = bool(str(row.get("note") or "").strip()) and normalized(row.get("note")) not in {"ej uppehall.", "ej uppehåll."}
    return (0 if row.get("no_stop") else 1, 1 if dwell > 0 else 0, 1 if meaningful_note else 0)


def build_services(rows: list[dict[str, Any]], station_names: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Tjänster och rutter ur tidtabellsraderna: en tjänst per tåg och trafikdagar.

    Svarar med tjänsterna, rutternas steg och vilken tjänst varje rad hör
    till. Samma kod bygger Clouds paket och serverns lokalt ändrade
    tidtabell, så att en rad hittar sin tjänst på samma sätt på båda ställena.
    """
    services: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    groups = service_groups(rows)
    service_ids: dict[str, str] = {}
    for (number, days, key), group in groups.items():
        # Ett tåg utan nyckel behåller sitt gamla id; det med nyckel får den i id:t.
        service_id = safe_id("service", f"{number}-{days}-{key}" if key else f"{number}-{days}")
        ordered = service_stops(group)
        explicit = explicit_day_order(ordered) is not None
        first_day = min((row["day_offset"] for row in ordered if _absolute_minute(row) is not None), default=0) if explicit else 0
        stops = []
        previous_minute: int | None = None
        day_offset = 0
        for index, row in enumerate(ordered):
            minute = clock_minutes(row.get("sort_time"))
            if minute is None:
                continue
            if explicit:
                # Dygnet som underlaget anger, räknat från tågets första stopp.
                day_offset = row["day_offset"] - first_day
            elif previous_minute is not None and minute < previous_minute:
                day_offset += 1
            previous_minute = minute
            station_name = row.get("station") or station_names.get(str(row.get("station_id")), "")
            stop = {"station_id": row["station_id"], "station_name": station_name, "stop_order": index,
                    "arrival_time": row.get("arrival_time"), "departure_time": row.get("departure_time"),
                    "service_day_offset": day_offset, "service_minute": minute + day_offset * 24 * 60}
            stops.append(stop)
            routes.append({"id": f"{service_id}-{index}", "service_id": service_id, "train_number": number, "days": days, **stop})
        for row in group:
            service_ids[row["id"]] = service_id
        service = {"id": service_id, "train_number": number, "days": days,
                   "train_type": (ordered[0] if ordered else group[0]).get("train_type") or "person", "stops": stops}
        if key:
            service["service_key"] = key
        color = next((str(row.get("train_color")) for row in group if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(row.get("train_color") or ""))), None)
        if color:
            service["color"] = color  # tågslagets färg i planen, som i arrangörens grafblad
        # Planens id för tåget följer tjänsten när alla rader med id har samma,
        # så att det finns kvar också när servern bygger om tjänsterna efter en
        # lokal ändring (Server #222).
        sources = {str(row.get("source_train_id")).strip() for row in group if str(row.get("source_train_id") or "").strip()}
        if len(sources) == 1:
            service["source_train_id"] = next(iter(sources))
        services.append(service)
    return services, routes, service_ids
