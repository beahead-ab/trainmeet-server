// Skärmarna Översikt och Banöversikt när samma tågnummer går flera gånger om
// dagen. Casper såg tåg 319 på linjen Charlottendal–Gässlösa med "ank 19:05"
// när klockan var 10:26, och "Inga avvikelser": skärmen tog den första turen
// med numret i tidtabellen, kvällens, i stället för den tur tåget kör. Nu
// pekar rörelsen som avgick ut turen (annars den närmast avgången eller
// klockan), och ankomst, sen ankomst och tågets plats på kartan följer den.
// Kör: node tests/js/overview-run.test.cjs
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const stations = [['cda', 'CDA', 'Charlottendal'], ['gsl', 'GSL', 'Gässlösa'], ['bsv', 'BSV', 'Borås Västra']].map(([id, code, name]) => ({ id, code, name }));
const connections = [{ id: 'c1', station_a_id: 'cda', station_b_id: 'gsl', track_type: 'double' }, { id: 'c2', station_a_id: 'gsl', station_b_id: 'bsv', track_type: 'single' }];
const stop = (station_id, stop_order, arrival_time, departure_time) => ({ station_id, stop_order, arrival_time, departure_time });
// Kvällsturen först i bilden, som i Caspers tidtabell.
const services = [
  { id: 's-319-pm', train_number: '319', days: 'Dagl', stops: [stop('cda', 0, null, '18:50'), stop('gsl', 1, '19:05', '19:06'), stop('bsv', 2, '19:20', null)] },
  { id: 's-319-am', train_number: '319', days: 'Dagl', stops: [stop('cda', 0, null, '10:20'), stop('gsl', 1, '10:40', '10:41'), stop('bsv', 2, '10:55', null)] },
  { id: 's-42', train_number: '42', days: 'Dagl', stops: [stop('bsv', 0, null, '10:32'), stop('gsl', 1, '10:36', '10:37'), stop('cda', 2, '10:50', null)] },
];
const trains = services.flatMap((s) => s.stops.map((st) => ({ id: `m-${s.id}-${st.station_id}`, service_id: s.id, train_number: s.train_number, station_id: st.station_id, arrival_time: st.arrival_time, departure_time: st.departure_time })));
const routes = services.flatMap((s) => s.stops.map((st) => ({ ...st, train_number: s.train_number, service_id: s.id })));
const seconds = (value) => value.split(':').reduce((sum, part) => sum * 60 + Number(part), 0);

// display({ time, movement, departed }) – tåg 319 ute Charlottendal → Gässlösa, 42 inne i Borås Västra.
const display = ({ time = '10:26:00', movement = 'm-s-319-am-cda', departed = null } = {}) => () => {
  const extra = { ...(movement ? { movement_id: movement } : {}), ...(departed !== null ? { departed_seconds: seconds(departed) } : {}) };
  return { data: {
    clock: { configured: true, running: false, time, speed: 5, source: 'internal', available: true, can_control: true },
    meet: { id: 'meet-1', name: 'Köra tåg 2026' }, active_day: 'Dagl', publication_id: 'pub-1', stations, connections, routes, services, trains,
    train_positions: [{ train_number: '319', status: 'connection', connection_id: 'c1', from_station_id: 'cda', to_station_id: 'gsl', ...extra },
      { train_number: '42', status: 'station', station_id: 'bsv' }],
    connection_states: [{ id: 'c1', state: 'occupied', channels: [{ train_number: '319', from_station_id: 'cda', to_station_id: 'gsl', state: 'occupied', ...extra }] }, { id: 'c2', state: 'free', channels: [] }],
    connection: { screens: [] }, display: { graph_station_order: ['cda', 'gsl', 'bsv'], topology_branch_station_ids: [] } } };
};

