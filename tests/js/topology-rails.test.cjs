// Bankartan och tågdiagrammet efter Caspers skärmbilder 2026-10-07: räls med syllar,
// dubbelspår som två spår, stationen en bricka som täcker sina spår, bara namn (inga
// koder), antalet tåg inne som en liten bricka vid stationen, inga namn som krockar, ingen
// ram på skärmarna och diagrammets fönster en halvtimme före nu. Tågen rör sig efter
// träffklockan och går på dubbelspårets vänstra eller högra spår efter träffens val.
// Banan liknar Caspers: 20 stationer, tre grenar och dubbelspår kring Charlottendal.
// Kör: node tests/js/topology-rails.test.cjs
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const S = [['fab', 'FÄB', 'Fäboda'], ['fag', 'FÄG', 'Fäboda grusgrop'], ['snm', 'SNM', 'Snöflemåla'], ['kbg', 'KBG', 'Kristineberg'], ['avk', 'ÅVK', 'Ångviken'],
  ['sal', 'SAL', 'Salsborg'], ['cda', 'CDA', 'Charlottendal'], ['gsl', 'GSL', 'Gässlösa'], ['bsv', 'BSV', 'Borås Västra'], ['sns', 'SNS', 'Syltenäs'], ['mfd', 'MFD', 'Mariefred'],
  ['odl', 'ODL', 'Oredal'], ['asb', 'ÅSB', 'Åssjöbo'], ['ryt', 'RYT', 'Ryr timmer'], ['syd', 'SYD', 'Sydpolen'], ['vo', 'VÖ', 'Växjö'], ['vst', 'VST', 'Vagnsta'],
  ['bea', 'BEA', 'Bea Logistikpark'], ['r', 'R', 'Rotebro'], ['nav', 'NÄV', 'Nävhyttan']];
const stations = S.map(([id, code, name]) => ({ id, code, name }));
const L = [['fab', 'fag'], ['fag', 'snm'], ['snm', 'kbg'], ['kbg', 'avk'], ['avk', 'sal'], ['sal', 'cda', 2], ['cda', 'gsl', 2], ['gsl', 'bsv', 2], ['bsv', 'sns'], ['sns', 'mfd'],
  ['mfd', 'odl'], ['odl', 'asb'], ['kbg', 'ryt'], ['ryt', 'syd'], ['sns', 'vo'], ['cda', 'vst', 2], ['vst', 'bea', 2], ['bea', 'r', 2], ['r', 'nav', 2]];
const connections = L.map(([a, b, n], i) => ({ id: `c${i}`, station_a_id: a, station_b_id: b, track_type: n === 2 ? 'double' : 'single' }));
const doubles = connections.filter((c) => c.track_type === 'double').length;
const order = ['fab', 'fag', 'snm', 'kbg', 'avk', 'sal', 'cda', 'vst', 'bea', 'r', 'nav', 'gsl', 'bsv', 'sns', 'vo', 'mfd', 'odl', 'asb', 'ryt', 'syd'];
const hhmm = (minute) => `${String(Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`;
const T = [['93', -15, ['cda', 'sal', 'avk', 'kbg', 'snm']], ['92', 10, ['sal', 'cda', 'gsl', 'bsv']], ['3571', 10, ['kbg', 'avk', 'sal', 'cda', 'gsl', 'bsv', 'sns', 'mfd']],
  ['8782', 10, ['sns', 'bsv', 'gsl', 'cda', 'sal', 'avk', 'kbg', 'ryt']], ['7101', -15, ['r', 'bea', 'vst', 'cda']], ['4202', 40, ['sns', 'mfd', 'odl']]];
const services = T.map(([n, start, path]) => ({ id: `s-${n}`, train_number: n, days: 'Dagl',
  stops: path.map((station_id, i) => { const a = 360 + start + i * 9; return { station_id, stop_order: i,
    arrival_time: i ? hhmm(a) : null, departure_time: i < path.length - 1 ? hhmm(a + 1) : null }; }) }));
