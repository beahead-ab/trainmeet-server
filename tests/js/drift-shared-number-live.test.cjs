// Två tåg som delar nummer på Drift (Cloud #100, resten på Server): DSB IC 116
// står inne i Charlottendal och SJ X 116 är ute mot Vagnsta. Kartan, händelserna,
// tågpanelen och sökningen håller isär dem: taggen visar "IC 116" och "X 116",
// ett tryck på X 116 tänder bara det tåget och tågpanelen frågar efter just det
// (service_key). Tidtabellen i Inställningar visar en rättelse från Cloud som
// Clouds, inte som admins lokala ändringar. API:et är fixturen i kr-fixture.cjs.
// Kör: node tests/js/drift-shared-number-live.test.cjs [katalog för skärmbilder]
const assert = require('node:assert/strict');
const path = require('node:path');
const { open } = require('./kr-fixture.cjs');

const shots = process.argv[2] || null;
const label = (key, short) => ({ service_key: key, train_label: `${key} 116`, short_label: `${short} 116` });
const ic = { id: 's-116-ic', train_number: '116', days: 'Dagl', ...label('DSB IC', 'IC'), stops: [
  { station_id: 'lek', stop_order: 0, arrival_time: null, departure_time: '05:00' }, { station_id: 'cda', stop_order: 1, arrival_time: '05:10', departure_time: null }] };
const x = { id: 's-116-x', train_number: '116', days: 'Dagl', ...label('SJ X', 'X'), stops: [
  { station_id: 'cda', stop_order: 0, arrival_time: null, departure_time: '05:08' }, { station_id: 'vag', stop_order: 1, arrival_time: '05:25', departure_time: '05:26' },
  { station_id: 'mun', stop_order: 2, arrival_time: '05:36', departure_time: null }] };
const rows = [
  { id: 'ic-lek', service_id: ic.id, train_number: '116', station_id: 'lek', departure_time: '05:00' },
  { id: 'ic-cda', service_id: ic.id, train_number: '116', station_id: 'cda', arrival_time: '05:10' },
  { id: 'x-cda', service_id: x.id, train_number: '116', station_id: 'cda', departure_time: '05:08' },
  { id: 'x-vag', service_id: x.id, train_number: '116', station_id: 'vag', arrival_time: '05:25', departure_time: '05:26' },
  { id: 'x-mun', service_id: x.id, train_number: '116', station_id: 'mun', arrival_time: '05:36' },
];
const display = (data) => ({
  ...data,
  services: [...data.services, ic, x],
  routes: [...data.routes, ...[ic, x].flatMap((s) => s.stops.map((st) => ({ train_number: '116', station_id: st.station_id, arrival_time: st.arrival_time, departure_time: st.departure_time, service_id: s.id })))],
  trains: rows,
  movement_live: { 'ic-lek': { departure: 'departed', departed_seconds: 5 * 3600 }, 'ic-cda': { arrival: 'arrived', arrived_seconds: 5 * 3600 + 11 * 60 },
    'x-cda': { departure: 'departed', departed_seconds: 5 * 3600 + 8 * 60 } },
  train_positions: [{ train_number: '116', status: 'station', station_id: 'cda', movement_id: 'ic-cda' }, ...data.train_positions],
  connection_states: [...data.connection_states, { id: 'c5', state: 'occupied', channels: [{ train_number: '116', from_station_id: 'cda', to_station_id: 'vag', state: 'occupied', movement_id: 'x-cda', departed_seconds: 5 * 3600 + 8 * 60 }] }],
});
const trainCalls = [];
const trainDetail = (request, url) => {
  trainCalls.push(Object.fromEntries(url.searchParams));
  const service = url.searchParams.get('service_key') === 'SJ X' ? x : ic;
  return { data: { train_number: '116', service_key: service.service_key, active_day: 'Dagl', services: [{ service_id: service.id, train_type: 'Persontåg', delay_minutes: null,
    now: service === x ? { state: 'on_line', from_station_id: 'cda', to_station_id: 'vag', since: '05:08' } : { state: 'arrived', station_id: 'cda' },
    stops: service.stops.map((stop) => ({ ...stop, station_code: stop.station_id.toUpperCase(), movement_id: null, planned_track: '1', actual_track: null, arrival: 'none', departure: 'none' })) }] } };
};

