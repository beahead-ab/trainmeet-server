// Verkliga tider och förseningar i tidtabellen, som i SJ:s app (Casper 2026-10-08):
// en försening från +3 min är röd, den nya tiden räknas fram, ett tåg som står
// kvar efter sin avgångstid räknas upp ("beräknad"), och ett läge som bara
// tidtabellen gav är i tid. changeTracker säger vilka rader som just ändrats.
// Kör: node --test tests/js/train-live.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const model = require('../../src/tmbox_gateway/web/drift-model.js');

const at = (hhmm) => { const [h, m, s = 0] = hhmm.split(':').map(Number); return h * 3600 + m * 60 + s; };
const minute = (hhmm) => at(hhmm) / 60;
// 101: A 09:20 → B 09:35/09:37 → C 09:50
const stops = [['a', null, '09:20'], ['b', '09:35', '09:37'], ['c', '09:50', null]];
const snapshot = (live = {}, extra = {}) => ({
  clock: { time: '09:00:00' },
  stations: [{ id: 'a', name: 'Alvesta' }, { id: 'b', name: 'Bor' }, { id: 'c', name: 'Cdal' }],
  services: [{ id: 's101', train_number: '101', stops: stops.map(([station_id, arrival_time, departure_time], stop_order) => ({ station_id, arrival_time, departure_time, stop_order })) }],
  trains: stops.map(([station_id, arrival_time, departure_time], i) => ({ id: `m${i}`, service_id: 's101', train_number: '101', station_id, arrival_time, departure_time })),
  movement_live: live, connection_states: [], train_positions: [], ...extra,
});
const live101 = (snap, now) => model.trainLive(snap, at(now)).get('101');

test('a late departure is red from three minutes, and the arrivals ahead get a new time', () => {
  const two = live101(snapshot({ m0: { arrival: 'none', departure: 'departed', departed_seconds: at('09:22') } }), '09:25');
  assert.equal(two.state, 'on_line');
  assert.deepEqual([two.delayMinutes, two.late], [2, false], 'two minutes is on time');
  const seven = live101(snapshot({ m0: { arrival: 'none', departure: 'departed', departed_seconds: at('09:27') } }), '09:30');
  assert.deepEqual([seven.delayMinutes, seven.late, seven.estimated], [7, true, false]);
  assert.deepEqual([seven.from, seven.to], ['a', 'b']);
  assert.equal(seven.stops[1].expectedArrival, minute('09:42'), 'B 09:35 becomes 09:42');
  assert.equal(seven.stops[2].expectedArrival, minute('09:57'));
  const three = live101(snapshot({ m0: { arrival: 'none', departure: 'departed', departed_seconds: at('09:23') } }), '09:24');
  assert.equal(three.late, true, 'exactly three is late');
});

test('a train still standing after its departure time counts up, estimated', () => {
  const at26 = live101(snapshot(), '09:26');
  assert.deepEqual([at26.state, at26.station, at26.delayMinutes, at26.estimated, at26.late], ['waiting', 'a', 6, true, true]);
  assert.equal(live101(snapshot(), '09:30').delayMinutes, 10, 'and keeps counting');
  assert.equal(live101(snapshot(), '09:00').state, 'not_departed');
});

test('a position the timetable gave is on time until its departure has passed', () => {
  const placed = { m0: { arrival: 'none', departure: 'departed', by_timetable: true }, m1: { arrival: 'arrived', departure: 'positioned', by_timetable: true } };
  const before = live101(snapshot(placed), '09:36');
  assert.deepEqual([before.state, before.station, before.delayMinutes, before.late], ['at_station', 'b', 0, false]);
  const after = live101(snapshot(placed), '09:45');
  assert.deepEqual([after.state, after.delayMinutes, after.estimated], ['waiting', 8, true]);
});

test('a position recorded without a time keeps the delay known before it', () => {
  // 101 left Alvesta seven late; its arrival in Bor came in afterwards without a time ("hoppar fram").
  const jumped = { m0: { departure: 'departed', departed_seconds: at('09:27') }, m1: { arrival: 'arrived', departure: 'positioned' } };
  const train = live101(snapshot(jumped), '09:36');
  assert.deepEqual([train.state, train.station, train.delayMinutes, train.late], ['at_station', 'b', 7, true]);
});

test('an arrival at the last station gives the arrival delay', () => {
  const done = { m0: { departure: 'departed', departed_seconds: at('09:20') }, m1: { arrival: 'arrived', departure: 'departed', arrived_seconds: at('09:35'), departed_seconds: at('09:38') },
    m2: { arrival: 'arrived', arrived_seconds: at('09:56') } };
  const train = live101(snapshot(done), '10:10');
  assert.deepEqual([train.state, train.station, train.delayMinutes, train.late], ['arrived', 'c', 6, true]);
  assert.equal(train.arrivedAt, minute('09:56'));
});

test('a train on a line by its channel is on the line even without times', () => {
  const channel = { connection_states: [{ id: 'ab', channels: [{ train_number: '101', from_station_id: 'a', to_station_id: 'b', state: 'occupied' }] }] };
  const train = live101(snapshot({}, channel), '09:21');
  assert.deepEqual([train.state, train.from, train.to, train.late], ['on_line', 'a', 'b', false]);
});

