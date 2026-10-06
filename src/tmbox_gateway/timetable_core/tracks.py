"""Spårkatalogen: varje station och driftplats har sina spår med fasta id:n.

Tidtabellsraden bär spårets namn, som det står på skyltarna; katalogen bär
identiteten. Ett paket slår upp namnet i katalogen när det byggs.
"""
from __future__ import annotations

from typing import Any

from .times import normalized, safe_id


def track_catalogue(owner: dict[str, Any], station_id: str, operating_point_id: str | None) -> list[dict[str, Any]]:
    """Return the owner's track catalogue, upgrading a plain list of names.

    Early drafts stored tracks as a sorted set of strings. That collected the
    names but gave them no identity: no stable id, no label separate from the
    name, no active flag and no order of its own. Reading the catalogue is
    what migrates it, so no draft needs a separate migration pass.
    """
    entries: list[dict[str, Any]] = []
    for index, entry in enumerate(owner.get("tracks", []) or []):
        if isinstance(entry, dict):
            entries.append(entry)
            continue
        label = str(entry).strip()
        if not label:
            continue
        entries.append(
            {
                "id": _track_id(station_id, operating_point_id, label),
                "display_label": label,
                "active": True,
                "sort_order": (index + 1) * 10,
            }
        )
    owner["tracks"] = entries
    return entries


def ensure_track(
    owner: dict[str, Any],
    station_id: str,
    operating_point_id: str | None,
    label: str,
) -> dict[str, Any] | None:
    """Find or create the catalogue entry for a track name.

    An import that meets a track nobody has catalogued yet adds it rather than
    refusing the whole file: the flow must not require a complete catalogue
    before anyone can import anything. Matching is on the normalised label, so
    reimporting the same timetable never duplicates a track.
    """
    cleaned = str(label or "").strip()
    if not cleaned:
        return None
    entries = track_catalogue(owner, station_id, operating_point_id)
    for entry in entries:
        if normalized(entry["display_label"]) == normalized(cleaned):
            return entry
    entry = {
        "id": _track_id(station_id, operating_point_id, cleaned),
        "display_label": cleaned,
        "active": True,
        "sort_order": (len(entries) + 1) * 10,
    }
    entries.append(entry)
    return entry


def find_track(
    owner: dict[str, Any],
    station_id: str,
    operating_point_id: str | None,
    label: str,
) -> dict[str, Any] | None:
    cleaned = str(label or "").strip()
    if not cleaned:
        return None
    for entry in track_catalogue(owner, station_id, operating_point_id):
        if normalized(entry["display_label"]) == normalized(cleaned):
            return entry
    return None


def _track_id(station_id: str, operating_point_id: str | None, label: str) -> str:
    return safe_id("track", f"{station_id}-{operating_point_id or ''}-{label}")


def track_owner(draft: dict[str, Any], row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """The catalogue a train row's track belongs to, and its operating point."""
    station = next(
        (item for item in draft["stations"] if item["id"] == row.get("station_id")), None
    )
    if station is None:
        return None, None
    operating_point_id = row.get("operating_point_id")
    if operating_point_id:
        point = next(
            (
                item
                for item in station.get("operating_points", [])
                if item["id"] == operating_point_id
            ),
            None,
        )
        return point, operating_point_id
    return station, None


def resolve_row_track(draft: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    """Look up the catalogue entry a row's track name refers to.

    The grid a human edits holds the label an operator reads on a display; the
    catalogue holds identity. Resolving at publish keeps one source of truth
    without forcing ids into a spreadsheet cell.
    """
    owner, operating_point_id = track_owner(draft, row)
    if owner is None:
        return None
    return find_track(owner, row["station_id"], operating_point_id, row.get("track") or "")
