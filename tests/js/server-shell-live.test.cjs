// End-to-end browser smoke against REAL isolated HTTP servers + temporary SQLite.
// No page.route, response fixtures, production servers, MQTT or Cloud network IO.
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
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const errors = [];
    const page = await browser.newPage({ viewport: { width: 1280, height: 960 } });
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => errors.push(error.message));
    const requests = [];
    page.on('request', request => requests.push(request.url()));
    const screenshot = async name => {
      if (process.env.SERVER_SHELL_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.SERVER_SHELL_SCREENSHOTS, 'live-' + name + '.png'), fullPage: true });
    };
    async function login(base) {
      await page.goto(base);
      await page.locator('#login-form').waitFor({ state: 'visible' }).catch(async error => {
        console.error('Login state:', page.url(), await page.locator('body').innerText());
        console.error('Auth:', await (await page.request.get(base + '/v1/auth/status')).text());
        console.error('Setup:', await (await page.request.get(base + '/v1/setup')).text());
        await screenshot('login-failure');
        throw error;
      });
      await page.locator('#login-username').fill('smoke-admin');
      await page.locator('#login-password').fill('isolated-browser-test');
      await page.locator('#login-form button[type="submit"]').click();
      await page.locator('#workspace-options button').first().waitFor();
    }
    await login(urls.eu);
    assert.equal(await page.locator('#workspace-options button').count(), 2);
    const messages = await page.request.get(urls.eu + '/assets/shell-messages.js');
    assert.equal(messages.status(), 200);
    assert.match(await messages.text(), /Cloud-only Server shell copy/);
    await screenshot('eu-workspaces');
    await page.locator('#workspace-options button').first().click();
    await page.locator('#overview-clock-start').click();
    await page.waitForFunction(() => document.querySelector('#overview-clock-start').disabled && !document.querySelector('#overview-clock-stop').disabled);
    assert.equal((await (await page.request.get(urls.eu + '/v1/clock')).json()).running, true);
    await page.locator('#overview-clock-stop').click();
    await page.waitForFunction(() => !document.querySelector('#overview-clock-start').disabled);
    assert.equal((await (await page.request.get(urls.eu + '/v1/clock')).json()).running, false);
    await screenshot('eu-overview');
    await page.locator('#application-menu summary').click();
    await page.locator('#open-settings').click();
    await page.locator('#device-list').getByText('TBX-SMOKE', { exact: true }).waitFor();
    assert.equal(await page.locator('#device-management').isVisible(), true);
    assert.equal(await page.locator('#device-form').isVisible(), false);
    await screenshot('eu-settings');
    await page.locator('#workspace-home').click();
    assert.equal(await page.locator('#overview-view').isVisible(), true);
    await page.locator('#application-menu summary').click();
    await page.locator('#application-menu a[href="#workspaces"]').click();
    await page.getByRole('button', { name: /^TKL/ }).click();
    await page.waitForURL(urls.eu + '/tkl/');
    await page.locator('body').waitFor();
    await screenshot('tkl-entry');
    await page.getByRole('button', { name: 'Anslut', exact: true }).click();
    await page.getByRole('button', { name: /^CDA/ }).click();
    await page.getByRole('button', { name: 'Bekräfta station och fortsätt', exact: true }).click();
    await page.locator('.operator-field input').fill('Isolated smoke operator');
    await page.getByRole('button', { name: 'Starta trafikpass', exact: true }).click();
    await page.getByRole('button', { name: 'Hem', exact: true }).waitFor().catch(async error => {
      await screenshot('tkl-home-failure');
      console.error('TKL state:', await page.locator('body').innerText());
      throw error;
    });
    const documentCount = requests.filter(url => url === urls.eu + '/tkl/').length;
    await page.getByRole('button', { name: 'Hem', exact: true }).click();
    assert.equal(page.url(), urls.eu + '/tkl/');
    assert.equal(await page.evaluate(() => localStorage.getItem('trainmeet-tkl.station-id')), 'station-a');
    assert.equal(requests.filter(url => url === urls.eu + '/tkl/').length, documentCount, 'TKL Home must not reload or leave the workspace');
    await screenshot('tkl-home');
    await page.getByRole('button', { name: 'Meny', exact: true }).click();
    await page.locator('a[href="/#settings"]').click();
    await page.locator('#settings-heading').waitFor({ state: 'visible' });
    await page.locator('#workspace-home').click();
    await page.waitForURL(urls.eu + '/tkl/');
    await page.getByRole('button', { name: 'Hem', exact: true }).waitFor();

    // Separate origin/database, no meet change in the EU fixture.
    await login(urls.us);
    assert.equal(await page.locator('#workspace-options button').count(), 2);
    assert.equal(await page.getByRole('button', { name: /^Dispatcher/ }).count(), 1);
    assert.equal(await page.getByRole('button', { name: /^Conductor/ }).count(), 0);
    assert.equal(await page.getByRole('button', { name: /^TKL/ }).count(), 0);
    await page.locator('#workspace-options button').first().click();
    assert.equal(await page.locator('#us-runtime-summary').isVisible(), true);
    assert.equal(await page.locator('.topology-overview-card').isVisible(), false);
    await page.locator('#overview-clock-start').click();
    await page.waitForFunction(() => document.querySelector('#overview-clock-start').disabled && !document.querySelector('#overview-clock-stop').disabled);
    assert.equal((await (await page.request.get(urls.us + '/v1/clock')).json()).running, true);
    await page.locator('#overview-clock-stop').click();
    await page.waitForFunction(() => !document.querySelector('#overview-clock-start').disabled);
    assert.equal((await (await page.request.get(urls.us + '/v1/clock')).json()).running, false);
    await screenshot('us-overview');
    assert.deepEqual(errors, []);
    assert.ok(requests.every(url => url.startsWith(urls.eu + '/') || url.startsWith(urls.us + '/')), 'Unexpected non-fixture network request');
    console.log('LIVE isolated HTTP/SQLite smoke passed:', JSON.stringify(urls), 'EU/US clocks, chooser, Settings TMBox, Home, TKL setup + Home + Settings return, served i18n asset.');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
    if (fixture.exitCode === null) await new Promise(resolve => fixture.once('exit', resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
