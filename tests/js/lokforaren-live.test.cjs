// Lokföraren against a real TrainMeet Server: a driver signs in, chooses
// duty 132, follows the NOW line at 4×, checks in, sees the others on the
// layout, saves an icon, shows on the maps (Drift, big screen, participants)
// and sees a stopped clock.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const fs = require('node:fs');
const zlib = require('node:zlib');
const os = require('node:os');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

function png(size) {
  // A plain drawn square, no real person: enough for the crop and upload.
  const rows = [];
  for (let y = 0; y < size; y++) {
    const row = Buffer.alloc(1 + size * 3);
    for (let x = 0; x < size; x++) row.set(x < size / 2 ? [15, 93, 87] : [243, 244, 246], 1 + x * 3);
    rows.push(row);
  }
  const chunk = (type, data) => {
    const body = Buffer.concat([Buffer.from(type), data]);
    const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32 ? zlib.crc32(body) >>> 0 : 0);
    return Buffer.concat([length, body, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0); header.writeUInt32BE(size, 4); header.set([8, 2, 0, 0, 0], 8);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', header),
    chunk('IDAT', zlib.deflateSync(Buffer.concat(rows))), chunk('IEND', Buffer.alloc(0))]);
}

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'lokforaren-live-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '', browser;
  fixture.stderr.on('data', (value) => { stderr += value; });
  try {
    const { base, stations } = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: fixture.stdout });
      const timeout = setTimeout(() => reject(new Error('Backend start timeout: ' + stderr)), 15000);
      lines.once('line', (line) => { clearTimeout(timeout); resolve(JSON.parse(line)); });
      fixture.once('exit', (code) => { clearTimeout(timeout); reject(new Error('Backend exited: ' + code + ' ' + stderr)); });
    });
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const owner = await browser.newContext();
    await owner.request.post(base + '/v1/auth/login', { data: { email: 'casper@example.se', password: 'ett-langt-losenord' } });
    const clock = (data) => owner.request.post(base + '/v1/clock', { data });
    const context = await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));

    // 1. A driver who signs in on the admin page is sent to Lokföraren.
    await page.goto(base + '/login');
    await page.locator('#login-email').fill('benny@example.se');
    await page.locator('#login-password').fill('forarens-losenord');
    await page.locator('#login-form button[type="submit"]').click();
    await page.waitForURL(base + '/lokforaren/');
    await page.locator('#view-choose').waitFor({ state: 'visible' });

    // 2. Choose 132 (DEMO) by number.
    await page.locator('#duty-query').fill('132');
    await page.locator('#duty-found').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#found-demo').isVisible(), true);
    await page.locator('#duty-activate').click();
    await clock({ action: 'set', time: '17:51:00', speed: 4 });
    await page.locator('#view-korplan').waitFor({ state: 'visible' });

    // 3. The NOW line spans the whole width and moves with the server clock.
    const line = page.locator('.tl-nowline');
    await line.waitFor({ state: 'visible' });
    const box = await line.boundingBox();
    assert.ok(box.width >= 389, `the NOW line is ${box.width}px wide`);
    const offset = () => line.evaluate((element) => new DOMMatrix(getComputedStyle(element).transform).m42);
    const first = await offset();
    await page.waitForTimeout(3000);
    const second = await offset();
    assert.ok(second > first, `the line moved from ${first} to ${second}`);
    assert.ok(second - first < 3, 'at 4× the line moves under a pixel a second');
    assert.match(await page.locator('#next-title').innerText(), /Tveiten/);

    // 3b. Seconds: a tap on the clock hides them in the header and on the NOW chip, and
    //     the same choice is under Me; another tap brings them back.
    await page.locator('#clock').click();
    await page.waitForFunction(() => /^\d{2}:\d{2}$/.test(document.querySelector('#clock').textContent));
    assert.match(await page.locator('.tl-nowchip').innerText(), /^\d{2}:\d{2}$/);
    await page.locator('.lf-tab[data-tab="me"]').click();
    assert.equal(await page.locator('#me-seconds').isChecked(), false);
    await page.locator('#me-seconds').check();
    await page.waitForFunction(() => /^\d{2}:\d{2}:\d{2}$/.test(document.querySelector('#clock').textContent));
    await page.locator('.lf-tab[data-tab="duty"]').click();
    await page.locator('.tl-nowchip').dispatchEvent('click'); // den rör sig hela tiden
    await page.waitForFunction(() => /^\d{2}:\d{2}$/.test(document.querySelector('#clock').textContent));
    await page.locator('#clock').click();
    await page.waitForFunction(() => /^\d{2}:\d{2}:\d{2}$/.test(document.querySelector('#clock').textContent));

    // 4. Check in at the suggested station; it shows on the run plan and on the layout.
    await page.locator('#open-checkin').click();
    await page.locator('#sheet-checkin').waitFor({ state: 'visible' });
    assert.match(await page.locator('#checkin-submit').innerText(), /Tveiten/);
    await page.locator('#checkin-submit').click();
    await page.locator('#sheet-checkin').waitFor({ state: 'hidden' });
    await page.locator('.tl-metag').waitFor({ state: 'visible' });
    await page.locator('.lf-tab[data-tab="drivers"]').click();
    await page.locator('#bana .lf-pill.is-me').waitFor();
    assert.match(await page.locator('#people').innerText(), /Tveiten · nyss/);

    // 5. Save an icon: it replaces the initials everywhere.
    const image = path.join(os.tmpdir(), `lokforaren-icon-${process.pid}.png`);
    fs.writeFileSync(image, png(300));
    await page.locator('.lf-tab[data-tab="me"]').click();
    await page.locator('#open-icon').click();
    await page.locator('#icon-file').setInputFiles(image);
    await page.locator('#icon-save').click();
    await page.locator('#sheet-icon').waitFor({ state: 'hidden' });
    await page.locator('#me-avatar img').waitFor();

    // 5b. The same icon in the account: the owner sees it among the users and
    //     changes his own there.
    const admin = await owner.newPage();
    admin.on('pageerror', (error) => errors.push(error.message));
    await admin.goto(base + '/installningar#anvandare');
    const bennyRow = admin.locator('#users-rows tr', { hasText: 'Benny' });
    await bennyRow.locator('.users-avatar img').waitFor();
    assert.match(await bennyRow.locator('.users-avatar img').getAttribute('src'), /^\/v1\/account\/avatars\/.+\?v=\d+$/);
    const casperRow = admin.locator('#users-rows tr', { hasText: 'Casper' });
    assert.equal(await casperRow.locator('.users-avatar').innerText(), 'CA');
    await casperRow.locator('td.r button').click(); // Redigera, på adminens språk
    await admin.locator('#user-edit-icon').waitFor({ state: 'visible' });
    await admin.locator('#user-edit-icon-file').setInputFiles(image);
    await admin.locator('#user-edit-icon-preview img').waitFor();
    await casperRow.locator('.users-avatar img').waitFor();
    await admin.close();
    fs.unlinkSync(image);

    // 5c. The drivers on the layout in Drift: the owner sees Benny's icon and Maja's
    //     initials at the stations they checked in at, and Maja moves at once when she
    //     checks in somewhere else (/v1/events, "drivers").
    const checkIn = (on, station) => on.evaluate(async (name) => {
      const response = await fetch('/v1/driver/checkins', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ station: name }) });
      if (!response.ok) throw new Error(await response.text());
    }, station);
    await checkIn(page, stations[1]);
    const maja = await browser.newContext({ locale: 'sv-SE' });
    await maja.request.post(base + '/v1/auth/login', { data: { email: 'maja@example.se', password: 'forarens-losenord' } });
    const majaPage = await maja.newPage();
    await majaPage.goto(base + '/lokforaren/');
    await checkIn(majaPage, stations[0]);
    const drift = await owner.newPage();
    drift.on('pageerror', (error) => errors.push(error.message));
    await drift.goto(base + '/drift');
    const mark = (who) => drift.locator('#overview-topology .topology-driver', { has: drift.locator('title', { hasText: who }) });
    await mark('Benny').locator('image').waitFor({ state: 'attached' });
    assert.match(await mark('Benny').locator('image').getAttribute('href'), /^\/v1\/display\/driver-avatars\/.+\?v=\d+$/);
    assert.equal(await mark('Maja').locator('text').textContent(), 'MA');
    const stationOf = (who) => mark(who).evaluate((element) => element.closest('.topology-node').getAttribute('aria-label'));
    assert.match(await stationOf('Benny'), new RegExp('^' + stations[1]));
    assert.match(await stationOf('Maja'), new RegExp('^' + stations[0]));
    await checkIn(majaPage, stations[stations.length - 1]);
    await drift.waitForFunction((name) => [...document.querySelectorAll('#overview-topology .topology-driver')]
      .some((element) => element.querySelector('title')?.textContent.startsWith('Maja') && element.closest('.topology-node').getAttribute('aria-label').startsWith(name)),
    stations[stations.length - 1], { timeout: 8000 });
    await drift.close();

    // 5d. The same on the big screen and the participants' page, without signing in:
    //     Benny with his icon; Maja, who turns "Show my icon on the layout" off, is gone.
    const guest = await browser.newContext({ locale: 'sv-SE', viewport: { width: 1600, height: 900 } });
    const screen = await guest.newPage();
    screen.on('pageerror', (error) => errors.push(error.message));
    await screen.goto(base + '/display/topology');
    const onScreen = (who) => screen.locator('#topology-svg .topology-driver', { has: screen.locator('title', { hasText: who }) });
    await onScreen('Benny').locator('image').waitFor({ state: 'attached' });
    assert.equal(await onScreen('Benny').locator('image').evaluate((image) => image.getBBox().width > 0), true);
    assert.equal(await screen.evaluate(async (url) => (await fetch(url)).status, await onScreen('Benny').locator('image').getAttribute('href')), 200);
    await onScreen('Maja').waitFor({ state: 'attached' });
    await majaPage.evaluate(async () => {
      const response = await fetch('/v1/driver/profile', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ visible: false }) });
      if (!response.ok) throw new Error(await response.text());
    });
    await onScreen('Maja').waitFor({ state: 'detached', timeout: 8000 });
    const participant = await guest.newPage();
    participant.on('pageerror', (error) => errors.push(error.message));
    await participant.goto(base + '/');
    await participant.locator('#pv-topology .topology-driver title', { hasText: 'Benny' }).waitFor({ state: 'attached' });
    assert.equal(await participant.locator('#pv-topology .topology-driver title', { hasText: 'Maja' }).count(), 0);
    await guest.close();
    await maja.close();

    // 6. A stopped clock stops the line and says so.
    await page.locator('.lf-tab[data-tab="duty"]').click();
    await clock({ action: 'stop' });
    await page.locator('#banner.is-stopped').waitFor({ state: 'visible', timeout: 8000 });
    const stopped = await offset();
    await page.waitForTimeout(2500);
    assert.equal(await offset(), stopped);

    // 7. English, and no style attributes anywhere (the server's CSP forbids them).
    await page.locator('.lf-tab[data-tab="me"]').click();
    await page.locator('#me-language').selectOption('en');
    assert.equal(await page.locator('.lf-tab[data-tab="drivers"] span').innerText(), 'Drivers');
    const html = fs.readFileSync(path.join(root, 'src/tmbox_gateway/web/lokforaren/index.html'), 'utf8');
    assert.equal(/\sstyle=/.test(html), false);
    assert.deepEqual(errors, []);
    console.log('lokforaren-live: ok (sign-in, duty 132, NOW line, seconds, check-in, layout, icon in app and account, drivers on the Drift map, big screen and participants page, stopped clock, English)');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
