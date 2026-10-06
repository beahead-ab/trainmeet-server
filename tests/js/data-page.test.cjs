// Tidtabell (/tidtabell): Clouds Data-vy, den riktiga data-workspace.js, på
// serverns sida under serverns CSP. API:et är en fixtur som svarar som
// http_server.py: hela utkastet sparas mot den revision sidan utgick från,
// och en annan revision ger 409. Proven går som en admin gör: ändrar en
// avgång och sparar, ser cellen markerad och raden om lokala ändringar,
// får inte skriva över någon annans ändring, går tillbaka till Cloud och
// väljer Behåll eller Ta när Cloud har en ny version.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const stations = [
  { id: 'cda', name: 'Charlottendal', code: 'CDA', aliases: [], tracks: [{ id: 'cda-1', display_label: '1' }, { id: 'cda-2', display_label: '2' }] },
  { id: 'lek', name: 'Lekby', code: 'LEK', aliases: [], tracks: [{ id: 'lek-1', display_label: '1' }] },
];
const connections = [{ id: 'c-cda-lek', station_a_id: 'cda', station_b_id: 'lek', track_type: 'single' }];
const cloudTrains = () => [
  { id: 'm-93-cda', station: 'Charlottendal', station_id: 'cda', train_number: '93', days: 'Dagl', track: '1', departure_time: '05:15', sort_time: '05:15' },
  { id: 'm-93-lek', station: 'Lekby', station_id: 'lek', train_number: '93', days: 'Dagl', track: '1', arrival_time: '05:25', departure_time: '05:26', sort_time: '05:25' },
];

function server() {
  const api = { revision: 0, counter: 0, trains: cloudTrains(), posts: [], discards: [], decisions: [], pending: false, decision: null, lines: [], changes: { trains: {}, connections: {} } };
  const local = () => ({ active: api.revision > 0, revision: api.revision, count: api.lines.length, lines: api.lines });
  const state = (extra = {}) => ({
    changed: false, base_publication_id: 'pub-9', revision: api.revision, config_version: 3, meet_generation: 7 + api.counter,
    local_edits: local(), review: { findings: { conflicts: [], observations: [], questions: [], journeys: [] }, sanity: [] },
    local_changes: api.changes, ...extra,
  });
  const draft = () => ({ id: 'meet-1', name: 'Grimslöv 2027', active_day: 'Dagl', operating_region: 'eu', stations, connections, trains: structuredClone(api.trains), panels: [], files: [], display: {} });
  // Någon annan sparar (en annan flik, en annan admin).
  api.otherSaves = (departure) => {
    api.trains[1].departure_time = departure;
    api.revision = ++api.counter;
    api.lines = [`Tåg 93 vid Lekby: avgång 05:26 → ${departure}`];
    api.changes = { trains: { 'm-93-lek': ['departure_time'] }, connections: {} };
  };
  const stale = { status: 409, data: { error: 'stale_local_edits', message: 'Tidtabellen har ändrats av någon annan. Läs in sidan igen.' } };
  const handlers = {
    '/v1/meet-data': (request) => {
      if (request.method() === 'GET') return { data: { ...state(), draft: draft() } };
      const body = JSON.parse(request.postData());
      api.posts.push(body);
      if (body.expected_revision !== api.revision || body.base_publication_id !== 'pub-9') return stale;
      const before = new Map(cloudTrains().map((row) => [row.id, row]));
      api.trains = body.draft.trains.map(({ findings, ...row }) => row);
      api.revision = ++api.counter;
      api.lines = []; api.changes = { trains: {}, connections: {} };
      for (const row of api.trains) {
        const cloud = before.get(row.id);
        if (cloud.departure_time !== row.departure_time) {
          api.lines.push(`Tåg ${row.train_number} vid ${row.station}: avgång ${cloud.departure_time} → ${row.departure_time}`);
          api.changes.trains[row.id] = ['departure_time'];
        }
      }
      return { data: state({ changed: true }) };
    },
    '/v1/meet-data/discard': (request) => {
      const body = JSON.parse(request.postData());
      api.discards.push(body);
      if (body.expected_revision !== api.revision) return stale;
      api.trains = cloudTrains(); api.revision = 0; api.counter++; api.lines = []; api.changes = { trains: {}, connections: {} };
      return { data: state({ changed: true, backup: '/var/lib/trainmeet/backups/b2' }) };
    },
    '/v1/runtime/pending': () => ({ data: api.pending ? {
      pending: true, publication_id: 'pub-10', meet_name: 'Grimslöv 2027', active_publication_id: 'pub-9',
      changes: { first_activation: false, stations: { added: { count: 1, names: ['Vagnsta'], more: 0 }, removed: { count: 0, names: [], more: 0 }, renamed: { count: 0, names: [], more: 0 } },
        connections: { added: 1, removed: 0 }, timetable: { added: 4, removed: 0, changed: { count: 1, names: ['93'], more: 0 }, total_before: 2, total_after: 6 } },
      local_edits: local(), decision: api.decision,
    } : { pending: false } }),
    '/v1/cloud/local-decision': (request) => {
      const body = JSON.parse(request.postData());
      api.decisions.push(body);
      if (body.publication_id !== 'pub-10') return { status: 409, data: { error: 'pending_revision_changed', message: 'En annan Cloud-version väntar nu.' } };
      if (body.decision === 'keep') {
        api.decision = 'keep';
        return { data: { state: 'local_changes_kept', message_template: 'Lokala ändringar behålls. Cloud-versionen väntar tills du väljer Ta Cloud-versionen.', message_values: {}, decision: 'keep' } };
      }
      api.pending = false; api.decision = null; api.trains = cloudTrains(); api.revision = 0; api.lines = []; api.changes = { trains: {}, connections: {} };
      return { data: { state: 'ok', message_template: 'Config för {name} är uppdaterad. Pågående drift har bevarats.', message_values: { name: 'Grimslöv 2027' }, decision: 'take', backup: '/var/lib/trainmeet/backups/b3' } };
    },
  };
  return { api, handlers };
}

