// Full catalogue against the real API, at phone widths and with the meet clock.
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'lokforaren-live-fixture.py')], {
    cwd: root, env: {...process.env, LOKFORAREN_FULL_CATALOG: '1', PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter)},
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '', browser;
  fixture.stderr.on('data', (chunk) => { stderr += chunk; });
  try {
    const {base} = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({input: fixture.stdout});
      const timer = setTimeout(() => reject(new Error('Fixture timeout: ' + stderr)), 15000);
      lines.once('line', (line) => { clearTimeout(timer); resolve(JSON.parse(line)); });
      fixture.once('exit', (code) => { clearTimeout(timer); reject(new Error('Fixture exit ' + code + ': ' + stderr)); });
    });
    browser = await chromium.launch({headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {})});
    const owner = await browser.newContext();
    await owner.request.post(base + '/v1/auth/login', {data: {email: 'casper@example.se', password: 'ett-langt-losenord'}});
    await owner.request.post(base + '/v1/clock', {data: {action: 'stop'}});
    const context = await browser.newContext({locale: 'sv-SE', viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    await context.request.post(base + '/v1/auth/login', {data: {email: 'benny@example.se', password: 'forarens-losenord'}});
    const page = await context.newPage(), errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    let catalogueRequests = 0;
    page.on('request', (request) => { if (new URL(request.url()).pathname === '/v1/driver/duties') catalogueRequests++; });
    const count = (n) => page.waitForFunction((n) => document.querySelectorAll('.lf-result').length === n, n);
    await page.goto(base + '/lokforaren/');
    await count(143); // 142 real services plus the explicitly marked demo.
    assert.equal(await page.locator('#duty-count').innerText(), '143 av 143 uppdrag');
    assert.equal(await page.locator('.lf-result__nr').first().innerText(), '1');
    assert.equal(await page.locator('.lf-result__nr').last().innerText(), '142');

    await page.locator('#duty-query').fill('sj P101');
    await count(71);
    assert.equal(await page.locator('#duty-found').isVisible(), false);
    await page.locator('#duty-operator').selectOption('CargoNet');
    await count(0);
    assert.match(await page.locator('#duty-results').innerText(), /Inga uppdrag matchar/);
    await page.locator('#duty-reset').click();
    await count(143);
    await page.locator('#duty-time').selectOption('soon');
    await count(71);
    await page.locator('#duty-time').selectOption('ongoing');
    await count(72);
    await owner.request.post(base + '/v1/clock', {data: {action: 'set', time: '18:00:00'}});
    await count(143);
    await page.locator('#duty-reset').click();
    await page.locator('#duty-sort').selectOption('start');
    assert.equal(await page.locator('.lf-result__nr').first().innerText(), '2');
    assert.equal(catalogueRequests, 1, 'typing and filters do not refetch the catalogue');

    // A late detail response must not re-enable activation after a new filter.
    let release;
    const delayed = new Promise((resolve) => { release = resolve; });
    await page.route('**/v1/driver/duties/catalog-142', async (route) => { await delayed; await route.continue(); });
    const detail = page.waitForRequest('**/v1/driver/duties/catalog-142');
    await page.locator('#duty-query').fill('142');
    await detail;
    await page.locator('#duty-query').fill('sj');
    const lateResponse = page.waitForResponse('**/v1/driver/duties/catalog-142');
    release();
    await lateResponse;
    assert.equal(await page.locator('#duty-found').isVisible(), false);
    assert.equal(await page.locator('#duty-activate').isDisabled(), true);
    await page.unroute('**/v1/driver/duties/catalog-142');
    assert.equal((await (await context.request.get(base + '/v1/driver/session')).json()).active, null);

    // Language, both themes and narrow layouts preserve the same list and controls.
    await page.locator('.lf-tab[data-tab="me"]').click();
    await page.locator('#me-language').selectOption('en');
    await page.locator('#me-theme').selectOption('light');
    await page.locator('.lf-tab[data-tab="duty"]').click();
    await count(71);
    assert.equal(await page.locator('#duty-count').innerText(), '71 of 143 duties');
    for (const width of [320, 390, 1440]) {
      await page.setViewportSize({width, height: 844});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow at ' + width);
    }
    await page.locator('#duty-reset').click();
    await page.locator('#duty-query').fill('142');
    await page.locator('#duty-found').waitFor({state: 'visible'});
    await page.locator('#duty-activate').click();
    await page.locator('#view-korplan').waitFor({state: 'visible'});
    assert.equal((await (await context.request.get(base + '/v1/driver/session')).json()).active.duty.id, 'catalog-142');

    // A failed catalogue request has an explicit retry; a retry restores all rows.
    let fail = true;
    await page.route('**/v1/driver/duties', (route) => fail ? (fail = false, route.abort()) : route.continue());
    await page.locator('.lf-tab[data-tab="me"]').click();
    await page.locator('.lf-tab[data-tab="catalog"]').click();
    await page.locator('#duty-retry').waitFor({state: 'visible'});
    await page.locator('#duty-retry').click();
    await count(143);
    assert.equal(await page.locator('#duty-retry').isVisible(), false);
    assert.deepEqual(errors, []);
    console.log('lokforaren-duties-live: ok (143 duties, combined filters, meet time, races, retry, activation, English, phone/desktop)');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
