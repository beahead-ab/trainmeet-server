// Sidhuvudet på smal skärm: två rader, inte fyra eller fem. Rad 1 är loggan,
// träffen och knapparna, rad 2 är klockan, kontrolluppgifterna och sökfältet.
// Inget sticker ut i sidled, och menyn "Öppna på skärm" ryms på skärmen. På en
// dator är sidhuvudet en rad som förut. API:et är fixturen i kr-fixture.cjs.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const ITEMS = ['#workspace-home', '#app-chrome .tm-switch', '#header-cloud-status', '#header-findings', '#app-chrome .kr-search',
  '#app-clock', '#app-chrome .screen-menu', '#header-settings', '#header-help', '#kr-theme-toggle', '#logout'];

const measure = (page) => page.evaluate((items) => {
  const boxes = {};
  for (const selector of items) {
    const element = document.querySelector(selector);
    if (!element) continue;
    const box = element.getBoundingClientRect();
    if (box.width && box.height && getComputedStyle(element).visibility !== 'hidden') {
      boxes[selector] = { top: box.top, bottom: box.bottom, left: box.left, right: box.right, middle: (box.top + box.bottom) / 2 };
    }
  }
  const rows = [];
  for (const box of Object.values(boxes).sort((a, b) => a.middle - b.middle)) {
    if (!rows.length || box.middle - rows[rows.length - 1] > 12) rows.push(box.middle);
  }
  const name = document.querySelector('#app-meet-name');
  return { boxes, rows: rows.length, nameCut: name.scrollWidth > name.clientWidth + 1, height: Math.round(document.querySelector('#app-chrome').getBoundingClientRect().height),
    width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth + 1 };
}, ITEMS);

const sameRow = (a, b) => Math.abs(a.middle - b.middle) <= 12;

(async () => {
  for (const [width, route, theme] of [[390, '/drift', 'dark'], [390, '/installningar', 'dark'], [390, '/drift', 'light'],
                                       [700, '/drift', 'dark'], [700, '/installningar', 'light']]) {
    const h = await open({ route, width, height: 800, theme });
    const { page } = h;
    page.setDefaultTimeout(10000);
    const where = `${width} ${route} ${theme}`;
    try {
      await page.locator('#app-chrome #logout').waitFor({ state: 'visible' });
      await page.waitForTimeout(600);
      const m = await measure(page);
      assert.equal(m.rows, 2, `${where}: two rows in the header, not ${m.rows}`);
      assert.ok(m.height <= 100, `${where}: the header is at most 100 px high (${m.height})`);
      assert.equal(m.overflow, false, `${where}: no sideways scrolling`);
      assert.equal(m.nameCut, false, `${where}: the meet name is shown in full`);
      for (const [selector, box] of Object.entries(m.boxes)) {
        assert.ok(box.left >= -1 && box.right <= m.width + 1, `${where}: ${selector} stays on screen`);
      }
      const b = m.boxes;
      assert.ok(sameRow(b['#workspace-home'], b['#logout']) && sameRow(b['#workspace-home'], b['#app-chrome .screen-menu']),
        `${where}: logo, screens menu and log out share the first row`);
      assert.ok(b['#app-chrome .kr-search'].top > b['#logout'].bottom, `${where}: the search is on the second row`);
      if (b['#header-findings']) assert.ok(sameRow(b['#header-findings'], b['#app-chrome .kr-search']) && b['#header-findings'].right <= b['#app-chrome .kr-search'].left,
        `${where}: the findings sit before the search`);
      if (b['#app-clock']) assert.ok(sameRow(b['#app-clock'], b['#app-chrome .kr-search']) && b['#app-clock'].right <= b['#app-chrome .kr-search'].left,
        `${where}: the clock sits first on the second row`);
      const summary = page.locator('#app-chrome .screen-menu summary');
      assert.match(await summary.innerText(), /Öppna på skärm/, `${where}: the screens button keeps its name`);
      await summary.click();
      const menu = await page.locator('#app-chrome .screen-menu nav').boundingBox();
      assert.ok(menu.x >= 0 && menu.x + menu.width <= width, `${where}: the screens menu opens inside the screen`);
      assert.deepEqual(h.errors, []);
    } finally { await h.browser.close(); }
  }
  // Mellan 900 och 1200 px trängdes träffens namn förut ihop till 16 px under
  // versionsmärket. Nu ger sökfältet efter först.
  for (const [width, route] of [[1280, '/drift'], [1280, '/installningar'], [1024, '/drift'], [1024, '/installningar']]) {
    const h = await open({ route, width, height: 800, theme: 'dark' });
    try {
      await h.page.locator('#app-chrome #logout').waitFor({ state: 'visible' });
      await h.page.waitForTimeout(600);
      const m = await measure(h.page);
      assert.deepEqual([m.rows, m.height], [1, 56], `${width} ${route}: one row of 56 px as before`);
      assert.equal(m.nameCut, false, `${width} ${route}: the meet name is shown in full`);
      const switchBox = m.boxes['#app-chrome .tm-switch'];
      for (const [selector, box] of Object.entries(m.boxes)) {
        if (selector === '#app-chrome .tm-switch' || selector === '#workspace-home') continue;
        assert.ok(box.left >= switchBox.right - 1 || box.right <= switchBox.left + 1, `${width} ${route}: ${selector} does not cover the meet name`);
      }
    } finally { await h.browser.close(); }
  }
  const out = await open({ route: '/login', width: 390, height: 800, theme: 'dark', loggedOut: true });
  try {
    await out.page.locator('#login').waitFor({ state: 'visible' });
    const shown = await out.page.evaluate(() => [document.querySelector('#app-chrome .tm-logo__lines'), document.querySelector('#app-chrome .kr-search')]
      .map((element) => element.getBoundingClientRect().width > 0));
    assert.deepEqual(shown, [true, true], 'logged out on a phone: the name TrainMeet SERVER and the search stay');
  } finally { await out.browser.close(); }
  console.log('header-mobile: ok');
})().catch((error) => { console.error(error); process.exit(1); });
