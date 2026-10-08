// Tidtabellen som trafikinformation, som i SJ:s app (Casper 2026-10-08): en
// försening från +3 min är en röd bricka med vita siffror och den planerade tiden
// står överstruken med den nya bredvid; en rad som just ändrats lyser upp kort och
// får "Nyss", men aldrig första gången listan ritas. Deltagarvyn på en telefon,
// Nästa händelser på Drift och skärmarnas Översikt. Kör: node tests/js/live-timetable.test.cjs
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const seconds = (value) => value.split(':').reduce((sum, part) => sum * 60 + Number(part), 0);
const stations = [{ id: 'a', code: 'ALV', name: 'Alvesta' }, { id: 'b', code: 'BOR', name: 'Bor' }, { id: 'c', code: 'CDA', name: 'Charlottendal' }];
const connections = [{ id: 'ab', station_a_id: 'a', station_b_id: 'b', track_type: 'single' }, { id: 'bc', station_a_id: 'b', station_b_id: 'c', track_type: 'single' }];
// 101 A 09:20 → B 09:35/09:37 → C 09:50 avgick 09:27 (+7); 404 A 09:10 → B 09:25 avgick 09:12 (+2: i tid);
// 202 B 09:45 → C 09:58 har inte gått.
// 606 (persontåg) A 09:16 → B 09:31 avgick 09:14 (2 min för tidigt); 505 (godståg) A 09:15 → B 09:30 avgick 09:13.
const T = { 101: [['a', null, '09:20'], ['b', '09:35', '09:37'], ['c', '09:50', null]], 404: [['a', null, '09:10'], ['b', '09:25', null]], 202: [['b', null, '09:45'], ['c', '09:58', null]],
  505: [['a', null, '09:15'], ['b', '09:30', null]], 606: [['a', null, '09:16'], ['b', '09:31', null]] };
const TYPES = { 505: 'goods' };
const services = Object.entries(T).map(([n, stops]) => ({ id: `s${n}`, train_number: n, days: 'Dagl', train_type: TYPES[n] || 'person',
  stops: stops.map(([station_id, arrival_time, departure_time], stop_order) => ({ station_id, arrival_time, departure_time, stop_order })) }));
const trains = Object.entries(T).flatMap(([n, stops]) => stops.map(([station_id, arrival_time, departure_time], i) => ({ id: `m${n}-${i}`, service_id: `s${n}`, train_number: n, station_id, arrival_time, departure_time, days: 'Dagl' })));
const routes = trains.map((row) => ({ ...row, service_id: row.service_id }));
const clock = { configured: true, running: false, time: '09:30:00', speed: 1, source: 'internal', available: true, can_control: true, style: 'digital', show_seconds: true };

// Version 1: 101 och 404 ute. Version 2: 101 har kommit in till Bor 09:43.
// level: träffens förval (display.deviation_level); utan det gäller 2.
const display = (version, level = 4) => ({ data: {
  clock, meet: { id: 'meet-1', name: 'Grimslöv 2027' }, active_day: 'Dagl', publication_id: 'pub-9', stations, connections, services, routes, trains,
  movement_live: {
    'm101-0': { arrival: 'none', departure: 'departed', departed_seconds: seconds('09:27:00'), by_timetable: false },
    'm404-0': { arrival: 'none', departure: 'departed', departed_seconds: seconds('09:12:00'), by_timetable: false },
    'm505-0': { arrival: 'none', departure: 'departed', departed_seconds: seconds('09:13:00'), by_timetable: false },
    'm606-0': { arrival: 'none', departure: 'departed', departed_seconds: seconds('09:14:00'), by_timetable: false },
    ...(version === 2 ? { 'm101-1': { arrival: 'arrived', departure: 'positioned', arrived_seconds: seconds('09:43:00'), by_timetable: false } } : {}),
  },
  connection_states: [{ id: 'ab', state: 'occupied', channels: [
    ...(version === 1 ? [{ train_number: '101', from_station_id: 'a', to_station_id: 'b', state: 'occupied' }] : []),
    { train_number: '404', from_station_id: 'a', to_station_id: 'b', state: 'occupied' }] }, { id: 'bc', state: 'free', channels: [] }],
  train_positions: [{ train_number: '404', status: 'connection', connection_id: 'ab', from_station_id: 'a', to_station_id: 'b' },
    ...(version === 1 ? [{ train_number: '101', status: 'connection', connection_id: 'ab', from_station_id: 'a', to_station_id: 'b' }] : [{ train_number: '101', status: 'station', station_id: 'b' }])],
  connection: { screens: [] }, display: { graph_station_order: ['a', 'b', 'c'], ...(level ? { deviation_level: level } : {}) } } });

