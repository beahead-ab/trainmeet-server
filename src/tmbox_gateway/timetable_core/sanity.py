"""Rimlighetskontroll: hela trafikspelet jämfört med sig självt.

Underlaget har inga avstånd, hastigheter eller minsta gångtider. Kontrollen
jämför därför varje tåg med de andra tågen på samma sträcka och station: tar
nästan alla 15 minuter mellan två stationer och ett tar två timmar, är det
värt en blick. Det som hittas är råd till arrangören, aldrig ett fel som
blockerar publicering, och det följer inte med i driftpaketet.

Varje avvikelse kan ha ett förslag: den minsta ändringen som gör tågets hela
resa rimlig igen. Arrangören väljer själv om förslaget ska användas.
"""
from collections import Counter, defaultdict
import hashlib
import json
from statistics import median

from .findings import clock, day_label, ordered_stops, source_refs, train_number_key
from .times import clock_minutes as _clock_minutes

#: Minsta antal ANDRA tåg på samma stationspar eller station innan något
#: jämförs. Med färre finns ingen vanlig tid att avvika från.
MIN_OTHER_TRAINS = 3
#: Ovanligt lång gångtid: minst 2,5 gånger de andra tågens median ...
SLOW_FACTOR = 2.5
#: ... och minst 20 minuter mer, så att 5 minuter mot 2 inte räknas.
SLOW_MARGIN = 20
#: Ovanligt kort gångtid: högst en tredjedel av medianen ...
FAST_DIVISOR = 3
#: ... och minst 5 minuter kortare, så att 1 minut mot 3 inte räknas.
FAST_MARGIN = 5
#: Ovanligt långt uppehåll: minst fem gånger medianen ...
DWELL_FACTOR = 5
#: ... och minst 45 minuter längre än den ...
DWELL_MARGIN = 45
#: ... och aldrig under en halvtimme.
DWELL_MINIMUM = 30
#: Utan tre andra tåg att jämföra med markeras bara uppehåll på tre timmar
#: eller mer. Ett tåg som står så länge mitt i en resa är sällan avsikten.
DWELL_FALLBACK = 180
#: En tid som går bakåt med mer än ett halvt dygn är ett dygnsskifte, inte
#: ett fel: 23:50 till 00:05 är 15 minuter. Mindre än så bakåt är ett fel.
MIDNIGHT_WRAP = 720
#: Ligger felet inom en kvart, och inom halva den vanliga tiden, från hela
#: timmar är det troligen en felskriven timme. Då föredras hela timmar:
#: 12:20 → 10:20 hellre än 10:15 när andra tar 15 min, men inte 12:05 → 10:05.
HOUR_SLACK = 15

TITLES = {
    'slow_leg': 'Ovanligt lång gångtid',
    'fast_leg': 'Ovanligt kort gångtid',
    'backwards_leg': 'Tåget är framme innan det har gått',
    'backwards_dwell': 'Avgång före ankomst',
    'long_dwell': 'Ovanligt långt uppehåll',
}
KINDS = tuple(TITLES)
FIELDS = ('arrival_time', 'departure_time')
FIELD_NAMES = {'arrival_time': 'ankomst', 'departure_time': 'avgång'}


def duration(minutes):
    """'15 min', '2 h', '2 h 5 min' – alltid utan tecken, halva minuter uppåt."""
    hours, rest = divmod(_whole(abs(minutes)), 60)
    if not hours: return f'{rest} min'
    return f'{hours} h {rest} min' if rest else f'{hours} h'


def _whole(value):
    """Hela minuter, halvor uppåt: medianen av 14 och 15 är 15."""
    return int(value + 0.5)


def _wrap(minutes):
    return minutes + 1440 if minutes < -MIDNIGHT_WRAP else minutes


def _start(stop):
    """Där en sträcka börjar: avgången, eller den enda tiden stoppet har."""
    return stop['departure'] if stop['departure'] is not None else stop['arrival']


def _end(stop):
    return stop['arrival'] if stop['arrival'] is not None else stop['departure']


def _start_field(stop):
    return 'departure_time' if stop['departure'] is not None else 'arrival_time'


def _end_field(stop):
    return 'arrival_time' if stop['arrival'] is not None else 'departure_time'


def _cells(stop):
    """Stoppets tider som går att ändra: bara de som finns och kan tolkas."""
    return [(field, stop['arrival' if field == 'arrival_time' else 'departure'])
            for field in FIELDS if stop['arrival' if field == 'arrival_time' else 'departure'] is not None]


