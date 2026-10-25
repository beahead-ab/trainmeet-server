// Browser + actual HTTP/session/controller; only OS/network IO is substituted.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'pi-network-live-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = ''; fixture.stderr.on('data', d => stderr += d);
  const { url } = await new Promise((resolve, reject) => {
    readline.createInterface({ input: fixture.stdout }).once('line', l => resolve(JSON.parse(l)));
    fixture.once('exit', () => reject(new Error(stderr)));
  });
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ locale: 'sv-SE', viewport: { width: 1280, height: 900 } });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto(url + '/login');
    await page.locator('#login-email').fill('smoke-admin@example.se');
    await page.locator('#login-password').fill('isolated-browser-test');
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator('#overview-view').waitFor({ state: 'visible' });
    await page.goto(url + '/installningar#server');
    await page.locator('#pi-network-panel').waitFor({ state: 'visible' });
    assert.equal(await page.locator('.pi-network-profile').count(), 2);
    const home = page.locator('.pi-network-profile').filter({ hasText: 'Hemma' });
    assert.equal(await home.getByRole('button', { name: 'Ta bort', exact: true }).isDisabled(), true);
    const meet = page.locator('.pi-network-profile').filter({ hasText: 'Träffens nät' });
    await meet.getByRole('button', { name: 'Flytta upp', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.pi-network-profile strong')?.textContent === 'Träffens nät');
    await page.locator('#pi-network-add').click();
    await page.locator('#pi-network-ssid').fill('Klubblokalen');
    await page.locator('#pi-network-password').fill('a-private-test-password');
    await page.locator('#pi-network-form button[type="submit"]').click();
    await page.locator('#pi-network-edit').waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#pi-network-password').inputValue(), '');
    const club = page.locator('.pi-network-profile').filter({ hasText: 'Klubblokalen' });
    await club.getByRole('button', { name: 'Redigera', exact: true }).click();
    assert.equal(await page.locator('#pi-network-password').inputValue(), '');
    // Cross in top right, normal Escape and Save/Cancel modal conventions.
    assert.equal(await page.locator('#pi-network-edit .modal-close').count(), 1);
    await page.keyboard.press('Escape');
    await page.locator('#pi-network-edit').waitFor({ state: 'hidden' });
    await club.getByRole('button', { name: 'Anslut nu', exact: true }).click();
    await page.locator('#pi-network-action-submit').click();
    await page.locator('#pi-network-trial').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#pi-network-add').isDisabled(), true);
    await page.locator('#pi-network-rollback').click();
    await page.locator('#pi-network-trial').waitFor({ state: 'hidden' });
    assert.match(await page.locator('#pi-network-state').textContent(), /Hemma/);
    await club.getByRole('button', { name: 'Anslut nu', exact: true }).click();
    await page.locator('#pi-network-action-submit').click();
    await page.locator('#pi-network-trial').waitFor({ state: 'visible' });
    await page.locator('#pi-network-confirm').click();
    await page.locator('#pi-network-trial').waitFor({ state: 'hidden' });
    assert.match(await page.locator('#pi-network-state').textContent(), /Klubblokalen/);
    const status = await (await page.request.get(url + '/v1/host/wifi')).text();
    assert(!status.includes('a-private-test-password'));
    assert(!(await page.evaluate(() => JSON.stringify(localStorage))).includes('a-private-test-password'));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#pi-network-add').click();
    assert(await page.locator('#pi-network-edit').isVisible());
    const bounds = await page.locator('#pi-network-edit').boundingBox();
    assert(bounds.width <= 390 && bounds.x >= 0);
    if (process.env.PI_NETWORK_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.PI_NETWORK_SCREENSHOTS, 'pi-network-mobile.png') });
    await page.keyboard.press('Escape');
    await page.setViewportSize({ width: 1280, height: 1000 });
    await page.locator('#pi-network-panel').scrollIntoViewIfNeeded();
    if (process.env.PI_NETWORK_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.PI_NETWORK_SCREENSHOTS, 'pi-network-desktop.png') });
    // The browser must not send passwords over remote plain HTTP at all.
    const readOnly = JSON.parse(status); readOnly.can_manage = false;
    await page.route('**/v1/host/wifi', route => route.fulfill({ json: readOnly }));
    await page.reload();
    await page.locator('#pi-network-panel').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#pi-network-add').isDisabled(), true);
    assert.equal(await page.locator('#pi-network-scan').isDisabled(), true);
    await page.unroute('**/v1/host/wifi');
    // Server/cloud installs without the helper must not expose dead controls.
    await page.route('**/v1/host/wifi', route => route.fulfill({ json: { supported: false } }));
    await page.reload();
    await page.locator('#server').waitFor({ state: 'visible' });
    assert(await page.locator('#pi-network-panel').isHidden());
    assert.deepEqual(errors, []);
    console.log('Pi network desktop/mobile: ok');
  } finally { await browser.close(); fixture.stdin.end(); }
})().catch(e => { console.error(e); process.exit(1); });
