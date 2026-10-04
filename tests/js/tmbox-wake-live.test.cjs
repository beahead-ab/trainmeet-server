// Webb-TMBoxen när telefonen somnar och vaknar, mot en riktig server med
// tillfällig databas (samma fixtur som server-shell-live). Inget nätverk utåt.
//
// En telefon som vaknar laddar ofta om sidan innan Wi-Fi är tillbaka. Boxen
// ska då vara samma box som förut, med samma enhetskod och station, och ansluta
// av sig själv när nätet kommer. Förr gav den upp vid första misslyckade
// försöket och visade "Starta ny TMBox", som skapade en ny box som
// administratören fick tilldela på nytt.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

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
    const admin = await browser.newContext({ locale: 'sv-SE' });
    assert.equal((await admin.request.post(base + '/v1/auth/login', { data: { email: 'smoke-admin@example.se', password: 'isolated-browser-test' } })).status(), 200);
    const phone = await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 } });
    const page = await phone.newPage();
    page.setDefaultTimeout(15000);
    const errors = [], enrolments = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/v1/browser-clients') enrolments.push(request.url()); });

    await page.goto(base + '/tmbox/');
    await page.locator('.box-code').getByText(/^WEB/).waitFor();
    const box = await page.evaluate(() => JSON.parse(localStorage.getItem('trainmeet.browser-tmbox')));
    assert.equal(enrolments.length, 1, 'the first visit makes the box');
    enrolments.length = 0;
    assert.equal((await admin.request.post(base + '/v1/devices/assign', { data: { device_code: box.device_code, station_id: 'station-a' } })).status(), 200);
    await page.locator('.box h2').getByText('Charlottendahl', { exact: true }).waitFor();
    const connected = () => page.waitForFunction(() => !document.querySelector('#connection-dot')?.classList.contains('is-offline')
      && /Ansluten/.test(document.querySelector('#connection')?.textContent || ''));
    await connected();

    // 1. Telefonen vaknar och laddar om sidan, men nätet är inte tillbaka:
    //    servern går inte att nå på några sekunder.
    let reachable = false;
    await page.route('**/v1/**', route => reachable ? route.continue() : route.abort('internetdisconnected'));
    await page.reload();
    await page.waitForTimeout(1500);
    assert.equal(await page.locator('#start-client').isVisible(), false, 'ingen ny box erbjuds för att nätet saknas en stund');
    reachable = true;
    await page.locator('.box h2').getByText('Charlottendahl', { exact: true }).waitFor();
    await connected();
    assert.equal(await page.locator('.box-code').getByText(box.device_code, { exact: true }).count(), 1, 'samma enhetskod');
    assert.equal(enrolments.length, 0, 'ingen ny box skapades');
    assert.equal((await page.evaluate(() => JSON.parse(localStorage.getItem('trainmeet.browser-tmbox')))).access_token, box.access_token);

    // 2. Sidan ligger kvar men telefonen sover: dold, utan nät i 20 s, och
    //    tillbaka. Boxen ansluter igen så fort den syns.
    await page.unroute('**/v1/**');
    await phone.setOffline(true);
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.waitForTimeout(20000);
    await phone.setOffline(false);
    await page.evaluate(() => {
      Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    const back = Date.now();
    await connected();
    assert.ok(Date.now() - back < 3000, 'ansluten inom ett par sekunder efter att den syns igen');
    assert.equal(await page.locator('#start-client').isVisible(), false);
    assert.equal(enrolments.length, 0);

    // 3. Administratören tog bort boxen under tiden: då, och bara då, är det
    //    rätt att erbjuda en ny.
    const removed = await admin.request.post(base + '/v1/devices/remove', { data: { device_id: box.client_id } });
    assert.ok(removed.ok(), await removed.text());
    await page.reload();
    await page.locator('#start-client').waitFor({ state: 'visible' });
    assert.equal(enrolments.length, 0, 'en ny box skapas först när någon ber om det');
    assert.deepEqual(errors, []);
    console.log('tmbox wake live: ok');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
