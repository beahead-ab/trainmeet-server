// Snabbinställningen för sekunderna, i en riktig webbläsare mot en riktig
// server med tillfällig databas (samma fixtur som server-shell-live). Inget
// nätverk utåt.
//
// Casper 2026-10-16: "Klickar man på klockan så togglas sekunder av/på
// (snabbinställning)." Kör och rubrikraden visar timmar och minuter tills
// någon trycker; storskärmen och deltagarvyn följer klockans inställning tills
// dess. Valet gäller webbläsaren och är detsamma som Sekunder i storskärmens
// verktygsrad.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const KEY = 'trainmeet.displayClockSeconds';

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'server-shell-live-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '';
  fixture.stderr.on('data', value => { stderr += value; });
  let browser;
  try {
    const urls = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: fixture.stdout });
      const timeout = setTimeout(() => reject(new Error('Backend start timeout: ' + stderr)), 15000);
      lines.once('line', line => { clearTimeout(timeout); resolve(JSON.parse(line)); });
      fixture.once('exit', code => { clearTimeout(timeout); reject(new Error('Backend exited: ' + code + ' ' + stderr)); });
    });
    const base = urls.eu;
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const errors = [];
    const watch = page => { page.setDefaultTimeout(15000); page.on('pageerror', error => errors.push(error.message)); };
    const shot = async (page, name) => { if (process.env.SHOTS) await page.screenshot({ path: path.join(process.env.SHOTS, name) }); };

    const page = await browser.newPage({ locale: 'sv-SE', viewport: { width: 1280, height: 900 } });
    watch(page);
    await page.goto(base + '/login');
    await page.locator('#login-email').fill('smoke-admin@example.se');
    await page.locator('#login-password').fill('isolated-browser-test');
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator('#overview-view').waitFor({ state: 'visible' });
    const get = async route => (await page.request.get(base + route)).json();
    const generation = (await get('/v1/server-context')).selected_meet.generation;
    // Siffror på skärmarna, med sekunder i klockans inställning; klockan går 4×.
    const appearance = await page.request.post(base + '/v1/clock', { data: { action: 'appearance', style: 'digital', show_seconds: true } });
    assert.equal(appearance.ok(), true, await appearance.text());
    const started = await page.request.post(base + '/v1/clock', { data: { action: 'start', time: '10:40:00', speed: 4, meet_generation: generation } });
    assert.equal(started.ok(), true, await started.text());

    // Kör: minuter tills någon trycker, sedan sekunder, och tillbaka.
    const kor = page.locator('#overview-clock');
    await page.waitForFunction(() => /^\d\d:\d\d$/.test(document.querySelector('#overview-clock').textContent));
    assert.equal(await kor.getAttribute('role'), 'button');
    assert.equal(await kor.getAttribute('title'), 'Tryck för att visa sekunderna');
    await kor.click();
    await page.waitForFunction(() => /^\d\d:\d\d:\d\d$/.test(document.querySelector('#overview-clock').textContent));
    assert.equal(await page.evaluate(key => localStorage.getItem(key), KEY), 'on', 'valet sparas i webbläsaren');
    assert.equal(await kor.getAttribute('title'), 'Tryck för att dölja sekunderna');
    // En 4×-klocka visar varje träffsekund: siffrorna ritas om oftare än en gång i sekunden.
    const seen = await page.evaluate(() => new Promise(resolve => {
      const values = new Set(), started = performance.now();
      const timer = setInterval(() => {
        values.add(document.querySelector('#overview-clock').textContent);
        if (performance.now() - started > 950) { clearInterval(timer); resolve(values.size); }
      }, 50);
    }));
    assert.ok(seen >= 3, `sekunderna går i träffens takt (${seen} olika på en sekund)`);
    await shot(page, 'clock-seconds-kor.png');
    await kor.focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => /^\d\d:\d\d$/.test(document.querySelector('#overview-clock').textContent));
    assert.equal(await page.evaluate(key => localStorage.getItem(key), KEY), 'off');

    // Rubrikraden på en annan sida: samma val, och ett tryck där gäller Kör också.
    await page.goto(base + '/tidtabell');
    const chip = page.locator('#app-clock');
    await chip.waitFor({ state: 'visible' });
    await page.waitForFunction(() => /^\d\d:\d\d$/.test(document.querySelector('#app-clock').textContent));
    await chip.click();
    await page.waitForFunction(() => /^\d\d:\d\d:\d\d$/.test(document.querySelector('#app-clock').textContent));
    await page.goto(base + '/');
    await page.waitForFunction(() => /^\d\d:\d\d:\d\d$/.test(document.querySelector('#overview-clock').textContent));

    // En gäst som öppnar klockan under Stora vyer följer träffen och kan inte
    // ställa om sekunderna (#209).
    const guestContext = await browser.newContext({ locale: 'sv-SE', viewport: { width: 1280, height: 900 } });
    const guest = await guestContext.newPage();
    watch(guest);
    await guest.goto(base + '/');
    await guest.locator('#pv-screens-menu summary').click();
    await guest.locator('#pv-screens-list a[href="/display/clock"]').click();
    await guest.locator('#clock-view .cd-ss').waitFor();
    assert.equal(await guest.locator('#display-app').getAttribute('data-read-only'), 'true');
    await guest.locator('#clock-view').click();
    assert.equal(await guest.locator('#clock-view .clock-digital').getAttribute('data-seconds'), 'true');
    assert.equal(await guest.evaluate(key => localStorage.getItem(key), KEY), null);
    assert.equal(await guest.locator('#display-clock-seconds').isVisible(), false);
    await guestContext.close();

    // Storskärmen i lokalen (öppnad med sin adress) följer inställningen
    // (sekunder på) tills någon trycker på klockan.
    const screen = await (await browser.newContext({ locale: 'sv-SE', viewport: { width: 1280, height: 900 } })).newPage();
    watch(screen);
    await screen.goto(base + '/display/clock');
    await screen.waitForFunction(() => /^\d\d$/.test(document.querySelector('#clock-view .cd-ss')?.textContent || ''));
    await screen.locator('#clock-view').click();
    await screen.waitForFunction(() => document.querySelector('#clock-view .clock-digital')?.dataset.seconds === 'false');
    assert.equal(await screen.locator('#clock-view .cd-ss').textContent(), '');
    assert.equal(await screen.locator('#display-clock-seconds').inputValue(), 'off', 'verktygsraden visar samma val');
    await shot(screen, 'clock-seconds-screen.png');
    await screen.locator('#clock-view').click();
    await screen.waitForFunction(() => /^\d\d$/.test(document.querySelector('#clock-view .cd-ss')?.textContent || ''));
    assert.equal(await screen.locator('#display-clock-seconds').inputValue(), 'on');

    // Deltagarvyn på en telefon: samma sak.
    const phone = await (await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 } })).newPage();
    watch(phone);
    await phone.goto(base + '/');
    await phone.waitForFunction(() => /^:\d\d$/.test(document.querySelector('#pv-clock small')?.textContent || ''));
    await phone.locator('#pv-clock').click();
    await phone.waitForFunction(() => document.querySelector('#pv-clock small')?.textContent === '');
    await shot(phone, 'clock-seconds-participant.png');
    await phone.locator('#pv-clock').click();
    await phone.waitForFunction(() => /^:\d\d$/.test(document.querySelector('#pv-clock small')?.textContent || ''));

    assert.deepEqual(errors, []);
    console.log('clock seconds live: ok');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
