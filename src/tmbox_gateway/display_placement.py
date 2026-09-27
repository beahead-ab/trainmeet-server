"""Local presentation only: never change destinations, panel keys or routes."""
from dataclasses import replace


def station_connections(config, station_id):
    return [c for c in config.connections.values()
            if station_id in (c.station_a_id, c.station_b_id)]


def station_overrides(config, station_id, sides):
    """Validate one station's replacement overrides (omission follows Cloud)."""
    if station_id not in config.stations:
        raise ValueError("Stationen finns inte i den aktiva träffen.")
    connections = {c.id: c for c in station_connections(config, station_id)}
    if not isinstance(sides, dict) or any(key not in connections or value not in ("left", "right")
                                         for key, value in sides.items()):
        raise ValueError("Välj vänster eller höger för stationens sträckor.")
    return {key: {"side": side, "other_station_id": connections[key].other_station(station_id)}
            for key, side in sides.items()}


def default_side(config, station_id, connection):
    sides = {p.slot_position(key)[1] for p in config.panels.values() if p.station_id == station_id
             for key, value in p.slots.items() if value == connection.id}
    return next(iter(sides)) if len(sides) == 1 else ("right" if connection.station_a_id == station_id else "left")


def effective_sides(config, overrides, station_id):
    result = {}
    for connection in station_connections(config, station_id):
        saved = overrides.get(station_id, {}).get(connection.id, {})
        valid = saved.get("other_station_id") == connection.other_station(station_id) and saved.get("side") in {"left", "right"}
        result[connection.id] = saved["side"] if valid else default_side(config, station_id, connection)
    return result


def apply_placement(config, overrides):
    panels = {}
    for identifier, panel in config.panels.items():
        saved = overrides.get(panel.station_id, {})
        valid = {key: value["side"] for key, value in saved.items() if key in config.connections
                 and panel.station_id in (config.connections[key].station_a_id, config.connections[key].station_b_id)
                 and value.get("other_station_id") == config.connections[key].other_station(panel.station_id)
                 and value.get("side") in {"left", "right"}}
        changed = any(value in valid and valid[value] != panel.slot_position(key)[1]
                      for key, value in panel.slots.items())
        positions, counts = {}, {"left": 0, "right": 0}
        if changed:
            for key, connection_id in panel.slots.items():
                if connection_id:
                    side = valid.get(connection_id, panel.slot_position(key)[1])
                    counts[side] += 1
                    positions[key] = (counts[side], side)
            # The legacy panel renderer has exactly two cells per side. Never
            # create invisible row 3/4: keep its published layout in this case.
            # Station-based v2/terminal16 views use the override independently
            # and can page through any number of connections on either side.
            if max(counts.values()) > 2:
                positions = {}
        panels[identifier] = replace(panel, display_positions=positions)
    return replace(config, panels=panels)
