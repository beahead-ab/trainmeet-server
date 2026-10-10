// Drift · Kontrollrummet: siffrorna bakom vyn (web/drift-model.js).
// Rena funktioner över /v1/display-bilden, boxlistan och Clouds presentation.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const model = require(path.resolve(__dirname, '../../src/tmbox_gateway/web/drift-model.js'));

const stations = [
  {id: 'a', name: 'Alvesta', code: 'ALV'},
  {id: 'b', name: 'Bjärred', code: 'BJD'},
  {id: 'c', name: 'Charlottenberg', code: 'CHB'},
];

function stop(station, order, arrival, departure, extra = {}) {
  return {station_id: station, stop_order: order, arrival_time: arrival, departure_time: departure, ...extra};
}

function snapshot(overrides = {}) {
  return {
    clock: {time: '10:00:00'},
    stations,
    services: [
      {train_number: '101', stops: [stop('a', 1, null, '09:50:00'), stop('b', 2, '10:20:00', '10:25:00'), stop('c', 3, '10:50:00', null)]},
      {train_number: '20', stops: [stop('c', 1, null, '10:10:00'), stop('b', 2, '10:30:00', '10:30:00'), stop('a', 3, '11:00:00', null)]},
      {train_number: '3', stops: [stop('a', 1, null, '12:00:00'), stop('c', 2, '12:40:00', null)]},
    ],
    routes: [],
    connection_states: [],
    train_positions: [],
    ...overrides,
  };
}

test('minutes and hhmm read clock times and reject everything else', () => {
  assert.equal(model.minutes('05:12'), 312);
  assert.equal(model.minutes('05:12:40'), 312);
  assert.equal(model.minutes('7:05'), 425);
  for (const bad of [null, undefined, '', 'nu', '5']) assert.equal(model.minutes(bad), null);
  assert.equal(model.hhmm('05:12:40'), '05:12');
  assert.equal(model.hhmm('soon'), '');
});

test('services keeps one train per number, the one with most stops, sorted as numbers', () => {
  const list = model.services(snapshot({
    services: [
      {train_number: '20', stops: [stop('a', 1, null, '10:00')]},
      {train_number: '3', stops: []},
      {train_number: '20', stops: [stop('a', 1, null, '10:00'), stop('b', 2, '10:10', null)]},
      {train_number: '', stops: [stop('a', 1, null, '10:00')]},
    ],
  }));
  assert.deepEqual(list.map((service) => service.train_number), ['3', '20']);
  assert.equal(list[1].stops.length, 2);
  assert.deepEqual(model.services(null), []);
});

test('trains reads reserved and occupied channels first, then noted positions', () => {
  const found = model.trains(snapshot({
    connection_states: [{channels: [
      {train_number: '101', state: 'reserved', from_station_id: 'a', to_station_id: 'b'},
      {train_number: '20', state: 'occupied', from_station_id: 'c', to_station_id: 'b'},
      {train_number: '3', state: 'free', from_station_id: 'a', to_station_id: 'c'},
      {state: 'occupied', from_station_id: 'a', to_station_id: 'c'},
    ]}],
    train_positions: [
      {train_number: '101', status: 'station', station_id: 'a'},
      {train_number: '7', status: 'connection', from_station_id: 'b', to_station_id: 'c'},
      {train_number: '8', status: 'station', station_id: 'c'},
    ],
  }));
  assert.deepEqual(found.onLine.map((train) => [train.trainNumber, train.departed]), [['101', false], ['20', true], ['7', true]]);
  assert.deepEqual(found.atStation, [{trainNumber: '8', station: 'c'}]);
});