const routes = services.flatMap((s) => s.stops.map((st) => ({ train_number: s.train_number, station_id: st.station_id, arrival_time: st.arrival_time, departure_time: st.departure_time, service_id: s.id })));
// Ute: 8782 och 3571 åt var sitt håll på dubbelspåret Gässlösa–Charlottendal (3571 klart, inte
// avgått), 93 på enkelspår. Inne: två tåg i Charlottendal, ett i Syltenäs.
const channels = { c6: [['8782', 'gsl', 'cda', 'occupied'], ['3571', 'cda', 'gsl', 'reserved']], c3: [['93', 'avk', 'kbg', 'occupied']] };
const positions = [{ train_number: '92', station_id: 'cda', status: 'station' }, { train_number: '95', station_id: 'cda', status: 'station' }, { train_number: '4202', station_id: 'sns', status: 'station' },
  { train_number: '8782', status: 'connection', connection_id: 'c6', from_station_id: 'gsl', to_station_id: 'cda' }, { train_number: '93', status: 'connection', connection_id: 'c3', from_station_id: 'avk', to_station_id: 'kbg' }];
// display({ time, running, speed, side, departed }) – departed: { tågnummer: träffklockans sekunder vid avgången }.
const display = ({ time = '06:20:00', running = false, speed = 1, side, departed = {} } = {}) => () => ({ data: {
  clock: { configured: true, running, time, speed, source: 'internal', available: true, can_control: true },
  meet: { id: 'meet-1', name: 'Grimslöv 2027' }, active_day: 'Dagl', publication_id: 'pub-9', stations, connections, routes, services,
  train_positions: positions.map((p) => (p.status === 'connection' && departed[p.train_number] !== undefined ? { ...p, departed_seconds: departed[p.train_number] } : p)),
  connection_states: connections.map((c) => ({ id: c.id, state: channels[c.id] ? 'occupied' : 'free',
    channels: (channels[c.id] || []).map(([train_number, from_station_id, to_station_id, state]) => ({ train_number, from_station_id, to_station_id, state,
      ...(departed[train_number] !== undefined && state === 'occupied' ? { departed_seconds: departed[train_number] } : {}) })) })),
  connection: { screens: [] }, display: { graph_station_order: order, topology_branch_station_ids: ['ryt', 'syd', 'vo', 'vst', 'bea', 'r', 'nav'],
    ...(side ? { traffic_side: side } : {}) } } });
const seconds = (value) => value.split(':').reduce((sum, part) => sum * 60 + Number(part), 0);

// Allt kartan ritar, mätt på skärmen.
const inspect = (page, selector) => page.evaluate((selector) => {
  const svg = document.querySelector(selector);
  const box = (element) => { const b = element.getBoundingClientRect(); return { x1: b.left, y1: b.top, x2: b.right, y2: b.bottom }; };
  const node = (name) => svg.querySelector(`.topology-node[aria-label^="${name},"]`);
  const station = (name) => { const r = node(name).querySelector('.topology-station'); return { tag: r.tagName, w: Number(r.getAttribute('width')), h: Number(r.getAttribute('height')) }; };
  const tag = (number) => { const t = svg.querySelector(`.topology-train[data-train-number="${number}"]`); return t && { kind: [...t.classList].find((c) => ['on-line', 'cleared', 'at-station'].includes(c)), ...box(t) }; };
  const centreY = (name) => { const b = box(node(name).querySelector('.topology-station')); return (b.y1 + b.y2) / 2; };
  return {
    classes: [...svg.classList],
    tracks: [...svg.querySelectorAll('g.topology-track')].map((g) => ({ double: g.classList.contains('double'), lanes: g.querySelectorAll('.topology-lane').length,
      rails: g.querySelectorAll('.topology-rail').length, sleepers: g.querySelectorAll('.topology-sleepers').length })),
    codes: svg.querySelectorAll('.topology-code').length,
    names: [...svg.querySelectorAll('.topology-name')].map((n) => ({ text: n.textContent, ...box(n) })),
    bricks: [...svg.querySelectorAll('.topology-station')].map(box),
    counts: Object.fromEntries([...svg.querySelectorAll('.topology-count')].map((c) => [c.closest('.topology-node').getAttribute('aria-label').split(',')[0], c.textContent])),
    badges: [...svg.querySelectorAll('.topology-count-badge')].map((b) => ({ station: b.closest('.topology-node').getAttribute('aria-label').split(',')[0], ...box(b) })),
    salsborg: station('Salsborg'), charlottendal: station('Charlottendal'), angviken: station('Ångviken'),
    lineY: centreY('Gässlösa'), t8782: tag('8782'), t3571: tag('3571'), t93: tag('93'),
  };
}, selector);

const overlap = (a, b, margin = 0) => a.x1 < b.x2 + margin && b.x1 < a.x2 + margin && a.y1 < b.y2 + margin && b.y1 < a.y2 + margin;

