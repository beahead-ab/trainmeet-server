"""The day's legs: each departure paired with the arrival it leads to.

Automatic stations (automatic.py) and the timetable placement go by these.
They lived in simulation.py until the simulation was removed (Server 4.0).
"""
from __future__ import annotations

from .operations import _time_to_seconds
from .runtime import matches_active_day
from .train_routes import resolve_departure, RouteResolutionError


#: How long a box or TKL counts as present after it was last heard from.
PRESENCE_SECONDS = 45


def plan_legs(publication, day):
    """Every complete leg of the day and what kept the others out.

    Automatic stations run the legs that are complete and leave the rest to
    people; the errors say why a train is left out.
    """
    payload = publication.payload
    movements = {str(m["id"]): m for m in payload["trains"] if matches_active_day(m["days"], day)}
    times = {}
    errors = []
    for service in payload.get("services", []):
        if not matches_active_day(service["days"], day):
            continue
        previous = -1
        for stop in sorted(service["stops"], key=lambda s: s["stop_order"]):
            offset = int(stop.get("service_day_offset") or 0) * 86400
            arrival = _time_to_seconds(stop["arrival_time"]) + offset if stop.get("arrival_time") else None
            departure = _time_to_seconds(stop["departure_time"]) + offset if stop.get("departure_time") else None
            # A visit spanning midnight has one day offset, but two clock times.
            if arrival is not None and departure is not None and departure < arrival:
                departure += 86400
            first = arrival if arrival is not None else departure
            if first is None or first < previous:
                errors.append(f"Tåg {service['train_number']}: otydligt dygn eller besöksordning.")
            previous = departure if departure is not None else (arrival or 0)
            times[(service["id"], int(stop["stop_order"]))] = (arrival, departure)
    legs = {}
    for key, movement in movements.items():
        if not movement.get("departure_time"):
            continue
        try:
            leg = resolve_departure(payload, day, movement["station_id"], key)
            _, departure = times[(leg["service_id"], leg["from_stop_order"])]
            arrival, _ = times[(leg["service_id"], leg["to_stop_order"])]
            if departure is None or arrival is None or not 0 < arrival - departure <= 86400:
                raise ValueError("Restid saknas eller är orimlig")
            target = movements[leg["to_movement_id"]]
            for row in (movement, target):
                track = publication.track_catalogue().get(row.get("track_id"))
                if not track or not track.active or track.station_id != row["station_id"]:
                    raise ValueError("Aktivt planerat spår saknas")
            legs[key] = {**leg, "departure": departure, "arrival": arrival,
                         "duration": arrival - departure, "track_id": target["track_id"]}
        except (RouteResolutionError, KeyError, ValueError) as error:
            errors.append(f"Tåg {movement['train_number']} ({key}): {error}")
    incoming = {leg["to_movement_id"]: key for key, leg in legs.items()}
    for key, movement in movements.items():
        if movement.get("arrival_time") and key not in incoming:
            errors.append(f"Tåg {movement['train_number']}: ankomsten vid {movement['station_id']} saknar föregående avgång.")
    for key, leg in legs.items():
        prior = legs.get(incoming.get(key))
        leg["previous"] = incoming.get(key)
        leg["dwell"] = max(0, leg["departure"] - prior["arrival"]) if prior else 0
    journeys = {}
    for leg in legs.values():
        journey = journeys.setdefault((leg["train_number"], leg["service_id"]), [leg["departure"], leg["arrival"]])
        journey[0] = min(journey[0], leg["departure"])
        journey[1] = max(journey[1], leg["arrival"])
    for (number, service), (start, end) in journeys.items():
        if any(other_number == number and other_service != service and start < other_end and other_start < end
               for (other_number, other_service), (other_start, other_end) in journeys.items()):
            errors.append(f"Tågnummer {number} används i överlappande tåglopp.")
    if not legs:
        errors.append("Ingen komplett tågväg finns för den valda trafikdagen.")
    return legs, errors
