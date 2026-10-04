// Browser regression for the two places the admin page restarts the server:
// the last step of the installation guide and "Starta om servern" under
// Programuppdatering. Both used a message line that no longer existed
// (configMessage), so "Slutför och starta om" showed "configMessage is not
// defined" and never reloaded the page. Every request is a local fixture.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const web = path.resolve(__dirname, '../../src/tmbox_gateway/web');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  try {
    const page = await browser.newPage({ locale: 'sv-SE', viewport: { width: 1200, height: 900 } });
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.accept());
    const runtime = { configured: true, linked: true, meet_name: 'Grimslöv 2027', active_day: 'Dagl', station_count: 11, train_count: 499, publication_id: 'pub-1' };
    let installed = false, restarts = 0, infoCalls = 0;
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname.startsWith('/v1/')) {
        let data = {}, status = 200;
        switch (url.pathname) {
          case '/v1/setup': case '/v1/setup/status':
            data = installed ? { required: false, admin_configured: true, runtime } : { required: true, admin_configured: true, step: 'finish', server_name: 'Bennys Pi', runtime };
            break;
          case '/v1/auth/status': data = { authenticated: true, at_the_machine: false }; break;
          case '/v1/setup/complete': installed = true; data = { message: 'Installationen är klar.' }; break;
          case '/v1/server/restart': restarts += 1; data = { message: 'Servern startar om.' }; break;
          case '/v1/info': infoCalls += 1; data = { protocol_version: 1, gateway_id: 'Bennys Pi', authentication_required: true }; break;
          case '/v1/server-context': case '/v1/workspaces':
            data = { selected_meet: { id: 'meet-1', name: runtime.meet_name, publication_id: 'pub-1', operating_region: 'eu', generation: 1 }, operating_region: 'eu', available_workspaces: ['administration', 'tmbox'] };
            break;
          case '/v1/runtime': data = { ...runtime, restart_required: true }; break;
          case '/v1/software': case '/v1/software/update': data = { installed_version: '1.16.4', installed_build: 'test', steps: [] }; break;
          default: data = {};
        }
        return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
      }
      const name = url.pathname.startsWith('/assets/') ? url.pathname.slice(8) : url.pathname.endsWith('.png') ? 'trainmeet-logo.png' : 'index.html';
      const target = path.resolve(web, name);
      if (!target.startsWith(web + path.sep) || !fs.existsSync(target)) return route.fulfill({ status: 404 });
      const ext = path.extname(target);
      const contentType = { '.js': 'application/javascript', '.html': 'text/html', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' }[ext] || 'application/octet-stream';
      return route.fulfill({ status: 200, contentType, body: fs.readFileSync(target) });
    });

    // Step 4 of 4: finishing restarts the server and reloads the page when it is back.
    await page.goto('http://127.0.0.1:9999/');
    await page.locator('#setup-finish-form').waitFor({ state: 'visible' });
    assert.match(await page.locator('#setup-runtime-summary').innerText(), /Grimslöv 2027/);
    const reloaded = page.waitForEvent('load');
    await page.locator('#setup-finish-form button[type=submit]').click();
    await reloaded;
    assert.equal(restarts, 1);
    assert.ok(infoCalls >= 1, 'The page waits for the server to answer again');
    await page.locator('#setup').waitFor({ state: 'hidden' });
    assert.deepEqual(errors, [], 'No script error while finishing the installation');

    // "Starta om servern" under Programuppdatering reports next to its own button.
    await page.goto('http://127.0.0.1:9999/installningar');
    await page.locator('#software-update-message').waitFor({ state: 'attached' });
    const restartReload = page.waitForEvent('load');
    await page.evaluate(() => { state.restartRequired = true; state.restarting = false; return restartServer(); });
    await restartReload;
    assert.equal(restarts, 2);
    assert.deepEqual(errors, [], 'No script error when restarting from Programuppdatering');
    console.log('Server restarts: installation guide and Programuppdatering reload the page without script errors.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
