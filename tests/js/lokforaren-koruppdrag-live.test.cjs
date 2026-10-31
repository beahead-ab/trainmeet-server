// Köruppdragen i Inställningar → Lokförare mot en riktig TrainMeet Server
// (#225): administratören lägger in planeringsfilen och sedan PDF:en,
// granskar och gör köruppdraget tillgängligt; föraren hittar tjänsten och
// dess förlaga i Lokföraren.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');
const { tinyPdf, tinyPlan } = require('./lokforaren-fixtures.cjs');
const root = path.resolve(__dirname, '../..');

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'lokforaren-live-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '', browser;
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'lokforaren-koruppdrag-'));
  fixture.stderr.on('data', (value) => { stderr += value; });
  try {
    const { base, stations } = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: fixture.stdout });
      const timeout = setTimeout(() => reject(new Error('Backend start timeout: ' + stderr)), 15000);
      lines.once('line', (line) => { clearTimeout(timeout); resolve(JSON.parse(line)); });
      fixture.once('exit', (code) => { clearTimeout(timeout); reject(new Error('Backend exited: ' + code + ' ' + stderr)); });
    });
    const [first, last] = [stations[0], stations[stations.length - 1]];
    const middle = stations[Math.floor(stations.length / 2)];
    // ASCII-namn: Playwright skickar ingen change-händelse för å, ä och ö i filnamnet.
    const planFile = path.join(folder, 'Grimslov-2027.json');
    fs.writeFileSync(planFile, JSON.stringify(tinyPlan([first, middle, last])));
    const pdfFile = path.join(folder, 'Koruppdrag-Grimslov-2027.pdf');
    fs.writeFileSync(pdfFile, tinyPdf([
      [[40, 780, 'Köruppdrag Grimslöv 2027']],
      [[40, 780, 'Tjeneste 211'], [40, 740, first], [240, 740, 'G 5453']],
      [[40, 740, last], [240, 740, 'G 5456']],
      [[40, 780, 'Tjeneste 212'], [40, 740, middle], [240, 740, '21']],
      [[40, 780, 'Tjeneste 213'], [40, 740, first], [240, 740, 'G 5481']],
    ]));

    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const owner = await browser.newContext({ locale: 'sv-SE', viewport: { width: 1280, height: 900 } });
    await owner.request.post(base + '/v1/auth/login', { data: { email: 'casper@example.se', password: 'ett-langt-losenord' } });
    const page = await owner.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    const message = page.locator('#lf-docs-message');

    // 1. Utan planeringsfil säger sidan att den behövs först.
    await page.goto(base + '/installningar#lokforare');
    await page.locator('#lokforare').waitFor({ state: 'visible' });
    await page.locator('#lf-docs-empty').waitFor({ state: 'visible' });
    await page.locator('#lf-file').setInputFiles(pdfFile);
    await page.waitForFunction(() => /Börja med planeringsfilen/.test(document.querySelector('#lf-docs-message').textContent));

    // 2. Planeringsfilen ger tjänsterna, som syns i granskningen.
    await page.locator('#lf-file').setInputFiles(planFile);
    await page.waitForFunction(() => /3 tjänster lästes in ur Grimslöv 2027/.test(document.querySelector('#lf-docs-message').textContent));
    await page.locator('#lf-review').waitFor({ state: 'visible' });
    assert.deepEqual(await page.locator('#lf-review-rows tr td:first-child').allInnerTexts(), ['211', '212', '213']);
    assert.match(await page.locator('#lf-review-rows tr').first().innerText(), /17:10–21:02\s+G 5453 · G 5456/);
    assert.match(await page.locator('#lf-docs-rows').innerText(), /Ingen förlaga än/);

    // 3. PDF:en blir förlagan; sidorna hittas för varje tjänst.
    await page.locator('#lf-file').setInputFiles(pdfFile);
    await page.waitForFunction(() => /Sidorna hittades för 3 av 3 tjänster i 5 sidor/.test(document.querySelector('#lf-docs-message').textContent), null, { timeout: 30000 });
    await page.waitForFunction(() => [...document.querySelectorAll('#lf-review-rows tr td:last-child')].map((cell) => cell.textContent).join(' ') === '2–3 4 5');
    assert.match(await page.locator('#lf-docs-rows').innerText(), /sidor för 3 av 3 tjänster/);

    // 4. Förarna ser inget förrän köruppdraget är tillgängligt.
    const driver = await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    await driver.request.post(base + '/v1/auth/login', { data: { email: 'benny@example.se', password: 'forarens-losenord' } });
    const before = await (await driver.request.get(base + '/v1/driver/duties?q=211')).json();
    assert.deepEqual(before.duties, []);
    await page.getByRole('button', { name: 'Gör tillgängligt för förarna' }).click();
    await page.waitForFunction(() => /är tillgängligt/.test(document.querySelector('#lf-docs-message').textContent));
    assert.match(await page.locator('#lf-docs-rows .kr-pill.ok').innerText(), /Tillgängligt/);
    if (process.env.LF_SHOTS) await page.locator('#lokforare').screenshot({ path: path.join(process.env.LF_SHOTS, 'admin-koruppdrag.png') });

    // 5. Föraren hittar tjänsten och dess förlaga.
    const phone = await driver.newPage();
    phone.on('pageerror', (error) => errors.push(error.message));
    await phone.goto(base + '/lokforaren/');
    await phone.locator('#view-choose').waitFor({ state: 'visible' });
    await phone.locator('#duty-query').fill('211');
    await phone.locator('#duty-found').waitFor({ state: 'visible' });
    const source = phone.locator('#found-source');
    await source.waitFor({ state: 'visible' });
    assert.match(await source.innerText(), /sida 2–3/);
    assert.match(await source.getAttribute('href'), /^\/v1\/driver\/documents\/[0-9a-f]{12}\.pdf#page=2$/);
    if (process.env.LF_SHOTS) await phone.screenshot({ path: path.join(process.env.LF_SHOTS, 'driver-found.png') });
    const original = await driver.request.get(base + (await source.getAttribute('href')).split('#')[0]);
    assert.equal(original.headers()['content-type'], 'application/pdf');
    await phone.locator('#duty-activate').click();
    await phone.locator('#view-korplan').waitFor({ state: 'visible' });
    if (process.env.LF_SHOTS) await phone.screenshot({ path: path.join(process.env.LF_SHOTS, 'driver-korplan.png') });
    assert.deepEqual(errors, []);
    console.log('lokforaren-koruppdrag-live: ok (plan file, PDF pages, review, available, driver finds duty and original)');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
    fs.rmSync(folder, { recursive: true, force: true });
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