const rows = (page, selector) => page.evaluate((selector) => [...document.querySelectorAll(selector)].map((row) => ({
  text: row.textContent.replace(/\s+/g, ' ').trim(), delay: row.querySelector('.tm-delay')?.textContent || null,
  was: [...row.querySelectorAll('.tm-was')].map((el) => el.textContent), now: [...row.querySelectorAll('.tm-new')].map((el) => el.textContent),
  updated: row.classList.contains('is-updated'), recent: Boolean(row.querySelector('.tm-recent')), early: row.querySelector('.tm-early')?.textContent || null,
  animation: getComputedStyle(row).animationName,
  pill: row.querySelector('.tm-delay') ? (() => { const s = getComputedStyle(row.querySelector('.tm-delay')); return { bg: s.backgroundColor, fg: s.color }; })() : null })), selector);

// Kontrast mellan två rgb()-färger.
const luminance = (rgb) => { const [r, g, b] = rgb.match(/\d+/g).map(Number).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a, b) => { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

(async () => {
  // ── Deltagarvyn på en telefon ────────────────────────────────────────
  for (const theme of ['dark', 'light']) {
    let version = 1;
    const view = await open({ route: '/', width: 390, height: 844, theme, loggedOut: true,
      api: { '/v1/display': () => display(version) } });
    const { page } = view;
    try {
      await page.locator('#participant-view').waitFor({ state: 'visible' });
      await page.waitForFunction(() => document.querySelectorAll('#pv-timetable .pv-item').length >= 2);
      await page.locator('#pv-timetable-toggle').click(); // hela tidtabellen: alla fem tåg
      const list = await rows(page, '#pv-timetable .pv-item');
      const late = list.find((row) => row.text.startsWith('101'));
      assert.equal(late.delay, '+7', `${theme}: 101 is seven minutes late`);
      assert.deepEqual(late.was, ['09:20', '09:50'], 'the planned departure and arrival struck through');
      assert.deepEqual(late.now, ['09:27', '09:57'], 'the actual departure and the new arrival');
      assert.match(late.text, /På väg mot Bor/);
      assert.ok(contrast(late.pill.bg, late.pill.fg) >= 4.5, `${theme}: white on red is readable (${late.pill.bg} / ${late.pill.fg})`);
      const onTime = list.find((row) => row.text.startsWith('404'));
      assert.equal(onTime.delay, null, 'two minutes is on time: no red');
      assert.match(list.find((row) => row.text.startsWith('202')).text, /om 15 min/);
      // För tidigt: persontåget 606 får en grön −2, godståget 505 får gå tidigare.
      assert.equal(list.find((row) => row.text.startsWith('606'))?.early, '\u22122', `${theme}: a passenger train that left early`);
      assert.equal(list.find((row) => row.text.startsWith('505'))?.early ?? null, null, 'a freight train may leave early');
      const events = await rows(page, '#pv-events .pv-item--event');
      const into = events.find((row) => row.text.includes('101'));
      assert.equal(into?.delay, '+7', 'Nästa händelser: 101 into Bor, late');
      assert.deepEqual([into.was, into.now], [['09:35'], ['09:42']]);
      assert.ok([...list, ...events].every((row) => !row.updated && !row.recent), 'nothing flashes the first time');
      // Visning: deltagaren väljer "När det inträffar" (2). Inga siffror och
      // ingen överstrykning, bara markeringen när något händer.
      await page.locator('#pv-view-open').click();
      const ink = await page.evaluate(() => [...document.querySelectorAll('#pv-view-levels .pv-level b')].map((b) => [getComputedStyle(b).color, getComputedStyle(document.querySelector('#pv-view-card')).backgroundColor]));
      assert.equal(ink.length, 6, 'Som träffen and the five levels');
      assert.ok(ink.every(([fg, bg]) => contrast(fg, bg) >= 4.5), `${theme}: the level names are readable ${JSON.stringify(ink[0])}`);
      await page.locator('#pv-view-levels input[value="2"]').check();
      await page.locator('#pv-view-close').click();
      assert.equal(await page.evaluate(() => localStorage.getItem('trainmeet.deviationLevel')), '2');
      const quiet = (await rows(page, '#pv-timetable .pv-item')).find((row) => row.text.startsWith('101'));
      assert.deepEqual([quiet.delay, quiet.was, quiet.early], [null, [], null], `${theme}: level 2 shows no delay`);
      assert.match(quiet.text, /På väg mot Bor/);
      assert.ok((await rows(page, '#pv-timetable .pv-item')).every((row) => !row.updated && !row.recent), `${theme}: changing the level is not a change in the traffic`);
      // Nästa uppdatering: 101 har kommit in till Bor. Bara den raden lyser upp.
      version = 2;
      await page.waitForFunction(() => document.querySelector('#pv-timetable .pv-item.is-updated'), null, { timeout: 12000 });
      const after = await rows(page, '#pv-timetable .pv-item');
      const changed = after.find((row) => row.text.startsWith('101'));
      assert.ok(changed.updated && changed.recent, `${theme}: the changed row lights up and says Nyss`);
      assert.match(changed.text, /Vid Bor|Väntar i Bor/);
      assert.equal(changed.animation, 'tm-row-flash');
      assert.ok(after.filter((row) => !row.text.startsWith('101')).every((row) => !row.updated), 'the others stay as they were');
      // Minskad rörelse: ingen animation, bara "Nyss".
      await page.emulateMedia({ reducedMotion: 'reduce' });
      const calm = (await rows(page, '#pv-timetable .pv-item')).find((row) => row.text.startsWith('101'));
      assert.equal(calm.animation, 'none');
      assert.ok(calm.recent);
      // Ingen markering (1): inte ens "Nyss".
      await page.locator('#pv-view-open').click();
      await page.locator('#pv-view-levels input[value="1"]').check();
      await page.locator('#pv-view-close').click();
      assert.ok((await rows(page, '#pv-timetable .pv-item')).every((row) => !row.recent && !row.delay), `${theme}: level 1 marks nothing`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'no sideways scroll on a phone');
      assert.deepEqual(view.errors, []);
      assert.deepEqual(view.violations, []);
    } finally { await view.browser.close(); }
  }

  // ── Nästa händelser på Drift ─────────────────────────────────────────
  const drift = await open({ route: '/drift', api: { '/v1/display': () => display(1), '/v1/clock': (request) => (request.method() === 'GET' ? { data: clock } : undefined) } });
  try {
    await drift.page.waitForFunction(() => document.querySelector('#drift-upcoming .kr-ev .tm-delay'));
    const events = await rows(drift.page, '#drift-upcoming .kr-ev');
    const late = events.find((row) => row.text.includes('101'));
    assert.deepEqual([late.delay, late.was, late.now], ['+7', ['09:35'], ['09:42']], 'Drift: 101 into Bor, seven late');
    assert.equal(events.find((row) => row.text.includes('404'))?.delay ?? null, null, 'Drift: 404 on time');
    assert.deepEqual(drift.errors, []);
  } finally { await drift.browser.close(); }

  // ── Skärmarnas Översikt ──────────────────────────────────────────────
  const screen = await open({ route: '/display/dashboard', api: { '/v1/display': () => display(1) } });
  try {
    await screen.page.waitForFunction(() => document.querySelector('#dashboard-view .dash-row .tm-delay'));
    const events = await rows(screen.page, '#dashboard-view .dash-card .dash-row:not(.dash-row--line)');
    const late = events.find((row) => row.text.includes('101'));
    assert.deepEqual([late.delay, late.was, late.now], ['+7', ['09:35'], ['09:42']], 'screen: 101 into Bor, seven late');
    assert.match(late.text, /ankommer Bor från Alvesta/);
    // Skärmens eget val går före träffens förval.
    await screen.page.selectOption('#display-deviation-level', '2');
    await screen.page.waitForFunction(() => !document.querySelector('#dashboard-view .dash-row .tm-delay'));
    assert.equal(await screen.page.evaluate(() => localStorage.getItem('trainmeet.displayDeviationLevel')), '2');
    assert.deepEqual(screen.errors, []);
  } finally { await screen.browser.close(); }
  console.log('live-timetable: ok');
})().catch((error) => { console.error(error); process.exitCode = 1; });
