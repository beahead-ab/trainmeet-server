// Deltagarvyn på en dator: från 1100 px två kolumner, klockan och kartan till
// vänster, händelser, tidtabell och knappar till höger. Kartan ryms i sin ruta
// utan att dras i sidled, även efter att fönstret ändrat storlek. På en telefon
// är vyn en kolumn, och kartan är samma som i Drift: stående, med varje
// stations kod och antal tåg, och inget att dra i sidled. API:et är fixturen i
// kr-fixture.cjs.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const measure = (page) => page.evaluate(() => {
  const svg = document.querySelector('#pv-topology');
  const host = svg.closest('.pv-map');
  const column = (selector) => document.querySelector(selector).closest('.pv-col')?.classList.contains('pv-col--main') ? 'main' : 'side';
  const [, , boxWidth, boxHeight] = svg.getAttribute('viewBox').split(' ').map(Number);
  return { svg: Math.round(svg.getBoundingClientRect().width), host: host.clientWidth, upright: boxHeight > boxWidth * 1.5,
    stations: svg.querySelectorAll('.topology-station').length, codes: [...svg.querySelectorAll('.topology-code')].map((code) => code.textContent),
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
    assert.ok(phone.upright, 'on a phone held upright the line stands upright, as in Drift');
    assert.ok(phone.svg <= phone.host, `the map fits its panel (${phone.svg} > ${phone.host})`);
    assert.equal(phone.hint, false, 'nothing to drag sideways, so no hint');
    assert.equal(phone.codes.length, phone.stations, 'every station shows its code and number of trains, as in Drift');
    assert.ok(phone.codes.every((code) => /^\S+ · \d+$/.test(code)), `codes read "CST · 0" (${phone.codes.slice(0, 2)})`);
    assert.equal(phone.overflow, false, 'the page itself does not scroll sideways on a phone');
    assert.deepEqual(h.errors, []);
  } finally { await h.browser.close(); }
  // Samma karta som inloggad i Drift: samma sorts linje och samma rader under varje station.
  const drift = await open({ route: '/drift', width: 390, height: 844, theme: 'dark' });
  try {
    await drift.page.waitForFunction(() => document.querySelector('#overview-topology')?.getAttribute('viewBox'));
    await drift.page.waitForTimeout(400);
    const logged = await drift.page.evaluate(() => {
      const svg = document.querySelector('#overview-topology');
      const [, , w, hgt] = svg.getAttribute('viewBox').split(' ').map(Number);
      return { upright: hgt > w * 1.5, codes: [...svg.querySelectorAll('.topology-code')].map((code) => code.textContent).sort() };
    });
    const guest = await open({ route: '/', width: 390, height: 844, theme: 'dark', loggedOut: true });
    try {
      await settle(guest.page);
      const out = await measure(guest.page);
      assert.deepEqual([out.upright, [...out.codes].sort()], [logged.upright, logged.codes], 'logged out shows the same map as logged in');
    } finally { await guest.browser.close(); }
  } finally { await drift.browser.close(); }
  console.log('participant-desktop: ok');
})().catch((error) => { console.error(error); process.exit(1); });
