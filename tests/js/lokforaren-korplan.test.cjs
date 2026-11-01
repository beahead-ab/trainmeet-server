// Lokföraren: körplanens tidsaxel och NU-linjen (#226).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const K = require('../../src/tmbox_gateway/web/lokforaren/korplan.js');

const demo = {
  trains: [{number: 'G 5453', from: 'Spiten', to: 'Kjøllstad', vmax: 80}, {number: 'G 5456', from: 'Kjøllstad', to: 'Spiten', vmax: 80}],
  events: [
    {kind: 'duty_start', time: '17:10', station: 'Spiten', notes: ['Förberedelse']},
    {kind: 'departure', time: '17:30', station: 'Spiten', train: 'G 5453'},
    {kind: 'stop', arrival: '17:58', departure: '18:14', station: 'Tveiten', train: 'G 5453', notes: ['Rundgång', 'Skjut vagnar']},
    {kind: 'arrival', time: '18:25', station: 'Kjøllstad', train: 'G 5453'},
    {kind: 'departure', time: '19:35', station: 'Kjøllstad', train: 'G 5456'},
    {kind: 'stop', arrival: '20:02', departure: '20:14', station: 'Tveiten', train: 'G 5456', notes: ['Rundgång']},
    {kind: 'arrival', time: '20:42', station: 'Spiten', train: 'G 5456'},
    {kind: 'duty_end', time: '21:02', station: 'Spiten'},
  ],
};

test('the axis rises strictly, so the NOW line never jumps or stands still', () => {
  const plan = K.build(demo);
  let previous = -Infinity;
  for (let minute = plan.start - 20; minute <= plan.end + 30; minute += 0.25) {
    const y = plan.y(minute);
    assert.ok(y > previous, `y(${minute}) = ${y} after ${previous}`);
    previous = y;
  }
});

test('one meet minute is 7 px and a long layover is pressed to 160 px', () => {
  const plan = K.build(demo);
  const at = (text) => plan.y(K.minutes(text));
  assert.equal(Math.round(at('17:58') - at('17:30')), 28 * 7);
  assert.equal(Math.round(at('19:35') - at('18:25')), 160);
  assert.deepEqual(plan.gaps.map((gap) => [gap.station, gap.minutes]), [['Kjøllstad', 70]]);
  assert.deepEqual(plan.trains.map((train) => train.number), ['G 5453', 'G 5456']);
  assert.deepEqual(plan.segments.map((segment) => segment.kind), ['idle', 'run', 'dwell', 'run', 'idle', 'run', 'dwell', 'run', 'idle']);
});

test('the line moves four times as far at 4× as at 1× in the same real time', () => {
  const plan = K.build(demo);
  const start = 17 * 3600 + 40 * 60;
  const moved = (factor) => plan.y(K.axisMinutes(start + 10 * factor, plan)) - plan.y(K.axisMinutes(start, plan));
  assert.ok(Math.abs(moved(4) - 4 * moved(1)) < 1e-9);
  assert.ok(moved(1) > 0);
});

test('a duty over midnight keeps one running axis', () => {
  const plan = K.build({events: [
    {kind: 'departure', time: '23:40', station: 'A', train: 'T 1'},
    {kind: 'arrival', time: '00:20', station: 'B', train: 'T 1'},
  ]});
  assert.equal(plan.rows[1].t, 24 * 60 + 20);
  assert.equal(K.axisMinutes(10 * 60, plan), 24 * 60 + 10); // 00:10 on the clock is after 23:40
  assert.equal(K.label(24 * 60 + 20), '00:20');
});

test('next moment and the station to suggest follow the clock', () => {
  const plan = K.build(demo);
  const now = K.minutes('17:53');
  assert.equal(K.next(plan, now).row.station, 'Tveiten');
  assert.equal(K.nearestStation(plan, now).station, 'Tveiten');
  assert.equal(K.next(plan, K.minutes('18:00')).kind, 'departure');
  assert.equal(K.next(plan, K.minutes('21:30')), null);
});

test('a check-in is full for ten real minutes and gone after fifteen', () => {
  assert.deepEqual(K.fade(599), {opacity: 1, gray: 0});
  const halfway = K.fade(750);
  assert.ok(halfway.opacity > 0.4 && halfway.opacity < 0.6);
  assert.equal(K.fade(900).opacity, 0);
});

test('every message of the driver app exists in English, Danish, Norwegian and German', () => {
  require('../../src/tmbox_gateway/web/lokforaren/messages.js');
  const missing = Object.entries(globalThis.LokforarenMessages)
    .filter(([, row]) => ['en', 'da', 'nb', 'de'].some((language) => !row[language]))
    .map(([source]) => source);
  assert.deepEqual(missing, []);
  // Anvisningarna ur planeringsfilen skrivs med de här mallarna i app.js.
  const fs = require('node:fs');
  const app = fs.readFileSync(require.resolve('../../src/tmbox_gateway/web/lokforaren/app.js'), 'utf8');
  const block = app.slice(app.indexOf('const MEET_TEXT'), app.indexOf('function instructionLines'));
  const templates = [...block.matchAll(/"([^"]*[a-zåäö][^"]*)"/g)].map((match) => match[1]).filter((text) => /\s/.test(text));
  assert.ok(templates.length >= 25, `found ${templates.length} templates`);
  assert.deepEqual(templates.filter((text) => !globalThis.LokforarenMessages[text]), []);
});

test('instruction lines give the run plan room', () => {
  const plan = K.build({events: [
    {kind: 'departure', time: '10:00', station: 'A', train: 'G 1'},
    {kind: 'stop', arrival: '10:02', departure: '10:03', station: 'B', train: 'G 1', instructions: [
      {kind: 'crosses', meets: [{train: 'RE 1', from: '10:02', to: '10:03'}, {train: 'RE 3', from: '10:02', to: '10:03'}]},
      {kind: 'couple', vehicles: ['CN 02 Di4'], days: [1, 3, 5]}]},
    {kind: 'arrival', time: '10:04', station: 'C', train: 'G 1'},
  ]});
  assert.equal(K.noteLines(plan.rows[1]), 4);
  // Mellan B:s ankomst och avgång står fyra rader anvisningar: mer än en minut.
  assert.ok(plan.y(10 * 60 + 3) - plan.y(10 * 60 + 2) >= 44 + 8 + 4 * 18);
});