test('trainStates and stationCounts say where each train is', () => {
  const snap = snapshot({
    connection_states: [{channels: [
      {train_number: '101', state: 'reserved', from_station_id: 'a', to_station_id: 'b'},
      {train_number: '20', state: 'occupied', from_station_id: 'c', to_station_id: 'b'},
    ]}],
    train_positions: [{train_number: '3', status: 'station', station_id: 'a'}, {train_number: '12', status: 'station', station_id: 'a'}],
  });
  const states = model.trainStates(snap);
  assert.equal(states.get('101').state, 'cleared');
  assert.equal(states.get('20').state, 'on-line');
  assert.equal(states.get('3').state, 'at-station');
  const counts = model.stationCounts(snap);
  assert.deepEqual(counts.get('a'), ['3', '12']);
  assert.deepEqual(counts.get('b'), []);
});

test('lateTrains are the trains on the line whose booked arrival has already passed', () => {
  const snap = snapshot({
    clock: {time: '10:30:00'},
    connection_states: [{channels: [
      {train_number: '101', state: 'occupied', from_station_id: 'a', to_station_id: 'b'},
      {train_number: '20', state: 'occupied', from_station_id: 'c', to_station_id: 'b'},
    ]}],
    routes: [
      {train_number: '101', station_id: 'b', arrival_time: '10:20:00'},
      {train_number: '20', station_id: 'b', arrival_time: '10:45:00'},
    ],
  });
  assert.deepEqual(model.lateTrains(snap).map((train) => train.trainNumber), ['101']);
});

test('placement lists the neighbours the cloud put left and right', () => {
  const presentation = {supported: true, stations: [{station_id: 'b', connections: [
    {side: 'left', other_station_code: 'ALV'}, {side: 'right', other_station_code: 'CHB'},
  ]}]};
  assert.deepEqual(model.placement(presentation, 'b'), {available: true, left: ['ALV'], right: ['CHB']});
  assert.equal(model.placement(presentation, 'a').available, false);
  assert.equal(model.placement({supported: false, stations: presentation.stations}, 'b').available, false);
  assert.equal(model.placement(null, 'b').available, false);
});

test('connectionTone: online is fine, lost warns, anything else is off', () => {
  assert.equal(model.connectionTone({state: 'online'}), 'ok');
  assert.equal(model.connectionTone({state: 'lost'}), 'warn');
  assert.equal(model.connectionTone({state: 'offline'}), 'off');
  assert.equal(model.connectionTone(null), 'off');
});

test('stationRows: waiting boxes first, then every station with its box or as unmanned', () => {
  const devices = [
    {device_id: 'w1', station_id: null, connection: {state: 'online'}},
    {device_id: 'd1', station_id: 'a', connection: {state: 'online'}},
    {device_id: 'd2', station_id: 'b', connection: {state: 'lost'}},
    {device_id: 'd3', station_id: 'b', connection: {state: 'online'}},
  ];
  const rows = model.stationRows({snapshot: snapshot({train_positions: [{train_number: '3', status: 'station', station_id: 'a'}]}), devices});
  assert.deepEqual(rows.map((row) => [row.kind, row.station?.id || null, row.tone]), [
    ['waiting', null, 'warn'], ['box', 'a', 'ok'], ['box', 'b', 'warn'], ['box', 'b', 'ok'], ['unmanned', 'c', 'off'],
  ]);
  assert.equal(rows[1].trains, 1);
  assert.equal(new Set(rows.map((row) => row.key)).size, rows.length);
});

test('stationRows: the automatic stations - unmanned ones are automatic, a box keeps its row, a lost one warns', () => {
  // /v1/automatic-stations. Simuleringen gav samma bild förut; nu är det automatiken i vanlig drift.
  const automatic = {enabled: true, stations: [{id: 'a', mode: 'automatic'}, {id: 'b', mode: 'disconnected'}, {id: 'c', mode: 'automatic'}]};
  const devices = [{device_id: 'd1', station_id: 'a', connection: {state: 'offline'}}, {device_id: 'd2', station_id: 'b', connection: {state: 'online'}}];
  const rows = model.stationRows({snapshot: snapshot(), devices, automatic});
  assert.deepEqual(rows.map((row) => [row.kind, row.station.id, row.tone, row.auto?.mode]),
    [['box', 'a', 'off', 'automatic'], ['box', 'b', 'warn', 'disconnected'], ['automatic', 'c', 'auto', 'automatic']]);
  assert.equal(model.stats(snapshot(), rows).automatic, 2, 'a and c are run by the automation');
  const off = model.stationRows({snapshot: snapshot(), devices, automatic: {enabled: false, stations: automatic.stations}});
  assert.deepEqual(off.map((row) => row.kind), ['box', 'box', 'unmanned'], 'switched off: nothing is automatic');
});

