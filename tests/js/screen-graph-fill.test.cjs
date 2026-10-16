// Storskärmens tågdiagram (4.12.0), efter Caspers skärmbild 2026-10-16:
// duken fyller en 16:10-skärm utan svarta band, texten är större, och
// skärmen kan fälla ut stationer i spårrader (menyn Spårrader eller ett tryck
// på stationens namn). Tåget står på det spår det verkligen fick.
// Kör: node tests/js/screen-graph-fill.test.cjs
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const stations = [['a', 'A', 'Alvesta'], ['c', 'CDA', 'Charlottendal'], ['d', 'D', 'Dalby'], ['e', 'E', 'Eslöv']].map(([id, code, name]) => ({ id, code, name }));
const connections = [['a', 'c'], ['c', 'd'], ['d', 'e']].map(([a, b], i) => ({ id: `c${i}`, station_a_id: a, station_b_id: b, track_type: 'single' }));
const tracks = [['c', '1a'], ['c', '1b'], ['c', '2a'], ['c', '2b'], ['c', '3'], ['c', '4'], ['d', '1'], ['d', '2'], ['e', '1']]
  .map(([station_id, label], i) => ({ id: `t-${station_id}-${label}`, station_id, display_label: label, sort_order: i, active: true }));
const hhmm = (m) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
const services = [['91', 370, ['a', 'c', 'd', 'e']], ['92', 380, ['e', 'd', 'c', 'a']], ['93', 400, ['a', 'c', 'd']]].map(([n, start, path]) => ({
  id: `s-${n}`, train_number: n, days: 'Dagl', train_type: 'person',
  stops: path.map((station_id, i) => ({ station_id, stop_order: i, service_day_offset: 0,
    arrival_time: i ? hhmm(start + i * 12) : null, departure_time: i < path.length - 1 ? hhmm(start + i * 12 + 2) : null })) }));
const routes = services.flatMap((s) => s.stops.map((st) => ({ train_number: s.train_number, station_id: st.station_id, arrival_time: st.arrival_time, departure_time: st.departure_time, service_id: s.id })));
// 91 är planerat på 1a men TKL tog in det på 1b; 93 saknar spår och har standardspåret 3.
const trains = [
  { id: 'r91', service_id: 's-91', station_id: 'c', train_number: '91', track: '1a' },
  { id: 'r92', service_id: 's-92', station_id: 'c', train_number: '92', track: '2b' },
  { id: 'r93', service_id: 's-93', station_id: 'c', train_number: '93', track: null, default_tracks: ['3', '4'] },
];
const display = () => ({ data: {
  clock: { configured: true, running: false, time: '06:30:00', speed: 1, source: 'internal', available: true, can_control: true },
  meet: { id: 'meet-1', name: 'Grimslöv 2027' }, active_day: 'Dagl', publication_id: 'pub-9', stations, connections, tracks, trains, routes, services,
  movement_live: { r91: { arrival: 'arrived', departure: 'none', actual_track: 't-c-1b' } },
  train_positions: [], connection_states: connections.map((c) => ({ id: c.id, state: 'free', channels: [] })), connection: { screens: [] } } });