def _value(stops, cell):
    index, field = cell
    return stops[index]['arrival' if field == 'arrival_time' else 'departure']


def _stops(rows):
    """Ett tågs stopp i samma körordning som konfliktkontrollen ser."""
    result = []
    for index, stop in enumerate(ordered_stops(rows)):
        row, ids = stop['row'], sorted(stop['ids'])
        result.append(dict(index=index, row=row, ids=ids, key=ids[0], days=stop['days'], station=row['station_id'],
                           arrival=_clock_minutes(row.get('arrival_time')), departure=_clock_minutes(row.get('departure_time')),
                           passing=bool(row.get('no_stop'))))
    return result


def _relations(stops):
    """Sträckor mellan stopp som följer på varandra samma veckodag, och mellanstoppen.

    Varje veckodag följs för sig. Dagvarianter med olika tider blir då två
    resor och inte en sicksack mellan dem. En sträcka som körs flera dagar
    är en sträcka, med dagarna samlade.
    """
    legs = defaultdict(set); inner = set()
    for day in range(7):
        sequence = [s for s in stops if day in s['days']]
        for a, b in zip(sequence, sequence[1:]):
            if a['station'] == b['station'] or _start(a) is None or _end(b) is None: continue
            legs[(a['index'], b['index'])].add(day)
        timed = [s for s in sequence if _start(s) is not None]
        inner.update(s['index'] for s in timed[1:-1])
    return legs, inner


def _leg_minutes(stops, i, j):
    return _wrap(_end(stops[j]) - _start(stops[i]))


def _pair(a, b):
    return tuple(sorted((a['station'], b['station'])))


def _dwell(stop):
    """Uppehållet i minuter, eller None för passager och stopp utan båda tiderna."""
    if stop['arrival'] is None or stop['departure'] is None or stop['passing']: return None
    return _wrap(stop['departure'] - stop['arrival'])


def _samples(trains):
    """Varje tågs gångtider per stationspar och uppehåll per station."""
    legs = defaultdict(lambda: defaultdict(list)); dwells = defaultdict(lambda: defaultdict(list))
    for number, rows in trains.items():
        stops = _stops(rows); relations, inner = _relations(stops)
        for i, j in relations:
            minutes = _leg_minutes(stops, i, j)
            if minutes >= 0: legs[_pair(stops[i], stops[j])][number].append(minutes)
        for stop in stops:
            minutes = _dwell(stop)
            if minutes is not None and minutes >= 0 and stop['index'] in inner: dwells[stop['station']][number].append(minutes)
    return {'leg': legs, 'dwell': dwells}


def _typical(tables, number):
    """De ANDRA tågens median, ett värde per tåg: tåget jämförs aldrig med sig självt.

    Svaret är (median, antal tåg); medianen är None med färre än
    MIN_OTHER_TRAINS tåg. Eftersom tåget självt inte räknas ändras inte
    jämförelsen när ett förslag provas på det.
    """
    cache = {}
    def typical(kind, key):
        if (kind, key) not in cache:
            values = [median(v) for other, v in tables[kind].get(key, {}).items() if other != number]
            cache[(kind, key)] = (median(values) if len(values) >= MIN_OTHER_TRAINS else None, len(values))
        return cache[(kind, key)]
    return typical