test('stationRows: a station its TKL works is manned, not unmanned; a box at an automatic station does not man it', () => {
  // Drift bygger raderna av boxarna; en TKL står inte i den listan. Förut stod
  // en station som bara hade sin TKL som "Obemannad" (Casper, 2026-10-10).
  const automatic = {enabled: true, stations: [
    {id: 'a', mode: 'automatic', operator: null, available_operators: ['d1']},
    {id: 'b', mode: 'manual', operator: 'tkl-b', available_operators: ['tkl-b']},
    {id: 'c', mode: 'automatic', operator: null, available_operators: []}]};
  const devices = [{device_id: 'd1', station_id: 'a', connection: {state: 'online'}}, {device_id: 'd2', station_id: 'a', connection: {state: 'online'}}];
  const rows = model.stationRows({snapshot: snapshot(), devices, automatic});
  assert.deepEqual(rows.map((row) => [row.kind, row.station.id, row.tone, row.first]),
    [['box', 'a', 'ok', true], ['box', 'a', 'ok', false], ['operator', 'b', 'ok', true], ['automatic', 'c', 'auto', true]]);
  const stats = model.stats(snapshot(), rows);
  assert.equal(stats.manned, 1, 'only b: the boxes at a are connected but the automation works a');
  assert.equal(stats.automatic, 2);
});

test('stats counts trains on the line, cleared, at stations, manned stations and deviations', () => {
  const snap = snapshot({
    clock: {time: '10:30:00'},
    connection_states: [{channels: [
      {train_number: '101', state: 'occupied', from_station_id: 'a', to_station_id: 'b'},
      {train_number: '20', state: 'reserved', from_station_id: 'c', to_station_id: 'b'},
    ]}],
    train_positions: [{train_number: '3', status: 'station', station_id: 'a'}],
    routes: [{train_number: '101', station_id: 'b', arrival_time: '10:20:00'}],
  });
  const rows = model.stationRows({snapshot: snap, devices: [{device_id: 'd1', station_id: 'a', connection: {state: 'online'}}]});
  assert.deepEqual(model.stats(snap, rows), {onLine: 1, cleared: 1, atStations: 1, manned: 1, stations: 3, automatic: 0, deviations: 1});
});

test('events (whole line): each train’s next event, in time order', () => {
  const snap = snapshot({
    connection_states: [{channels: [{train_number: '101', state: 'occupied', from_station_id: 'a', to_station_id: 'b'}]}],
    train_positions: [{train_number: '20', status: 'station', station_id: 'c'}],
  });
  const list = model.events(snap, {limit: 10});
  assert.deepEqual(list.map((event) => [event.train, event.kind, event.time, event.delta]), [
    ['20', 'dep', '10:10', 10], ['101', 'arr', '10:20', 20], ['3', 'dep', '12:00', 120],
  ]);
  assert.equal(list[0].state, 'at-station');
  assert.equal(list[1].state, 'on-line');
  assert.equal(list[1].station, 'Bjärred');
});

test('events (whole line): a train that is not running yet is listed by its first coming stop', () => {
  const list = model.events(snapshot(), {limit: 10});
  assert.deepEqual(list.map((event) => [event.train, event.time]), [['20', '10:10'], ['101', '10:25'], ['3', '12:00']]);
  assert.equal(list[0].nextStationId, 'b');
});

test('events (one station) are in time order and skip what already happened', () => {
  const list = model.events(snapshot(), {station: 'b'});
  assert.deepEqual(list.map((event) => [event.train, event.kind, event.time]), [['101', 'arr', '10:20'], ['101', 'dep', '10:25'], ['20', 'arr', '10:30']]);
});

