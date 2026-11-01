// Tågdiagrammet i Drift och på storskärmen under serverns riktiga CSP
// (Cloud #100, C6, jämförelsen med Värnamo 2026-11-01):
// - tågslagens färger syns (förut stoppade CSP:n style-attributen: 2 färger i
//   stället för Clouds 12–17);
// - Hela dagen över midnatt står som "00:00–01:00 (+1 dygn)", inte som en timme;
// - hela dagen på 390 px rullas i sidled i panelen i stället för att tryckas ihop;
// - alla linjer på storskärmen: namnen skrivs inte över varandra, och linjens
//   rubrik ligger ovanpå tågen.
// Kör: node tests/js/graph-colours-live.test.cjs
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const LINES = 8, PER_LINE = 6;
const stations = [], connections = [], planLines = [];
for (let line = 0; line < LINES; line += 1) {
  const ids = [];
  for (let index = 0; index < PER_LINE; index += 1) {
    const id = `s${line}-${index}`;
    stations.push({ id, code: `L${line}S${index}`, name: `Station ${line + 1}.${index + 1} Långnamn` });
    ids.push(id);
    if (index) connections.push({ id: `c-${line}-${index}`, station_a_id: ids[index - 1], station_b_id: id, track_type: 'single' });
  }
  planLines.push({ number: String(line + 1), name: `Linje ${line + 1}`, station_ids: ids });
}
const COLOURS = ['#c62828', '#1565c0', '#2e7d32', '#6a1b9a', '#ef6c00', '#00838f'];
const hhmm = (m) => `${String(Math.floor(((m % 1440) + 1440) % 1440 / 60)).padStart(2, '0')}:${String(((m % 60) + 60) % 60).padStart(2, '0')}`;
const services = [];
for (let line = 0; line < LINES; line += 1) {
  // Tåg hela dagen, i sex färger, och ett nattåg över midnatt på linje 1.
  for (let k = 0; k < 8; k += 1) {
    const start = 5 * 60 + k * 120 + line * 7;
    const reverse = k % 2 === 1;
    const path = Array.from({ length: PER_LINE }, (_, index) => `s${line}-${reverse ? PER_LINE - 1 - index : index}`);
    const n = `${line + 1}${String(k).padStart(2, '0')}`;
    services.push({ id: `t-${n}`, train_number: n, days: 'Dagl', train_type: 'person', color: COLOURS[(line + k) % COLOURS.length],
      stops: path.map((station_id, index) => ({ station_id, stop_order: index, service_day_offset: 0,
        arrival_time: index ? hhmm(start + index * 9) : null, departure_time: index < path.length - 1 ? hhmm(start + index * 9 + 1) : null })) });
  }
}
services.push({ id: 't-night', train_number: '991', days: 'Dagl', train_type: 'goods', color: '#4e342e',
  stops: ['s0-0', 's0-1', 's0-2'].map((station_id, index) => ({ station_id, stop_order: index, service_day_offset: index ? 1 : 0,
    arrival_time: index ? hhmm(23 * 60 + 50 + index * 15) : null, departure_time: index < 2 ? hhmm(23 * 60 + 50 + index * 15 + 1) : null })) });
const routes = services.flatMap((s) => s.stops.map((st) => ({ train_number: s.train_number, station_id: st.station_id, arrival_time: st.arrival_time, departure_time: st.departure_time, service_id: s.id })));
const clock = { configured: true, running: false, time: '08:00:00', speed: 1, source: 'internal', available: true, can_control: true, style: 'digital', show_seconds: false };
const display = () => ({ data: { clock, meet: { id: 'meet-1', name: 'Färgprov', operating_region: 'eu' }, active_day: 'Dagl', publication_id: 'pub-1',
  stations, connections, tracks: [], trains: [], services, routes, display: { plan_lines: planLines },
  train_positions: [], connection_states: connections.map((c) => ({ id: c.id, state: 'free', channels: [] })), connection: { screens: [] }, server_time: new Date().toISOString() } });
const api = {
  '/v1/display': display,
  '/v1/cloud/presentation': () => ({ data: { supported: false } }),
  '/v1/devices': () => ({ data: { devices: [], terminals: [], stations, languages: [{ code: 'sv', name: 'Svenska' }] } }),
};

async function drift(width, theme = 'light') {
  const run = await open({ route: '/drift', theme, width, height: 900, api, running: false, time: '08:00:00' });
  for (let i = 0; i < 40 && await run.page.evaluate(() => document.querySelectorAll('#overview-graph .pl').length) < 4; i++) await run.page.waitForTimeout(250);
  await run.page.selectOption('#drift-graph-window', '0');
  await run.page.waitForTimeout(500);
  return run;
}

