// Deltagarvyn på en dator: från 1100 px två kolumner, klockan och kartan till
// vänster, händelser, tidtabell och knappar till höger. Kartan ryms i sin ruta
// utan att dras i sidled, även efter att fönstret ändrat storlek. På en telefon
// är vyn en kolumn som förut. API:et är fixturen i kr-fixture.cjs.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const measure = (page) => page.evaluate(() => {
  const svg = document.querySelector('#pv-topology');
  const host = svg.closest('.pv-map');
  const column = (selector) => document.querySelector(selector).closest('.pv-col')?.classList.contains('pv-col--main') ? 'main' : 'side';
  return { svg: Math.round(svg.getBoundingClientRect().width), host: host.clientWidth,
    hint: !document.querySelector('#pv-map-hint').hidden,
    overflow: document.documentElement.scrollWidth > innerWidth + 1,
    columns: getComputedStyle(document.querySelector('.pv-page')).gridTemplateColumns.split(' ').filter((c) => c !== 'none').length,
    clock: column('#pv-clock'), map: column('#pv-topology'), events: column('#pv-events'), timetable: column('#pv-timetable') };
});

const settle = async (page) => {
  await page.locator('#participant-view').waitFor({ state: 'visible' });
  await page.waitForFunction(() => document.querySelector('#pv-topology')?.getAttribute('viewBox'));
  await page.waitForTimeout(400);
};

(async () => {
  for (const theme of ['dark', 'light']) {
    const h = await open({ route: '/', width: 1440, height: 900, theme, loggedOut: true });
    const { page } = h;
    page.setDefaultTimeout(10000);
    try {
      await settle(page);
      const wide = await measure(page);
      assert.equal(wide.columns, 2, `${theme}: two columns on a desktop`);
      assert.deepEqual([wide.clock, wide.map, wide.events, wide.timetable], ['main', 'main', 'side', 'side'], 'clock and map left, the rest right');
      assert.ok(wide.svg <= wide.host, `${theme}: the map fits its panel (${wide.svg} > ${wide.host})`);
      assert.equal(wide.hint, false, `${theme}: no "drag sideways" hint when the map fits`);
      assert.equal(wide.overflow, false, `${theme}: no sideways scrolling`);
      await page.setViewportSize({ width: 1150, height: 900 });
      await page.waitForTimeout(600);
      const narrower = await measure(page);
      assert.ok(narrower.svg <= narrower.host, `the map is redrawn to fit after a resize (${narrower.svg} > ${narrower.host})`);
      assert.equal(narrower.overflow, false, 'no sideways scrolling after a resize');
      assert.deepEqual(h.errors, []);
    } finally { await h.browser.close(); }
  }
  const h = await open({ route: '/', width: 390, height: 844, theme: 'dark', loggedOut: true });
  try {
    await settle(h.page);
    const phone = await measure(h.page);
    assert.equal(phone.columns, 0, 'one column on a phone');
    assert.ok(phone.svg > phone.host && phone.hint, 'on a phone the map keeps its size and is dragged sideways');
    assert.equal(phone.overflow, false, 'the page itself does not scroll sideways on a phone');
    assert.deepEqual(h.errors, []);
  } finally { await h.browser.close(); }
  console.log('participant-desktop: ok');
})().catch((error) => { console.error(error); process.exit(1); });
