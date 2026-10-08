// Toasten vid dygnsskiftet: vad vyn ska säga ur /v1/display (day-change.js).
// Kör: node --test tests/js/day-change.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const { notice, SHOWN_SECONDS } = require('../../src/tmbox_gateway/web/day-change.js');

const at = '2026-10-09T03:00:00Z';
const snapshot = (calendar, serverTime = '2026-10-09T03:00:20Z') => ({ calendar, server_time: serverTime });
const changed = { day_number: 2, weekday: 'Lör', waiting: false, last_change: { kind: 'day_change', day_number: 2, weekday: 'Lör', at } };

test('a day change just now is told once', () => {
  assert.deepEqual(notice(snapshot(changed)), { kind: 'changed', key: at, dayNumber: 2, weekday: 'Lör' });
  assert.equal(notice(snapshot(changed), at), null, 'already shown on this page');
});

test('a page opened long after the change says nothing', () => {
  assert.equal(SHOWN_SECONDS, 90);
  assert.ok(notice(snapshot(changed, '2026-10-09T03:01:29Z')));
  assert.equal(notice(snapshot(changed, '2026-10-09T03:01:31Z')), null);
});

test('only the day change itself is told, and waiting is told while it lasts', () => {
  assert.equal(notice(snapshot({ ...changed, last_change: { ...changed.last_change, kind: 'time_machine' } })), null);
  assert.equal(notice(snapshot({ ...changed, last_change: null })), null);
  assert.deepEqual(notice(snapshot({ ...changed, last_change: null, waiting: true })), { kind: 'waiting', key: 'waiting' });
  assert.equal(notice({ calendar: null }), null);
  assert.equal(notice(snapshot({ ...changed, last_change: { ...changed.last_change, at: 'när som helst' } })), null);
});
