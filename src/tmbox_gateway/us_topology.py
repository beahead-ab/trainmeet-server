"""Independent US references and explicit whole-segment operating limits.

MP numbers and drawing coordinates never establish connectivity or permission.
Runtime v2 reserves whole edges and their endpoint nodes conservatively; it does
not infer partial-edge limits, distances or dispatcher territorial permissions.
"""
from __future__ import annotations

import math
from typing import Any

SCHEMA_V2 = "trainmeet.us.runtime/2"
PROFILE_V2 = "tm-us-twc-manual-v2"
REFERENCE_COLLECTIONS = ("mp_systems", "mileposts", "locations", "limits")


class USError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def text(value: Any, field: str, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise USError(f"{field}: enter 1–{limit} characters")
    return value.strip()


def number(value: Any, field: str) -> float:
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise USError(f"{field}: a finite number is required")
    return float(value)


def rows(value: Any, field: str, maximum: int = 1000) -> list[dict]:
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(v, dict) for v in value):
        raise USError(f"{field}: expected a list of at most {maximum} objects")
    return value


def indexed(value: Any, field: str) -> dict[str, dict]:
    result = {}
    for row in rows(value, field):
        key = text(row.get("id"), f"{field}.id", 80)
        if key != row['id'] or key in result:
            raise USError(f"Duplicate or untrimmed {field} ID: {key}")
        result[key] = row
    return result


def reference(row: dict, field: str, catalogue: dict, *, optional=False):
    value = row.get(field)
    if optional and (value is None or value == ''):
        return None
    text(value, field, 80)
    if value not in catalogue:
        raise USError(f"Unknown {field}: {value}")
    return catalogue[value]


def reference_list(row: dict, field: str, catalogue: dict):
    value = row.get(field, [])
    if not isinstance(value, list) or len(value) > 100:
        raise USError(f"Invalid {field}")
    return [reference({field: item}, field, catalogue) for item in value]


def validate_references(package: dict):
    territories = indexed(package['territories'], 'territories')
    nodes = indexed(package['nodes'], 'nodes')
    segments = indexed(package['segments'], 'segments')
    catalogues = {key: indexed(package.get(key), key) for key in REFERENCE_COLLECTIONS}
    systems, markers = catalogues['mp_systems'], catalogues['mileposts']
    for system in systems.values():
        text(system.get('name'), 'MP system name')
        reference(system, 'territory_id', territories)
    for marker in markers.values():
        text(marker.get('name'), 'milepost name')
        number(marker.get('value'), 'milepost value')
        territory = reference(marker, 'mp_system_id', systems)['territory_id']
        node = reference(marker, 'node_id', nodes, optional=True)
        segment = reference(marker, 'segment_id', segments, optional=True)
        if node and node['territory_id'] != territory:
            raise USError('Milepost node must belong to its MP system territory')
        if segment:
            endpoints = {segment['from_node'], segment['to_node']}
            if not any(nodes[n]['territory_id'] == territory for n in endpoints):
                raise USError('Milepost segment must touch its MP system territory')
            if node and node['id'] not in endpoints:
                raise USError('Milepost node is not an endpoint of its segment')
    for location in catalogues['locations'].values():
        text(location.get('name'), 'location name')
        territory = reference(location, 'territory_id', territories)['id']
        linked_nodes = reference_list(location, 'node_ids', nodes)
        linked_markers = reference_list(location, 'milepost_ids', markers)
        if any(n['territory_id'] != territory for n in linked_nodes) or any(
                systems[m['mp_system_id']]['territory_id'] != territory for m in linked_markers):
            raise USError('Location references must belong to its territory')
    for limit in catalogues['limits'].values():
        text(limit.get('name'), 'boundary name')
        text(limit.get('kind'), 'boundary kind', 80)
        territory = reference(limit, 'territory_id', territories)['id']
        node = reference(limit, 'node_id', nodes)
        marker = reference(limit, 'milepost_id', markers, optional=True)
        if node['territory_id'] != territory:
            raise USError('Boundary node must belong to its territory')
        if marker:
            if systems[marker['mp_system_id']]['territory_id'] != territory:
                raise USError('Boundary milepost must belong to its territory')
            if marker.get('node_id') and marker['node_id'] != node['id']:
                raise USError('Boundary node conflicts with its milepost')
            if marker.get('segment_id'):
                segment = segments[marker['segment_id']]
                if node['id'] not in {segment['from_node'], segment['to_node']}:
                    raise USError('Boundary node conflicts with its milepost segment')
    for key in ('mileposts', 'locations'):
        for row in catalogues[key].values():
            for field, maximum in (('x', 100), ('y', 10000)):
                if row.get(field) is not None and not 0 <= number(row[field], field) <= maximum:
                    raise USError(f'Diagram {field} must be 0–{maximum}')
    return catalogues


