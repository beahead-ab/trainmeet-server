// Två tåg som möts på dubbelspår (Casper 2026-10-08): båda har avgått och är mitt
// på samma sträcka samtidigt. Vart och ett går på sitt eget spår, taggarna täcker
// inte varandra, och de rör sig åt var sitt håll när klockan går. Vänstertrafik
// (Sverige) och högertrafik (Danmark) byter sida. Drift, skärmarnas Banöversikt
// och deltagarvyn. Kör: node tests/js/double-track-meet.test.cjs
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const stations = [{ id: 'a', code: 'ALV', name: 'Alvesta' }, { id: 'b', code: 'BOR', name: 'Bor' }];
const connections = [{ id: 'ab', station_a_id: 'a', station_b_id: 'b', track_type: 'double' }];
// 11 A 06:10 → B 06:20 och 12 B 06:10 → A 06:20: klockan 06:15 möts de mitt på sträckan.
const services = [
  { id: 's11', train_number: '11', days: 'Dagl', stops: [{ station_id: 'a', departure_time: '06:10', stop_order: 0 }, { station_id: 'b', arrival_time: '06:20', stop_order: 1 }] },
  { id: 's12', train_number: '12', days: 'Dagl', stops: [{ station_id: 'b', departure_time: '06:10', stop_order: 0 }, { station_id: 'a', arrival_time: '06:20', stop_order: 1 }] }];
const routes = services.flatMap((s) => s.stops.map((st) => ({ ...st, train_number: s.train_number, service_id: s.id })));
const departed = 6 * 3600 + 10 * 60;
// Som serverns klocka: går den, har den kommit längre vid varje ny hämtning.
const display = (time, running, side) => { const start = Date.now(); return () => ({ data: {
  clock: { configured: true, running, speed: 1, source: 'internal', available: true, can_control: true,
    time: running ? clockAt(time, (Date.now() - start) / 1000) : time },
  meet: { id: 'meet-1', name: 'Grimslöv 2027' }, active_day: 'Dagl', publication_id: 'pub-9', stations, connections, routes, services, trains: [],
  train_positions: [{ train_number: '11', status: 'connection', connection_id: 'ab', from_station_id: 'a', to_station_id: 'b', departed_seconds: departed },
    { train_number: '12', status: 'connection', connection_id: 'ab', from_station_id: 'b', to_station_id: 'a', departed_seconds: departed }],
  connection_states: [{ id: 'ab', state: 'occupied', channels: [
    { train_number: '11', from_station_id: 'a', to_station_id: 'b', state: 'occupied', departed_seconds: departed },
    { train_number: '12', from_station_id: 'b', to_station_id: 'a', state: 'occupied', departed_seconds: departed }] }],
  connection: { screens: [] }, display: { graph_station_order: ['a', 'b'], ...(side ? { traffic_side: side } : {}) } } }); };
const clockAt = (time, plus) => { const total = Math.floor(time.split(':').reduce((sum, part) => sum * 60 + Number(part), 0) + plus);
  return [Math.floor(total / 3600), Math.floor(total / 60) % 60, total % 60].map((v) => String(v).padStart(2, '0')).join(':'); };

const measure = (page, selector) => page.evaluate((selector) => {
  const svg = document.querySelector(selector);
  const box = (el) => { const b = el.getBoundingClientRect(); return { x1: b.left, y1: b.top, x2: b.right, y2: b.bottom, cx: (b.left + b.right) / 2, cy: (b.top + b.bottom) / 2 }; };
  const tag = (n) => { const t = svg.querySelector(`.topology-train[data-train-number="${n}"]`); return t && { kind: [...t.classList].find((c) => ['on-line', 'cleared', 'at-station'].includes(c)), ...box(t) }; };
  const nodes = [...svg.querySelectorAll('.topology-node .topology-station')].map(box);
  return { t11: tag('11'), t12: tag('12'), a: nodes[0], b: nodes[1] };
}, selector);
const overlap = (p, q) => p.x1 < q.x2 && q.x1 < p.x2 && p.y1 < q.y2 && q.y1 < p.y2;

