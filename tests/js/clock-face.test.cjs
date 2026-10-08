// Urtavlor: hur visarna går och hur en uppladdad klocka ritas (clock-face.js).
// Kör: node --test tests/js/clock-face.test.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const faces = require('../../src/tmbox_gateway/web/clock-face.js');

const at = (h, m, s) => h * 3600 + m * 60 + s;
const close = (actual, expected, message) => assert.ok(Math.abs(actual - expected) < 1e-9, `${message}: ${actual} ≠ ${expected}`);

test('the built-in faces glide: every hand moves continuously', () => {
  const angles = faces.handAngles(at(10, 7, 30));
  close(angles.hour, (10 + 7.5 / 60) * 30, 'hour');
  close(angles.minute, 7.5 * 6, 'minute');
  close(angles.second, 180, 'second');
  close(faces.handAngles(at(10, 7, 30.5)).second, 183, 'a smooth second hand moves between whole seconds');
});

test('a station clock: the minute hand jumps, the hour hand moves each minute, the second hand ticks', () => {
  const motion = { hour: 'minute', minute: 'jump', second: 'tick' };
  const angles = faces.handAngles(at(10, 7, 30.6), motion);
  close(angles.minute, 42, 'minute stays on 7 until the minute is full');
  close(angles.hour, (10 + 7 / 60) * 30, 'hour without the seconds');
  close(angles.second, 180, 'second on whole seconds');
  close(faces.handAngles(at(10, 7, 59.9), motion).minute, 42, 'still 7');
  close(faces.handAngles(at(10, 8, 0), motion).minute, 48, 'then 8');
});

test('a sweep second hand goes round in its time and waits at 12', () => {
  const motion = { second: 'sweep', sweep_seconds: 58.5 };
  close(faces.handAngles(at(9, 0, 29.25), motion).second, 180, 'half way at 29.25 s');
  close(faces.handAngles(at(9, 0, 58.5), motion).second, 360, 'at 12 after 58.5 s');
  close(faces.handAngles(at(9, 0, 59.7), motion).second, 360, 'and waits there');
  close(faces.handAngles(at(9, 1, 0), motion).second, 0, 'until the minute turns');
});

test('the minute bounce rings out after a jump and only while running', () => {
  const bounce = faces.bounceTracker();
  assert.equal(bounce(600, true, 0), 0, 'nothing on the first minute seen');
  assert.equal(bounce(601, true, 1000), 0, 'starts at the jump');
  assert.notEqual(bounce(601, true, 1010), 0, 'swings just after');
  assert.equal(bounce(601, true, 2500), 0, 'and has rung out');
  assert.equal(bounce(602, false, 3000), 0, 'a stopped clock does not bounce');
  close(faces.handAngles(at(10, 7, 0), { minute: 'jump' }, 1.5).minute, 43.5, 'the bounce is added to the jumped minute hand');
});

const face = {
  style: 'custom:mitt-ur', id: 'mitt-ur', name: 'Mitt "ur" <3', sha256: 'a'.repeat(64),
  motion: { minute: 'jump' },
  layers: {
    dial: '/v1/clock-faces/mitt-ur/aaaaaaaaaaaaaaaa/dial', hour: '/v1/clock-faces/mitt-ur/aaaaaaaaaaaaaaaa/hour',
    minute: '/v1/clock-faces/mitt-ur/aaaaaaaaaaaaaaaa/minute', second: '/v1/clock-faces/mitt-ur/aaaaaaaaaaaaaaaa/second',
  },
};

test('an uploaded clock is drawn as images, with the hands where app.js turns them', () => {
  const svg = faces.markup(face, { showSeconds: true });
  assert.match(svg, /^<svg class="clock-face clock-face--custom" viewBox="0 0 200 200"/);
  assert.equal((svg.match(/<image /g) || []).length, 4);
  for (const hand of ['hour', 'minute', 'second']) assert.match(svg, new RegExp(`data-clock-hand="${hand}" transform="rotate\\(0 100 100\\)"`));
  assert.match(svg, /aria-label="Mitt &quot;ur&quot; &lt;3"/, 'the name is escaped');
  assert.equal((faces.markup(face, { showSeconds: false }).match(/<image /g) || []).length, 3, 'no second hand when seconds are hidden');
  assert.match(faces.markup(face, { stopped: true }), /class="clock-face clock-face--custom stopped"/);
});