def validate_node_path(package: dict, path: list[dict]) -> list[dict]:
    segments = indexed(package['segments'], 'segments')
    previous_exit = None
    seen = set()
    for leg in path:
        if set(leg) != {'segment_id', 'from_node', 'to_node'}:
            raise USError('Runtime v2 requires whole segments with explicit from_node and to_node, not MP intervals')
        segment = reference(leg, 'segment_id', segments)
        start = text(leg.get('from_node'), 'from_node', 80)
        end = text(leg.get('to_node'), 'to_node', 80)
        if start != leg['from_node'] or end != leg['to_node'] or start == end or {start, end} != {segment['from_node'], segment['to_node']}:
            raise USError('Limits must be the two endpoints of the selected segment')
        if segment['id'] in seen:
            raise USError('Repeated track segment')
        if previous_exit is not None and start != previous_exit:
            raise USError('Track path is not connected at the specified limits')
        seen.add(segment['id'])
        previous_exit = end
    return path


def validate_schedules(package: dict):
    """A place can span tracks, but cannot teleport a run between components."""
    nodes = indexed(package['nodes'], 'nodes')
    locations = indexed(package['locations'], 'locations')
    adjacency = {n: set() for n in nodes}
    for s in package['segments']:
        adjacency[s['from_node']].add(s['to_node'])
        adjacency[s['to_node']].add(s['from_node'])
    components = {}
    for node_id in nodes:
        if node_id in components:
            continue
        pending = [node_id]
        while pending:
            n = pending.pop()
            if n not in components:
                components[n] = node_id
                pending.extend(adjacency[n] - components.keys())
    for run in package['runs']:
        reachable, previous = None, ''
        for stop in run.get('schedule', []):
            if stop['time'] < previous:
                raise USError('Timetable is not in chronological order')
            previous = stop['time']
            linked = locations[stop['location_id']]['node_ids'] if 'location_id' in stop else [stop['node_id']]
            candidates = {components[n] for n in linked}
            reachable = candidates if reachable is None else reachable & candidates
            if not reachable:
                raise USError('No connected track path through all timetable locations')


def node_paths_conflict(package: dict, first: list[dict], second: list[dict]) -> bool:
    segments = indexed(package['segments'], 'segments')
    for a in first:
        for b in second:
            if a['segment_id'] == b['segment_id'] or {a['from_node'], a['to_node']} & {b['from_node'], b['to_node']}:
                return True
            if set(segments[a['segment_id']].get('conflict_resources', [])) & set(segments[b['segment_id']].get('conflict_resources', [])):
                return True
    return False


def node_label(package: dict, node_id: str) -> str:
    node = next(n for n in package['nodes'] if n['id'] == node_id)
    territory = next(t for t in package['territories'] if t['id'] == node['territory_id'])
    return f"{territory['name']} / {node['name']} [{node_id}]"


def validate_position(package: dict, value: Any) -> dict:
    if not isinstance(value, dict) or set(value) not in ({'segment_id', 'node_id'}, {'segment_id', 'milepost_id'}):
        raise USError('Position must identify a segment and exactly one track node or independent milepost')
    segment = reference(value, 'segment_id', indexed(package['segments'], 'segments'))
    endpoints = {segment['from_node'], segment['to_node']}
    if 'node_id' in value:
        node = reference(value, 'node_id', indexed(package['nodes'], 'nodes'))
        if node['id'] not in endpoints:
            raise USError('Position node is outside the segment')
    else:
        marker = reference(value, 'milepost_id', indexed(package['mileposts'], 'mileposts'))
        # An explicit segment association narrows a marker to that track even
        # when its node is shared by several edges. Never match by MP value.
        if marker.get('segment_id'):
            belongs = marker['segment_id'] == segment['id']
        else:
            belongs = marker.get('node_id') in endpoints
        if not belongs:
            raise USError('Position milepost is not associated with this segment')
    return dict(value)
