const test = require('node:test');
const assert = require('node:assert/strict');
const fx = require('../../src/tmbox_gateway/web/tmbox-fixtures.js');
const { render } = require('../../src/tmbox_gateway/web/tmbox-render.js');
require('../../src/tmbox_gateway/web/tmbox-guide.js');

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
  assert.equal(globalThis.TMBoxGuide.length, 13);
  assert.ok(globalThis.TMBoxGuide.every(flow => flow.title && flow.status && flow.steps.length >= 2));
  assert.ok(globalThis.TMBoxGuide.some(flow => flow.status.includes('Saknas')));
  assert.equal(globalThis.TMBoxLegacyDeviceScreens.length, 18);
});