(async () => {
  for (const [width, height] of [[1440, 900], [390, 844]]) {
    trainCalls.length = 0;
    const h = await open({ route: '/drift', width, height, running: false, display, api: { '/v1/train': trainDetail } });
    const { page } = h;
    page.setDefaultTimeout(10000);
    try {
      await page.locator('body[data-route-ready]').waitFor({ state: 'attached' });
      const tag = (key) => page.locator(`#overview-topology g.topology-train[data-train-number="${key}"]`);
      await tag('SJ X 116').waitFor({ state: 'attached' });
      // Taggen på linjen säger vilket tåg det är; numret ensamt är inte längre nyckeln.
      assert.equal(await tag('SJ X 116').locator('text.train-number').textContent(), 'X 116');
      assert.equal(await page.locator('#overview-topology g.topology-train[data-train-number="116"]').count(), 0);
      // Händelserna: X 116 med sin nyckel.
      await page.locator('#drift-upcoming button[data-train-number="SJ X 116"]').first().waitFor({ state: 'attached' });
      assert.equal(await page.locator('#drift-upcoming button[data-train-number="116"]').count(), 0);

      // Välj X 116 på kartan: bara det tänds, och tågpanelen frågar efter just det.
      await tag('SJ X 116').dispatchEvent('click');
      await page.locator('#drift-train-detail:not([hidden])').waitFor();
      await page.waitForFunction(() => /X 116/.test(document.querySelector('#drift-train-detail')?.textContent || ''));
      assert.match(await page.locator('#drift-train-detail').innerText(), /X 116/);
      assert.ok(trainCalls.some((call) => call.number === '116' && call.service_key === 'SJ X'), JSON.stringify(trainCalls));
      assert.match(await tag('SJ X 116').getAttribute('class'), /\bselected\b/);
      // IC 116 står inne och syns på Drifts karta bara när det är valt.
      assert.equal(await tag('DSB IC 116').count(), 0);
      if (shots) await page.screenshot({ path: path.join(shots, `drift-delat-nummer-${width}.png`), fullPage: false });

      // Sök "ic": IC 116, och valet tänder det i stället.
      if (width >= 1024) {
        await page.locator('#kr-search').fill('116');
        await page.locator('#kr-search-results li').first().waitFor();
        const found = await page.locator('#kr-search-results li').allInnerTexts();
        assert.ok(found.some((text) => /IC 116/.test(text)) && found.some((text) => /X 116/.test(text)), JSON.stringify(found));
        await page.locator('#kr-search').fill('ic');
        await page.locator('#kr-search-results li', { hasText: 'IC 116' }).dispatchEvent('mousedown');
        await tag('DSB IC 116').waitFor({ state: 'attached' });
        assert.equal(await tag('DSB IC 116').locator('text.train-number').textContent(), 'IC 116');
        assert.match(await tag('DSB IC 116').getAttribute('class'), /\bselected\b/);
        assert.doesNotMatch(await tag('SJ X 116').getAttribute('class'), /\bselected\b/);
        await page.waitForFunction(() => /IC 116/.test(document.querySelector('#drift-train-detail')?.textContent || ''));
        assert.ok(trainCalls.some((call) => call.service_key === 'DSB IC'), JSON.stringify(trainCalls));
      }
      assert.deepEqual(h.errors, []);
      assert.deepEqual(h.violations, []);
    } finally { await h.browser.close(); }
  }

  // Tidtabellen: en rättelse från Cloud under spelet står som Clouds.
  const meetData = (source) => () => ({ data: {
    changed: false, base_publication_id: 'pub-9', revision: 1, config_version: 3, meet_generation: 7,
    local_edits: { active: true, revision: 1, count: 1, source, lines: ['Tåg 202 vid Charlottendal: ankomst 10:00 → 10:05'] },
    review: { findings: { conflicts: [], observations: [], questions: [], journeys: [] }, sanity: [] }, local_changes: { trains: {}, connections: {} },
    draft: { id: 'meet-1', name: 'Grimslöv 2027', active_day: 'Dagl', operating_region: 'eu', stations: [], connections: [], trains: [], panels: [], files: [], display: {} } } });
  for (const [source, text, button] of [['cloud', '1 rättelse från Cloud gäller redan under spelet.', false], ['local', '1 lokal ändring ovanpå Cloud-versionen.', true]]) {
    const h = await open({ route: '/tidtabell', api: { '/v1/meet-data': meetData(source), '/v1/runtime/pending': () => ({ data: { pending: false } }) } });
    const { page } = h;
    page.setDefaultTimeout(8000);
    try {
      await page.locator('#data-local-state').getByText(text).waitFor();
      assert.equal(await page.locator('#data-discard').isVisible(), button, `${source}: discard`);
      assert.equal(await page.locator('#data-local').getAttribute('data-source'), source);
      assert.equal(await page.locator('#data-local-lines summary').innerText(), source === 'cloud' ? 'Visa Clouds rättelser' : 'Visa de lokala ändringarna');
      if (source === 'cloud') assert.match(await page.locator('#data-local-note').innerText(), /Hela den nya versionen tas i bruk när banan är fri/);
      if (shots && source === 'cloud') await page.locator('#data-local').screenshot({ path: path.join(shots, 'tidtabell-rattelse-fran-cloud.png') });
      assert.deepEqual(h.errors, []);
      assert.deepEqual(h.violations, []);
    } finally { await h.browser.close(); }
  }
  console.log('drift-shared-number-live: ok');
})().catch((error) => { console.error(error); process.exit(1); });
