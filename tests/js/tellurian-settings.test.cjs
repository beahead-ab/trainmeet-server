// Inställningar → Klarering → Tellurian Dispatch (#222): avstängt som standard,
// Testa anslutning ger en förhandsrapport, Skugga visar avvikelser, och läget där
// Tellurian bestämmer går inte att välja. Riktig webbkod med strikt CSP; API:et
// är en fixtur (kr-fixture.cjs). Simulerade data, ingen Tellurian-server.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const status = (settings, shadow = null, report = null) => ({ data: {
  available: true, reason: null, meet_id: 'meet-1', settings, modes: ['off', 'shadow'],
  authority_available: false, shadow, report,
} });
const REPORT = {
  simulated: true, address: 'http://192.168.1.40:5000', checked_at_local: '05:12',
  api: { status: 'passed', version: 'v1', dispatchers: 10, read: 10 },
  stations: { matched: Array.from({ length: 10 }, (_, i) => ({ code: 'S' + i })), unmatched_trainmeet: [{ code: 'VÄX', reason: 'missing' }], unmatched_tellurian: [] },
  stretches: { matched: [{ from: 'CDA', to: 'LEK', passes: 1 }], unseen: [], unmapped: [], unmatched_tellurian: [] },
  trains: { matched: [{ number: '102' }], unmatched_trainmeet: [{ number: '3902' }], ambiguous: [{ number: '421' }], unmatched_tellurian: ['GC G 4711'] },
  clock: { trainmeet: 'internal', name: '', tellurian: 'unknown', warning: true },
  dispatch_mode: { value: 'clearance', region: 'eu', ok: true },
  unknown_actions: [], activation: { shadow: true, authority: false, blockers: ['stations'], warnings: ['trains', 'clock'] },
};
const SHADOW = { state: 'connected', sse: true, last_contact: 1, last_contact_local: '05:12', error: null, simulated: true,
  dispatchers: 10, sections: 24, deviations: { count: 1, unmapped: 0, items: [
    { kind: 'state_differs', train: '93', tellurian_train: 'SJ RC 93', from: 'CDA', to: 'LEK', trainmeet: 'accepted', tellurian: 'requested' },
  ] } };

(async () => {
  let settings = { base_url: '', test_instance: false, mode: 'off' };
  const posts = [];
  const h = await open({ route: '/installningar#klarering', width: 1280, height: 900, api: {
    '/v1/clearance/tellurian': async (request) => {
      if (request.method() === 'POST') {
        // authorizedFetch adds the meet generation to every POST; the server ignores it here.
        const { meet_generation, ...body } = JSON.parse(request.postData());
        posts.push(body);
        if (body.mode === 'authority') return { status: 409, data: { error: 'tellurian_authority_unavailable', message: 'Auktoritetsläget kommer när Stefans server finns. Välj Av eller Skugga.' } };
        settings = { base_url: body.base_url, test_instance: body.test_instance, mode: body.mode };
      }
      return status(settings, settings.mode === 'shadow' ? SHADOW : null, posts.length ? REPORT : null);
    },
    '/v1/clearance/tellurian/test': async (request) => {
      const { meet_generation, ...body } = JSON.parse(request.postData());
      posts.push({ test: body });
      if (body.base_url === '8.8.8.8') return { status: 400, data: { error: 'tellurian_address_not_local', message: 'Bara en lokal adress på träffens nät godtas (privat IPv4, 169.254.x.x eller .local). En publik adress kräver valet Testinstans.' } };
      return { data: REPORT };
    },
  } });
  const { page } = h;
  page.setDefaultTimeout(8000);
  try {
    await page.locator('#klarering').waitFor({ state: 'visible' });
    await page.waitForFunction(() => document.querySelector('#tellurian-status-text').textContent.trim() === 'Av');
    assert.equal(await page.locator('#tellurian-mode').inputValue(), 'off');
    assert.equal(await page.locator('#tellurian-mode option[value="authority"]').isDisabled(), true);
    assert.deepEqual(await page.locator('#tellurian-mode option:not([disabled])').evaluateAll(o => o.map(x => x.value)), ['off', 'shadow']);
    assert.equal(await page.locator('#tellurian-form [data-save-submit]').isDisabled(), true);
    assert.equal(await page.locator('#tellurian-report').isHidden(), true);

    // A public address is refused by the server, with its own words.
    await page.locator('#tellurian-address').fill('8.8.8.8');
    await page.locator('#tellurian-test').click();
    await page.waitForFunction(() => /lokal adress/.test(document.querySelector('#tellurian-message').textContent));

    // Testa anslutning: the unsaved address goes to the server, the report is shown.
    await page.locator('#tellurian-address').fill('192.168.1.40:5000');
    await page.locator('#tellurian-test').click();
    await page.locator('#tellurian-report').waitFor({ state: 'visible' });
    const report = await page.locator('#tellurian-report-body li').allTextContents();
    assert.match(report[0], /Simulerad/);
    assert.ok(report.some(r => /10 matchade.*saknas i Tellurian: VÄX/.test(r)), report.join('\n'));
    assert.ok(report.some(r => /tvetydiga, inte matchade: 421/.test(r)), report.join('\n'));
    assert.ok(report.some(r => /serverns egen klocka, inte FastClock/.test(r)), report.join('\n'));
    assert.deepEqual(posts.at(-1), { test: { base_url: '192.168.1.40:5000', test_instance: false } });
    assert.match(await page.locator('#tellurian-message').textContent(), /Inget skrevs till Tellurian/);

    // Skugga: Spara sends the meet it was read for; status and deviations follow.
    await page.locator('#tellurian-mode').selectOption('shadow');
    assert.equal(await page.locator('#tellurian-form [data-save-state]').textContent(), 'Ändrat: Adress, Läge');
    await page.locator('#tellurian-form [data-save-submit]').click();
    await page.waitForFunction(() => document.querySelector('#tellurian-status-text').textContent.trim() === 'Skugga · ansluten');
    assert.deepEqual(posts.at(-1), { base_url: '192.168.1.40:5000', test_instance: false, mode: 'shadow', meet_id: 'meet-1' });
    await page.locator('#tellurian-deviations').waitFor({ state: 'visible' });
    assert.match(await page.locator('#tellurian-deviations-list li').first().textContent(), /SJ RC 93 CDA→LEK.*Olika läge: TrainMeet klarerad · Tellurian begärd/);
    assert.match(await page.locator('#tellurian-status-note').textContent(), /simulerad/);
    assert.equal(await page.locator('#tellurian-form [data-save-submit]').isDisabled(), true);

    // English: the same panel, translated; data such as codes stay as they are.
    await page.evaluate(() => globalThis.TrainMeetI18n.setLanguage('en'));
    await page.evaluate(() => globalThis.TrainMeetTellurian.refresh());
    await page.waitForFunction(() => document.querySelector('#tellurian-status-text').textContent.trim() === 'Shadow · connected');
    assert.match(await page.locator('#klarering-title').textContent(), /Clearance/);

    assert.deepEqual(h.errors, []);
    assert.deepEqual(h.violations, []);
    console.log('tellurian settings ok');
  } finally {
    await h.browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
