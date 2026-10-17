// Sidan Anslut ett ställverk (/anslut), i en riktig webbläsare mot en riktig
// server med tillfällig databas (samma fixtur som server-shell-live). Inget
// nätverk utåt.
//
// Casper 2026-10-17: "Är man inloggad som admin borde koden synas enklare.
// Inte i vyn som alla ser men någon sida direkt anslutning till den vy som
// alla ser." Deltagarvyn länkar till /anslut; utan inloggning visas
// inloggningen och därefter samma sida med adressen och koden.
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
    const context = await browser.newContext({ locale: 'sv-SE', viewport: { width: 1280, height: 900 } });
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: base });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const shot = async name => { if (process.env.SHOTS) await page.screenshot({ path: path.join(process.env.SHOTS, name) }); };

    // Deltagarvyn: länken syns för alla, koden gör det inte.
    await page.goto(base + '/');
    const link = page.locator('#pv-signalbox-link');
    await link.waitFor({ state: 'visible' });
    assert.equal(await link.getAttribute('href'), '/anslut');
    assert.equal(await page.locator('#participant-view').evaluate(node => /\b\d{3}-?\d{3}\b/.test(node.textContent)), false, 'ingen kod i deltagarvyn');
    await shot('connect-page-participant.png');
    await link.click();

    // Utan inloggning: inloggningen, och efter den samma sida.
    await page.locator('#login-form').waitFor({ state: 'visible' });
    assert.equal(new URL(page.url()).pathname, '/anslut');
    await page.locator('#login-email').fill('smoke-admin@example.se');
    await page.locator('#login-password').fill('isolated-browser-test');
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator('#connect-view').waitFor({ state: 'visible' });
    assert.equal(new URL(page.url()).pathname, '/anslut', 'inloggningen leder tillbaka till sidan');
    assert.equal(await page.locator('#overview-view').isVisible(), false);

    const connection = await (await page.request.get(base + '/v1/display/connection')).json();
    assert.equal(connection.code_state, 'valid', JSON.stringify(connection));
    await page.waitForFunction(code => document.querySelector('#connect-page-code').textContent === code, connection.code);
    // Samma adress som Drift visar: serverns egen på det lokala nätet, sidans adress bakom HTTPS.
    assert.equal(await page.locator('#connect-page-address').textContent(), `http://${connection.host}:${connection.port}`);
    assert.equal(await page.locator('#connect-page-state').textContent(), 'Gäller tills vidare');
    await shot('connect-page.png');

    // Kopiera lägger koden i urklipp.
    await page.locator('[data-copy="connect-page-code"]').click();
    await page.waitForFunction(async code => (await navigator.clipboard.readText()) === code, connection.code);

    // Ny kod byter koden på sidan.
    await page.locator('#connect-page-new-code').click();
    await page.waitForFunction(old => { const text = document.querySelector('#connect-page-code').textContent; return /^\d{3}-?\d{3}$/.test(text) && text !== old; }, connection.code);
    const renewed = await (await page.request.get(base + '/v1/display/connection')).json();
    assert.equal(await page.locator('#connect-page-code').textContent(), renewed.code);

    // Drift länkar hit från Anslut en box, och inloggad leder / till Drift som förut.
    await page.goto(base + '/drift');
    await page.locator('#overview-view').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#connect-terminals a[href="/anslut"]').count(), 1);
    assert.equal(await page.locator('#connect-view').isVisible(), false);

    assert.deepEqual(errors, []);
    console.log('connect page live: ok');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
