"""Lokala ändringar i tidtabellen, ovanpå Clouds publicering.

Cloud äger tidtabellen och publicerar den som ett paket. På plats behöver
admin ändå kunna flytta en tid, byta spår eller ta bort ett tåg mitt under
träffen, utan att gå via Cloud. Ändringarna ligger som ett lager ovanpå
paketet: samma publicerings-id och samma rad-id:n, så TKL-läget och boxarna
följer med, och Cloud ser ingenting förrän admin tar nästa Cloud-version.

Modulen gör om paketet till Clouds utkastform, den som Data-vyn visar, och
tar hela utkastet tillbaka. Bara det Data-vyn får ändra får ändras; tjänster
och rutter byggs om med Clouds egen kod i timetable_core, så att tågen blir
exakt de Cloud skulle ha gett. Här finns ingen databas och ingen motor; det
sköter runtime, operations och http_server.
"""
from __future__ import annotations

import copy
from typing import Any

from .timetable_core import build_services, canonical_days, check_draft, check_sanity, parse_clock, resolve_row_track

#: Det Data-vyn får ändra på en tågrad. Allt annat på raden följer Cloud.
ROW_FIELDS = ("train_number", "days", "track", "arrival_time", "departure_time", "arrival_from", "departure_to", "no_stop", "note")
#: Fält som bara finns i utkastet. De räknas inte som en ändring och går inte in i paketet.
DRAFT_ONLY = {"track", "station", "sort_time"}
TRACK_TYPES = ("single", "double")
MEET_FIELDS = ("id", "name", "slug", "active_day", "timezone", "default_dispatch_mode", "traffic_direction",
               "clock_time", "country", "default_language", "start_date", "end_date")


class LocalEditError(ValueError):
    """Utkastet går inte att spara. Texten säger varför, på svenska."""


def draft_from_publication(payload: dict[str, Any]) -> dict[str, Any]:
    """Paketet i Clouds utkastform: det Data-vyn visar och sparar.

    Stationernas spårkatalog tas ur paketets `tracks`, som Cloud byggde ur
    just de katalogerna, så ett spårnamn på en rad slås upp på samma sätt
    som i Cloud. Raderna får `station` (namn) och `track` (spårets namn).
    """
    meet = payload.get("meet") or {}
    clock = payload.get("clock") or {}
    stations = copy.deepcopy(payload.get("stations") or [])
    catalogues: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for track in payload.get("tracks") or []:
        entry = {key: track[key] for key in ("id", "display_label", "active", "sort_order") if key in track}
        catalogues.setdefault((str(track["station_id"]), track.get("operating_point_id") or None), []).append(entry)
    for station in stations:
        station["tracks"] = catalogues.get((str(station["id"]), None), [])
        for point in station.get("operating_points") or []:
            point["tracks"] = catalogues.get((str(station["id"]), point["id"]), [])
    labels = {str(track["id"]): str(track["display_label"]) for track in payload.get("tracks") or []}
    names = {str(station["id"]): str(station.get("name") or station.get("code") or "") for station in stations}
    trains = []
    for row in payload.get("trains") or []:
        item = {key: value for key, value in row.items() if key not in {"service_id", "track_id"}}
        item["station"] = names.get(str(row.get("station_id")), str(row.get("station") or ""))
        item["track"] = labels.get(str(row.get("track_id") or ""), "")
        trains.append(item)
    draft: dict[str, Any] = {key: meet.get(key) for key in MEET_FIELDS}
    draft.update({
        "operating_region": "eu",
        "clock_speed": clock.get("speed", 1),
        "stations": stations,
        "connections": copy.deepcopy(payload.get("connections") or []),
        "trains": trains,
        "panels": copy.deepcopy(payload.get("panels") or []),
        "display": copy.deepcopy(payload.get("display") or {}),
        "files": [],
        "draft_findings": copy.deepcopy(payload.get("findings") or []),
    })
    return draft


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _same(a: Any, b: Any) -> bool:
    """Lika som en människa ser det: tomt är tomt, och blanksteg räknas inte."""
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    return _text(a) == _text(b)


def _row_label(row: dict[str, Any], names: dict[str, str]) -> str:
    return f"Tåg {_text(row.get('train_number')) or '?'} vid {names.get(str(row.get('station_id')), row.get('station_id'))}"