test('events (one train) are its remaining stops', () => {
  const list = model.events(snapshot(), {train: '101'});
  assert.deepEqual(list.map((event) => [event.stationId, event.kind, event.time]), [['b', 'dep', '10:25'], ['c', 'arr', '10:50']]);
  assert.deepEqual(model.events(snapshot(), {train: 'nope'}), []);
});

test('events honours the limit', () => {
  assert.equal(model.events(snapshot(), {limit: 1}).length, 1);
});

test('graph: a window on whole hours with the now line inside, lit trains get their segment', () => {
  const snap = snapshot({connection_states: [{channels: [{train_number: '101', state: 'occupied', from_station_id: 'a', to_station_id: 'b'}]}]});
  const graph = model.graph(snap, {windowMinutes: 180, train: '20'});
  assert.equal(graph.start % 60, 0);
  assert.equal(graph.end - graph.start, 180);
  assert.equal(graph.now, 600);
  assert.ok(graph.start <= graph.now && graph.now <= graph.end);
  const byNumber = Object.fromEntries(graph.lines.map((line) => [line.number, line]));
  assert.equal(byNumber['101'].lit, true);
  assert.deepEqual(byNumber['101'].segment.map((point) => point.station), ['a', 'b']);
  assert.equal(byNumber['20'].selected, true);
  assert.equal(byNumber['20'].lit, true);
  assert.equal(byNumber['3'].lit, false);
  assert.equal(byNumber['3'].segment, null);
});

test('graph: the whole day covers every point on whole hours', () => {
  const graph = model.graph(snapshot(), {windowMinutes: 0});
  assert.equal(graph.start, 9 * 60);
  assert.equal(graph.end, 13 * 60);
});

test('graph: stations follow the shown order, then the rest', () => {
  const snap = snapshot({display: {graph_station_order: ['c', 'zz', 'a']}});
  assert.deepEqual(model.graph(snap).stations.map((station) => station.id), ['c', 'a', 'b']);
});

test('graph: a night train that passes midnight stays continuous around the clock', () => {
  const night = snapshot({
    clock: {time: '00:10:00'},
    services: [{train_number: '900', stops: [
      stop('a', 1, null, '23:40:00'),
      stop('b', 2, '00:20:00', '00:25:00', {service_day_offset: 1}),
      stop('c', 3, '00:50:00', null, {service_day_offset: 1}),
    ]}],
  });
  const graph = model.graph(night, {windowMinutes: 180});
  // The clock is time of day; the train is moved to the occurrence around it.
  const points = graph.lines[0].points.map((point) => point.minute);
  assert.deepEqual(points, [-20, 20, 25, 50]);
  assert.equal(graph.now, 10);
  assert.ok(graph.start <= graph.now && graph.now <= graph.end);
  // The whole day shows the timetable as it is, with the clock among the night train's points.
  const day = model.graph(night, {windowMinutes: 0});
  assert.deepEqual(day.lines[0].points.map((point) => point.minute), [1420, 1460, 1465, 1490]);
  assert.equal(day.now, 10 + 1440);
});

test('graph: before the first train the window shows this morning, not the night train tomorrow (#137)', () => {
  // After Nollställ träffen the clock stands at the plan's start, before the
  // first train, while a night train is still out at that hour next morning.
  const early = snapshot({
    clock: {time: '05:00:00'},
    services: [
      {train_number: '1', stops: [stop('a', 1, null, '05:30:00'), stop('b', 2, '06:00:00', null)]},
      {train_number: '2', stops: [stop('b', 1, null, '07:00:00'), stop('c', 2, '07:30:00', null)]},
      {train_number: '9', stops: [stop('a', 1, null, '22:00:00'), stop('c', 2, '06:00:00', null, {service_day_offset: 1})]},
    ],
  });
  for (const windowMinutes of [120, 180, 360]) {
    const graph = model.graph(early, {windowMinutes});
    assert.equal(graph.now, 5 * 60, `${windowMinutes} min`);
    assert.ok(graph.start <= 5 * 60 && 5 * 60 < graph.end, `${windowMinutes} min`);
    const byNumber = Object.fromEntries(graph.lines.map((line) => [line.number, line.points.map((point) => point.minute)]));
    assert.deepEqual(byNumber['1'], [330, 360]);
    assert.deepEqual(byNumber['2'], [420, 450]);
    // Last night's night train, arriving at 06:00 this morning.
    assert.deepEqual(byNumber['9'], [22 * 60 - 1440, 6 * 60]);
  }
  const day = model.graph(early, {windowMinutes: 0});
  assert.equal(day.now, 5 * 60);
});

