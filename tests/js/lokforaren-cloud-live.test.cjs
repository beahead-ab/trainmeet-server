// A meet with only the drivers (Cloud #113): no line and no TMBoxes yet,
// köruppdrag from Cloud whose pages the server fetched with the package.
// The driver finds duty 81 and opens its own pages from the server; Drift,
// the clock and the big screens open without errors on the empty meet.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'lokforaren-cloud-live-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '', browser;
  fixture.stderr.on('data', (value) => { stderr += value; });
  try {
    const { base } = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: fixture.stdout });
      const timeout = setTimeout(() => reject(new Error('Backend start timeout: ' + stderr)), 15000);
      lines.once('line', (line) => { clearTimeout(timeout); resolve(JSON.parse(line)); });
      fixture.once('exit', (code) => { clearTimeout(timeout); reject(new Error('Backend exited: ' + code + ' ' + stderr)); });
    });
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });

    // 1. The driver finds 81 and gets its own pages from the server.
    const driver = await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const page = await driver.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(base + '/login');
    await page.locator('#login-email').fill('benny@example.se');
    await page.locator('#login-password').fill('forarens-losenord');
    await page.locator('#login-form button[type="submit"]').click();
    await page.waitForURL(base + '/lokforaren/');
    await page.locator('#duty-query').fill('81');
    await page.locator('#duty-found').waitFor({ state: 'visible' });
    const source = page.locator('#found-source');
    await source.waitFor({ state: 'visible' });
    assert.equal(await source.getAttribute('href'), '/v1/driver/duties/duty-81.pdf');
    assert.match(await source.textContent(), /349–352/);
    const pdf = await page.request.get(base + '/v1/driver/duties/duty-81.pdf');
    assert.equal(pdf.status(), 200);
    assert.equal(pdf.headers()['content-type'], 'application/pdf');
    assert.ok((await pdf.body()).subarray(0, 5).equals(Buffer.from('%PDF-')));
    await page.locator('#duty-activate').click();
    await page.locator('#view-korplan').waitFor({ state: 'visible' });
    assert.deepEqual(errors, []);

    // 2. The administrator's pages and the big screens open on the empty meet.
    const owner = await browser.newContext({ locale: 'sv-SE', viewport: { width: 1440, height: 900 } });
    await owner.request.post(base + '/v1/auth/login', { data: { email: 'casper@example.se', password: 'ett-langt-losenord' } });
    for (const address of ['/drift', '/installningar', '/display/clock', '/display/topology', '/display/graph', '/display/dashboard', '/']) {
      const screen = await owner.newPage();
      const seen = [];
      screen.on('pageerror', (error) => seen.push(error.message));
      const response = await screen.goto(base + address);
      assert.equal(response.status(), 200, address);
      await screen.waitForLoadState('networkidle');
      await screen.waitForTimeout(400);
      assert.deepEqual(seen, [], address);
      await screen.close();
    }
    console.log('lokforaren-cloud-live: ok');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
