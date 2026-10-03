// Webbens knappsats ska bete sig som boxens (firmware/common/server_terminal.h
// i trainmeet-tmbox): samma spärregel, samma väntetext, samma tider.
const test = require('node:test');
const assert = require('node:assert/strict');
const {screenChanged, guarded, overlay, commandId, times} = require('../../src/tmbox_gateway/terminal16_web/terminal.js');

const frame = {view_token: 'a', lines: ['                ', 'Nr# A:Kö   12:34'], keys: {
  '#': {label: 'Begär klartecken', acts: true}, 'C': {label: 'Föregående tåg', acts: false},
  'D': {label: 'Nästa tåg', acts: false}, 'A': {label: 'Förfrågningskö (0 väntar)', acts: false},
}};

test('a new view or new key meanings is a screen change; the clock ticking is not', () => {
  assert.equal(screenChanged(null, frame), true);
  assert.equal(screenChanged(frame, {...frame}), false);
  assert.equal(screenChanged(frame, {...frame, lines: ['                ', 'Nr# A:Kö   12:35']}), false);
  assert.equal(screenChanged(frame, {...frame, view_token: 'b'}), true, 'C to the next train keeps the labels');
  assert.equal(screenChanged(frame, {...frame, keys: {...frame.keys, '#': {label: 'Ge klart', acts: true}}}), true);
  assert.equal(screenChanged(frame, {...frame, keys: {...frame.keys, 'A': {label: 'Förfrågningskö (1 väntar)', acts: false}}}), true);
});

test('after a screen change only keys that act wait; browsing answers at once', () => {
  const until = 1500, now = 1000;
  assert.equal(guarded(frame, '#', '', now, until), true);
  for (const key of ['C', 'D', 'A']) assert.equal(guarded(frame, key, '', now, until), false, key);
  assert.equal(guarded(frame, '#', '', until, until), false, 'the guard ends');
});

test('digits and train search are never guarded', () => {
  for (const key of '0123456789') assert.equal(guarded(frame, key, '', 1000, 1500), false, key);
  assert.equal(guarded(frame, '#', '39', 1000, 1500), false, '# with digits searches, it does not act');
});

test('a key without the flag counts as acting, so an older server is never less careful', () => {
  const old = {...frame, keys: {'#': {label: 'Begär klartecken'}, 'C': {label: 'Föregående tåg'}}};
  assert.equal(guarded(old, '#', '', 1000, 1500), true);
  assert.equal(guarded(old, 'C', '', 1000, 1500), true);
});

test('waiting is shown on the display after 1.5 s, as on the box', () => {
  const model = {busy: true, sentAt: 0, unansweredAt: 0};
  assert.equal(overlay(model, 1499), '');
  assert.equal(overlay(model, 1500), 'VANTAR PA SVAR');
  model.busy = false; model.unansweredAt = 30000;
  assert.equal(overlay(model, 30000), 'INGET SVAR');
  assert.equal(overlay(model, 32999), 'INGET SVAR');
  assert.equal(overlay(model, 33000), '');
  for (const text of ['VANTAR PA SVAR', 'INGET SVAR']) assert.ok(text.length <= 16);
});

test('the same times as the box', () => {
  assert.deepEqual(times, {WAITING_SHOWN_MS: 1500, COMMAND_GIVE_UP_MS: 30000, UNANSWERED_SHOWN_MS: 3000,
    SILENCE_MS: 15000, POLL_MS: 500});
});

test('a key reaches the server on plain LAN HTTP, where randomUUID is missing', () => {
  // Benny's box at http://trainmeet.local: A, # and * did nothing (2026-10-03).
  const lan = {getRandomValues: bytes => require('node:crypto').webcrypto.getRandomValues(bytes)};
  const ids = new Set(Array.from({length: 50}, () => commandId(lan)));
  assert.equal(ids.size, 50, 'every press its own id');
  for (const id of ids) assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(commandId({randomUUID: () => 'from-the-browser'}), 'from-the-browser', 'HTTPS and localhost keep their own');
});