(async () => {
  // Drift i full bredd: färgerna, och Hela dagen säger att den går in på nästa dygn.
  for (const theme of ['light', 'dark']) {
    const run = await drift(1440, theme);
    try {
      const facts = await run.page.evaluate(() => ({
        colours: new Set([...document.querySelectorAll('#overview-graph .pl')].map((n) => getComputedStyle(n).stroke)).size,
        meta: document.querySelector('#drift-graph-meta').textContent,
        wide: document.querySelector('#overview-graph').parentElement.classList.contains('is-wide'),
      }));
      assert.ok(facts.colours >= 6, `${theme}: the train colours reach Drift under the CSP (${facts.colours})`);
      assert.match(facts.meta, /^05:00–01:00 \(\+1 dygn\) · /, `${theme}: the whole day over midnight says so: ${facts.meta}`);
      assert.equal(facts.wide, false, 'full width needs no scrolling');
      assert.deepEqual(run.violations, [], `${theme}: no CSP violations in Drift`);
      assert.deepEqual(run.errors, []);
    } finally { await run.browser.close(); }
  }

  // 390 px: minst 40 px per timme, rullas i panelen, klockslagen fria, klockan i bild.
  {
    const run = await drift(390);
    try {
      const facts = await run.page.evaluate(() => {
        const svg = document.querySelector('#overview-graph'), box = svg.parentElement;
        const axis = [...svg.querySelectorAll('.ax')].map((n) => n.getBoundingClientRect());
        const now = svg.querySelector('.now')?.getBoundingClientRect();
        const panel = box.getBoundingClientRect();
        return { wide: box.classList.contains('is-wide'), svg: svg.getBoundingClientRect().width, scrolls: box.scrollWidth > box.clientWidth,
          overlaps: axis.slice(1).filter((r, i) => r.left < axis[i].right).length, labels: axis.length,
          nowVisible: Boolean(now) && now.left >= panel.left && now.right <= panel.right,
          pageWider: document.documentElement.scrollWidth > window.innerWidth };
      });
      assert.ok(facts.wide && facts.scrolls, `the whole day scrolls sideways in the panel ${JSON.stringify(facts)}`);
      assert.ok(facts.svg >= 20 * 40, `05:00–01:00 at least 40 px per hour (${facts.svg})`);
      assert.equal(facts.overlaps, 0, 'the hours do not overlap');
      assert.ok(facts.labels >= 10);
      assert.ok(facts.nowVisible, 'the clock is in view when the graph opens');
      assert.equal(facts.pageWider, false, 'the page itself does not get wider');
      assert.deepEqual(run.violations, []);
    } finally { await run.browser.close(); }
  }

  // Storskärmen, alla åtta linjer på 1440 × 900: namnen krymper med raderna och
  // skrivs inte över varandra, rubrikerna ligger ovanpå tågen, färgerna syns.
  for (const theme of ['light', 'dark']) {
    const tv = await open({ route: '/display/graph', theme, width: 1440, height: 900, api, running: false });
    try {
      for (let i = 0; i < 40 && await tv.page.evaluate((n) => document.querySelectorAll('#graph-svg .sc-graph-label').length < n, LINES * PER_LINE); i++) await tv.page.waitForTimeout(250);
      await tv.page.waitForTimeout(300);
      const facts = await tv.page.evaluate(() => {
        const svg = document.querySelector('#graph-svg');
        const boxes = [...svg.querySelectorAll('.sc-graph-label')].map((n) => n.getBoundingClientRect()).sort((a, b) => a.top - b.top);
        // Glyferna, inte radrutan: en text tar ungefär 64 % av sin rutas höjd.
        const overlaps = boxes.slice(1).filter((r, i) => { const p = boxes[i], h = r.height * 0.18; return r.top + h < p.bottom - h && r.left < p.right && p.left < r.right; }).length;
        const children = [...svg.children];
        const lastTrains = children.findIndex((n) => n.querySelector?.('.sc-graph-line'));
        const firstTitle = children.findIndex((n) => n.classList?.contains('sc-graph-title'));
        return { scale: Number(getComputedStyle(svg).getPropertyValue('--graph-label-scale') || 1), overlaps,
          titlesOnTop: firstTitle > lastTrains && lastTrains >= 0,
          colours: new Set([...svg.querySelectorAll('.sc-graph-line')].map((n) => getComputedStyle(n).stroke)).size };
      });
      assert.ok(facts.scale < 1 && facts.scale >= 0.5, `${theme}: the names shrink with the rows (${facts.scale})`);
      assert.equal(facts.overlaps, 0, `${theme}: no station name over another`);
      assert.ok(facts.titlesOnTop, `${theme}: the line titles are drawn above the trains`);
      assert.ok(facts.colours >= 6, `${theme}: the train colours reach the screen (${facts.colours})`);
      assert.deepEqual(tv.violations, [], `${theme}: no CSP violations on the screen`);
      assert.deepEqual(tv.errors, []);
    } finally { await tv.browser.close(); }
  }
  console.log('graph-colours-live: ok');
})().catch((error) => { console.error(error); process.exit(1); });
