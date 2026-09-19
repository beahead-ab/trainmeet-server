"""Read-only, server-owned identification for the train-first TMBox profile.

A planned route is not permission to depart and is not a physical position.
Only explicit service/visit links are used: never nearest time, first neighbour,
station-name guesses or a destination chosen by the device. Traffic transitions
must re-resolve this identity under the meet/traffic lock before using it.
"""
from __future__ import annotations

import re
from typing import Any

from .runtime import matches_active_day


class RouteResolutionError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _time(value: Any) -> str:
    return str(value or "")


def _order(value: Any) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
        raise RouteResolutionError("invalid_stop_order")
    return int(value)


def _visits(stops: list[dict], movement: dict) -> list[int]:
    """Match a row to an exact visit, including repeated calls at one station.

    Older publications lack stop_order on movement rows. Matching both scheduled
    times is permissible only when it gives one exact visit. Optional explicit
    day/order fields further constrain, never override, the source timetable.
    """
    result = []
    for index, stop in enumerate(stops):
        if stop.get("station_id") != movement.get("station_id"):
            continue
        if any(_time(stop.get(k)) != _time(movement.get(k)) for k in ("arrival_time", "departure_time")):
            continue
        if movement.get("stop_order") is not None and _order(movement["stop_order"]) != _order(stop["stop_order"]):
            continue
        if movement.get("service_day_offset") is not None and movement["service_day_offset"] != stop.get("service_day_offset", 0):
            continue
        result.append(index)
    return result


def resolve_departure(payload: dict, active_day: str, station_id: str, movement_id: str) -> dict:
    """Resolve a planned next leg, or fail closed with a specific reason.

    Returned identifiers are publication/day/visit scoped. They are *not* a
    clearance or a lease and must not bypass live line/position validation.
    """
    rows = [row for row in payload.get("trains", []) if str(row.get("id")) == movement_id]
    if len(rows) != 1:
        raise RouteResolutionError("unknown_or_duplicate_movement")
    movement = rows[0]
    if movement.get("station_id") != station_id:
        raise RouteResolutionError("movement_not_at_station")
    if not matches_active_day(str(movement.get("days", "")), active_day):
        raise RouteResolutionError("movement_not_on_active_day")
    services = [item for item in payload.get("services", []) if item.get("id") == movement.get("service_id")]
    if len(services) != 1:
        raise RouteResolutionError("missing_or_duplicate_service")
    service = services[0]
    if (str(service.get("train_number")) != str(movement.get("train_number"))
            or not matches_active_day(str(service.get("days", "")), active_day)):
        raise RouteResolutionError("service_movement_mismatch")
    stops = sorted(service.get("stops", []), key=lambda stop: _order(stop.get("stop_order")))
    if len({_order(stop.get("stop_order")) for stop in stops}) != len(stops):
        raise RouteResolutionError("duplicate_stop_order")
    visits = _visits(stops, movement)
    if len(visits) != 1:
        raise RouteResolutionError("ambiguous_visit" if visits else "movement_visit_mismatch")
    index = visits[0]
    current = stops[index]
    identity = {
        "publication_id": str(payload.get("publication_id", "")), "active_day": active_day,
        "service_id": service["id"], "train_number": str(movement["train_number"]),
        "from_station_id": station_id, "from_movement_id": movement_id,
        "from_stop_order": _order(current["stop_order"]),
    }
    if index == len(stops) - 1:
        if movement.get("departure_time"):
            raise RouteResolutionError("departure_without_next_visit")
        return {**identity, "status": "terminal"}
    if not movement.get("departure_time"):
        raise RouteResolutionError("missing_departure_time")
    target = stops[index + 1]
    target_id = target.get("station_id")
    stations = [station for station in payload.get("stations", []) if station.get("id") == target_id]
    if len(stations) != 1 or target_id == station_id:
        raise RouteResolutionError("invalid_next_station")
    links = [link for link in payload.get("connections", [])
             if {link.get("station_a_id"), link.get("station_b_id")} == {station_id, target_id}]
    if len(links) != 1:
        raise RouteResolutionError("ambiguous_connection" if links else "no_published_connection")
    receivers = []
    for row in payload.get("trains", []):
        if (row.get("station_id") != target_id or row.get("service_id") != service["id"]
                or str(row.get("train_number")) != identity["train_number"]
                or not matches_active_day(str(row.get("days", "")), active_day)):
            continue
        if _visits(stops, row) == [index + 1]:
            receivers.append(row)
    if len(receivers) != 1:
        raise RouteResolutionError("ambiguous_receiving_movement" if receivers else "missing_receiving_movement")
    if not receivers[0].get("arrival_time"):
        raise RouteResolutionError("missing_arrival_time")
    link = links[0]
    return {
        **identity, "status": "resolved", "to_station_id": target_id,
        "to_station_code": stations[0]["code"], "to_station_name": stations[0]["name"],
        "to_movement_id": str(receivers[0]["id"]), "to_stop_order": _order(target["stop_order"]),
        "connection_id": str(link["id"]), "track_type": link["track_type"],
        "dispatch_mode": link.get("dispatch_mode_override") or payload.get("meet", {}).get("default_dispatch_mode", "clearance"),
    }


def describe_departure(payload: dict, active_day: str, station_id: str, movement_id: str) -> dict:
    """Additive lookup metadata. A failed route does not hide the train itself."""
    try:
        return resolve_departure(payload, active_day, station_id, movement_id)
    except RouteResolutionError as error:
        return {"status": "unresolved", "reason": error.reason}