const cell = (page, address) => page.getByRole('grid', { name: 'Tågrörelser' }).getByRole('gridcell', { name: new RegExp(`^${address} `) });
async function edit(page, address, value) {
  await cell(page, address).dblclick();
  const input = page.getByRole('textbox', { name: `Redigera cell ${address}` });
  await input.fill(value);
  await input.press('Enter');
}

(async () => {
  const { api, handlers } = server();
  const h = await open({ route: '/tidtabell', api: handlers });
  const { page } = h;
  page.setDefaultTimeout(8000);
  const dialogs = [];
  page.on('dialog', (dialog) => { dialogs.push(dialog.message()); dialog.accept(); });
  try {
    // Sidan: Clouds rutnät i en shadow root, sidhuvudets länk markerad, samma som Cloud.
    await page.getByRole('grid', { name: 'Tågrörelser' }).waitFor();
    assert.equal(await page.evaluate(() => Boolean(document.querySelector('#data-workspace').shadowRoot?.querySelector('[role="grid"]'))), true, 'the grid is drawn in the shadow root');
    assert.equal(await page.locator('#header-data').getAttribute('aria-current'), 'page');
    assert.equal(await page.locator('#data-local-state').innerText(), 'Samma som Cloud-versionen.');
    assert.equal(await page.locator('#data-discard').isVisible(), false);
    assert.equal(await page.locator('#data-choice').isVisible(), false);
    assert.equal(await page.evaluate(() => document.documentElement.dataset.krPage), 'admin', 'no light flash before the page is drawn');
    assert.equal(await page.evaluate(() => document.querySelector('#data-workspace').dataset.theme), 'dark', 'the view follows the dark theme');
    const sheets = await page.evaluate(() => document.querySelector('#data-workspace').shadowRoot.adoptedStyleSheets.length);
    assert.ok(sheets >= 1, 'Cloud\'s styles are constructed style sheets, which the CSP allows');

    // Ändra en avgång och spara: hela utkastet, mot revisionen sidan utgick från.
    const departure = await cell(page, 'F1').getAttribute('aria-label');
    assert.match(departure, /05:15$/, 'F1 is the departure from Charlottendal');
    await edit(page, 'F1', '05:20');
    await page.getByRole('button', { name: 'Spara' }).click();
    await page.getByText('Ändringen gäller nu i driften.').waitFor();
    const [first] = api.posts;
    assert.deepEqual([first.expected_revision, first.base_publication_id, first.meet_generation], [0, 'pub-9', 7]);
    assert.equal(first.draft.trains.find((row) => row.id === 'm-93-cda').departure_time, '05:20');
    await page.locator('#data-local-state').getByText('1 lokal ändring ovanpå Cloud-versionen.').waitFor();
    assert.equal(await page.locator('#data-discard').isVisible(), true);
    await page.locator('#data-local-lines summary').click();
    assert.equal(await page.locator('#data-local-lines li').innerText(), 'Tåg 93 vid Charlottendal: avgång 05:15 → 05:20');
    await page.locator('td.tm-cell--local').first().waitFor();
    assert.match(await page.locator('td.tm-cell--local').first().getAttribute('title'), /Ändrad lokalt/);

    // Nästa sparning bär den nya revisionen och generationen.
    await edit(page, 'F1', '05:21');
    await page.getByRole('button', { name: 'Spara' }).click();
    await page.waitForFunction(() => !document.querySelector('#data-workspace').shadowRoot.querySelector('[aria-busy="true"]'));
    await page.locator('#data-local-lines li').getByText('05:15 → 05:21').waitFor();
    assert.deepEqual([api.posts[1].expected_revision, api.posts[1].meet_generation], [1, 8]);

    // Någon annan sparar medan sidan har osparat. Sidan läser in igen
    // (här: tillbaka till sidan via logotypen), men skriver inte över det osparade.
    await edit(page, 'F1', '05:30');
    api.otherSaves('05:40');
    await page.locator('#workspace-home').click();
    await page.waitForFunction(() => document.body.dataset.mode === 'kor');
    await page.evaluate(() => { history.pushState(null, '', '/tidtabell'); dispatchEvent(new PopStateEvent('popstate')); });
    await page.waitForFunction(() => document.body.dataset.mode === 'tidtabell');
    await page.waitForTimeout(400);
    assert.match(await cell(page, 'F1').getAttribute('aria-label'), /05:30$/, 'the unsaved edit is still there');
    await page.getByRole('button', { name: 'Spara' }).click();
    await page.getByText('Någon annan har ändrat tidtabellen sedan du började. Tryck Avbryt för att se den nya och gör sedan om din ändring.').waitFor();
    assert.equal(api.posts[2].expected_revision, 2, 'the save names the revision the edit started from');
    assert.equal(api.trains[1].departure_time, '05:40', 'the other change is not overwritten');
    // Avbryt: vyn tar då den nya tidtabellen.
    await page.getByRole('button', { name: 'Avbryt', exact: true }).click();
    await page.getByRole('button', { name: 'Kasta ändringar' }).click();
    await page.waitForFunction(() => /05:40$/.test(document.querySelector('#data-workspace').shadowRoot.querySelector('[role="grid"] [aria-label^="F2 "]')?.getAttribute('aria-label') || ''));
    assert.match(await cell(page, 'F1').getAttribute('aria-label'), /05:21$/, 'the own saved change is still in the layer');

    // Återgå till Cloud-versionen: frågar först, skickar revisionen.
    await page.locator('#data-discard').click();
    await page.locator('#data-local-state').getByText('Samma som Cloud-versionen.').waitFor();
    assert.match(dialogs.at(-1), /Alla lokala ändringar tas bort/);
    assert.deepEqual(api.discards.map((body) => body.expected_revision), [3]);
    assert.equal(await page.locator('#data-message').innerText(), 'Clouds tidtabell gäller igen.');
    assert.equal(await page.locator('#data-discard').isVisible(), false);

    // Ny Cloud-version medan lokala ändringar finns: valet visas med båda sidorna.
    await edit(page, 'F1', '05:18');
    await page.getByRole('button', { name: 'Spara' }).click();
    await page.locator('#data-local-state').getByText('1 lokal ändring ovanpå Cloud-versionen.').waitFor();
    api.pending = true;
    await page.locator('#workspace-home').click();
    await page.evaluate(() => { history.pushState(null, '', '/tidtabell'); dispatchEvent(new PopStateEvent('popstate')); });
    await page.locator('#data-choice').waitFor();
    const changes = await page.locator('#data-choice-changes li').allInnerTexts();
    assert.deepEqual(changes, ['Nya stationer: Vagnsta', '1 nya sträckor', '4 nya tågrörelser', 'Ändrade tåg: 93']);
    assert.deepEqual(await page.locator('#data-choice-local li').allInnerTexts(), ['Tåg 93 vid Charlottendal: avgång 05:15 → 05:18']);
    await page.locator('#data-keep').click();
    await page.locator('#data-choice-note').getByText(/Du har valt att behålla dina ändringar/).waitFor();
    assert.deepEqual(api.decisions[0], { decision: 'keep', publication_id: 'pub-10', expected_revision: 5, meet_generation: 12 });
    assert.equal(await page.locator('#data-keep').isVisible(), false, 'kept: only Take remains');
    assert.equal(await page.locator('#data-choice-message').innerText(), 'Lokala ändringar behålls. Cloud-versionen väntar tills du väljer Ta Cloud-versionen.');
    await page.locator('#data-take').click();
    await page.locator('#data-choice').waitFor({ state: 'hidden' });
    assert.match(dialogs.at(-1), /Dina lokala ändringar försvinner/);
    assert.equal(api.decisions[1].decision, 'take');
    await page.locator('#data-local-state').getByText('Samma som Cloud-versionen.').waitFor();

    assert.deepEqual(h.violations, [], 'nothing is blocked by the CSP');
    assert.deepEqual(h.errors, []);
    console.log('data-page: ok');
  } finally {
    await h.browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
