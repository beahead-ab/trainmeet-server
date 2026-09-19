// Browser regression for the one-meet Cloud-only admin shell. No live server
// or user data is touched: every request is fulfilled from local fixtures.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const web = path.resolve(__dirname, '../../src/tmbox_gateway/web');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
    const errors = [];
    const calls = [];
    const screenshot = async name => {
      if (process.env.SERVER_SHELL_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.SERVER_SHELL_SCREENSHOTS, name + '.png'), fullPage: true });
    };
    page.on('pageerror', error => { errors.push(error.message); console.error('Browser:', error.message); });
    page.setDefaultTimeout(12000);
    let region = 'eu';
    let stations = [{ id: 'a', code: 'A', name: 'Alpha' }, { id: 'b', code: 'B', name: 'Beta' }];
    let running = false;
    const user = { user_id: 'u-1', username: 'admin', role: 'owner', invitation_pending: false };
    const runtime = { configured: true, linked: true, cloud_auto_sync: true, meet_name: 'Demo meet', active_day: 'Dagl', publication_id: 'pub-1', server_name: 'Demo server', central_url: 'https://cloud.trainmeet.app/config' };
    const clock = () => ({ configured: true, running, time: '06:00:00', speed: 4 });
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname.startsWith('/v1/')) {
        calls.push([request.method(), url.pathname, request.postData()]);
        let data = {};
        switch (url.pathname) {
          case '/v1/setup': case '/v1/setup/status': data = { required: false, admin_configured: true, runtime }; break;
          case '/v1/auth/status': data = { authenticated: true, at_the_machine: false, username: 'admin' }; break;
          case '/v1/server-context': data = { selected_meet: { id: 'meet-1', name: 'Demo meet', publication_id: 'pub-1', operating_region: region, generation: 7 }, operating_region: region, available_workspaces: region === 'eu' ? ['administration', 'tkl', 'tmbox'] : ['administration', 'dispatcher', 'conductor'], cloud_update: { linked: true, auto_sync: true, state: 'current', current_publication_id: 'pub-1' } }; break;
          case '/v1/runtime': data = runtime; break;
          case '/v1/info': data = { gateway_id: 'Demo server', traffic_session_name: 'Demo meet', runtime }; break;
          case '/v1/admin/access': data = { username: 'admin', password_configured: true }; break;
          case '/v1/devices': data = { devices: [{ device_id: 'esp-1', device_code: 'TBX-123', station_id: 'a', model: 'ESP8266' }], stations: [{ id: 'a', code: 'A', name: 'Alpha' }] }; break;
          case '/v1/admin/users': data = { role: 'owner', users: [user], user }; break;
          case '/v1/admin/users/update': data = { user }; break;
          case '/v1/clock': if (request.method() === 'POST') running = JSON.parse(request.postData()).action === 'start'; data = clock(); break;
          case '/v1/display': data = { clock: clock(), meet: { id: 'meet-1', name: 'Demo meet' }, active_day: 'Dagl', publication_id: 'pub-1', stations, connections: [], routes: [{ train_number: '421', station_id: 'a', departure_time: '06:05' }, { train_number: '421', station_id: 'b', arrival_time: '06:20' }], train_positions: [], connection_states: [], connection: { screens: [] } }; break;
          case '/v1/config/check': data = { message: 'Senaste config används.' }; break;
          case '/v1/software/update': case '/v1/software': data = { installed_version: '1.6.2', installed_build: 'test', steps: [] }; break;
          default: data = { backups: [], panels: [], message: 'Sparat' };
        }
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
      }
      const name = url.pathname.startsWith('/assets/') ? url.pathname.slice(8) : url.pathname.endsWith('.png') ? 'trainmeet-logo.png' : 'index.html';
      const target = path.resolve(web, name);
      if (!target.startsWith(web + path.sep) || !fs.existsSync(target)) return route.fulfill({ status: 404 });
      const ext = path.extname(target);
      const contentType = { '.js': 'application/javascript', '.html': 'text/html', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2' }[ext] || 'application/octet-stream';
      return route.fulfill({ status: 200, contentType, body: fs.readFileSync(target) });
    });
    await page.goto('http://127.0.0.1:9999/');
    await page.locator('#workspace-options button').first().waitFor();
    assert.equal(await page.locator('#workspace-picker').isVisible(), true);
    assert.equal(await page.locator('#workspace-options button').count(), 3);
    assert.equal(await page.locator('#workspace-options button svg[aria-hidden="true"]').count(), 3);
    for (const [language, heading, administration] of [
      ['sv', 'Välj arbetsyta', 'Drift och administration'],
      ['da', 'Vælg arbejdsområde', 'Drift og administration'],
      ['nb', 'Velg arbeidsområde', 'Drift og administrasjon'],
      ['en', 'Choose workspace', 'Operations and administration'],
      ['de', 'Arbeitsbereich auswählen', 'Betrieb und Verwaltung'],
    ]) {
      await page.locator('[data-language-picker]').selectOption(language);
      assert.equal(await page.locator('#workspace-heading').innerText(), heading);
      assert.equal(await page.locator('#workspace-options button strong').first().innerText(), administration);
      assert.equal(await page.locator('[data-operating-mode], #build-view, #build-sidebar').count(), 0);
    }
    await page.locator('[data-language-picker]').selectOption('sv');
    await screenshot('workspace-chooser');
    await page.getByRole('button', { name: 'TMBox v2', exact: true }).focus();
    await page.keyboard.press('Enter');
    await page.locator('#tmbox-v2-view').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#run-tabs').isVisible(), false);
    assert.equal(await page.locator('#keypad-v2 button').count(), 16);
    assert.equal(await page.locator('#workspace-home').getAttribute('href'), '/#tmbox');
    await screenshot('tmbox-workspace');
    await page.reload();
    await page.locator('#tmbox-v2-view').waitFor({ state: 'visible' });
    await page.locator('#application-menu summary').click();
    await page.locator('#open-settings').click();
    await page.locator('#settings-heading').waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => tmboxV2.timer), null);
    await page.locator('#workspace-home').click();
    await page.locator('#tmbox-v2-view').waitFor({ state: 'visible' });
    await page.locator('#application-menu summary').click();
    await page.locator('#application-menu a[href="#screens"]').click();
    await page.locator('#displays-view').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#run-tabs').isVisible(), false);
    await page.locator('#workspace-home').click();
    await page.locator('#tmbox-v2-view').waitFor({ state: 'visible' });
    await page.locator('#application-menu summary').click();
    await page.locator('#application-menu a[href="#workspaces"]').click();
    await page.locator('#workspace-picker').waitFor({ state: 'visible' });
    assert.equal(await page.evaluate(() => tmboxV2.timer), null);
    await page.locator('#workspace-options button').first().click();
    await page.locator('#overview-view').waitFor({ state: 'visible' });
    assert.equal(await page.locator('[data-run-tab="tmbox"]').count(), 0);
    assert.equal(await page.locator('#overview-view').isVisible(), true);
    assert.equal(await page.locator('#run-tabs').count(), 0);
    assert.equal(await page.locator('#overview-view #overview-traffic').isVisible(), true);
    assert.equal(await page.locator('#traffic-stations .traffic-card').count(), 2);
    assert.equal(await page.locator('#overview-timetable').getAttribute('open'), null);
    // Filtering reuses the overview's snapshot, no extra fetch or timer.
    const displayCalls = calls.filter(call => call[1] === '/v1/display').length;
    await page.locator('#traffic-station').selectOption('b');
    assert.equal(await page.locator('#traffic-stations .traffic-card').count(), 1);
    assert.match(await page.locator('#traffic-stations').innerText(), /Beta/);
    await page.locator('#traffic-only-deviations').check();
    assert.equal(calls.filter(call => call[1] === '/v1/display').length, displayCalls);
    await page.locator('#traffic-only-deviations').uncheck();
    stations = [{ id: 'a', code: 'A', name: 'Renamed Alpha' }];
    await page.evaluate(() => refreshLocalClock());
    assert.equal(await page.locator('#traffic-station').inputValue(), '');
    assert.match(await page.locator('#traffic-station').innerText(), /Renamed Alpha/);
    assert.doesNotMatch(await page.locator('#traffic-station').innerText(), /Beta/);
    await page.locator('#overview-timetable > summary').click();
    await page.locator('#overview-graph').waitFor({ state: 'visible' });
    await page.locator('#overview-timetable > summary').click();
    await page.evaluate(() => sessionStorage.removeItem('trainmeet.workspace'));
    await page.goto('http://127.0.0.1:9999/#traffic');
    await page.waitForURL('http://127.0.0.1:9999/#overview');
    assert.equal(await page.locator('#overview-traffic').isVisible(), true);
    await page.locator('#overview-clock-start').click();
    await page.waitForFunction(() => document.querySelector('#overview-clock-start').disabled && !document.querySelector('#overview-clock-stop').disabled);
    assert.equal(running, true);
    await page.locator('#overview-clock-stop').click();
    await page.waitForFunction(() => !document.querySelector('#overview-clock-start').disabled);
    assert.equal(running, false);
    assert.equal(JSON.parse(calls.find(call => call[0] === 'POST' && call[1] === '/v1/clock')[2]).meet_generation, 7);
    await screenshot('overview');
    await page.locator('#application-menu summary').click();
    await page.locator('#open-settings').click();
    await page.locator('#users-rows button').waitFor();
    assert.equal(await page.locator('#device-management').isVisible(), true);
    assert.equal(await page.locator('#users-invite-form').isVisible(), false);
    await page.locator('[data-language-picker]').selectOption('en');
    assert.equal(await page.locator('#cloud-auto-status').innerText(), 'Automatic config updates are enabled.');
    assert.equal(await page.locator('#system-runtime-name').innerText(), 'Demo meet');
    await page.locator('[data-language-picker]').selectOption('sv');
    await screenshot('settings');
    await page.locator('[data-open-modal="server-identity-form-modal"]').click();
    await page.locator('#admin-server-name').fill('Unsubmitted name');
    await page.evaluate(() => refreshInfo());
    assert.equal(await page.locator('#admin-server-name').inputValue(), 'Unsubmitted name');
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#server-identity-form-modal [data-close-modal]').click();
    await page.locator('#users-rows button').click();
    assert.equal(await page.locator('#user-edit-modal').isVisible(), true);
    await page.locator('#user-edit-password').fill('password1');
    await page.locator('#user-edit-password-confirm').fill('password2');
    await page.locator('#user-edit-form [type="submit"]').click();
    assert.match(await page.locator('#user-edit-form .modal-feedback').innerText(), /inte likadana/);
    assert.equal(calls.some(call => call[1] === '/v1/admin/users/update'), false);
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#user-edit-modal [data-close-modal]').click();
    await page.locator('#runtime-check-update').click();
    assert.equal(calls.some(call => call[0] === 'POST' && call[1] === '/v1/config/check'), true);
    await page.locator('#workspace-home').click();
    await page.locator('#overview-view').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#overview-view').isVisible(), true);
    await page.setViewportSize({ width: 360, height: 780 });
    await page.goto('http://127.0.0.1:9999/#workspaces');
    await page.locator('#workspace-picker').waitFor({ state: 'visible' });
    await page.locator('[data-language-picker]').selectOption('de');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await screenshot('workspace-chooser-mobile');
    await page.goto('http://127.0.0.1:9999/#settings');
    await page.locator('#users-rows button').waitFor();
    await page.locator('[data-language-picker]').selectOption('de');
    const overflow = await page.evaluate(() => ({ body: document.documentElement.scrollWidth, width: innerWidth }));
    assert.ok(overflow.body <= overflow.width + 1, JSON.stringify(overflow));
    await page.locator('[data-open-modal="device-form-modal"]').click();
    const rect = await page.locator('#device-form-modal').boundingBox();
    assert.ok(rect.x >= 0 && rect.x + rect.width <= 361, JSON.stringify(rect));
    await screenshot('mobile-device-modal');
    await page.locator('#device-form-modal [data-close-modal]').click();
    await page.locator('[data-language-picker]').selectOption('sv');
    region = 'us';
    await page.goto('http://127.0.0.1:9999/#workspaces');
    await page.reload();
    await page.locator('#workspace-options button').first().waitFor();
    assert.equal(await page.locator('#workspace-options button').count(), 3);
    assert.equal(await page.getByRole('button', { name: /^TKL/ }).count(), 0);
    assert.equal(await page.getByRole('button', { name: 'TMBox v2', exact: true }).count(), 0);
    await page.goto('http://127.0.0.1:9999/#tmbox');
    await page.locator('#workspace-picker').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#tmbox-v2-view').isVisible(), false);
    assert.equal(await page.evaluate(() => tmboxV2.timer), null);
    await page.locator('#workspace-options button').first().click();
    await page.locator('#overview-view').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#us-runtime-summary').isVisible(), true);
    assert.equal(await page.locator('.topology-overview-card').isVisible(), false);
    assert.equal(await page.locator('#overview-traffic').isVisible(), false);
    assert.equal(await page.locator('#overview-timetable').isVisible(), false);
    assert.equal(calls.some(call => call[1].includes('local-configuration') || call[1] === '/v1/operating-mode'), false);
    assert.deepEqual(errors, []);
    console.log('Cloud-only shell: workspace routing, EU/US clock, dialogs, config check and 360px layout passed.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
