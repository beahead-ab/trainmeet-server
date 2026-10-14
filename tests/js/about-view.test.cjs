// Om trafikspelet (Casper 2026-10-14): berättelsen, instruktionerna och
// programmet ur Cloud, och stationernas instruktioner. Deltagarvyn har ett kort
// som leder in i bladet, boken i sidhuvudet (och Öppna-menyn på telefonen)
// öppnar det var man än är, och stationspanelen på Drift visar stationens egna
// instruktioner. Texterna ritas som element, aldrig som markup.
// Kör: node tests/js/about-view.test.cjs
const assert = require('node:assert/strict');
const { open, stations } = require('./kr-fixture.cjs');

const ABOUT = {
  story: '# Trafikidé\n\nModern trafik **dygnet runt** på en bana med fyra grenar.\n\n## Tema\n\n- Godståg till hamnen\n- Persontåg varje timme\n\nHej <img src=x onerror=alert(1)> [farlig](javascript:alert(1)) och [Värnamo](https://example.org/varnamo).',
  instructions: { sv: 'Ring bara vid fel.', da: 'Ring kun ved fejl.' },
  program: '1. 07:30 Frukost\n2. 08:00 Trafikstart',
};
const LEKBY = '# Kritisk station\nTa emot godståg på spår 3.';
const withTexts = stations.map((station) => (station.id === 'lek' ? { ...station, instructions: LEKBY } : station));
const summary = { story: true, instructions: ['sv', 'da'], program: true, stations: 1, available: true };

// /v1/about med texterna, och /v1/display med vad som finns att läsa och Lekbys instruktioner.
const api = (aboutStatus = 200, withAbout = true) => ({
  '/v1/about': () => (aboutStatus === 200
    ? { data: { publication_id: 'pub-9', meet: { name: 'Grimslöv 2027', language: 'sv' }, about: ABOUT, stations: [{ id: 'lek', name: 'Lekby', code: 'LEK', instructions: LEKBY }] } }
    : { status: aboutStatus, data: { error: 'boom' } }),
  '/v1/display': () => ({ data: {
    clock: { configured: true, running: false, time: '05:12:40', speed: 4, source: 'internal', available: true, can_control: true, style: 'digital', show_seconds: true },
    meet: { id: 'meet-1', name: 'Grimslöv 2027' }, active_day: 'Dagl', publication_id: 'pub-9', stations: withTexts, connections: [], routes: [], services: [],
    train_positions: [], connection_states: [], connection: { screens: [] }, display: {},
    about: withAbout ? summary : { story: false, instructions: [], program: false, stations: 0, available: false } } }),
});

const sheetState = (page) => page.evaluate(() => {
  const sheet = document.querySelector('#tm-about-sheet');
  const body = sheet?.querySelector('.tm-about-sheet__body');
  const box = sheet?.getBoundingClientRect();
  return sheet && {
    open: sheet.open, meet: sheet.querySelector('.tm-about-sheet__meet').textContent,
    tabs: [...sheet.querySelectorAll('.tm-about-sheet__tab')].map((tab) => tab.textContent),
    active: sheet.querySelector('.tm-about-sheet__tab.is-active')?.textContent || null,
    text: body.textContent.replace(/\s+/g, ' ').trim(),
    headings: [...body.querySelectorAll('h3, h4, h5')].map((h) => `${h.tagName}:${h.textContent}`),
    images: body.querySelectorAll('img').length,
    links: [...body.querySelectorAll('a')].map((a) => a.getAttribute('href')),
    bold: body.querySelector('strong')?.textContent || null,
    items: [...body.querySelectorAll('li')].map((li) => li.textContent),
    inside: box.left >= -1 && box.right <= innerWidth + 1 && box.top >= -1 && box.bottom <= innerHeight + 1,
    overflow: document.documentElement.scrollWidth > innerWidth + 1,
  };
});
const tab = (page, name) => page.locator('#tm-about-sheet .tm-about-sheet__tab', { hasText: name }).click();