function passing(m, where, side) {
  assert.ok(m.t11 && m.t12, `${where}: both trains are drawn`);
  assert.deepEqual([m.t11.kind, m.t12.kind], ['on-line', 'on-line'], `${where}: both have left`);
  assert.ok(!overlap(m.t11, m.t12), `${where}: the two tags do not cover each other ${JSON.stringify([m.t11, m.t12])}`);
  // Mitt på sträckan, vid samma ställe: de möts.
  const along = Math.abs(m.b.cx - m.a.cx) >= Math.abs(m.b.cy - m.a.cy) ? 'cx' : 'cy';
  const middle = (m.a[along] + m.b[along]) / 2, length = Math.abs(m.b[along] - m.a[along]);
  for (const t of [m.t11, m.t12]) assert.ok(Math.abs(t[along] - middle) < length * 0.2, `${where}: ${JSON.stringify(t)} near the middle ${middle}`);
  // Var sitt spår: på var sin sida om linjen mellan stationerna.
  const across = along === 'cx' ? 'cy' : 'cx';
  const line = (m.a[across] + m.b[across]) / 2;
  assert.ok(Math.sign(m.t11[across] - line) === -Math.sign(m.t12[across] - line) && m.t11[across] !== line, `${where}: opposite tracks (${side})`);
  return Math.sign(m.t11[across] - line);
}

(async () => {
  for (const [route, selector] of [['/drift', '#overview-topology'], ['/display/topology', '#topology-svg'], ['/', '#pv-topology']]) {
    const sides = {};
    for (const side of ['left', 'right']) {
      const view = await open({ route, loggedOut: route === '/', width: route === '/' ? 1280 : 1440, height: 900, api: { '/v1/display': display('06:15:00', false, side) } });
      try {
        await view.page.waitForFunction((sel) => document.querySelectorAll(`${sel} .topology-train`).length >= 2, selector);
        sides[side] = passing(await measure(view.page, selector), `${route} ${side}`, side);
        assert.deepEqual(view.errors, []);
      } finally { await view.browser.close(); }
    }
    assert.equal(sides.left, -sides.right, `${route}: left- and right-hand traffic swap the tracks`);
    // Klockan går: tågen glider åt var sitt håll.
    const view = await open({ route, loggedOut: route === '/', width: route === '/' ? 1280 : 1440, height: 900, api: { '/v1/display': display('06:14:00', true) } });
    try {
      await view.page.waitForFunction((sel) => document.querySelectorAll(`${sel} .topology-train`).length >= 2, selector);
      const before = await measure(view.page, selector);
      await view.page.waitForTimeout(4500);
      const after = await measure(view.page, selector);
      const axis = Math.abs(before.b.cx - before.a.cx) >= Math.abs(before.b.cy - before.a.cy) ? 'cx' : 'cy';
      const towardB = Math.sign(before.b[axis] - before.a[axis]);
      assert.ok((after.t11[axis] - before.t11[axis]) * towardB > 0, `${route}: 11 moves towards Bor`);
      assert.ok((after.t12[axis] - before.t12[axis]) * towardB < 0, `${route}: 12 moves towards Alvesta`);
      assert.deepEqual(view.errors, []);
    } finally { await view.browser.close(); }
  }
  // Tågrutter på Drift: det valda tåget rör sig på sin karta också.
  const drift = await open({ route: '/drift', width: 1440, height: 900, api: { '/v1/display': display('06:14:00', true) } });
  try {
    await drift.page.waitForFunction(() => document.querySelectorAll('#overview-topology .topology-train').length >= 2);
    await drift.page.evaluate(() => { document.querySelector('#drift-timetable-dialog').showModal(); selectOverviewTrain('11'); });
    await drift.page.waitForFunction(() => document.querySelector('#overview-route-map .topology-train[data-train-number="11"]'));
    const where = () => drift.page.evaluate(() => { const b = document.querySelector('#overview-route-map .topology-train[data-train-number="11"]').getBoundingClientRect(); return [b.left, b.top]; });
    const before = await where();
    await drift.page.waitForTimeout(4500);
    const after = await where();
    assert.ok(Math.hypot(after[0] - before[0], after[1] - before[1]) > 0.5, `the route map moves the train too (${before} → ${after})`);
    assert.deepEqual(drift.errors, []);
  } finally { await drift.browser.close(); }
  console.log('double-track-meet: ok');
})().catch((error) => { console.error(error); process.exitCode = 1; });