def _check(number, rows, typical):
    """Ett tågs avvikelser, i körordning. Körs om på varje förslag som provas.

    `fit` säger hur väl resan stämmer med de andra tågen: antal sträckor som
    inget jämförbart tåg kör och summan av alla avvikelser från medianerna i
    minuter. Den skiljer mellan två förslag som båda gör tåget rimligt.
    `loose` är stoppen på de sträckor som inget jämförbart tåg kör.
    """
    stops = _stops(rows); legs, inner = _relations(stops); found = []; loose = set(); deviation = 0
    for (i, j), on in legs.items():
        minutes = _leg_minutes(stops, i, j)
        usual, samples = typical('leg', _pair(stops[i], stops[j]))
        if usual is None: loose.update((i, j))
        else: deviation += abs(minutes - usual)
        if minutes < 0: kind = 'backwards_leg'
        elif usual is not None and minutes >= SLOW_FACTOR * usual and minutes - usual >= SLOW_MARGIN: kind = 'slow_leg'
        elif usual is not None and minutes <= usual / FAST_DIVISOR and usual - minutes >= FAST_MARGIN: kind = 'fast_leg'
        else: continue
        found.append(dict(kind=kind, stops=(i, j), at=j, minutes=minutes, usual=usual, samples=samples, days=on))
    for stop in stops:
        minutes = _dwell(stop)
        if minutes is None: continue
        usual, samples = typical('dwell', stop['station'])
        if usual is not None and stop['index'] in inner: deviation += abs(minutes - usual)
        # Avgång före ankomst är fel var i resan det än står. Ett långt
        # uppehåll räknas bara mitt i resan: ett tåg får stå i timmar på
        # sin utgångs- eller slutstation.
        if minutes < 0: kind = 'backwards_dwell'
        elif stop['index'] not in inner: continue
        elif usual is not None and minutes >= max(DWELL_FACTOR * usual, usual + DWELL_MARGIN) and minutes >= DWELL_MINIMUM: kind = 'long_dwell'
        elif usual is None and minutes >= DWELL_FALLBACK: kind = 'long_dwell'
        else: continue
        found.append(dict(kind=kind, stops=(stop['index'],), at=stop['index'], minutes=minutes, usual=usual, samples=samples, days=stop['days']))
    found.sort(key=lambda f: (f['at'], len(f['stops']) == 1, KINDS.index(f['kind'])))
    seen = Counter()
    for item in found:
        # Id:t säger vad och var, aldrig när: ett beslut om avvikelsen ska
        # inte tappas för att en tid rättas någon annanstans i tåget.
        identity = f"{item['kind']}:{number}:{'>'.join(stops[i]['station'] for i in item['stops'])}"
        seen[identity] += 1
        item['id'] = identity if seen[identity] == 1 else f'{identity}#{seen[identity]}'
        item['touch'] = {stops[i]['key'] for i in item['stops']}
    unknown = sum(1 for i, j in legs if typical('leg', _pair(stops[i], stops[j]))[0] is None)
    return stops, found, (unknown, deviation), loose


def _deltas(delta, usual):
    """Förskjutningar att prova: hela timmar först om felet ser ut som en felskriven timme."""
    whole = 60 * round(delta / 60)
    options = [whole] if whole and whole != delta and abs(whole - delta) <= min(HOUR_SLACK, usual / 2) else []
    return options + ([delta] if delta else [])


def _candidates(stops, found, loose, typical):
    """Möjliga rättelser för tågets avvikelser, i den ordning de provas.

    Målet är alltid de andra tågens median, avrundad till hela minuter:
    (a) en tid i stoppet där avvikelsen är, eller i stoppet före;
    (b) båda tiderna i stoppet, lika mycket;
    (c) allt från stoppet till resans slut, när resten av tåget är förskjutet.
    Till sist förankras varje berört stopp, och varje stopp på en sträcka
    som inget annat tåg kör, mot de andra stoppen i tåget: direkt efter
    eller före ett stopp som andra tåg kör till, eller med vanligt uppehåll.
    En felskriven ankomst flyttar nämligen stoppet i körordningen, och då är
    det inte grannen i den ordningen som visar den rätta tiden.
    """
    pool = []
    def add(change):
        change = {cell: minute % 1440 for cell, minute in change.items() if minute % 1440 != _value(stops, cell)}
        if change and all(c['change'] != change for c in pool):
            pool.append(dict(change=change, stops={index for index, _ in change}, order=len(pool),
                             hour=all((minute - _value(stops, cell)) % 60 == 0 for cell, minute in change.items())))
    def shift(current, target, usual):
        return _deltas((target - current + MIDNIGHT_WRAP) % 1440 - MIDNIGHT_WRAP, usual)
    def remainder(first, on):
        return [k for k in range(first, len(stops)) if stops[k]['days'] & on]
    def anchor(s, on):
        stop = stops[s]
        for other in stops:
            if other['station'] == stop['station'] or not other['days'] & on or _start(other) is None: continue
            usual, _ = typical('leg', _pair(other, stop))
            if usual is None: continue
            for target, field, current in ((_start(other) + _whole(usual), _end_field(stop), _end(stop)),
                                           (_end(other) - _whole(usual), _start_field(stop), _start(stop))):
                for delta in shift(current, target, usual):
                    add({(s, field): current + delta})
                    add({(s, cell): minute + delta for cell, minute in _cells(stop)})
        usual, _ = typical('dwell', stop['station'])
        if _dwell(stop) is not None and usual is not None:
            for delta in shift(stop['departure'], stop['arrival'] + _whole(usual), usual):
                add({(s, 'departure_time'): stop['departure'] + delta})
            for delta in shift(stop['arrival'], stop['departure'] - _whole(usual), usual):
                add({(s, 'arrival_time'): stop['arrival'] + delta})
    for item in found:
        if len(item['stops']) == 2 and item['usual'] is not None:
            i, j = item['stops']; a, b = stops[i], stops[j]
            for delta in _deltas(_whole(item['usual']) - item['minutes'], item['usual']):
                add({(j, _end_field(b)): _end(b) + delta})
                add({(i, _start_field(a)): _start(a) - delta})
                add({(j, field): minute + delta for field, minute in _cells(b)})
                add({(k, field): minute + delta for k in remainder(j, item['days']) for field, minute in _cells(stops[k])})
        elif len(item['stops']) == 1 and item['usual'] is not None:
            s = item['at']; stop = stops[s]
            for delta in _deltas(_whole(item['usual']) - item['minutes'], item['usual']):
                add({(s, 'departure_time'): stop['departure'] + delta})
                add({(s, 'arrival_time'): stop['arrival'] - delta})
                add({(s, 'departure_time'): stop['departure'] + delta,
                     **{(k, field): minute + delta for k in remainder(s + 1, stop['days']) for field, minute in _cells(stops[k])}})
    on = set().union(*(item['days'] for item in found))
    for s in sorted({s for item in found for s in item['stops']} | loose): anchor(s, on)
    return pool