function checkMap(map, where) {
  // Räls: dubbelspår är två spår med syllar, enkelspår ett.
  assert.equal(map.tracks.filter((t) => t.double).length, doubles, `${where}: every double track drawn as double`);
  for (const t of map.tracks) {
    assert.equal(t.lanes, t.double ? 2 : 1, `${where}: lanes ${JSON.stringify(t)}`);
    assert.equal(t.rails, t.lanes * 2, `${where}: two rails a lane`);
    assert.equal(t.sleepers, t.lanes, `${where}: sleepers under each lane`);
  }
  // Stationen täcker sina spår: avlång vid dubbelspår i sidled, rundad kvadrat vid en korsning, cirkel annars.
  assert.equal(map.angviken.tag, 'rect');
  assert.equal(map.angviken.w, map.angviken.h, `${where}: a station on single track is round`);
  assert.ok(map.salsborg.h > map.salsborg.w, `${where}: a station on double track stands upright ${JSON.stringify(map.salsborg)}`);
  assert.ok(map.charlottendal.h > map.angviken.h && map.charlottendal.w > map.angviken.w, `${where}: a crossing of double tracks is a wide brick`);
  // Bara namn; antalet tåg inne står bara där det finns tåg.
  assert.equal(map.codes, 0, `${where}: no station codes on the map`);
  assert.equal(map.names.length, stations.length);
  assert.deepEqual(map.counts, { Charlottendal: '2', 'Syltenäs': '1' }, `${where}: the number of trains inside, only where there are any`);
  // Inga namn på varandra, på en station eller på en antalsbricka.
  for (const [i, a] of map.names.entries()) {
    for (const b of map.names.slice(i + 1)) assert.ok(!overlap(a, b), `${where}: ${a.text} over ${b.text}`);
    for (const brick of map.bricks) assert.ok(!overlap(a, brick), `${where}: ${a.text} over a station`);
    for (const badge of map.badges) assert.ok(!overlap(a, badge), `${where}: ${a.text} over the count at ${badge.station}`);
  }
  // Antalet inne är en liten bricka vid stationens hörn, som i skissen, inte i stationen.
  assert.deepEqual(map.badges.map((b) => b.station).sort(), ['Charlottendal', 'Syltenäs'], `${where}: a count badge where trains are inside`);
  // På dubbelspår går tåget på sitt eget spår, till vänster i färdriktningen: 8782 mot
  // Charlottendal och 3571 därifrån ligger på var sin sida om mittlinjen och täcker inte varandra.
  assert.equal(map.t8782.kind, 'on-line');
  assert.equal(map.t3571.kind, 'cleared');
  const mid = (t) => (t.y1 + t.y2) / 2;
  const leftward = Math.sign(mid(map.t8782) - map.lineY), rightward = Math.sign(mid(map.t3571) - map.lineY);
  assert.ok(leftward !== 0 && rightward === -leftward, `${where}: opposite lanes (${mid(map.t8782)} / ${mid(map.t3571)} around ${map.lineY})`);
  assert.ok(!overlap(map.t8782, map.t3571), `${where}: the two tags do not cover each other`);
  // På enkelspår ligger tåget på linjen.
  assert.ok(Math.abs(mid(map.t93) - map.lineY) < 1, `${where}: on single track the tag rides the line`);
}

