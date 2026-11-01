// A meet that so far only has its drivers (Cloud #113), against a real
// TrainMeet Server: the duty sheets come from Cloud, Settings shows what the
// meet has and where to change it, the driver opens the own pages of the duty
// (fetched once with the server link), and Drift, the big screen and the
// participants' page run without a layout.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'lokforare-cloud-live-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '', browser;
  fixture.stderr.on('data', (value) => { stderr += value; });
  const lines = readline.createInterface({ input: fixture.stdout });
  const nextLine = (what) => new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(what + ' timeout: ' + stderr)), 15000);
    lines.once('line', (line) => { clearTimeout(timeout); resolve(JSON.parse(line)); });
  });
  try {
    const started = nextLine('Backend start');
    fixture.once('exit', (code) => { if (code) console.error('Backend exited: ' + code + ' ' + stderr); });
    const { base, meet, slug } = await started;
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const errors = [];
    const watch = (page, where) => {
      page.on('pageerror', (error) => errors.push(`${where}: ${error.message}`));
      page.on('console', (message) => { if (message.type() === 'error') errors.push(`${where}: ${message.text()}`); });
    };

    // 1. Settings → Drivers: the duty sheets from Cloud, no upload here.
    const owner = await browser.newContext({ locale: 'sv-SE', viewport: { width: 1280, height: 900 } });
    await owner.request.post(base + '/v1/auth/login', { data: { email: 'casper@example.se', password: 'ett-langt-losenord' } });
    // The first overview starts the fetch of the duties' own pages in the background.
    let cloud;
    for (let tries = 0; tries < 100; tries++) {
      cloud = (await (await owner.request.get(base + '/v1/driver-admin/overview')).json()).cloud;
      if (cloud.parts_here === cloud.parts) break;
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    assert.deepEqual([cloud.duties, cloud.parts, cloud.parts_here, cloud.linked], [2, 1, 1, true]);
    const settings = await owner.newPage();
    settings.setDefaultTimeout(15000);
    watch(settings, 'settings');
    await settings.goto(base + '/installningar#lokforare');
    await settings.locator('#lf-cloud-status').waitFor({ state: 'visible' });
    assert.equal(await settings.locator('#lf-cloud-title').textContent(), 'Köruppdragen från Cloud');
    const facts = await settings.locator('#lf-cloud-facts').textContent();
    assert.ok(facts.startsWith(`${meet} · 2 tjänster · publicerad `), facts);
    assert.equal(await settings.locator('#lf-cloud-status').textContent(), 'Tillgängligt för förarna');
    assert.equal(await settings.locator('#lf-cloud-note').isHidden(), true, 'all the pages are on the server');
    assert.equal(await settings.locator('#lf-cloud-link').getAttribute('href'), `https://cloud.example/traff/${slug}/lokforare`);
    assert.equal(await settings.locator('#lf-cloud-link-text').textContent(), 'Ändra i Cloud');
    assert.equal(await settings.locator('#lf-local').isHidden(), true, 'nothing was added on the server');
    assert.equal(await settings.locator('#lokforare input[type="file"]').count(), 0, 'no upload on the server');
    // The same row in English.
    await settings.evaluate(() => globalThis.TrainMeetI18n.setLanguage('en'));
    await settings.waitForFunction(() => document.querySelector('#lf-cloud-status').textContent === 'Available to drivers');
    assert.equal(await settings.locator('#lf-cloud-link-text').textContent(), 'Change in Cloud');
    assert.ok((await settings.locator('#lf-cloud-facts').textContent()).includes('2 duties'));
    await settings.evaluate(() => globalThis.TrainMeetI18n.setLanguage('sv'));

    // 2. Lokföraren: duty 81 has its own pages, duty 82 has none.
    const driver = await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const app = await driver.newPage();
    app.setDefaultTimeout(15000);
    watch(app, 'lokforaren');
    await app.goto(base + '/login');
    await app.locator('#login-email').fill('benny@example.se');
    await app.locator('#login-password').fill('forarens-losenord');
    await app.locator('#login-form button[type="submit"]').click();
    await app.waitForURL(base + '/lokforaren/');
    await app.locator('#duty-query').fill('82');
    await app.locator('#duty-found').waitFor({ state: 'visible' });
    assert.equal(await app.locator('#found-source').isHidden(), true, 'duty 82 has no pages');
    await app.locator('#duty-query').fill('81');
    await app.locator('#found-source').waitFor({ state: 'visible' });
    assert.equal(await app.locator('#found-source').getAttribute('href'), '/v1/driver/duties/duty-81.pdf');
    assert.equal(await app.locator('#found-source').textContent(), 'Förlagan i köruppdraget · sida 3–4');
    const pages = await driver.request.get(base + '/v1/driver/duties/duty-81.pdf');
    assert.equal(pages.status(), 200);
    assert.equal(pages.headers()['content-type'], 'application/pdf');
    assert.ok((await pages.body()).subarray(0, 5).equals(Buffer.from('%PDF-')));
    await app.locator('#duty-activate').click();
    await app.locator('#view-korplan').waitFor({ state: 'visible' });
    const checkin = await driver.request.post(base + '/v1/driver/checkins', { data: { station: 'Norrby', track: '1' } });
    assert.equal(checkin.status(), 200, await checkin.text());

    // 3. Drift without a layout: the clock and the drivers, no empty map.
    const drift = await owner.newPage();
    drift.setDefaultTimeout(15000);
    watch(drift, 'drift');
    await drift.goto(base + '/drift');
    await drift.locator('#drivers-only-summary').waitFor({ state: 'visible' });
    await drift.waitForFunction(() => document.querySelector('#drivers-only-detail').textContent === '1 förare incheckad: Benny');
    for (const selector of ['#drift-map', '#drift-graph', '#drift-stats']) {
      assert.equal(await drift.locator(selector).isHidden(), true, selector);
    }

    // 4. The big screen and the participants' page run without errors.
    for (const [where, address] of [['screen', '/display/topology'], ['participant', '/']]) {
      const page = await owner.newPage();
      watch(page, where);
      await page.goto(base + address);
      await page.waitForTimeout(1500);
      await page.close();
    }
    assert.deepEqual(errors, []);

    fixture.stdin.end();
    const { fetches } = await nextLine('Backend stop');
    assert.equal(fetches, 1, 'the pages are fetched from Cloud once');
    console.log('lokforare cloud live: drivers-only meet, settings, Lokföraren pages, Drift');
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    fixture.kill();
  }
})();