def _edited_row(original: dict[str, Any], item: dict[str, Any], reference: dict[str, Any], names: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Raden som den blir, och fält för fält vad som ändrades mot Cloud."""
    label = _row_label(original, names)
    for key, value in item.items():
        if key in ROW_FIELDS or key in DRAFT_ONLY or key == "id":
            continue
        if key in original and not _same(original.get(key), value):
            raise LocalEditError(f"{label}: fältet {key} ändras i Cloud, inte här")
    row = copy.deepcopy(original)
    changed: dict[str, dict[str, Any]] = {}

    def set_field(key: str, value: Any) -> None:
        if not _same(original.get(key), value):
            changed[key] = {"from": original.get(key), "to": value}
            row[key] = value

    if "train_number" in item:
        number = _text(item["train_number"])
        if not number:
            raise LocalEditError(f"{label}: tågnumret får inte vara tomt")
        set_field("train_number", number)
    if "days" in item:
        set_field("days", canonical_days(_text(item["days"])))
    for key in ("arrival_time", "departure_time"):
        if key in item:
            text = _text(item[key])
            if text and parse_clock(text) is None:
                raise LocalEditError(f"{label}: {text!r} är inte ett klockslag (HH:MM)")
            set_field(key, parse_clock(text) if text else None)
    if not row.get("arrival_time") and not row.get("departure_time"):
        raise LocalEditError(f"{label}: ankomst eller avgång måste finnas")
    for key in ("arrival_from", "departure_to", "note"):
        if key in item:
            set_field(key, _text(item[key]) or None)
    if "no_stop" in item:
        set_field("no_stop", bool(item["no_stop"]))
    if "track" in item:
        name = _text(item["track"])
        entry = resolve_row_track(reference, {**row, "track": name}) if name else None
        if name and entry is None:
            raise LocalEditError(f"{label}: spåret {name} finns inte i stationens spårkatalog")
        if name and not entry.get("active", True):
            raise LocalEditError(f"{label}: spåret {name} är inaktiverat")
        set_field("track_id", entry["id"] if entry else None)
    if "arrival_time" in changed or "departure_time" in changed:
        # Som Data-vyn: sorttiden följer ankomsten, annars avgången.
        set_field("sort_time", row.get("arrival_time") or row.get("departure_time"))
    return row, changed


def apply_draft(base: dict[str, Any], draft: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Det effektiva paketet ur ett sparat utkast, och vad som skiljer från Cloud.

    Utkastet är hela tidtabellen som Data-vyn skickar den. Rader som saknas
    är borttagna; rader som inte finns i Cloud är ett fel, eftersom nya tåg
    läggs till i Cloud. Sträckor kan bara byta mellan enkel- och dubbelspår.
    Svaret är (paket, ändringar), där ändringarna bara innehåller
    skillnaderna, med värdet före och efter.
    """
    if not isinstance(draft, dict):
        raise LocalEditError("Utkastet måste vara ett objekt")
    submitted = draft.get("trains")
    if not isinstance(submitted, list):
        raise LocalEditError("Utkastet saknar tågrader")
    reference = draft_from_publication(base)
    names = {str(station["id"]): str(station.get("name") or station["id"]) for station in base.get("stations") or []}
    base_rows = {str(row["id"]): row for row in base.get("trains") or []}
    rows: list[dict[str, Any]] = []
    trains: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for item in submitted:
        if not isinstance(item, dict) or not _text(item.get("id")):
            raise LocalEditError("En tågrad saknar id")
        row_id = str(item["id"])
        if row_id not in base_rows:
            raise LocalEditError(f"Tågraden {row_id} finns inte i Clouds tidtabell. Nya tåg läggs till i Cloud.")
        if row_id in seen:
            raise LocalEditError(f"Tågraden {row_id} förekommer två gånger")
        seen.add(row_id)
        row, changed = _edited_row(base_rows[row_id], item, reference, names)
        rows.append(row)
        if changed:
            trains[row_id] = changed
    removed = sorted(set(base_rows) - seen)

    connections: dict[str, dict[str, Any]] = {}
    base_connections = {str(item["id"]): item for item in base.get("connections") or []}
    submitted_connections = draft.get("connections", list(base_connections.values()))
    if not isinstance(submitted_connections, list):
        raise LocalEditError("Utkastets sträckor måste vara en lista")
    effective_connections = []
    seen_connections: set[str] = set()
    for item in submitted_connections:
        if not isinstance(item, dict) or str(item.get("id") or "") not in base_connections:
            raise LocalEditError("En sträcka finns inte i Clouds tidtabell. Sträckor ändras i Cloud.")
        original = base_connections[str(item["id"])]
        seen_connections.add(str(item["id"]))
        for key, value in item.items():
            if key in {"track_type", "reviewed"}:
                continue
            if key in original and not _same(original.get(key), value):
                raise LocalEditError(f"Sträckan {_connection_label(original, names)}: fältet {key} ändras i Cloud, inte här")
        connection = copy.deepcopy(original)
        track_type = _text(item.get("track_type", original.get("track_type")))
        if track_type not in TRACK_TYPES:
            raise LocalEditError(f"Sträckan {_connection_label(original, names)}: spårtypen måste vara single eller double")
        if track_type != original.get("track_type"):
            connection["track_type"] = track_type
            connections[str(item["id"])] = {"track_type": {"from": original.get("track_type"), "to": track_type}}
        effective_connections.append(connection)
    if seen_connections != set(base_connections):
        raise LocalEditError("Sträckor tas bort och läggs till i Cloud, inte här")

    effective = copy.deepcopy(base)
    effective["connections"] = effective_connections
    services, routes, service_ids = build_services(copy.deepcopy(rows), names)
    for row in rows:
        row["service_id"] = service_ids[str(row["id"])]
    effective["trains"] = rows
    effective["services"] = services
    effective["routes"] = routes
    edits = {"trains": trains, "removed_trains": removed, "connections": connections}
    return effective, edits


def _connection_label(connection: dict[str, Any], names: dict[str, str]) -> str:
    return f"{names.get(str(connection.get('station_a_id')), '?')}–{names.get(str(connection.get('station_b_id')), '?')}"


def review(effective: dict[str, Any], previous: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Clouds kontroll och rimlighetskontroll på det effektiva paketet.

    Räknas när något sparas och lagras, så att en Pi inte räknar om dem vid
    varje anrop. Paketets `findings` är kontrollens konflikter och
    observationer, som Cloud lägger dem.
    """
    draft = draft_from_publication(effective)
    report = check_draft(draft, previous=previous or effective.get("findings") or [])
    return {"findings": report, "sanity": check_sanity(draft)}


def with_findings(effective: dict[str, Any], reviewed: dict[str, Any]) -> dict[str, Any]:
    report = reviewed["findings"]
    effective["findings"] = [item for item in [*report.get("conflicts", []), *report.get("observations", [])]
                             if item.get("level") in {"conflict", "observation"}]
    return effective


def edit_count(edits: dict[str, Any]) -> int:
    return len(edits.get("trains") or {}) + len(edits.get("removed_trains") or []) + len(edits.get("connections") or {})


def changed_fields(edits: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """Fälten som är ändrade lokalt, per rad och sträcka: det Data-vyn markerar."""
    return {
        "trains": {row_id: [key for key in changed if key != "sort_time"] for row_id, changed in (edits.get("trains") or {}).items()},
        "connections": {connection_id: list(changed) for connection_id, changed in (edits.get("connections") or {}).items()},
    }


FIELD_NAMES = {"train_number": "tågnummer", "days": "dagar", "track_id": "spår", "arrival_time": "ankomst", "departure_time": "avgång",
               "arrival_from": "från", "departure_to": "till", "no_stop": "ej uppehåll", "note": "anmärkning", "sort_time": "sorttid"}


def describe(edits: dict[str, Any], base: dict[str, Any]) -> list[str]:
    """Ändringarna som en människa läser dem, en rad per tåg och sträcka."""
    names = {str(station["id"]): str(station.get("name") or station["id"]) for station in base.get("stations") or []}
    labels = {str(track["id"]): str(track["display_label"]) for track in base.get("tracks") or []}
    rows = {str(row["id"]): row for row in base.get("trains") or []}
    connections = {str(item["id"]): item for item in base.get("connections") or []}

    def shown(key: str, value: Any) -> str:
        if key == "track_id":
            return labels.get(str(value or ""), "–")
        if key == "no_stop":
            return "ja" if value else "nej"
        return _text(value) or "–"

    lines = []
    for row_id, changed in (edits.get("trains") or {}).items():
        row = rows.get(row_id, {})
        parts = [f"{FIELD_NAMES.get(key, key)} {shown(key, change['from'])} → {shown(key, change['to'])}"
                 for key, change in changed.items() if key != "sort_time"]
        lines.append(f"{_row_label(row, names)}: " + ", ".join(parts))
    for row_id in edits.get("removed_trains") or []:
        lines.append(f"{_row_label(rows.get(row_id, {}), names)}: borttaget")
    for connection_id, changed in (edits.get("connections") or {}).items():
        after = changed["track_type"]["to"]
        lines.append(f"Sträckan {_connection_label(connections.get(connection_id, {}), names)}: "
                     + ("dubbelspår" if after == "double" else "enkelspår"))
    return lines