test('graph: a train with fewer than two points on the diagram is left out', () => {
  const lonely = snapshot({services: [{train_number: '1', stops: [stop('a', 1, null, '10:00:00')]}]});
  assert.deepEqual(model.graph(lonely).lines, []);
});

test('search finds trains by number and stations by name or code, trains first', () => {
  const snap = snapshot();
  assert.deepEqual(model.search(snap, '  '), []);
  const byNumber = model.search(snap, '10');
  assert.deepEqual(byNumber.map((hit) => [hit.kind, hit.id]), [['train', '101']]);
  assert.equal(byNumber[0].detail, 'Alvesta → Charlottenberg');
  const byName = model.search(snap, 'bjär');
  assert.deepEqual(byName.map((hit) => [hit.kind, hit.id, hit.detail]), [['station', 'b', 'BJD']]);
  assert.deepEqual(model.search(snap, 'chb').map((hit) => hit.id), ['c']);
  assert.equal(model.search(snap, 'a', {limit: 2}).length, 2);
});

test('nothing breaks on an empty or missing picture', () => {
  for (const empty of [null, undefined, {}]) {
    assert.deepEqual(model.trains(empty), {onLine: [], atStation: []});
    assert.deepEqual(model.events(empty), []);
    assert.deepEqual(model.stationRows({snapshot: empty}), []);
    assert.deepEqual(model.graph(empty).lines, []);
    assert.deepEqual(model.search(empty, 'a'), []);
    assert.equal(model.stats(empty).stations, 0);
  }
});

// Vilken tur ett tåg på linjen kör. Tåg 7 går a → b två gånger om dagen, och
// kvällsturen ligger först i bilden, som den gjorde i Caspers tidtabell där
// morgontåget 319 fick "ank 19:05" klockan 10:26. Tåg 9 vänder: a → b → a → b.
function twiceADay(overrides = {}) {
  const service = (id, number, stops) => ({ id, train_number: number, days: 'Dagl', stops });
  const row = (id, service_id, number, station_id, arrival_time, departure_time) => ({ id, service_id, train_number: number, station_id, arrival_time, departure_time });
  return snapshot({
    services: [
      service('s-7-pm', '7', [stop('a', 0, null, '18:50'), stop('b', 1, '19:05', null)]),
      service('s-7-am', '7', [stop('a', 0, null, '09:00'), stop('b', 1, '09:20', null)]),
      service('s-9', '9', [stop('a', 0, null, '08:00'), stop('b', 1, '08:20', '08:25'), stop('a', 2, '08:45', '08:50'), stop('b', 3, '09:10', null)]),
    ],
    trains: [
      row('m-7-pm-a', 's-7-pm', '7', 'a', null, '18:50'), row('m-7-pm-b', 's-7-pm', '7', 'b', '19:05', null),
      row('m-7-am-a', 's-7-am', '7', 'a', null, '09:00'), row('m-7-am-b', 's-7-am', '7', 'b', '09:20', null),
      row('m-9-a1', 's-9', '9', 'a', null, '08:00'), row('m-9-b1', 's-9', '9', 'b', '08:20', '08:25'),
      row('m-9-a2', 's-9', '9', 'a', '08:45', '08:50'), row('m-9-b2', 's-9', '9', 'b', '09:10', null),
    ],
    ...overrides,
  });
}
const onLine = (number, extra = {}) => ({ train_number: number, status: 'connection', connection_id: 'c-ab', from_station_id: 'a', to_station_id: 'b', ...extra });