const overview = (page) => page.evaluate(() => {
  const row = document.querySelector('#dashboard-view .dash-row--line');
  const text = (selector) => document.querySelector(selector)?.textContent.trim();
  return {
    train: row?.querySelector('b')?.textContent, where: row?.querySelector('.dash-what')?.textContent, due: row?.querySelector('.dash-in')?.textContent.trim(),
    late: Boolean(row?.querySelector('.dash-in.is-late')), status: text('#dashboard-view .dash-status'), lateStatus: Boolean(document.querySelector('#dashboard-view .dash-status.is-late')),
    stats: [...document.querySelectorAll('#dashboard-view .dashboard-stat b')].map((b) => b.textContent),
  };
});

async function overviewAt(options, where) {
  const screen = await open({ route: '/display/dashboard', api: { '/v1/display': display(options) } });
  try {
    await screen.page.waitForSelector('#dashboard-view .dash-row--line');
    const seen = await overview(screen.page);
    assert.deepEqual(screen.errors, [], where);
    return seen;
  } finally { await screen.browser.close(); }
}

(async () => {
  // Översikt: morgonturens ankomst, inte kvällens, och ingen avvikelse.
  const morning = await overviewAt({ departed: '10:21:00' }, 'overview');
  assert.deepEqual([morning.train, morning.where, morning.due, morning.late], ['319', 'Charlottendal → Gässlösa', 'ank 10:40', false]);
  assert.ok(morning.status.startsWith('Inga avvikelser'), morning.status);
  assert.deepEqual([morning.stats[0], morning.stats[3]], ['1', '0'], 'one train on the line, no deviation');
  // Ankomsten passerad: sen ankomst på just den turen.
  const late = await overviewAt({ time: '10:50:00', departed: '10:21:00' }, 'late');
  assert.deepEqual([late.due, late.late, late.lateStatus, late.status, late.stats[3]], ['ank 10:40', true, true, '1 sen ankomst', '1']);
  // Ett äldre läge utan rörelse: turen närmast den faktiska avgången, annars närmast klockan.
  assert.equal((await overviewAt({ movement: null, departed: '10:21:00' }, 'older position')).due, 'ank 10:40');
  assert.equal((await overviewAt({ movement: null }, 'older position, planned')).due, 'ank 10:40');
  assert.equal((await overviewAt({ movement: null, time: '18:55:00' }, 'evening')).due, 'ank 19:05');
  // Kvällsturen när det är den rörelsen som avgick.
  assert.equal((await overviewAt({ movement: 'm-s-319-pm-cda' }, 'evening movement')).due, 'ank 19:05');

  // Banöversikt: remsan säger samma ankomst, och tåget har kommit en bit på
  // sträckan efter morgonturens planerade avgång 10:20 – förut stod det kvar
  // vid Charlottendal, eftersom kvällsturens avgång 18:50 låg före klockan.
  const tv = await open({ route: '/display/topology', api: { '/v1/display': display({ time: '10:36:00' }) } });
  try {
    await tv.page.waitForFunction(() => document.querySelector('#topology-svg .topology-train[data-train-number="319"]') && document.querySelector('#topology-online .sc-online__due'));
    await tv.page.waitForTimeout(200);
    const map = await tv.page.evaluate(() => {
      const centre = (element) => { const b = element.getBoundingClientRect(); return (b.left + b.right) / 2; };
      const station = (name) => centre(document.querySelector(`#topology-svg .topology-node[aria-label^="${name},"] .topology-station`));
      return { due: document.querySelector('#topology-online .sc-online__due').textContent.trim(), line: document.querySelector('#topology-online .sc-online__line').textContent.trim(),
        tag: centre(document.querySelector('#topology-svg .topology-train[data-train-number="319"]')), cda: station('Charlottendal'), gsl: station('Gässlösa') };
    });
    assert.equal(map.due, 'ank 10:40');
    assert.equal(map.line, '319Charlottendal → Gässlösa');
    const towards = Math.sign(map.gsl - map.cda);
    assert.ok(towards * (map.tag - (map.cda + map.gsl) / 2) > 0, `the train is past the middle of the section (${JSON.stringify(map)})`);
    assert.deepEqual(tv.errors, []);
    assert.deepEqual(tv.violations, []);
  } finally { await tv.browser.close(); }
  console.log('overview-run: ok');
})().catch((error) => { console.error(error); process.exit(1); });
