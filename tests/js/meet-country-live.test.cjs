// Träffens land från Cloud (meet.country) i en riktig webbläsare mot en riktig
// server: märket DK i deltagarvyn, sidhuvudet, Träff och Cloud och på TMBox-sidan,
// och danska på en ny box utan eget språkval.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
(async () => {
  const out = process.env.MEET_COUNTRY_SCREENSHOTS;
  const fixture = spawn('python3', [path.join(__dirname, 'meet-country-live-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let err = ''; fixture.stderr.on('data', d => err += d);
  const urls = await new Promise((resolve, reject) => { readline.createInterface({ input: fixture.stdout }).once('line', l => resolve(JSON.parse(l))); fixture.once('exit', c => reject(new Error(err))); });
  const base = urls.eu;
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ locale: 'sv-SE', viewport: { width: 1280, height: 860 } });
    page.setDefaultTimeout(15000);
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    // Deltagarvyn, utan inloggning.
    await page.goto(base + '/');
    await page.locator('#pv-region').getByText('DK', { exact: true }).waitFor();
    if (out) await page.screenshot({ path: path.join(out, 'srv-1-deltagare.png') });
    // Inloggad: sidhuvudet och Träff och Cloud.
    await page.goto(base + '/login');
    await page.locator('#login-email').fill('smoke-admin@example.se');
    await page.locator('#login-password').fill('isolated-browser-test');
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator('#overview-view').waitFor({ state: 'visible' });
    await page.locator('#server-region').getByText('DK', { exact: true }).waitFor();
    if (out) await page.screenshot({ path: path.join(out, 'srv-2-drift.png') });
    await page.goto(base + '/installningar#traff');
    await page.locator('#cloud-meet-region').getByText('DK', { exact: true }).waitFor();
    if (out) await page.screenshot({ path: path.join(out, 'srv-3-traff.png') });
    // Webb-TMBoxen: märket och danska utan eget val.
    const box = await browser.newPage({ locale: 'sv-SE', viewport: { width: 390, height: 844 } });
    await box.goto(base + '/tmbox/');
    await box.locator('#meet-region').getByText('DK', { exact: true }).waitFor();
    const identity = await box.evaluate(() => JSON.parse(localStorage.getItem('trainmeet.browser-tmbox')));
    const ui = await (await box.request.get(base + '/v1/tmbox/terminal', { headers: { Authorization: 'Bearer ' + identity.access_token } })).json();
    // Boxen har inget eget språkval: den skriver danska, landets språk.
    assert.match(ui.lines.join('\n'), /VENTER PÅ ADMIN/);
    if (out) await box.screenshot({ path: path.join(out, 'srv-4-tmbox.png') });
    const context = await (await page.request.get(base + '/v1/server-context')).json();
    assert.equal(context.country, 'dk');
    assert.equal(context.operating_region, 'eu');
    assert.deepEqual(errors, []);
    console.log('server country live: ok');
  } finally { await browser.close(); fixture.stdin.end(); }
})().catch(e => { console.error(e); process.exit(1); });