(async () => {
  // ── Deltagarvyn på en telefon: kortet och bladet ─────────────────────
  for (const theme of ['dark', 'light']) {
    const view = await open({ route: '/', width: 390, height: 844, theme, loggedOut: true, api: api() });
    const { page } = view;
    try {
      await page.locator('#participant-view').waitFor({ state: 'visible' });
      await page.locator('#pv-about-card').waitFor({ state: 'visible' });
      await page.waitForFunction(() => document.querySelector('#pv-about-teaser').textContent.length > 0);
      assert.equal(await page.locator('#pv-about-teaser').textContent(), 'Modern trafik dygnet runt på en bana med fyra grenar.',
        `${theme}: the card shows the story's first paragraph, not its heading`);
      await page.locator('#pv-about-open').click();
      await page.waitForFunction(() => document.querySelector('#tm-about-sheet .tm-about-sheet__tab'));
      let s = await sheetState(page);
      assert.equal(s.open, true);
      assert.equal(s.meet, 'Grimslöv 2027');
      assert.deepEqual(s.tabs, ['Berättelsen', 'Instruktioner', 'Program', 'Stationerna']);
      assert.equal(s.active, 'Berättelsen');
      assert.deepEqual(s.headings, ['H3:Trafikidé', 'H4:Tema'], 'the story starts at h3 under the sheet title');
      assert.equal(s.bold, 'dygnet runt');
      assert.deepEqual(s.items, ['Godståg till hamnen', 'Persontåg varje timme']);
      // Text, aldrig markup: ingen bild, ingen javascript-länk, men den vanliga länken.
      assert.equal(s.images, 0, `${theme}: an <img> in the text stays text`);
      assert.ok(s.text.includes('<img src=x onerror=alert(1)>'));
      assert.deepEqual(s.links, ['https://example.org/varnamo']);
      assert.equal(s.inside, true, `${theme}: the sheet fits the phone`);
      assert.equal(s.overflow, false, `${theme}: no sideways scroll`);
      await tab(page, 'Instruktioner');
      assert.equal((await sheetState(page)).text, 'Ring bara vid fel.', 'Swedish instructions for a Swedish reader');
      await tab(page, 'Program');
      assert.deepEqual((await sheetState(page)).items, ['07:30 Frukost', '08:00 Trafikstart']);
      await tab(page, 'Stationerna');
      s = await sheetState(page);
      assert.deepEqual(s.headings, ['H3:Lekby', 'H4:Kritisk station'], 'each station under its name, its own headings below');
      assert.ok(s.text.endsWith('Ta emot godståg på spår 3.'));
      await page.locator('#tm-about-sheet .tm-about-sheet__close').click();
      assert.equal((await sheetState(page)).open, false);
      assert.deepEqual(view.errors, []);
      assert.deepEqual(view.violations, []);
    } finally { await view.browser.close(); }
  }

  // ── En dansk läsare får de danska instruktionerna och danska flikar ──
  {
    const view = await open({ route: '/', width: 1280, height: 860, loggedOut: true, lang: 'da', api: api() });
    const { page } = view;
    try {
      await page.locator('#pv-about-open').click();
      await page.waitForFunction(() => document.querySelector('#tm-about-sheet .tm-about-sheet__tab'));
      assert.deepEqual((await sheetState(page)).tabs, ['Historien', 'Instruktioner', 'Program', 'Stationerne']);
      assert.equal(await page.locator('#tm-about-sheet-title').textContent(), 'Om trafikspillet');
      assert.equal(await page.locator('#tm-about-sheet .tm-about-sheet__close').getAttribute('aria-label'), 'Luk');
      await tab(page, 'Instruktioner');
      assert.equal((await sheetState(page)).text, 'Ring kun ved fejl.');
      assert.deepEqual(view.errors, []);
    } finally { await view.browser.close(); }
  }

  // ── Utan texter syns inget kort, och ett fel säger det ─────────────
  {
    const view = await open({ route: '/', width: 390, height: 844, loggedOut: true, api: api(200, false) });
    const { page } = view;
    try {
      await page.locator('#participant-view').waitFor({ state: 'visible' });
      await page.waitForTimeout(800);
      assert.equal(await page.locator('#pv-about-card').isVisible(), false, 'a meet without texts has no card');
    } finally { await view.browser.close(); }
  }
  {
    const view = await open({ route: '/', width: 390, height: 844, loggedOut: true, api: api(500) });
    const { page } = view;
    try {
      await page.locator('#pv-about-open').click();
      await page.locator('#tm-about-sheet .tm-about-sheet__empty', { hasText: 'Texterna gick inte att läsa. Försök igen.' }).waitFor();
    } finally { await view.browser.close(); }
  }

  // ── Drift: boken i sidhuvudet och stationens instruktioner ───────────
  for (const theme of ['dark', 'light']) {
    const view = await open({ route: '/drift', width: 1440, height: 900, theme, api: api() });
    const { page } = view;
    try {
      await page.locator('#header-about').waitFor({ state: 'visible' });
      await page.locator('#header-about').click();
      await page.waitForFunction(() => document.querySelector('#tm-about-sheet .tm-about-sheet__tab'));
      assert.equal((await sheetState(page)).active, 'Berättelsen');
      await page.keyboard.press('Escape');
      assert.equal((await sheetState(page)).open, false, 'Escape closes the sheet');
      await page.locator('#device-list .kr-rowlink', { hasText: 'Lekby' }).click();
      const aside = page.locator('#overview-station-inspector');
      await aside.locator('.kr-aside__md').waitFor();
      const section = await aside.evaluate((node) => {
        const md = node.querySelector('.kr-aside__md');
        return { title: md.closest('.kr-aside__sec').querySelector('h4').textContent, heading: md.querySelector('h5')?.textContent, text: md.textContent };
      });
      assert.deepEqual(section, { title: 'Stationens instruktioner', heading: 'Kritisk station', text: 'Kritisk stationTa emot godståg på spår 3.' });
      // En station utan instruktioner har inget sådant avsnitt.
      await page.locator('#device-list .kr-rowlink', { hasText: 'Devsjö' }).click();
      await aside.locator('.kr-aside__name', { hasText: 'Devsjö' }).waitFor();
      assert.equal(await aside.locator('.kr-aside__md').count(), 0);
      assert.deepEqual(view.errors, []);
      assert.deepEqual(view.violations, []);
    } finally { await view.browser.close(); }
  }

  // ── Inloggad på en telefon: boken göms, Öppna-menyn har Om trafikspelet ──
  {
    const view = await open({ route: '/drift', width: 390, height: 844, api: api() });
    const { page } = view;
    try {
      await page.locator('#app-chrome #logout').waitFor({ state: 'visible' });
      assert.equal(await page.locator('#header-about').isVisible(), false, 'the meet name goes first on a phone');
      await page.locator('#app-chrome .screen-menu summary').click();
      await page.locator('#menu-about').click();
      await page.waitForFunction(() => document.querySelector('#tm-about-sheet .tm-about-sheet__tab'));
      const s = await sheetState(page);
      assert.equal(s.open, true);
      assert.equal(s.inside, true);
      assert.equal(await page.locator('#app-chrome .screen-menu').evaluate((menu) => menu.open), false, 'the menu closes behind the sheet');
      assert.deepEqual(view.errors, []);
    } finally { await view.browser.close(); }
  }
  console.log('about-view: ok');
})().catch((error) => { console.error(error); process.exit(1); });