test('events carry the delay and the new time', () => {
  const snap = snapshot({ m0: { arrival: 'none', departure: 'departed', departed_seconds: at('09:27') } },
    { connection_states: [{ id: 'ab', channels: [{ train_number: '101', from_station_id: 'a', to_station_id: 'b', state: 'occupied' }] }] });
  const [event] = model.events(snap, { nowSeconds: at('09:30') });
  assert.deepEqual([event.kind, event.stationId, event.time, event.late, event.delayMinutes, event.expectedTime], ['arr', 'b', '09:35', true, 7, '09:42']);
});

test('a stop matching two rows is ambiguous and gives no movement', () => {
  const snap = snapshot();
  snap.trains.push({ ...snap.trains[0], id: 'twin' });
  assert.equal(model.movementOf(snap, snap.services[0], snap.services[0].stops[0]), null);
  assert.equal(model.movementOf(snap, snap.services[0], snap.services[0].stops[1]), 'm1');
});

test('changeTracker: the first sight is no change, a new signature is', () => {
  const tracker = model.changeTracker();
  assert.equal(tracker.note('101', ['waiting', 0], 1000), null, 'a list drawn the first time does not flash');
  assert.equal(tracker.note('101', ['waiting', 0], 2000), null);
  assert.equal(tracker.note('101', ['on_line', 7], 3000), 3000, 'changed');
  assert.equal(tracker.note('101', ['on_line', 7], 9000), 3000, 'remembers when');
  assert.equal(tracker.note('102', ['on_line', 0], 9000), null, 'a new row is not a change');
});

// ── För tidigt och nivåerna (Casper 2026-10-08) ─────────────────────────
const typed = (type, live) => { const snap = snapshot(live); snap.services[0].train_type = type; return snap; };

test('an early departure is early for a passenger train, never for goods or work', () => {
  const left = { m0: { departure: 'departed', departed_seconds: at('09:18') } };
  const passenger = live101(typed('person', left), '09:19');
  assert.deepEqual([passenger.delayMinutes, passenger.earlyMinutes, passenger.earlyKind, passenger.late], [0, 2, 'dep', false]);
  assert.equal(model.deviationView(4, passenger).mark?.text, '−2', 'Fler: a passenger train that left early is green −2');
  assert.equal(model.deviationView(4, passenger).mark?.tone, 'early');
  for (const type of ['goods', 'work']) {
    const train = live101(typed(type, left), '09:19');
    assert.equal(train.earlyMinutes, 2, `${type}: the time is known`);
    for (const level of [1, 2, 3, 4, 5]) assert.equal(model.deviationView(level, train).mark, null, `${type}: an early departure is allowed (level ${level})`);
  }
  assert.equal(model.deviationView(3, passenger).mark, null, 'Diskret shows no early trains');
});

test('an early arrival shows only in Allt, for every kind of train', () => {
  const done = { m0: { departure: 'departed', departed_seconds: at('09:20') }, m1: { arrival: 'arrived', arrived_seconds: at('09:32') } };
  for (const type of ['person', 'goods']) {
    const train = live101(typed(type, done), '09:33');
    assert.deepEqual([train.earlyMinutes, train.earlyKind], [3, 'arr']);
    assert.equal(model.deviationView(4, train).mark, null, `${type}: Fler shows no early arrival`);
    assert.equal(model.deviationView(5, train).mark?.text, '−3', `${type}: Allt does`);
  }
});

test('an estimated delay is never early', () => {
  const train = live101(snapshot(), '09:26');
  assert.deepEqual([train.earlyMinutes, train.earlyKind], [0, null]);
});

test('each level shows its own amount', () => {
  const late = (minutes, estimated = false) => ({ delayMinutes: minutes, estimated, earlyMinutes: 0 });
  const view = (level, train) => { const v = model.deviationView(level, train); return [v.flash, v.mark?.style ?? null, v.mark?.text ?? null, v.strike, v.estimated]; };
  assert.deepEqual(view(1, late(9)), [false, null, null, false, false], 'Ingen: nothing at all');
  assert.deepEqual(view(2, late(9)), [true, null, null, false, false], 'När det inträffar: only the flash');
  assert.deepEqual(view(3, late(4)), [true, null, null, false, false], 'Diskret: four minutes is nothing');
  assert.deepEqual(view(3, late(5)), [true, 'text', '+5', false, false], 'Diskret: small text from five');
  assert.deepEqual(view(4, late(2)), [true, null, null, false, false], 'Fler: two minutes is on time');
  assert.deepEqual(view(4, late(3, true)), [true, 'pill', '+3', true, true], 'Fler: the pill from three, estimated');
  assert.deepEqual(view(5, late(1)), [true, 'pill', '+1', true, false], 'Allt: from one minute');
});

test('the device choice goes before the meet default, which goes before level 2', () => {
  assert.equal(model.deviationLevel({}), 2);
  assert.equal(model.deviationLevel({ display: { deviation_level: 4 } }), 4);
  assert.equal(model.deviationLevel({ display: { deviation_level: 4 } }, '1'), 1);
  assert.equal(model.deviationLevel({ display: { deviation_level: 9 } }, ''), 2, 'an unknown level falls back');
});