test('a screen in dark mode uses the dark variant where the pack has one', () => {
  const dark = { ...face, dark_layers: { dial: '/v1/clock-faces/mitt-ur/aaaaaaaaaaaaaaaa/dark-dial' } };
  const night = faces.markup(dark, { dark: true });
  assert.match(night, /href="\/v1\/clock-faces\/mitt-ur\/aaaaaaaaaaaaaaaa\/dark-dial"/);
  assert.match(night, /href="\/v1\/clock-faces\/mitt-ur\/aaaaaaaaaaaaaaaa\/hour"/, 'the rest from the light layers');
  assert.doesNotMatch(faces.markup(dark, { dark: false }), /dark-dial/, 'a light screen uses the light dial');
  assert.doesNotMatch(faces.markup(face, { dark: true }), /dark-/, 'a pack without a dark variant looks the same in both');
  assert.equal(faces.layerFor(dark, 'dial', true), '/v1/clock-faces/mitt-ur/aaaaaaaaaaaaaaaa/dark-dial');
});

test('only the server’s own layer addresses are ever used', () => {
  const hostile = { ...face, layers: { ...face.layers, dial: 'javascript:alert(1)', hour: 'https://example.com/x.svg', minute: '/v1/clock-faces/x/aaaaaaaaaaaaaaaa/minute" onload="x' } };
  const svg = faces.markup(hostile);
  assert.doesNotMatch(svg, /javascript|example\.com|onload/);
  assert.equal((svg.match(/<image /g) || []).length, 1, 'only the valid second hand is left');
});

test('the approval dialog shows a clock before it is uploaded, from data addresses only', () => {
  const image = 'data:image/svg+xml;base64,PHN2Zy8+';
  const pack = { id: 'ny', name: 'Ny', motion: { minute: 'jump' }, layers: { dial: image, hour: image, minute: image, second: 'data:image/png;base64,iVBORw0K' },
    dark_layers: { dial: 'data:image/png;base64,QUJD' } };
  const svg = faces.markup(pack, { preview: true, dark: false, at: at(10, 8, 36) });
  assert.equal((svg.match(/<image /g) || []).length, 4);
  assert.match(svg, /data-clock-hand="minute" transform="rotate\(48 100 100\)"/, 'the hands already at 10:08:36, the minute hand jumping');
  assert.match(svg, /data-clock-hand="second" transform="rotate\(216 100 100\)"/);
  assert.match(faces.markup(pack, { preview: true, dark: true }), /href="data:image\/png;base64,QUJD"/, 'the dark dial on a dark page');
  assert.equal(faces.markup(pack, { dark: false }).includes('data:'), false, 'a data address is never used outside the preview');
  const hostile = { ...pack, layers: { dial: 'data:text/html;base64,PHNjcmlwdD4=', hour: 'data:image/svg+xml;base64,AA" onload="x', minute: '/v1/clock-faces/x/aaaaaaaaaaaaaaaa/minute' } };
  assert.doesNotMatch(faces.markup(hostile, { preview: true, dark: false }), /<image /, 'only image data, and no server address in the preview');
});

test('the faces remembered from the server are found by their style', () => {
  faces.remember([face]);
  assert.equal(faces.find('custom:mitt-ur').name, face.name);
  assert.equal(faces.find('stationsur'), null);
  assert.equal(faces.isCustom('custom:mitt-ur'), true);
  assert.equal(faces.isCustom('stationsur'), false);
  faces.remember(undefined);
  assert.ok(faces.find('custom:mitt-ur'), 'a snapshot without faces keeps what is known');
  faces.remember([]);
  assert.equal(faces.find('custom:mitt-ur'), null, 'an empty list forgets');
});