test('the movement that left points out the run the train is on', () => {
  const snap = twiceADay({ clock: { time: '09:05:00' }, train_positions: [onLine('7', { movement_id: 'm-7-am-a' })] });
  const [train] = model.trains(snap).onLine;
  assert.equal(train.movementId, 'm-7-am-a');
  const leg = model.onLineLeg(snap, train);
  assert.equal(leg.service.id, 's-7-am');
  assert.deepEqual([leg.departure_time, leg.arrival_time, leg.departure, leg.arrival], ['09:00', '09:20', 540, 560]);
  // Kvällsturen när det är den som avgick, även om klockan står på morgonen.
  const evening = model.onLineLeg(snap, { ...train, movementId: 'm-7-pm-a' });
  assert.deepEqual([evening.service.id, evening.arrival_time], ['s-7-pm', '19:05']);
  // Kanalen på sträckan bär också rörelsen.
  const viaChannel = twiceADay({ clock: { time: '09:05:00' }, connection_states: [{ id: 'c-ab', state: 'occupied',
    channels: [{ train_number: '7', from_station_id: 'a', to_station_id: 'b', state: 'occupied', movement_id: 'm-7-am-a' }] }] });
  assert.equal(model.onLineLeg(viaChannel, model.trains(viaChannel).onLine[0]).arrival_time, '09:20');
});

test('a second call at the same station is told apart by the movement', () => {
  const snap = twiceADay({ clock: { time: '08:55:00' }, train_positions: [onLine('9', { movement_id: 'm-9-a2' })] });
  const leg = model.onLineLeg(snap, model.trains(snap).onLine[0]);
  assert.deepEqual([leg.departure_time, leg.arrival_time], ['08:50', '09:10']);
  const first = model.onLineLeg(snap, { ...model.trains(snap).onLine[0], movementId: 'm-9-a1' });
  assert.deepEqual([first.departure_time, first.arrival_time], ['08:00', '08:20']);
});