(async () => {
  for (const theme of ['dark', 'light']) {
    // Drift, i riktiga pixlar.
    const drift = await open({ route: '/drift', theme, api: { '/v1/display': display() } });
    try {
      await drift.page.waitForFunction(() => document.querySelectorAll('#overview-topology .topology-name').length === 20 && document.querySelector('#overview-topology .topology-train'));
      await drift.page.waitForTimeout(300);
      const map = await inspect(drift.page, '#overview-topology');
      assert.ok(map.classes.includes('topology-kr') && !map.classes.includes('topology-screen'));
      checkMap(map, `Drift (${theme})`);
      // Diagrammet på Drift: namnen när det finns plats, aldrig namn och kod båda.
      const graph = await drift.page.evaluate(() => ({ names: document.querySelectorAll('#overview-graph .lbl').length, codes: document.querySelectorAll('#overview-graph .cnt').length }));
      assert.deepEqual(graph, { names: 20, codes: 0 }, 'Drift diagram: names only');
      assert.deepEqual(drift.errors, []);
      assert.deepEqual(drift.violations, []);
    } finally { await drift.browser.close(); }

    // Banöversikt på skärmen: samma ritning, större, och ingen ram runt kartan.
    const tv = await open({ route: '/display/topology', theme, api: { '/v1/display': display() } });
    try {
      await tv.page.waitForFunction(() => document.querySelectorAll('#topology-svg .topology-name').length === 20 && document.querySelector('#topology-svg .topology-train'));
      await tv.page.waitForTimeout(300);
      const map = await inspect(tv.page, '#topology-svg');
      assert.ok(map.classes.includes('topology-kr') && map.classes.includes('topology-screen'), map.classes.join(' '));
      checkMap(map, `Banöversikt (${theme})`);
      const frame = await tv.page.evaluate(() => { const s = getComputedStyle(document.querySelector('#display-stage .sc-content')); return { border: s.borderTopWidth, radius: s.borderTopLeftRadius, background: s.backgroundColor }; });
      assert.deepEqual(frame, { border: '0px', radius: '0px', background: 'rgba(0, 0, 0, 0)' }, 'no card or frame around the map');
      // Kartan fyller bredden: mer än tre fjärdedelar av ritytan mellan första och sista namnet.
      const span = await tv.page.evaluate(() => { const svg = document.querySelector('#topology-svg'); const xs = [...svg.querySelectorAll('.topology-station')].map((s) => s.getBBox()).flatMap((b) => [b.x, b.x + b.width]); return (Math.max(...xs) - Math.min(...xs)) / Number(svg.getAttribute('viewBox').split(' ')[2]); });
      assert.ok(span > 0.75, `the map uses the width (${span})`);
      assert.deepEqual(tv.errors, []);
      assert.deepEqual(tv.violations, []);
    } finally { await tv.browser.close(); }

    // Tågdiagrammet på skärmen: bara namn, fönstret en halvtimme före nu, ingen ram.
    const graph = await open({ route: '/display/graph', theme, api: { '/v1/display': display() } });
    try {
      await graph.page.waitForFunction(() => document.querySelectorAll('#graph-svg .sc-graph-label').length === 20);
      const seen = await graph.page.evaluate(() => {
        const labels = [...document.querySelectorAll('#graph-svg .sc-graph-label')];
        const boxes = labels.map((l) => { const b = l.getBBox(); return { x1: b.x, y1: b.y, x2: b.x + b.width, y2: b.y + b.height }; });
        const s = getComputedStyle(document.querySelector('#graph-scroll'));
        return { names: labels.map((l) => l.textContent), codes: document.querySelectorAll('#graph-svg .sc-graph-code').length, boxes,
          subtitle: document.querySelector('#screen-meet .sc-subtitle').textContent, frame: { border: s.borderTopWidth, background: s.backgroundColor } };
      });
      assert.deepEqual(seen.names, order.map((id) => stations.find((s) => s.id === id).name), 'the station names in the diagram order');
      assert.equal(seen.codes, 0, 'no station codes in the diagram');
      for (const [i, a] of seen.boxes.entries()) for (const b of seen.boxes.slice(i + 1)) assert.ok(!overlap(a, b), `names on top of each other: ${seen.names[i]}`);
      assert.match(seen.subtitle, /05:45–08:45/, `three hours from half an hour before 06:20: ${seen.subtitle}`);
      assert.deepEqual(seen.frame, { border: '0px', background: 'rgba(0, 0, 0, 0)' }, 'no frame around the diagram');
      assert.deepEqual(graph.errors, []);
    } finally { await graph.browser.close(); }
  }

  // Ett avgånget tåg rör sig mot nästa station i takt med träffklockan, från
  // den faktiska avgången. 93 Ångviken–Kristineberg: planerat 06:04–06:12.
  const at = async (options) => {
    const drift = await open({ route: '/drift', api: { '/v1/display': display(options) } });
    try {
      await drift.page.waitForFunction(() => document.querySelector('#overview-topology .topology-train[data-train-number="93"]'));
      const where = () => drift.page.evaluate(() => {
        const centre = (element) => { const b = element.getBoundingClientRect(); return { x: (b.left + b.right) / 2, y: (b.top + b.bottom) / 2 }; };
        const svg = document.querySelector('#overview-topology');
        const station = (name) => centre(svg.querySelector(`.topology-node[aria-label^="${name},"] .topology-station`));
        const from = station('Ångviken'), to = station('Kristineberg'), tag = centre(svg.querySelector('.topology-train[data-train-number="93"]'));
        return (tag.x - from.x) / (to.x - from.x);
      });
      return { first: await where(), later: async (ms) => { await drift.page.waitForTimeout(ms); return where(); }, close: () => drift.browser.close(), errors: drift.errors };
    } catch (error) { await drift.browser.close(); throw error; }
  };
  // Andelen mäts mellan där tåget står vid avgången och där det står framme (brickan
  // stannar före stationerna, så sträckan den glider är kortare än mellan stationerna).
  const departed = { 93: seconds('06:05:00') };
  const runs = { start: await at({ time: '06:05:00', departed }), early: await at({ time: '06:07:00', departed }),
    late: await at({ time: '06:11:00', departed }), end: await at({ time: '06:30:00', departed }), planned: await at({ time: '06:07:00' }) };
  try {
    const share = (run) => (run.first - runs.start.first) / (runs.end.first - runs.start.first);
    assert.ok(runs.end.first > runs.start.first + 0.1, `it moves towards Kristineberg (${runs.start.first} → ${runs.end.first})`);
    assert.ok(Math.abs(share(runs.early) - 0.25) < 0.05, `two of eight minutes after the departure (${share(runs.early)})`);
    assert.ok(Math.abs(share(runs.late) - 0.75) < 0.05, `six of eight minutes (${share(runs.late)})`);
    // Utan en registrerad avgång gäller den planerade 06:04: tre minuter av åtta.
    assert.ok(Math.abs(share(runs.planned) - 0.375) < 0.05, `the planned departure without a recorded one (${share(runs.planned)})`);
    assert.deepEqual(Object.values(runs).flatMap((run) => run.errors), []);
    // Medan klockan går flyttar tåget sig utan att kartan ritas om: 60× betyder en minut i sekunden.
    const running = await at({ time: '06:05:00', running: true, speed: 60, departed });
    try {
      // Varje mätning drygt en sekund efter den förra: tåget har flyttat sig varje gång.
      const samples = [running.first];
      for (let i = 0; i < 3; i += 1) samples.push(await running.later(1150));
      const shares = samples.map((x) => (x - runs.start.first) / (runs.end.first - runs.start.first));
      for (let i = 1; i < shares.length; i += 1) assert.ok(shares[i] > shares[i - 1] + 0.05, `it keeps moving every second (${shares.join(' → ')})`);
      assert.ok(shares.at(-1) < 1, `not yet at Kristineberg (${shares.at(-1)})`);
    } finally { await running.close(); }
  } finally { for (const run of Object.values(runs)) await run.close(); }
  // Tågdiagrammet på Drift går också: nu-linjen och klockan följer träffklockan
  // mellan uppdateringarna (app.js hämtar läget var 30:e sekund), och ett
  // avgånget tåg står på nu-linjen så långt upp på sträckan som det kommit.
  const graphAt = async ({ time, running = false, speed = 1, departed: when = {} }) => {
    const clock = { configured: true, running, time, speed, source: 'internal', available: true, can_control: true, style: 'digital', show_seconds: true };
    const drift = await open({ route: '/drift', running, api: { '/v1/display': display({ time, running, speed, departed: when }),
      '/v1/clock': (request) => (request.method() === 'GET' ? { data: clock } : undefined) } });
    await drift.page.waitForFunction(() => document.querySelector('#overview-graph .tr-tag[data-train-number="93"]'));
    const read = () => drift.page.evaluate(() => {
      const svg = document.querySelector('#overview-graph');
      const rect = svg.querySelector('.tr-tag[data-train-number="93"] rect');
      const row = (name) => Number([...svg.querySelectorAll('text.lbl')].find((t) => t.textContent === name).getAttribute('y')) - 4.5;
      return { right: Number(rect.getAttribute('x')) + Number(rect.getAttribute('width')), middle: Number(rect.getAttribute('y')) + 11,
        now: Number(svg.querySelector('line.now')?.getAttribute('x1')), from: row('Ångviken'), to: row('Kristineberg'),
        clock: document.querySelector('#overview-clock').textContent };
    });
    return { read, page: drift.page, close: () => drift.browser.close(), errors: drift.errors };
  };
  const still = await graphAt({ time: '06:09:45', departed });
  try {
    const g = await still.read();
    assert.ok(Math.abs(g.right + 6 - g.now) < 0.5, `the tag stands at the now line (${g.right + 6} / ${g.now})`);
    assert.ok(Math.abs((g.middle - g.from) / (g.to - g.from) - 4.75 / 8) < 0.02, `4¾ of eight minutes after 06:05 (${JSON.stringify(g)})`);
    assert.equal(await still.page.evaluate(() => document.querySelector('#overview-graph .nowt').textContent), '06:09', 'the minute, as on the clock');
    assert.deepEqual(still.errors, []);
  } finally { await still.close(); }
  const moving = await graphAt({ time: '06:06:00', running: true, speed: 60, departed });
  try {
    const first = await moving.read();
    await moving.page.waitForTimeout(3200);
    const later = await moving.read();
    assert.ok(later.now > first.now, `the now line moves with the clock (${first.now} → ${later.now})`);
    assert.ok(Math.abs(later.right + 6 - later.now) < 0.5, 'and the tag with it');
    const share = (g) => (g.middle - g.from) / (g.to - g.from);
    assert.ok(share(later) - share(first) > 0.25, `the tag climbs towards Kristineberg (${share(first)} → ${share(later)})`);
    assert.notEqual(later.clock, first.clock, `the clock runs between updates (${first.clock} → ${later.clock})`);
    // Läst i samma ögonblick: klockan och nu-linjen ritas i samma sekundslag.
    const [label, shown] = await moving.page.evaluate(() => [document.querySelector('#overview-graph .nowt').textContent,
      document.querySelector('#overview-clock').textContent]);
    assert.equal(label, shown, 'the now line says what the clock says');
  } finally { await moving.close(); }

  // Skärmens tågdiagram: samma läge, räknat från den faktiska avgången.
  const screen = await open({ route: '/display/graph', api: { '/v1/display': display({ time: '06:09:00', departed }) } });
  try {
    await screen.page.waitForFunction(() => [...document.querySelectorAll('#graph-svg .graph-train-group')]
      .some((el) => el.dataset.trainNumber === '93' && el.querySelector('.sc-graph-tag')));
    const g = await screen.page.evaluate(() => {
      const svg = document.querySelector('#graph-svg');
      const group = [...svg.querySelectorAll('.graph-train-group')].find((el) => el.dataset.trainNumber === '93' && el.querySelector('.sc-graph-tag'));
      const rect = group.querySelector('.sc-graph-tag');
      const row = (name) => Number([...svg.querySelectorAll('.sc-graph-label')].find((t) => t.textContent === name).getAttribute('y')) - 4.5;
      return { middle: Number(rect.getAttribute('y')) + 9, from: row('Ångviken'), to: row('Kristineberg') };
    });
    assert.ok(Math.abs((g.middle - g.from) / (g.to - g.from) - 0.5) < 0.02, `the screen's graph counts from the actual departure too (${JSON.stringify(g)})`);
    assert.deepEqual(screen.errors, []);
  } finally { await screen.browser.close(); }

  // Högertrafik: tågen byter spår. 8782 österut ligger då under mittlinjen, 3571 västerut över.
  for (const [side, sign] of [['left', -1], ['right', 1]]) {
    const drift = await open({ route: '/drift', api: { '/v1/display': display({ side }) } });
    try {
      await drift.page.waitForFunction(() => document.querySelector('#overview-topology .topology-train[data-train-number="8782"]'));
      const map = await inspect(drift.page, '#overview-topology');
      const mid = (t) => (t.y1 + t.y2) / 2;
      assert.equal(Math.sign(mid(map.t8782) - map.lineY), sign, `${side}-hand traffic: 8782 eastbound on its own track`);
      assert.equal(Math.sign(mid(map.t3571) - map.lineY), -sign, `${side}-hand traffic: 3571 westbound on the other`);
    } finally { await drift.browser.close(); }
  }

  // Diagrammet på Drift i en telefon: koderna i stället för namnen, aldrig båda.
  const phone = await open({ route: '/drift', width: 390, height: 844, api: { '/v1/display': display() } });
  try {
    await phone.page.waitForFunction(() => document.querySelectorAll('#overview-graph .cnt').length === 20);
    assert.equal(await phone.page.locator('#overview-graph .lbl').count(), 0, 'a phone shows the codes only');
    assert.equal(await phone.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'no sideways scrolling on a phone');
  } finally { await phone.browser.close(); }
  console.log('topology-rails: ok');
})().catch((error) => { console.error(error); process.exit(1); });
