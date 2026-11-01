// Inställningar → Klarering → Tellurian Dispatch (#222): avstängt som standard,
// Testa anslutning ger en förhandsrapport, Skugga visar avvikelser, och läget där
// Tellurian bestämmer går inte att välja. Drift får en rad om klareringen bara i
// skugga: kontakt, avvikelser och väg tillbaka hit. Riktig webbkod med strikt
// CSP; API:et är en fixtur (kr-fixture.cjs). Simulerade data, ingen Tellurian-server.
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
  let settings = { base_url: '', test_instance: false, mode: 'off' }, lastReport = null, shadowNow = SHADOW, refuse = false;
  const posts = [];
  const h = await open({ route: '/installningar#klarering', width: 1280, height: 900, api: {
    '/v1/clearance/tellurian': async (request) => {
      if (refuse) return { status: 403, data: { error: 'admin_required', message: 'Bara administratören.' } };
      if (request.method() === 'POST') {
        // authorizedFetch adds the meet generation to every POST; the server ignores it here.
        const { meet_generation, ...body } = JSON.parse(request.postData());
        posts.push(body);
        if (body.mode === 'authority') return { status: 409, data: { error: 'tellurian_authority_unavailable', message: 'Auktoritetsläget kommer när Stefans server finns. Välj Av eller Skugga.' } };
        settings = { base_url: body.base_url, test_instance: body.test_instance, mode: body.mode };
      }
      // Like the server: the status carries the last report that was made.
      return status(settings, settings.mode === 'shadow' ? shadowNow : null, lastReport);
    },
    '/v1/clearance/tellurian/test': async (request) => {
      const { meet_generation, ...body } = JSON.parse(request.postData());
      posts.push({ test: body });
      if (body.base_url === '8.8.8.8') return { status: 400, data: { error: 'tellurian_address_not_local', message: 'Bara en lokal adress på träffens nät godtas (privat IPv4, 169.254.x.x eller .local). En publik adress kräver valet Testinstans.' } };
      lastReport = REPORT;
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
    // Wait for this click's answer, not for a status poll that may show the report first.
    await page.waitForFunction(() => /Inget skrevs till Tellurian/.test(document.querySelector('#tellurian-message').textContent));
    await page.locator('#tellurian-report').waitFor({ state: 'visible' });
    const report = await page.locator('#tellurian-report-body li').allTextContents();
    assert.match(report[0], /Simulerad/);
    assert.ok(report.some(r => /10 matchade.*saknas i Tellurian: VÄX/.test(r)), report.join('\n'));
    assert.ok(report.some(r => /tvetydiga, inte matchade: 421/.test(r)), report.join('\n'));
    assert.ok(report.some(r => /serverns egen klocka, inte FastClock/.test(r)), report.join('\n'));
    assert.deepEqual(posts.at(-1), { test: { base_url: '192.168.1.40:5000', test_instance: false } });

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

    // Drift: one row about the clearance while Tellurian is read in shadow, with
    // the same status; the details stay in Settings.
    const row = page.locator('#drift-clearance');
    await page.goto('http://127.0.0.1:9999/drift'); // a new page load is Swedish again (kr-fixture)
    await row.waitFor({ state: 'visible' });
    await page.waitForFunction(() => document.querySelector('#drift-clearance-text').textContent.trim() === 'Skugga · ansluten');
    assert.equal(await page.locator('#drift-clearance-status').getAttribute('class'), 'kr-tag ok');
    assert.equal(await page.locator('#drift-clearance-note').textContent(), 'skillnader mot Tellurian: 1 · simulerad');
    assert.match(await row.textContent(), /TrainMeet klarerar\. Tellurian Dispatch läses bredvid\./);
    assert.equal(await row.locator('a').textContent(), 'Visa i Inställningar →');
    assert.equal(await row.locator('a').getAttribute('href'), '/installningar#klarering');
    // Under the clock row, before the meet's timeline.
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#overview-view > section')].slice(0, 3).map(s => s.id)),
      ['drift-clock', 'drift-clearance', 'drift-timeline']);
    // English, the same row.
    await page.evaluate(() => globalThis.TrainMeetI18n.setLanguage('en'));
    await page.evaluate(() => globalThis.TrainMeetTellurian.refresh());
    await page.waitForFunction(() => document.querySelector('#drift-clearance-text').textContent.trim() === 'Shadow · connected');
    assert.equal(await page.locator('#drift-clearance-note').textContent(), 'differences from Tellurian: 1 · simulated');
    assert.match(await row.textContent(), /TrainMeet clears trains\. Tellurian Dispatch is read alongside\./);
    assert.equal(await row.locator('a').textContent(), 'Show in Settings →');

    // No contact: when it was last heard from, in amber; no stale comparison.
    await page.evaluate(() => globalThis.TrainMeetI18n.setLanguage('sv'));
    shadowNow = { ...SHADOW, state: 'no_contact', sse: false, last_contact_local: '05:14', error: 'unreachable' };
    await page.evaluate(() => globalThis.TrainMeetTellurian.refresh());
    await page.waitForFunction(() => document.querySelector('#drift-clearance-text').textContent.trim() === 'Skugga · ingen kontakt');
    assert.equal(await page.locator('#drift-clearance-status').getAttribute('class'), 'kr-tag warn');
    assert.equal(await row.evaluate(r => r.classList.contains('warn')), true);
    assert.equal(await page.locator('#drift-clearance-note').textContent(), 'senast kontakt 05:14 · Tellurian svarar inte på adressen. · simulerad');

    // A phone: the row wraps inside the page instead of widening it.
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await row.evaluate(r => r.scrollWidth <= r.clientWidth + 1), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.setViewportSize({ width: 1280, height: 900 });

    // Contact again, no deviations.
    shadowNow = { ...SHADOW, deviations: { count: 0, unmapped: 0, items: [] } };
    await page.evaluate(() => globalThis.TrainMeetTellurian.refresh());
    await page.waitForFunction(() => document.querySelector('#drift-clearance-note').textContent === 'inga skillnader mot Tellurian · simulerad');
    assert.equal(await row.evaluate(r => r.classList.contains('warn')), false);

    // Off: Drift shows nothing about Tellurian at all.
    settings = { ...settings, mode: 'off' };
    await page.evaluate(() => globalThis.TrainMeetTellurian.refresh());
    await row.waitFor({ state: 'hidden' });

    // An answer that is not the status (here 403) hides the row rather than guess.
    settings = { ...settings, mode: 'shadow' };
    await page.evaluate(() => globalThis.TrainMeetTellurian.refresh());
    await row.waitFor({ state: 'visible' });
    refuse = true;
    await page.evaluate(() => globalThis.TrainMeetTellurian.refresh());
    await row.waitFor({ state: 'hidden' });
    refuse = false;

    assert.deepEqual(h.errors, []);
    assert.deepEqual(h.violations, []);
    console.log('tellurian settings ok');
    if (process.env.GITHUB_ACTIONS) console.log('::notice file=tests/js/tellurian-settings.test.cjs::tellurian settings ok');
  } finally {
    await h.browser.close();
  }
})().catch((error) => {
  console.error(error);
  // The job log is not always readable; an annotation names the failing check.
  if (process.env.GITHUB_ACTIONS) console.log('::error file=tests/js/tellurian-settings.test.cjs::' + String(error && error.message || error).split('\n').slice(0, 3).join(' | '));
  process.exit(1);
});
