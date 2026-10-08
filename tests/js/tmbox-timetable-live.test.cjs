// Webb-TMBoxens tidtabell med förseningar, mot en riktig server med tillfällig
// databas (samma fixtur som server-shell-live). Inget nätverk utåt.
//
// Casper 2026-10-08: "Glöm inte bort webb tmboxarna … I dessa ska man oxå
// kunna ställa in om man ska visa försenings markering." Boxen visar stationens
// tidtabell bredvid sig. Servern skickar varje rads försening; hur mycket som
// syns väljs i kortet Din TMBox (tomt följer träffens förval, 2).
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

const shift = (time, minutes) => {
  const [h, m] = time.split(':').map(Number);
  const value = (h * 60 + m + minutes + 1440) % 1440;
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
};

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
    const generation = (await (await admin.request.get(base + '/v1/server-context')).json()).selected_meet.generation;
    const clock = async (time) => {
      for (const data of [{ action: 'stop' }, { action: 'set', time: `${time}:00` }]) {
        const response = await admin.request.post(base + '/v1/clock', { data: { ...data, meet_generation: generation } });
        assert.equal(response.ok(), true, await response.text());
      }
    };

    const phone = await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 } });
    const page = await phone.newPage();
    page.setDefaultTimeout(15000);
    const errors = [], blocked = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (/Content Security Policy/i.test(message.text())) blocked.push(message.text()); });
    await page.goto(base + '/tmbox/');
    await page.locator('.box-code').getByText(/^WEB/).waitFor();
    const box = await page.evaluate(() => JSON.parse(localStorage.getItem('trainmeet.browser-tmbox')));
    assert.equal((await admin.request.post(base + '/v1/devices/assign', { data: { device_code: box.device_code, station_id: 'station-a' } })).status(), 200);
    await page.locator('.box-timetable caption').getByText('Tidtabell · Charlottendahl').waitFor();

    // Första avgången från stationen; klockan sex minuter efter den. Tåget står
    // kvar (tidtabellen ställde det där) och räknas som sex minuter sent, beräknat.
    const table = await (await phone.request.get(base + '/v1/tmbox/terminal/timetable', { headers: { Authorization: `Bearer ${box.access_token}` } })).json();
    const departure = table.rows.find(row => row.kind === 'departure');
    assert.ok(departure, 'the station has a departure');
    await clock(shift(departure.time, 6));
    const row = page.locator('.box-timetable tbody tr').filter({ has: page.locator('th', { hasText: new RegExp(`^${departure.train_number}$`) }) })
      .filter({ hasText: `Avg` }).first();
    const level = page.locator('#box-deviation-level');

    // 2 När det inträffar (träffens förval): inget rött, ingen ny tid.
    await page.waitForTimeout(5600);
    assert.equal(await level.inputValue(), '', 'follows the meet');
    assert.match(await level.locator('option').first().textContent(), /Som träffen: 2 · När det inträffar/);
    assert.equal(await row.locator('.tm-delay, .tm-early, .tm-was').count(), 0, 'level 2 shows no delay');
    assert.match(await page.locator('#box-deviation-note').textContent(), /Nyss/);

    // 4 Fler: röd bricka, överstruken tid och den nya tiden, "beräknad".
    await level.selectOption('4');
    await row.locator('.tm-delay').getByText('+6').waitFor();
    assert.equal(await row.locator('.tm-was').textContent(), departure.time);
    assert.equal(await row.locator('.tm-new').textContent(), `Avg ${shift(departure.time, 6)}`);
    assert.match(await row.locator('.tm-delay').getAttribute('aria-label'), /6 min sen · beräknad/);
    assert.equal(await row.locator('.tm-recent').count(), 0, 'a new level lights nothing up');
    if (process.env.SHOTS) await page.screenshot({ path: path.join(process.env.SHOTS, 'tmbox-level4.png'), fullPage: true });
    assert.equal(await page.evaluate(() => localStorage.getItem('trainmeet.tmbox.deviationLevel')), '4');
    const colours = await row.locator('.tm-delay').evaluate(element => [getComputedStyle(element).backgroundColor, getComputedStyle(element).color]);
    assert.deepEqual(colours, ['rgb(180, 35, 24)', 'rgb(255, 255, 255)'], 'red with white digits');

    // Förseningen växer: raden får "Nyss".
    await clock(shift(departure.time, 8));
    await row.locator('.tm-delay').getByText('+8').waitFor({ timeout: 8000 });
    await row.locator('.tm-recent').getByText('Nyss').waitFor();
    if (process.env.SHOTS) await page.locator('.box-timetable').screenshot({ path: path.join(process.env.SHOTS, 'tmbox-nyss.png') });
    assert.match(await row.getAttribute('class'), /is-updated/);

    // 3 Diskret: liten röd text, ingen bricka och ingen överstrykning.
    await level.selectOption('3');
    await row.locator('.tm-delay--text').getByText('+8').waitFor();
    assert.equal(await row.locator('.tm-was').count(), 0);

    // 1 Ingen markering: bara tidtabellens tid.
    await level.selectOption('1');
    await page.waitForFunction(() => !document.querySelector('.box-timetable .tm-delay'));
    assert.equal(await row.locator('td').first().textContent(), `Avg ${departure.time}`);

    // Valet sparas i webbläsaren och står kvar efter omladdning.
    await page.reload();
    await page.locator('.box-timetable caption').getByText('Tidtabell · Charlottendahl').waitFor();
    assert.equal(await page.locator('#box-deviation-level').inputValue(), '1');
    await page.locator('#box-deviation-level').selectOption('');
    await row.locator('td').first().getByText(`Avg ${departure.time}`).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('trainmeet.tmbox.deviationLevel')), null, 'as the meet clears the choice');

    // Ingen sidled på telefonen, inga fel och inga CSP-spärrar.
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    assert.deepEqual(blocked, []);
    console.log('PASS webb-TMBoxens tidtabell med förseningar och nivåval');
  } finally {
    await browser?.close();
    fixture.stdin.end();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