test('without a movement the run nearest the actual departure, else nearest the clock, is chosen', () => {
  const actual = twiceADay({ clock: { time: '09:05:00' }, train_positions: [onLine('7', { departed_seconds: 9 * 3600 + 2 * 60 })] });
  assert.equal(model.onLineLeg(actual, model.trains(actual).onLine[0]).arrival_time, '09:20');
  const lateEvening = twiceADay({ clock: { time: '00:10:00' }, train_positions: [onLine('7', { departed_seconds: 18 * 3600 + 58 * 60 })] });
  assert.equal(model.onLineLeg(lateEvening, model.trains(lateEvening).onLine[0]).arrival_time, '19:05', 'the actual departure counts, not the clock');
  const morning = twiceADay({ clock: { time: '09:05:00' }, train_positions: [onLine('7')] });
  assert.equal(model.onLineLeg(morning, model.trains(morning).onLine[0]).arrival_time, '09:20');
  const evening = twiceADay({ clock: { time: '18:55:00' }, train_positions: [onLine('7')] });
  assert.equal(model.onLineLeg(evening, model.trains(evening).onLine[0]).arrival_time, '19:05');
  // En rörelse som inte går att para med tidtabellen stoppar inget: då gäller närmast.
  const unknown = twiceADay({ clock: { time: '09:05:00' }, train_positions: [onLine('7', { movement_id: 'm-gone' })] });
  assert.equal(model.onLineLeg(unknown, model.trains(unknown).onLine[0]).arrival_time, '09:20');
  const mismatch = twiceADay({ clock: { time: '09:05:00' }, train_positions: [onLine('7', { movement_id: 'm-odd' })] });
  mismatch.trains.push({ id: 'm-odd', service_id: 's-7-am', train_number: '7', station_id: 'a', arrival_time: null, departure_time: '09:01' });
  assert.equal(model.onLineLeg(mismatch, model.trains(mismatch).onLine[0]).arrival_time, '09:20', 'a movement whose times match no stop');
  // En äldre bild utan turer: ruttstoppen räcker.
  const routesOnly = twiceADay({ clock: { time: '18:55:00' }, services: [], train_positions: [onLine('7')],
    routes: [['s-7-pm', 'a', 0, null, '18:50'], ['s-7-pm', 'b', 1, '19:05', null], ['s-7-am', 'a', 0, null, '09:00'], ['s-7-am', 'b', 1, '09:20', null]]
      .map(([service_id, station_id, stop_order, arrival_time, departure_time]) => ({ service_id, train_number: '7', station_id, stop_order, arrival_time, departure_time })) });
  assert.equal(model.onLineLeg(routesOnly, model.trains(routesOnly).onLine[0]).arrival_time, '19:05');
  // Ett tåg utan sträckan i tidtabellen men med en ankomst till stationen (ett extratåg med bara ruttstopp): ankomsten, utan avgång.
  const extra = twiceADay({ clock: { time: '14:26:00' }, train_positions: [onLine('900')], routes: [{ train_number: '900', station_id: 'b', arrival_time: '14:20' }] });
  const arrivalOnly = model.onLineLeg(extra, model.trains(extra).onLine[0]);
  assert.deepEqual([arrivalOnly.arrival_time, arrivalOnly.departure, arrivalOnly.from], ['14:20', null, null]);
  assert.deepEqual(model.lateTrains(extra).map((train) => train.trainNumber), ['900']);
  // Utan sträckan i tidtabellen: inget svar.
  const elsewhere = twiceADay({ train_positions: [onLine('7', { to_station_id: 'c' })] });
  assert.equal(model.onLineLeg(elsewhere, model.trains(elsewhere).onLine[0]), null);
});

test('late is judged on the run the train is on', () => {
  const onTime = twiceADay({ clock: { time: '09:10:00' }, train_positions: [onLine('7', { movement_id: 'm-7-am-a' })] });
  assert.deepEqual(model.lateTrains(onTime), []);
  assert.equal(model.stats(onTime).deviations, 0);
  const late = twiceADay({ clock: { time: '09:30:00' }, train_positions: [onLine('7', { movement_id: 'm-7-am-a' })] });
  assert.deepEqual(model.lateTrains(late).map((train) => train.trainNumber), ['7'], 'the morning arrival has passed; the evening one has not');
  assert.equal(model.stats(late).deviations, 1);
  const cleared = twiceADay({ clock: { time: '09:30:00' }, connection_states: [{ id: 'c-ab', state: 'reserved',
    channels: [{ train_number: '7', from_station_id: 'a', to_station_id: 'b', state: 'reserved', movement_id: 'm-7-am-a' }] }] });
  assert.deepEqual(model.lateTrains(cleared), [], 'a train with a clear has not left');
});

test('a station without contact counts down to the automation, or waits', () => {
  // 4.2.0: "Kontakt saknas – automatik om N min" på Drift, räknat från hämtningen.
  const fetched = 1_000_000;
  assert.equal(model.takesOverMinutes({ mode: 'disconnected', takes_over_in: 200 }, fetched, fetched), 4);
  assert.equal(model.takesOverMinutes({ mode: 'disconnected', takes_over_in: 200 }, fetched, fetched + 90_000), 2, 'counts on without a new fetch');
  assert.equal(model.takesOverMinutes({ mode: 'disconnected', takes_over_in: 200 }, fetched, fetched + 400_000), 1, 'never below one minute until it switches');
  assert.equal(model.takesOverMinutes({ mode: 'disconnected', takes_over_in: null }, fetched, fetched), null, 'Aldrig: the station waits');
  assert.equal(model.takesOverMinutes({ mode: 'manual', takes_over_in: 200 }, fetched, fetched), null);
  assert.equal(model.takesOverMinutes(null, fetched, fetched), null);
});