def _apply(rows, stops, change):
    """Tågets rader med ändringen gjord på varje rad i stoppet, som rutnätet gör det."""
    copies = {row['id']: dict(row) for row in rows}
    for (index, field), minute in change.items():
        for rid in stops[index]['ids']:
            copies[rid][field] = clock(minute)
            copies[rid]['sort_time'] = copies[rid].get('arrival_time') or copies[rid].get('departure_time') or None
    return list(copies.values())


def _choose(item, pool, before, unknown):
    """Bästa förslaget för avvikelsen, eller None.

    Helst ett som gör hela tåget rimligt. Har tåget flera fel som inte har
    med varandra att göra duger ett som tar bort just den här avvikelsen,
    inte skapar någon ny och inte rör stoppen där de andra sitter; de får
    sina egna förslag. Inget förslag får flytta tåget till sträckor som
    inget annat tåg kör: det vore att gömma felet, inte rätta det.

    Bland de som duger: färst sträckor som inget annat tåg kör (en tid som
    flyttar stoppet förbi nästa station gör resan till ett hopp, även om
    bara en cell ändras), färst ändrade tider, hela timmar (en felskriven
    timme är det vanligaste felet), en resa som
    stämmer bättre med de andra tågen, stoppet där avvikelsen är före andra
    stopp, tidigast stopp och sist provordningen.
    """
    best = None
    for candidate in pool:
        after, (unmatched, deviation) = candidate['after'], candidate['fit']
        if unmatched > unknown: continue
        if after:
            ids = {f['id'] for f in after}; touched = item['touch'] | candidate['keys']
            if item['id'] in ids or not ids <= before or any(f['touch'] & touched for f in after): continue
        rank = (bool(after), unmatched, len(candidate['change']), not candidate['hour'], deviation,
                item['at'] not in candidate['stops'], min(candidate['stops']), candidate['order'])
        if best is None or rank < best[0]: best = (rank, candidate)
    return best[1] if best else None


def _suggestion(stops, rows, change, names):
    cells = sorted(change, key=lambda cell: (cell[0], FIELDS.index(cell[1])))
    changes = [{'row_id': rid, 'field': field, 'from': rows[rid].get(field), 'to': clock(change[(index, field)])}
               for index, field in cells for rid in stops[index]['ids']]
    def describe(cell):
        return f'{FIELD_NAMES[cell[1]]} {names(stops[cell[0]]["station"])} {clock(_value(stops, cell))} → {clock(change[cell])}'
    if len(cells) <= 3:
        summary = ', '.join(describe(cell) for cell in cells)
    else:
        shift = (change[cells[0]] - _value(stops, cells[0]) + MIDNIGHT_WRAP) % 1440 - MIDNIGHT_WRAP
        summary = (f'{len(cells)} tider från {FIELD_NAMES[cells[0][1]]} {names(stops[cells[0][0]]["station"])} och framåt '
                   f'{duration(shift)} {"tidigare" if shift < 0 else "senare"} ({clock(_value(stops, cells[0]))} → {clock(change[cells[0]])} …)')
    return {'changes': changes, 'summary': summary}


