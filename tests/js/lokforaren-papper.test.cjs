// Rätta en tjänst efter pappret (#225): förminskningen och jämförelsen.
const test = require('node:test');
const assert = require('node:assert/strict');
const P = require('../../src/tmbox_gateway/web/lokforaren/papper.js');

// Tjänst 24 på servern (planeringsfilen 31 oktober) och på pappret (26 oktober).
const SERVER_24 = {
  number: '24', start: '05:00', end: '08:21', operator: 'Green Cargo AB', vehicle: 'GC 05 T44',
  trains: [{number: 'Kp 8230'}, {number: 'Kp 8231'}],
  events: [
    {kind: 'duty_start', time: '05:00', station: 'Steinsnes'},
    {kind: 'departure', time: '05:10', station: 'Steinsnes', track: '1', train: 'Kp 8230'},
    {kind: 'arrival', time: '05:22', station: 'Kjøllstad', track: '1', train: 'Kp 8230'},
    {kind: 'duty_end', time: '08:21', station: 'Steinsnes'},
  ],
};
const PAPER_24 = {
  number: '24', start: '03:01', end: '05:36', operator: 'Green Cargo fjärr', vehicle: 'GC 07 185 Br',
  trains: [{number: 'G 4007'}],
  events: [
    {kind: 'duty_start', time: '03:01', station: 'Spiten'},
    {kind: 'departure', time: '03:21', station: 'Spiten', track: '6', train: 'G 4007'},
    {kind: 'stop', arrival: '03:45', departure: '03:47', station: 'Nättelund', track: '1', train: 'G 4007'},
    {kind: 'arrival', time: '05:06', station: 'Skovborg', track: '4', train: 'G 4007'},
    {kind: 'duty_end', time: '05:36', station: 'Skovborg'},
  ],
};

test('a photo is made smaller in its own proportions, never larger', () => {
  assert.deepEqual(P.fit(4032, 3024), {width: 2000, height: 1500});
  assert.deepEqual(P.fit(3024, 4032), {width: 1500, height: 2000});
  assert.deepEqual(P.fit(1200, 900), {width: 1200, height: 900});
  assert.ok(P.MAX_BYTES < 2 * 1024 * 1024, 'under serverns gräns per sida');
});

test('two versions of duty 24: every fact and every row differs', () => {
  const result = P.compare(SERVER_24, PAPER_24);
  assert.deepEqual(result.facts.map((item) => [item.key, item.now, item.paper, item.same]), [
    ['time', '05:00–08:21', '03:01–05:36', false],
    ['trains', 'Kp 8230 · Kp 8231', 'G 4007', false],
    ['vehicle', 'Green Cargo AB · GC 05 T44', 'Green Cargo fjärr · GC 07 185 Br', false],
  ]);
  assert.deepEqual(result.rows.map((row) => [row.time, row.station, row.change]), [
    ['03:01', 'Spiten', 'added'],
    ['03:21', 'Spiten', 'added'],
    ['03:45–03:47', 'Nättelund', 'added'],
    ['05:06', 'Skovborg', 'added'],
    ['05:36', 'Skovborg', 'added'],
  ]);
  assert.equal(result.gone.length, 4);
  assert.equal(result.changes, 12);
});

test('the same duty with one later departure and another track', () => {
  const later = structuredClone(SERVER_24);
  later.events[1].time = '05:12';
  later.events[2].track = '2';
  const result = P.compare(SERVER_24, later);
  assert.deepEqual(result.rows.map((row) => row.change), ['same', 'changed', 'changed', 'same']);
  assert.deepEqual(result.rows[1].was, {time: '05:10', track: '1'});
  assert.deepEqual(result.gone, []);
  assert.equal(result.facts.every((item) => item.same), true);
  assert.equal(result.changes, 2);
});

test('a duty that is the same on paper has no changes', () => {
  assert.equal(P.compare(SERVER_24, structuredClone(SERVER_24)).changes, 0);
});
