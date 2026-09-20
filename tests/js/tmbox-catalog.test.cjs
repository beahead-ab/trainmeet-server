const test = require('node:test');
const assert = require('node:assert/strict');
const fx = require('../../src/tmbox_gateway/web/tmbox-fixtures.js');
const { render } = require('../../src/tmbox_gateway/web/tmbox-render.js');
require('../../src/tmbox_gateway/web/tmbox-guide.js');

test('every screen keeps its geometry in all five server-supplied languages', () => {
  const { execFileSync } = require('node:child_process');
  const path = require('node:path');
  const root = path.resolve(__dirname, '../..');
  const packs = JSON.parse(execFileSync('python3', ['-c',
    'import json; from tmbox_gateway.device_ui import LANGUAGES, ui_payload; print(json.dumps([ui_payload(c) for c,n in LANGUAGES]))'],
    { cwd: root, env: {...process.env, PYTHONPATH: path.join(root, 'src')}, encoding: 'utf8' }));
  assert.equal(packs.length, 5);
  for (const ui of packs) {
    assert.equal(typeof ui.messages['TAG'], 'string');
    assert.deepEqual(Object.keys(ui.languages[0]).sort(), ['code', 'name']);
    const config = {...fx.config, code: 'TAG', ui};
    for (const [, geometry] of fx.GEOMETRIES) {
      for (const [name, screen, movement] of [...fx.CASES, ...fx.EXTRA_CASES]) {
        const frame = render(geometry, fx.viewFor(screen, movement), config, fx.snapshot);
        assert.equal(frame.length, geometry.rows, ui.language + name);
        assert.ok(frame.every(line => line.length === geometry.cols), ui.language + name);
      }
      const idle = render(geometry, fx.viewFor('StationOverview', -1), config, fx.snapshot);
      assert.ok(idle[0].startsWith('TAG'), 'station code is data, never translated');
      assert.ok(idle[1].includes('D='), 'language key discoverable');
    }
  }
});

test('catalog covers all 19 screen types and all four display formats', () => {
  const cases = [...fx.CASES, ...fx.EXTRA_CASES];
  assert.equal(cases.length, 20);
  assert.equal(new Set(cases.map(c => c[1])).size, 19);
  for (const [, geometry] of fx.GEOMETRIES) {
    for (const [name, screen, movement] of cases) {
      const frame = render(geometry, fx.viewFor(screen, movement), fx.config, fx.snapshot);
      assert.equal(frame.length, geometry.rows, name);
      assert.ok(frame.every(line => line.length === geometry.cols), name);
    }
  }
});

test('functional guide distinguishes implemented steps from known gaps', () => {
  assert.equal(globalThis.TMBoxGuide.length, 14);
  assert.ok(globalThis.TMBoxGuide.every(flow => flow.title && flow.status && flow.steps.length >= 2));
  assert.ok(globalThis.TMBoxGuide.some(flow => flow.status.includes('Saknas')));
  assert.equal(globalThis.TMBoxLegacyDeviceScreens.length, 20);
});