def _text(number, item, stops, names):
    usual, samples = item['usual'], item['samples']
    others = f' Andra tåg tar {duration(usual)} (median av {samples} tåg).' if usual is not None else ''
    if len(item['stops']) == 2:
        a, b = (stops[i] for i in item['stops'])
        if item['kind'] == 'backwards_leg':
            return f'Tåg {number} är framme i {names(b["station"])} {clock(_end(b))} men lämnar {names(a["station"])} först {clock(_start(a))}.{others}'
        return f'Tåg {number} tar {duration(item["minutes"])} mellan {names(a["station"])} och {names(b["station"])}.{others}'
    stop = stops[item['at']]; name = names(stop['station'])
    if item['kind'] == 'backwards_dwell':
        return f'Tåg {number} avgår från {name} {clock(stop["departure"])}, {duration(item["minutes"])} före ankomsten {clock(stop["arrival"])}.'
    text = f'Tåg {number} står {duration(item["minutes"])} i {name} (ankomst {clock(stop["arrival"])}, avgång {clock(stop["departure"])}).'
    if usual is not None: return f'{text} Andra tåg står {duration(usual)} (median av {samples} tåg).'
    return f'{text} För få andra tåg stannar här för en jämförelse; uppehåll på {duration(DWELL_FALLBACK)} eller mer markeras ändå.'


def fingerprint(rows, ids):
    """Innehållet i raderna avvikelsen gäller. Ett beslut gäller tills de ändras."""
    relevant = [{k: rows[rid].get(k) for k in ('id', 'station_id', 'train_number', 'days', 'arrival_time', 'departure_time', 'no_stop')}
                for rid in sorted(ids)]
    return hashlib.sha256(json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_sanity(draft):
    """Alla avvikelser i det sparade EU-utkastet, tåg för tåg i nummerordning."""
    stations = {s['id']: s for s in draft.get('stations', [])}
    rows = {r['id']: r for r in draft.get('trains', [])}
    edges = {frozenset((e.get('station_a_id'), e.get('station_b_id'))): e['id'] for e in draft.get('connections', [])}
    names = lambda sid: stations.get(sid, {}).get('name') or sid
    trains = defaultdict(list)
    # Rader med okänd station eller utan tågnummer är strukturfel i
    # konfliktkontrollen. Här finns inget rimligt att jämföra dem med.
    for row in rows.values():
        number = str(row.get('train_number') or '')
        if row.get('station_id') in stations and number: trains[number].append(row)
    tables = _samples(trains); items = []
    for number in sorted(trains, key=train_number_key):
        typical = _typical(tables, number); group = trains[number]
        stops, found, (unknown, _), loose = _check(number, group, typical)
        if not found: continue
        pool = _candidates(stops, found, loose, typical)
        for candidate in pool:
            _, candidate['after'], candidate['fit'], _ = _check(number, _apply(group, stops, candidate['change']), typical)
            candidate['keys'] = {stops[index]['key'] for index in candidate['stops']}
        before = {f['id'] for f in found}
        for item in found:
            places = [stops[i] for i in item['stops']]
            # Stoppet där avvikelsen är först, så att "Visa i rutnätet" landar där.
            ids = [rid for stop in reversed(places) for rid in stop['ids']]
            chosen = _choose(item, pool, before, unknown)
            items.append(dict(
                id=item['id'], kind=item['kind'], level='sanity', title=TITLES[item['kind']], train_number=number,
                station_ids=[stop['station'] for stop in places], station=stops[item['at']]['station'],
                edge=edges.get(frozenset(stop['station'] for stop in places)) if len(places) == 2 else None,
                weekdays=sorted(item['days']), days_label=day_label(item['days']),
                value_minutes=item['minutes'], baseline_minutes=_whole(item['usual']) if item['usual'] is not None else None,
                samples=item['samples'], text=_text(number, item, stops, names),
                source_rows=ids, source_refs=source_refs(rows, ids), fingerprint=fingerprint(rows, ids),
                suggestion=_suggestion(stops, rows, chosen['change'], names) if chosen else None))
    return items
