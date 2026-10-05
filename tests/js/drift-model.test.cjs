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

test('stationRows: an automatic simulated station has no box, a disconnected one warns', () => {
  const simulation = {active: true, stations: [{id: 'a', mode: 'automatic'}, {id: 'b', mode: 'disconnected'}]};
  const devices = [{device_id: 'd2', station_id: 'b', connection: {state: 'online'}}];
  const rows = model.stationRows({snapshot: snapshot(), devices, simulation});
  assert.deepEqual(rows.map((row) => [row.kind, row.tone]), [['simulated', 'sim'], ['box', 'warn'], ['unmanned', 'off']]);
  const idle = model.stationRows({snapshot: snapshot(), devices, simulation: {active: false, stations: simulation.stations}});
  assert.deepEqual(idle.map((row) => row.kind), ['unmanned', 'box', 'unmanned']);
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
  assert.deepEqual(model.stats(snap, rows), {onLine: 1, cleared: 1, atStations: 1, manned: 1, stations: 3, simulated: 0, deviations: 1});
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

test('graph: a night train that passes midnight keeps counting upwards', () => {
  const night = snapshot({
    clock: {time: '00:10:00'},
    services: [{train_number: '900', stops: [
      stop('a', 1, null, '23:40:00'),
      stop('b', 2, '00:20:00', '00:25:00', {service_day_offset: 1}),
      stop('c', 3, '00:50:00', null, {service_day_offset: 1}),
    ]}],
  });
  const graph = model.graph(night, {windowMinutes: 180});
  const points = graph.lines[0].points.map((point) => point.minute);
  assert.deepEqual(points, [1420, 1460, 1465, 1490]);
  assert.equal(graph.now, 10 + 1440);
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
