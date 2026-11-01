// Lokföraren (#225): samma förarkonto på två telefoner mot en riktig server.
// Telefon A aktiverar tjänst 132. Telefon B får frågan om den ska ta över,
// tar över, och A får veta det i sidhuvudet och tar tillbaka tjänsten.
// Aldrig två aktiva tjänster och aldrig ett tyst byte.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'lokforaren-live-fixture.py')], {
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
    const errors = [];
    const phone = async () => {
      const context = await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
      const login = await context.request.post(base + '/v1/auth/login', { data: { email: 'benny@example.se', password: 'forarens-losenord' } });
      assert.equal(login.status(), 200);
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      page.on('pageerror', (error) => errors.push(error.message));
      await page.goto(base + '/lokforaren/');
      await page.waitForFunction(() => ['choose', 'korplan', 'countdown'].includes(document.querySelector('#app').dataset.screen));
      return page;
    };
    const onDuty = (page) => page.waitForFunction(() => ['korplan', 'countdown'].includes(document.querySelector('#app').dataset.screen));
    const elsewhere = (page) => page.waitForFunction(() => document.querySelector('#banner').classList.contains('is-elsewhere')
      && !document.querySelector('#banner').hidden, null, { timeout: 15000 });
    const bannerText = async (page) => (await page.locator('#banner-text').textContent()).trim();

    // 1. Telefon A aktiverar 132.
    const a = await phone();
    await a.locator('#duty-query').fill('132');
    await a.locator('#duty-found').waitFor({ state: 'visible' });
    await a.locator('#duty-activate').click();
    await onDuty(a);

    // 2. Telefon B, samma konto, öppnar appen: kontots tjänst är aktiv på en annan telefon.
    const b = await phone();
    await onDuty(b);
    await elsewhere(b);
    assert.equal(await bannerText(b), 'Tjänst 132 är aktiv på en annan telefon · tryck för att ta över här');
    await b.locator('.lf-tab[data-tab="me"]').click();
    assert.equal((await b.locator('#me-duty').textContent()).trim(), 'Tjänst 132 är aktiv på en annan telefon');
    assert.equal(await b.locator('#end-duty').isVisible(), false, 'B kan inte avsluta A:s tjänst');

    // 3. B byter till tjänst 7: en fråga, inget tyst byte.
    await b.locator('#change-duty').click();
    await b.locator('#duty-query').fill('7');
    await b.locator('#duty-found').waitFor({ state: 'visible' });
    await b.locator('#duty-activate').click();
    const prompt = b.locator('#duty-elsewhere');
    await prompt.waitFor({ state: 'visible' });
    assert.equal((await prompt.textContent()).trim(), 'Tjänst 132 är aktiv på en annan telefon – ta över här?');
    assert.equal((await b.locator('#duty-activate').textContent()).trim(), 'Ta över här');
    assert.equal(await b.evaluate(() => document.querySelector('#app').dataset.screen), 'choose', 'B väntar på svaret');
    const still = await (await a.context().request.get(base + '/v1/driver/session', { headers: {} })).json();
    assert.equal(still.active.duty.number, '132', 'ingenting har bytts än');

    // 4. B tar över. A får veta det i sidhuvudet och kan inte avsluta.
    await b.locator('#duty-activate').click();
    await onDuty(b);
    assert.equal((await b.locator('#duty-title').textContent()).trim(), 'Tjänst 7');
    await elsewhere(a);
    assert.equal(await bannerText(a), 'Tjänsten har tagits över på en annan telefon · tryck för att ta tillbaka');
    await a.locator('.lf-tab[data-tab="me"]').click();
    assert.equal(await a.locator('#end-duty').isVisible(), false);

    // 5. A tar tillbaka 132 genom att trycka på raden. Nu är det B som får veta.
    await a.locator('.lf-tab[data-tab="duty"]').click();
    await a.locator('#banner').click();
    await a.waitForFunction(() => document.querySelector('#banner').hidden || !document.querySelector('#banner').classList.contains('is-elsewhere'));
    await a.locator('.lf-tab[data-tab="me"]').click();
    await a.waitForFunction(() => document.querySelector('#me-duty').textContent.includes('132 är aktiv på den här telefonen'));
    assert.equal(await a.locator('#end-duty').isVisible(), true);
    await elsewhere(b);
    assert.equal(await bannerText(b), 'Tjänsten har tagits över på en annan telefon · tryck för att ta tillbaka');

    // 6. Servern har en enda aktiv tjänst för kontot.
    const presence = await (await a.context().request.get(base + '/v1/driver/presence')).json();
    assert.deepEqual(presence.drivers.filter((driver) => driver.me).map((driver) => driver.duty_number), ['132']);

    // 7. På engelska efter omladdning.
    await b.evaluate(() => { localStorage.setItem('lf.language', '"en"'); });
    await b.reload();
    await elsewhere(b);
    assert.equal(await bannerText(b), 'Duty 132 is active on another phone · tap to take it over here');

    assert.deepEqual(errors, []);
    console.log('lokforaren-two-phones-live: ok (question on the second phone, take over, taken-over line, take back, one active duty, English)');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
  }
})().catch((error) => { console.error(error); process.exit(1); });
