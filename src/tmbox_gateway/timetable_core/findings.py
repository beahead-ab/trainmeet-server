"""Deterministic EU timetable control. Half-open intervals, never runtime state.

Shared by Cloud and the server: the caller makes sure the meet is an EU meet.

Weekday timelines make day variants and Sunday midnight explicit. Unknown input
is reported, not silently promoted to a clean check. Source identities survive
deduplication, path inference and publication.
"""
from collections import defaultdict, deque
from datetime import UTC, datetime, date
import hashlib
import json
import re

from .times import clock_minutes as _clock_minutes

WEEK = 7 * 1440
ENGINE_VERSION = 4  # Invalidate persisted reports: v3 per-weekday intervals, v4 the sanity list.
DAY_NAMES = ('Mån', 'Tis', 'Ons', 'Tor', 'Fre', 'Lör', 'Sön')


def train_number_key(number):
    """Natural, deterministic ordering: 93 < 101, also T2 < T10."""
    return tuple((0, int(part)) if part.isdigit() else (1, part)
                 for part in re.split(r'(\d+)', number)), number


def day_label(weekdays):
    """'Mån–Tor', 'Mån, Fre' – or None when the finding applies every day of the week."""
    weekdays = sorted(set(weekdays))
    if not weekdays or len(weekdays) == 7: return None
    runs = []
    for day in weekdays:
        if runs and day == runs[-1][1] + 1: runs[-1][1] = day
        else: runs.append([day, day])
    return ', '.join(DAY_NAMES[a] if a == b else f'{DAY_NAMES[a]}–{DAY_NAMES[b]}' for a, b in runs)


def days(value):
    text = str(value or 'Dagl').strip().lower().replace('–', '-').replace('—', '-')
    if text in ('dagl', 'dagligen', 'daily', 'alla', '*'): return set(range(7)), True
    names = {'m': 0, 'må': 0, 'mån': 0, 'måndag': 0, 'ti': 1, 'tis': 1, 'tisdag': 1,
             'o': 2, 'on': 2, 'ons': 2, 'onsdag': 2, 'to': 3, 'tor': 3, 'torsdag': 3,
             'f': 4, 'fr': 4, 'fre': 4, 'fredag': 4, 'l': 5, 'lö': 5, 'lör': 5, 'lördag': 5,
             's': 6, 'sö': 6, 'sön': 6, 'söndag': 6}
    result = set()
    for part in re.split(r'[,;/]+', text):
        endpoints = [p.strip() for p in part.split('-')]
        if not 1 <= len(endpoints) <= 2 or any(p not in names for p in endpoints): return set(range(7)), False
        start, end = names[endpoints[0]], names[endpoints[-1]]
        result.update((start + i) % 7 for i in range((end - start) % 7 + 1))
    return result or set(range(7)), bool(result)


def clock(minute):
    minute = round(minute) % 1440
    return f'{minute // 60:02}:{minute % 60:02}'


def source_refs(rows, ids):
    """Källhänvisningarna för raderna, var och en en gång."""
    refs = []
    for rid in ids:
        row = rows.get(rid, {})
        refs += row.get('source_refs') or ([{'file_id': row['source_file_id'], 'row': row.get('source_row')}] if row.get('source_file_id') else [])
    return list({json.dumps(ref, sort_keys=True): ref for ref in refs}.values())


def sort_minute(row):
    explicit = _clock_minutes(row.get('sort_time'))
    return explicit if explicit is not None else _clock_minutes(row.get('departure_time') or row.get('arrival_time'))


def ordered_stops(group):
    """Ett tågs rader som stopp i körordning: {row, ids, days} per stopp.

    Dubbletter och dagvarianter med samma tider blir ett stopp som behåller
    alla sina rad-id:n. Ordningen följer tiden, vriden till den enda
    utgångsstationen eller förbi det långa nattuppehållet – aldrig en
    23-timmarssträcka. Delas med rimlighetskontrollen så att båda ser samma resa.
    """
    stops = {}
    for row in group:
        key = tuple(row.get(k) for k in ('station_id', 'arrival_time', 'departure_time', 'track', 'no_stop'))
        stop = stops.setdefault(key, {'row': row, 'ids': [], 'days': set()}); stop['ids'].append(row['id']); stop['days'].update(days(row.get('days'))[0])
    ordered = sorted(stops.values(), key=lambda s: (sort_minute(s['row']) if sort_minute(s['row']) is not None else 9999, s['row']['id']))
    origins = [s for s in ordered if s['row'].get('departure_time') and not s['row'].get('arrival_time')]
    if len(origins) == 1:
        pivot = ordered.index(origins[0]); ordered = ordered[pivot:] + ordered[:pivot]
    # With no explicit origin use the long overnight gap, not a 23-hour leg.
    elif len(ordered) > 1:
        mins = [sort_minute(s['row']) for s in ordered]
        if all(m is not None for m in mins):
            gaps = [((mins[(i + 1) % len(mins)] - m) % 1440, i) for i, m in enumerate(mins)]
            gap, index = max(gaps)
            if gap > 720:
                pivot = (index + 1) % len(ordered); ordered = ordered[pivot:] + ordered[:pivot]
    return ordered


