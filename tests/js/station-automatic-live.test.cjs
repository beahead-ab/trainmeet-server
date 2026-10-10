// En station lämnas till automatiken och tas tillbaka, mot en riktig server
// med tillfällig databas (samma fixtur som server-shell-live). Inget nätverk
// utåt. Casper, 2026-10-10: "jag går på toa ett tag".
//
// Webb-TMBoxen på en telefon lämnar sin station med * och # och tar tillbaka
// den med # och #. Drift visar det direkt och har knapparna Automatik och
// Aktiv även när boxen är ansluten. Under Inställningar väljer admin om
// automatiken tar över en station som tappat kontakten.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const shots = process.env.SCREENSHOT_DIR || '';

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'server-shell-live-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let diagnostics = '', browser;
  fixture.stderr.on('data', value => { diagnostics += value; });
  try {
    const urls = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Test server did not start')), 15000);
      readline.createInterface({ input: fixture.stdout }).once('line', line => { clearTimeout(timer); resolve(JSON.parse(line)); });
      fixture.once('exit', () => { clearTimeout(timer); reject(new Error(diagnostics)); });
    });
    const base = urls.eu;
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const errors = [];
    const watch = page => {
      page.setDefaultTimeout(15000);
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (/Content Security Policy/i.test(message.text())) errors.push(message.text()); });
    };
    const shot = async (page, name) => { if (shots) await page.screenshot({ path: path.join(shots, name + '.png'), fullPage: false }); };

    const adminContext = await browser.newContext({ locale: 'sv-SE', viewport: { width: 1280, height: 900 } });
    assert.equal((await adminContext.request.post(base + '/v1/auth/login', { data: { email: 'smoke-admin@example.se', password: 'isolated-browser-test' } })).status(), 200);
    const context = await (await adminContext.request.get(base + '/v1/server-context')).json();
    const generation = context.selected_meet.generation;
    const status = async () => (await (await adminContext.request.get(base + '/v1/automatic-stations')).json());
    const modeOf = async (id) => (await status()).stations.find(station => station.id === id);
    assert.equal((await adminContext.request.post(base + '/v1/automatic-stations', { data: { action: 'enable', enabled: true, meet_generation: generation } })).status(), 200);

    // Telefonen blir webb-TMBox på station-a.
    const phone = await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 } });
    const box = await phone.newPage(); watch(box);
    await box.goto(base + '/tmbox/');
    await box.locator('.box-code').getByText(/^WEB/).waitFor();
    const enrolled = await box.evaluate(() => JSON.parse(localStorage.getItem('trainmeet.browser-tmbox')));
    assert.equal((await adminContext.request.post(base + '/v1/devices/assign', { data: { device_code: enrolled.device_code, station_id: 'station-a' } })).status(), 200);
    const lcd = () => box.locator('.lcd').textContent();
    const lcdHas = (text) => box.waitForFunction(value => (document.querySelector('.lcd')?.textContent || '').includes(value), text);
    const lcdLacks = (text) => box.waitForFunction(value => !(document.querySelector('.lcd')?.textContent || '').includes(value), text);
    const key = async (value) => { await box.locator(`[data-key="${value}"]`).click(); await box.waitForTimeout(700); };
    await lcdHas('Nr#');
    await box.waitForFunction(() => document.querySelector('[data-key="*"]'));

    // Drift: stationen är bemannad av boxen och har knappen Automatik.
    const admin = await adminContext.newPage(); watch(admin);
    await admin.goto(base + '/drift');
    const row = admin.locator('#device-list tr[data-station-id="station-a"]');
    await row.getByRole('button', { name: 'Automatik', exact: true }).waitFor();
    assert.equal((await modeOf('station-a')).mode, 'manual');

    // 1. Boxen lämnar stationen: * frågar, # bekräftar.
    await key('*');
    await lcdHas('AUTOMATIK?');
    assert.equal((await modeOf('station-a')).mode, 'manual', 'the question changes nothing');
    await key('#');
    await lcdHas('AUTOMATIK');
    await lcdLacks('AUTOMATIK?');
    assert.match(await lcd(), /AUTOMATIK\s+CDA/);
    const left = await modeOf('station-a');
    assert.deepEqual([left.mode, left.released_by], ['automatic', 'operator']);
    await shot(box, 'box-automatik');
    // Drift hör det direkt: Automatisk, och Aktiv ger stationen tillbaka till boxen.
    await row.locator('.kr-tag.auto').waitFor();
    await row.getByRole('button', { name: 'Aktiv', exact: true }).waitFor();
    await shot(admin, 'drift-automatisk-aktiv');

    // 2. Admin ger tillbaka stationen med Aktiv.
    await row.getByRole('button', { name: 'Aktiv', exact: true }).click();
    await lcdLacks('AUTOMATIK');
    assert.equal((await modeOf('station-a')).mode, 'manual');
    await row.getByRole('button', { name: 'Automatik', exact: true }).waitFor();

    // 3. Admin lämnar den till automatiken medan boxen är ansluten.
    admin.once('dialog', dialog => { assert.match(dialog.message(), /till automatiken/); dialog.accept(); });
    await row.getByRole('button', { name: 'Automatik', exact: true }).click();
    await lcdHas('AUTOMATIK');
    assert.equal((await modeOf('station-a')).released_by, 'admin');

    // 4. Inställningar visar vem som lämnade den och har valet för tappad kontakt.
    await admin.goto(base + '/installningar#obemannade');
    await admin.locator('#obemannade').waitFor({ state: 'visible' });
    await admin.locator('#automatic-stations').getByText('lämnad av admin').waitFor();
    assert.equal(await admin.locator('#automatic-lost-contact').inputValue(), '0');
    assert.equal(await admin.locator('#automatic-lost-contact option[value="0"]').innerText(), 'Aldrig – stationen väntar på sin operatör');
    await admin.locator('#automatic-lost-contact').selectOption('5');
    await admin.locator('#automatic-form [type=submit]').click();
    await admin.waitForFunction(() => !document.querySelector('#automatic-form').dataset.dirty);
    assert.equal((await status()).lost_contact_minutes, 5);
    await shot(admin, 'installningar-obemannade');
    // 4.2.0: ett försenat tåg står 2 spelminuter som förval; admin väljer 1–10.
    assert.equal(await admin.locator('#automatic-late-stop').inputValue(), '2');
    await admin.locator('#automatic-late-stop').fill('3');
    await admin.locator('#automatic-disturbance-form [type=submit]').click();
    await admin.waitForFunction(() => !document.querySelector('#automatic-disturbance-form').dataset.dirty);
    assert.equal((await status()).disturbance.late_stop_minutes, 3);

    // 5. Tillbaka från toaletten: # frågar, # tar tillbaka stationen.
    await key('#');
    await lcdHas('TA TILLBAKA?');
    await key('#');
    await lcdLacks('AUTOMATIK');
    assert.equal((await modeOf('station-a')).mode, 'manual');
    await shot(box, 'box-aktiv');

    assert.deepEqual(errors, []);
    console.log('station automatic live: box leaves and takes back, Drift Automatik/Aktiv, lost-contact setting, late stop');
  } catch (error) {
    console.error('Backend diagnostics:', diagnostics);
    throw error;
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
    if (fixture.exitCode === null) await new Promise(resolve => fixture.once('exit', resolve));
  }
})().catch(error => { console.error(error); process.exit(1); });