(async () => {
  // 16:10: duken blir 1920 × 1200 och fyller fönstret ända ut i kanterna.
  for (const [width, height] of [[1600, 1000], [1920, 1080], [1280, 1024]]) {
    const tv = await open({ route: '/display/graph', width, height, api: { '/v1/display': display } });
    try {
      await tv.page.waitForFunction(() => document.querySelectorAll('#graph-svg .sc-graph-label').length === 4);
      const box = await tv.page.evaluate(() => {
        const rect = document.querySelector('#display-stage').getBoundingClientRect();
        const toolbar = document.querySelector('#display-app').dataset.chrome === 'window' ? document.querySelector('#display-toolbar').offsetHeight : 0;
        return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, toolbar };
      });
      const tall = height - box.toolbar;
      // Fönstret räknas upp till 4:3; en ännu högre skärm får band över och under som förut.
      if (width / tall <= 4 / 3 - 0.01) {
        assert.ok(Math.abs(box.left) <= 1 && Math.abs(box.right - width) <= 1, `${width}×${height}: the canvas fills the width ${JSON.stringify(box)}`);
      } else {
        assert.ok(Math.abs(box.left) <= 1 && Math.abs(box.right - width) <= 1 && Math.abs(box.bottom - height) <= 1 && box.top <= box.toolbar + 1,
          `${width}×${height}: no bands around the canvas ${JSON.stringify(box)}`);
      }
      assert.deepEqual(tv.errors, []);
      assert.deepEqual(tv.violations, []);
    } finally { await tv.browser.close(); }
  }

  const tv = await open({ route: '/display/graph', width: 1600, height: 1000, api: { '/v1/display': display } });
  try {
    const { page } = tv;
    await page.waitForFunction(() => document.querySelectorAll('#graph-svg .sc-graph-label').length === 4);
    // Större text än skissen: namnen 16 och tågnumren 15 % större än Drifts.
    const fonts = await page.evaluate(() => ({ name: getComputedStyle(document.querySelector('#graph-svg .sc-graph-label')).fontSize,
      time: getComputedStyle(document.querySelector('#graph-svg .sc-graph-time')).fontSize,
      train: getComputedStyle(document.querySelector('#graph-svg .sc-graph-train')).fontSize }));
    assert.deepEqual(fonts, { name: '16px', time: '15px', train: '13.2px' });
    assert.equal(await page.locator('#graph-svg .sc-graph-track').count(), 0, 'no track rows to begin with');

    // Spårrader: bara stationerna med mer än ett spår.
    const menu = page.locator('#display-graph-tracks');
    assert.equal(await menu.isVisible(), true);
    await menu.locator('summary').click();
    assert.deepEqual(await menu.locator('label').allInnerTexts(), ['Alla stationer', 'Charlottendal', 'Dalby']);
    await menu.getByLabel('Charlottendal').check();
    await page.waitForFunction(() => document.querySelectorAll('#graph-svg .sc-graph-track').length === 6);
    assert.equal(await page.evaluate(() => localStorage.getItem('trainmeet.displayGraphTracks')), '["c"]');
    const rows = await page.evaluate(() => {
      const svg = document.querySelector('#graph-svg');
      const y = Object.fromEntries([...svg.querySelectorAll('.sc-graph-track')].map((t) => [t.textContent, Number(t.getAttribute('y'))]));
      const dwell = (number) => [...svg.querySelectorAll(`.graph-train-group[data-train-number="${number}"] .sc-graph-dwell`)].map((l) => Number(l.getAttribute('y1')));
      const name = [...svg.querySelectorAll('.sc-graph-label')].find((t) => t.textContent === 'Charlottendal');
      const other = [...svg.querySelectorAll('.sc-graph-label')].find((t) => t.textContent === 'Alvesta');
      return { y, d91: dwell('91'), d92: dwell('92'), d93: dwell('93'), nameX: Number(name.getAttribute('x')), otherX: Number(other.getAttribute('x')) };
    });
    // Spårets rad har texten 3,5 enheter under linjen.
    const on = (label) => rows.y[label] - 3.5;
    assert.ok(rows.d91.some((y) => Math.abs(y - on('1b')) < 0.5), `91 stands on 1b, where TKL took it in ${JSON.stringify(rows)}`);
    assert.ok(rows.d92.some((y) => Math.abs(y - on('2b')) < 0.5), '92 on its timetable track 2b');
    assert.ok(rows.d93.some((y) => Math.abs(y - on('3')) < 0.5), '93 has no track in the timetable: its default 3');
    assert.ok(rows.otherX - rows.nameX >= 23, 'the folded-out name steps aside for the track names');

    // Ett tryck på namnet fäller ihop stationen igen; Alla stationer fäller ut båda.
    await menu.locator('summary').click();
    assert.equal(await menu.evaluate((node) => node.open), false);
    await page.locator('#graph-svg .sc-graph-label', { hasText: 'Charlottendal' }).click();
    await page.waitForFunction(() => document.querySelectorAll('#graph-svg .sc-graph-track').length === 0, null, { timeout: 5000 })
      .catch(async (error) => { console.error('stored', await page.evaluate(() => localStorage.getItem('trainmeet.displayGraphTracks'))); throw error; });
    assert.equal(await page.evaluate(() => localStorage.getItem('trainmeet.displayGraphTracks')), '[]');
    if (!(await menu.evaluate((node) => node.open))) await menu.locator('summary').click();
    await menu.getByLabel('Alla stationer').check();
    // Alla: varje station med sina spår, även Eslöv med sitt enda.
    await page.waitForFunction(() => document.querySelectorAll('#graph-svg .sc-graph-track').length === 9);
    assert.equal(await page.evaluate(() => localStorage.getItem('trainmeet.displayGraphTracks')), 'all');
    assert.equal(await menu.getByLabel('Dalby').isChecked(), true);
    // Banöversikten har inga spårrader att välja.
    await page.goto(page.url().replace('/display/graph', '/display/topology'));
    await page.waitForFunction(() => document.querySelector('#topology-svg .topology-name'));
    assert.equal(await menu.isVisible(), false);
    assert.deepEqual(tv.errors, []);
    assert.deepEqual(tv.violations, []);
  } finally { await tv.browser.close(); }
  console.log('screen-graph-fill: ok');
})().catch((error) => { console.error(error); process.exit(1); });