def unique_path(adjacency, source, target):
    """Return one shortest path, or None for missing/ambiguous paths."""
    if source == target: return []
    queue = deque([(source, [])]); distance = {source: 0}; found = []
    while queue:
        node, path = queue.popleft()
        if found and len(path) >= len(found[0]): continue
        for neighbor, edge in adjacency[node]:
            extended = path + [(node, neighbor, edge)]
            if neighbor == target:
                found.append(extended)
                if len(found) > 1: return None
            elif len(extended) <= distance.get(neighbor, float('inf')):
                distance[neighbor] = len(extended); queue.append((neighbor, extended))
    return found[0] if found else None


def check_draft(draft, *, previous=(), now=None):
    stamp = now or datetime.now(UTC).isoformat().replace('+00:00', 'Z')
    old = {f['id']: f for f in previous}
    stations = {s['id']: s for s in draft.get('stations', [])}
    edges = {e['id']: e for e in draft.get('connections', [])}
    rows = {r['id']: r for r in draft.get('trains', [])}
    findings = {}; journeys = []; adjacency = defaultdict(list)
    edge_events = defaultdict(list); station_events = defaultdict(list); edge_rows = defaultdict(set)

    def add(rule, subject, numbers, weekday, source_rows, text, start=None, end=None, title=None, **extra):
        # One finding per rule + place + train pair. The weekdays it occurs on are collected on the
        # finding (B5): a daily meeting is one conflict, not seven.
        # B7: `title` is the heading (where), `text` the body (who); `message` keeps the full sentence
        # for the meet server and older clients.
        pair = sorted(set(str(n) for n in numbers if n), key=train_number_key)
        identity = ':'.join([rule, str(subject), ','.join(pair)])
        message = f'{title}: {text[:1].lower()}{text[1:]}' if title else text
        item = findings.setdefault(identity, {
            'id': identity, 'rule': rule, 'level': 'conflict' if rule in ('A', 'B', 'structure') else 'observation',
            'title': title, 'text': text,
            'blocking': rule == 'structure', 'train_a': pair[0] if pair else None,
            'train_b': pair[1] if len(pair) > 1 else None, 'weekday': weekday, 'weekdays': [], 'days_label': None,
            'start': clock(start) if start is not None else None, 'end': clock(end) if end is not None else None,
            'start_minute': start, 'end_minute': end, 'source_rows': [], 'source_refs': [],
            'message': message, 'created_at': old.get(identity, {}).get('created_at', stamp), **extra})
        # Sunday-to-Monday overlaps can be discovered last. The representative
        # times and details must still belong to the first weekday, not to the
        # first occurrence encountered by the sweep.
        if weekday is not None and (item['weekday'] is None or weekday < item['weekday']):
            item.update(title=title, text=text, message=message,
                        start=clock(start) if start is not None else None,
                        end=clock(end) if end is not None else None,
                        start_minute=start, end_minute=end, **extra)
            item.pop('intervals', None)
        if weekday is not None and weekday not in item['weekdays']:
            item['weekdays'] = sorted(item['weekdays'] + [weekday])
            item['weekday'] = item['weekdays'][0]
            item['days_label'] = day_label(item['weekdays'])
        if start is not None and weekday == item['weekday']:
            interval = {'start': clock(start), 'end': clock(end), 'start_minute': start, 'end_minute': end}
            intervals = item.setdefault('intervals', [])
            if interval not in intervals:
                intervals.append(interval)
                intervals.sort(key=lambda value: (value['start_minute'], value['end_minute']))
        item['source_rows'] = sorted(set(item['source_rows']) | set(source_rows))
        item['source_refs'] = source_refs(rows, item['source_rows'])
        return item

    def structural(key, message, source_rows=()):
        add('structure', key, [], None, source_rows, message)

    if not str(draft.get('name') or '').strip(): structural('name', 'Träffen saknar namn.')
    if not draft.get('start_date') or not draft.get('end_date'): structural('dates', 'Träffen saknar start- eller slutdatum.')
    elif draft['end_date'] < draft['start_date']: structural('dates', 'Slutdatum är före startdatum.')
    else:
        try: date.fromisoformat(draft['start_date']); date.fromisoformat(draft['end_date'])
        except (ValueError, TypeError): structural('dates', 'Ange giltiga datum för träffen.')
    if len(stations) < 2: structural('stations', 'Minst två stationer krävs.')
    if not rows: structural('trains', 'Tidtabellen saknar tågrörelser.')
    for edge in edges.values():
        a, b = edge.get('station_a_id'), edge.get('station_b_id')
        if a not in stations or b not in stations or a == b:
            structural(f'edge:{edge["id"]}', 'En sträcka hänvisar till en okänd eller samma station.'); continue
        if edge.get('track_type') not in ('single', 'double'):
            structural(f'track-type:{edge["id"]}', 'Sträckan saknar giltig spårtyp.')
        adjacency[a].append((b, edge['id'])); adjacency[b].append((a, edge['id']))
    if stations:
        reached = set(); queue = [next(iter(stations))]
        while queue:
            node = queue.pop()
            if node in reached: continue
            reached.add(node); queue.extend(n for n, _ in adjacency[node] if n not in reached)
        if reached != set(stations): structural('disconnected', 'Banan är inte sammanhängande.')

    groups = defaultdict(list)
    for row in rows.values():
        rid, sid, number = row['id'], row.get('station_id'), str(row.get('train_number') or '')
        if sid not in stations: structural(f'station:{rid}', f'Tåg {number} hänvisar till en okänd station.', [rid]); continue
        if not number: structural(f'number:{rid}', 'En tågrad saknar tågnummer.', [rid]); continue
        operating_days, known = days(row.get('days'))
        if not known: add('D', f'days:{rid}', [number], None, [rid], f'okända trafikdagar ”{row.get("days")}”. Kontrollen antar alla dagar.', title=f'Tåg {number}', station=sid)
        arrival, departure = _clock_minutes(row.get('arrival_time')), _clock_minutes(row.get('departure_time'))
        if arrival is None and departure is None:
            add('D', f'time:{rid}', [number], None, [rid], f'tid saknas eller kan inte tolkas vid {stations[sid].get("name", sid)}.', title=f'Tåg {number}', station=sid)
        elif any(row.get(field) and _clock_minutes(row[field]) is None for field in ('arrival_time', 'departure_time')):
            add('D', f'time:{rid}', [number], None, [rid], 'en angiven tid kan inte tolkas.', title=f'Tåg {number}', station=sid)
        groups[number].append(row)

    for number, group in sorted(groups.items()):
        # Duplicate imports do not create duplicate visits or lose provenance.
        ordered = ordered_stops(group)
        previous_time = None; offset = 0; timed = []; journey_legs = defaultdict(list)
        for stop in ordered:
            r = stop['row']; arrival = _clock_minutes(r.get('arrival_time')); departure = _clock_minutes(r.get('departure_time'))
            minute = arrival if arrival is not None else departure
            if minute is None:
                timed.append({**stop, 'arrival': None, 'departure': None}); continue
            while previous_time is not None and minute + offset < previous_time: offset += 1440
            arrival = minute + offset
            departure = (departure if departure is not None else minute) + offset
            if departure < arrival: departure += 1440
            previous_time = departure
            timed.append({**stop, 'arrival': arrival, 'departure': departure})
            track = str(r.get('track') or '').strip()
            if track:
                for day in stop['days']:
                    station_events[(r['station_id'], track)].append(dict(start=arrival + day * 1440,
                        end=(arrival + 1 if r.get('no_stop') else departure) + day * 1440, number=number, ids=stop['ids']))
        for a, b in zip(timed, timed[1:]):
            start, end = a['departure'], b['arrival']; source_ids = a['ids'] + b['ids']
            if start is None or end is None: continue
            if a['row']['station_id'] == b['row']['station_id']: continue
            shared_days = a['days'] & b['days']
            if not shared_days: continue
            path = unique_path(adjacency, a['row']['station_id'], b['row']['station_id'])
            if not path:
                add('D', 'path:' + ','.join(sorted(source_ids)), [number], None, source_ids,
                    'ingen entydig tågväg mellan stationerna.', title=f'Tåg {number}', station=a['row']['station_id']); continue
            for i, (source, target, edge_id) in enumerate(path):
                leg_start = start + (end - start) * i / len(path); leg_end = start + (end - start) * (i + 1) / len(path)
                leg = dict(start=leg_start, end=leg_end, number=number, ids=source_ids, source=source, target=target)
                edge_rows[edge_id].update(source_ids)
                for day in shared_days:
                    edge_events[edge_id].append(dict(leg, start=leg_start + day * 1440, end=leg_end + day * 1440))
                    journey_legs[day].append(dict(edge=edge_id, source=source, target=target, start=clock(leg_start), end=clock(leg_end),
                                                 inferred=len(path) > 1, source_rows=source_ids))
        for day in sorted(set().union(*(stop['days'] for stop in timed)) if timed else set()):
            journeys.append(dict(train=number, weekday=day,
                                 source_rows=sorted(r['id'] for r in group if day in days(r.get('days'))[0]),
                                 legs=journey_legs[day]))

    def overlaps(events):
        # Include next-week copies, then retain each physical overlap once.
        expanded = sorted([dict(e, start=e['start'] + shift, end=e['end'] + shift) for e in events for shift in (0, WEEK)], key=lambda e: e['start'])
        active = []
        for current in expanded:
            active = [e for e in active if e['end'] - current['start'] >= 1]
            for prior in active:
                if prior['number'] == current['number']: continue
                start, end = max(prior['start'], current['start']), min(prior['end'], current['end'])
                if end - start >= 1 and start < WEEK + 1440: yield prior, current, start, end
            active.append(current)

    for edge_id, events in edge_events.items():
        edge = edges[edge_id]
        title = '–'.join(stations[s].get('name', s) for s in (edge['station_a_id'], edge['station_b_id']))
        for a, b, start, end in overlaps(events):
            weekday = int(start // 1440) % 7
            same = a['source'] == b['source']
            name = lambda sid: stations.get(sid, {}).get('name', sid)
            if same:
                gap = abs(a['start'] - b['start'])
                first, second = (a, b) if a['start'] <= b['start'] else (b, a)
                add('C', edge_id, [a['number'], b['number']], weekday, a['ids'] + b['ids'],
                    f'Tåg {second["number"]} följer tåg {first["number"]} med {gap:g} min lucka.',
                    start % WEEK, start % WEEK + end - start, title=title, edge=edge_id, gap_minutes=gap)
            elif edge.get('track_type') == 'single':
                add('A', edge_id, [a['number'], b['number']], weekday, a['ids'] + b['ids'],
                    f'Tåg {a["number"]} ({name(a["source"])} → {name(a["target"])}) och '
                    f'tåg {b["number"]} ({name(b["source"])} → {name(b["target"])}) är på linjen samtidigt.',
                    start % WEEK, start % WEEK + end - start, title=f'{title} (enkelspår)', edge=edge_id)
    for (sid, track), events in station_events.items():
        for a, b, start, end in overlaps(events):
            add('B', f'{sid}/{track}', [a['number'], b['number']], int(start // 1440) % 7, a['ids'] + b['ids'],
                f'Tåg {a["number"]} och tåg {b["number"]} står på samma spår samtidigt.',
                start % WEEK, start % WEEK + end - start, title=f'{stations[sid].get("name", sid)} spår {track}', station=sid, track=track)

    questions = []
    for edge_id, edge in edges.items():
        count = sum(f['rule'] == 'A' and f.get('edge') == edge_id for f in findings.values())
        if count < 2 or edge.get('track_type') != 'single': continue
        relevant = [{k: rows[rid].get(k) for k in ('id', 'station_id', 'train_number', 'days', 'arrival_time', 'departure_time', 'sort_time')}
                    for rid in sorted(edge_rows[edge_id])]
        fingerprint = hashlib.sha256(json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if edge.get('double_track_declined_fingerprint') == fingerprint: continue
        title = '–'.join(stations[s].get('name', s) for s in (edge['station_a_id'], edge['station_b_id']))
        questions.append(dict(id=f'double-track:{edge_id}', edge=edge_id, count=count, fingerprint=fingerprint,
                              message=f'Är det dubbelspår på {title}? I så fall försvinner {count} konflikter.'))
    items = sorted(findings.values(), key=lambda f: (f['rule'], (f['start_minute'] or 0) % 1440, f['id']))
    return dict(conflicts=[f for f in items if f['level'] == 'conflict'], observations=[f for f in items if f['level'] == 'observation'],
                questions=questions, journeys=journeys)
