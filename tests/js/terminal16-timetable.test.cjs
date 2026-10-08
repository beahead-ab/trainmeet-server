// Webb-TMBoxens tidtabellsrad: vad en nivå visar för serverns försening.
// Samma deviationView som serverns övriga vyer (drift-model.js).
// Kör: node --test tests/js/terminal16-timetable.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const drift = require('../../src/tmbox_gateway/web/drift-model.js');
const { timetableRow, clockShift } = require('../../src/tmbox_gateway/terminal16_web/terminal.js');

const row = (extra = {}) => ({ movement_id: 'm1', train_number: '101', kind: 'arrival', time: '09:35', state: 'planned',
  delay_minutes: 0, expected_time: null, estimated: false, early_minutes: 0, early_kind: null, train_type: 'person', ...extra });

test('a late row shows the new time from the level that strikes', () => {
  const late = row({ delay_minutes: 7, expected_time: '09:42' });
  assert.equal(timetableRow(late, 2, drift).shown, null, 'level 2: no new time');
  assert.equal(timetableRow(late, 3, drift).view.mark.style, 'text');
  assert.equal(timetableRow(late, 3, drift).shown, null);
  const four = timetableRow(late, 4, drift);
  assert.deepEqual([four.shown, four.view.mark.text, four.early], ['09:42', '+7', false]);
  assert.equal(timetableRow(row({ delay_minutes: 2, expected_time: '09:37' }), 4, drift).shown, null, 'two minutes is on time at level 4');
  assert.equal(timetableRow(row({ delay_minutes: 2, expected_time: '09:37' }), 5, drift).shown, '09:37');
});

test('an early departure shows for passenger trains only, and an early arrival only at level 5', () => {
  const early = { early_minutes: 2, early_kind: 'dep' };
  const passenger = timetableRow(row(early), 4, drift);
  assert.deepEqual([passenger.view.mark.text, passenger.shown, passenger.early], ['−2', '09:33', true]);
  for (const train_type of ['goods', 'work']) assert.equal(timetableRow(row({ ...early, train_type }), 5, drift).view.mark, null, `${train_type} may leave early`);
  const arrival = row({ early_minutes: 3, early_kind: 'arr', train_type: 'goods' });
  assert.equal(timetableRow(arrival, 4, drift).view.mark, null);
  assert.equal(timetableRow(arrival, 5, drift).view.mark.text, '−3');
});

test('a row that is done is marked, and the signature changes with what is shown', () => {
  assert.equal(timetableRow(row({ state: 'arrived' }), 2, drift).done, true);
  assert.equal(timetableRow(row({ kind: 'departure', state: 'departed' }), 2, drift).done, true);
  assert.equal(timetableRow(row({ kind: 'arrival', state: 'departed' }), 2, drift).done, false, 'departed towards us is not here yet');
  const quiet = timetableRow(row({ delay_minutes: 6 }), 2, drift).signature;
  assert.deepEqual(quiet, timetableRow(row({ delay_minutes: 7 }), 2, drift).signature, 'level 2: a growing delay is not news');
  assert.notDeepEqual(timetableRow(row({ delay_minutes: 6 }), 4, drift).signature, timetableRow(row({ delay_minutes: 7 }), 4, drift).signature);
});

test('clockShift wraps around midnight', () => {
  assert.equal(clockShift('23:58', 5), '00:03');
  assert.equal(clockShift('00:01', -2), '23:59');
  assert.equal(clockShift('--:--', 3), '--:--');
});
