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
    let cloudLinked = true;
    let cloudAuto = true;
    let cloudAutoFails = false;
    let clockSourceFails = false;
    let clockSettings = {source: 'internal', clock_name: '', user: '', has_password: false, poll_interval: 2};
    let devices = [{ device_id: 'esp-1', device_code: 'TBX-123', station_id: 'a', model: 'ESP8266' }];
    let removeFails = true;
    let identityFails = true;
    let releaseIdentity;
    let holdIdentity = false;
    let serverName = 'Demo server';
    const user = { user_id: 'u-1', username: 'admin', role: 'owner', invitation_pending: false };
    const runtime = { configured: true, linked: true, cloud_auto_sync: true, meet_name: 'Demo meet', active_day: 'Dagl', publication_id: 'pub-1', server_name: 'Demo server', central_url: 'https://cloud.trainmeet.app/config' };
    const clock = () => ({ configured: true, running, time: '06:00:00', speed: 4, source: clockSettings.source,
      external_name: clockSettings.clock_name, available: true, can_control: Boolean(clockSettings.user) });
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname.startsWith('/v1/')) {
        calls.push([request.method(), url.pathname, request.postData()]);
        let data = {};
        switch (url.pathname) {
          case '/v1/setup': case '/v1/setup/status': data = { required: false, admin_configured: true, runtime }; break;
          case '/v1/auth/status': data = { authenticated: true, at_the_machine: false, username: 'admin' }; break;
          case '/v1/browser-clients': case '/v1/browser-clients/self': data = { client_id: 'browser-tmbox-test', workspace: 'tmbox', device_code: 'WEB-TEST', access_token: 'test-only' }; break;
          case '/v1/tmbox-v2/assignment': data = { status: 'unassigned' }; break;
          case '/v1/server-context': data = { selected_meet: { id: 'meet-1', name: runtime.meet_name, publication_id: runtime.publication_id, operating_region: region, generation: 7 }, operating_region: region, available_workspaces: region === 'eu' ? ['administration', 'tkl', 'tmbox'] : ['administration', 'dispatcher', 'conductor'], cloud_update: { linked: cloudLinked, auto_sync: cloudAuto, state: 'current', current_publication_id: 'pub-1' } }; break;
          case '/v1/cloud/auto-sync':
            if (cloudAutoFails) return route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({message: 'Kunde inte spara testinställningen'})});
            cloudAuto = JSON.parse(request.postData()).enabled;
            data = {enabled: cloudAuto}; break;
          case '/v1/runtime': data = runtime; break;
          case '/v1/info': data = { gateway_id: serverName, server_name: serverName, traffic_session_name: 'Demo meet', runtime }; break;
          case '/v1/setup/server':
            if (holdIdentity) await new Promise(resolve => { releaseIdentity = resolve; });
            if (identityFails) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ message: 'Test: kunde inte spara' }) });
            serverName = JSON.parse(request.postData()).server_name;
            data = { server_name: serverName }; break;
          case '/v1/admin/access': data = { username: 'admin', password_configured: true }; break;
          case '/v1/devices': data = { devices, stations: [{ id: 'a', code: 'A', name: 'Alpha' }] }; break;
          case '/v1/devices/remove':
            if (removeFails) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ message: 'Tillfälligt fel' }) });
            devices = devices.filter(device => device.device_id !== JSON.parse(request.postData()).device_id);
            data = { removed: true }; break;
          case '/v1/admin/users': data = { role: 'owner', users: [user], user }; break;
          case '/v1/admin/users/update': data = { user }; break;
          case '/v1/clock': if (request.method() === 'POST') running = JSON.parse(request.postData()).action === 'start'; data = clock(); break;
          case '/v1/clock/source':
            if (request.method() === 'POST') {
              if (clockSourceFails) return route.fulfill({status: 502, contentType: 'application/json', body: JSON.stringify({message: 'Test: FastClock svarar inte'})});
              const body = JSON.parse(request.postData());
              const {password, ...safe} = body;
              clockSettings = {...clockSettings, ...safe, has_password: Boolean(password)};
              data = {settings: clockSettings, clock: clock()};
            } else data = clockSettings;
            break;
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
      await page.goto('http://127.0.0.1:9999/#settings');
      await page.locator('[data-language-picker]').selectOption(language);
      await page.goto('http://127.0.0.1:9999/#workspaces');
      assert.equal(await page.locator('#workspace-heading').innerText(), heading);
      assert.equal(await page.locator('#workspace-options button strong').first().innerText(), administration);
      assert.equal(await page.locator('[data-operating-mode], #build-view, #build-sidebar').count(), 0);
    }
    await page.goto('http://127.0.0.1:9999/#settings');
    await page.locator('[data-language-picker]').selectOption('sv');
    await page.goto('http://127.0.0.1:9999/#workspaces');
    await screenshot('workspace-chooser');
    // The retired embedded simulator is no longer an operational workspace.
    // Live transport/keyboard behavior is covered against SQLite in public-workspaces-live.
    assert.equal(await page.evaluate(() => WORKSPACES.tmbox.path), '/tmbox/');
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
    assert.equal(await page.locator('[data-language-picker]').count(), 1);
    assert.equal(await page.locator('#language-settings [data-language-picker]').isVisible(), true);
    // Meet details must not occupy the removed icon's 44px column, and the
    // status must stay a compact badge rather than stretch across the row.
    for (const sample of [
      {name: 'Grimslöv 2027', publication: 'publication-2027-09-19-1234567890', linked: true},
      {name: 'En mycket lång träffbenämning med Charlottendal och Vagnhärad 2027', publication: 'publication-' + '1234567890'.repeat(10), linked: false},
    ]) {
      runtime.meet_name = sample.name;
      runtime.publication_id = sample.publication;
      cloudLinked = sample.linked;
      await page.evaluate(() => refreshServerContext());
      for (const language of ['sv', 'da', 'nb', 'en', 'de']) {
        await page.locator('[data-language-picker]').selectOption(language);
        for (const width of [1440, 820, 600, 360, 320]) {
          await page.setViewportSize({width, height: 900});
          const layout = await page.locator('.cloud-connection-summary').evaluate(summary => {
            const details = summary.firstElementChild;
            const badge = summary.lastElementChild;
            const rect = element => {
              const {x, y, width, height, right, bottom} = element.getBoundingClientRect();
              return {x, y, width, height, right, bottom};
            };
            return {summary: rect(summary), details: rect(details), badge: rect(badge),
              textFits: [...details.children].every(element => element.scrollWidth <= element.clientWidth + 1 && element.scrollHeight <= element.clientHeight + 1),
              name: details.querySelector('b').textContent};
          });
          const label = `${language}/${width}/${sample.linked}`;
          assert.equal(layout.name, sample.name, label);
          assert.ok(layout.textFits, label + ': full meet name and publication must remain visible');
          assert.ok(layout.badge.width < 160, label + ': compact status badge');
          assert.ok(layout.badge.right <= layout.summary.right && layout.details.right <= layout.summary.right, label + ': content stays in summary');
          if (width > 760) {
            assert.ok(layout.details.width > layout.badge.width * 2, label + ': details get the flexible column');
            assert.ok(layout.details.right <= layout.badge.x, label + ': columns do not overlap');
          } else {
            assert.ok(layout.badge.y >= layout.details.bottom, label + ': badge stacks below details');
          }
          if (process.env.SERVER_SHELL_SCREENSHOTS && language === 'sv' && sample.linked && [1440, 360].includes(width)) {
            await page.locator('#sync-and-devices').screenshot({path: path.join(process.env.SERVER_SHELL_SCREENSHOTS, `cloud-summary-${width}.png`)});
          }
        }
      }
    }
    runtime.meet_name = 'Demo meet'; runtime.publication_id = 'pub-1'; cloudLinked = true;
    await page.evaluate(() => refreshServerContext());
    await page.locator('[data-language-picker]').selectOption('sv');
    await page.setViewportSize({width: 1200, height: 900});
    const addUser = await page.locator('#users-invite-open').boundingBox();
    const userTable = await page.locator('#users-table').boundingBox();
    assert.ok(addUser.y + addUser.height <= userTable.y, 'Add user is above, not stuck against the bottom row');
    const modalIds = await page.locator('dialog.admin-modal').evaluateAll(nodes => nodes.map(node => node.id));
    assert.equal(modalIds.length, 18);
    assert.ok(modalIds.includes('simulation-start-modal'));
    assert.ok(modalIds.includes('simulation-confirm-modal'));
    assert.ok(modalIds.includes('tmbox-language-modal'));
    assert.ok(modalIds.includes('device-language-modal'));
    for (const width of [1200, 360]) {
      await page.setViewportSize({ width, height: 900 });
      for (const id of modalIds) {
        await page.evaluate(id => openModal(id), id);
        const dialog = page.locator('#' + id);
        assert.equal(await dialog.locator(':scope > .modal-close').count(), 1);
        const close = dialog.locator(':scope > .modal-close');
        const bounds = await dialog.boundingBox(), button = await close.boundingBox();
        assert.ok(button.width >= 44 && button.height >= 44, id + ': touch target');
        assert.ok(button.x > bounds.x + bounds.width / 2 && button.x + button.width <= bounds.x + bounds.width, id + ': top right');
        assert.ok(button.y >= bounds.y && button.y <= bounds.y + 20, id + ': top edge');
        assert.equal(await close.getAttribute('aria-label'), 'Stäng');
        assert.equal(await dialog.locator('.modal-actions').count(), 1, id + ': one footer');
        assert.equal(await dialog.locator('.modal-actions > button').first().innerText(), 'Avbryt');
        assert.equal(await dialog.locator('.modal-actions > button').last().getAttribute('data-close-modal'), null, id + ': primary action last');
        assert.equal(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth), true, id + ': no horizontal clipping');
        assert.equal(await dialog.evaluate(el => el.contains(document.activeElement)), true, id + ': initial focus inside');
        await page.keyboard.press('Shift+Tab');
        await page.keyboard.press('Tab');
        assert.equal(await dialog.evaluate(el => el.contains(document.activeElement)), true, id + ': keyboard focus stays inside');
        // All actions, including destructive type=button actions, share busy protection.
        await page.evaluate(id => beginModalAction(document.getElementById(id)), id);
        assert.equal(await close.isDisabled(), true);
        await page.keyboard.press('Escape');
        assert.equal(await dialog.isVisible(), true, id + ': cannot close during save');
        await page.evaluate(id => endModalAction(document.getElementById(id)), id);
        if (width === 360) await screenshot('modal-' + id);
        await close.click();
        await dialog.waitFor({ state: 'hidden' });
      }
    }
    await page.setViewportSize({ width: 1200, height: 900 });
    // An invalid form is still cancellable (disabled Save is not a busy flag).
    cloudAuto = false;
    await page.evaluate(() => refreshServerContext());
    await page.locator('#cloud-auto-edit').click();
    assert.equal(await page.locator('#cloud-auto-enabled').isChecked(), false);
    await page.locator('#cloud-auto-enabled').check();
    await page.evaluate(() => refreshServerContext());
    assert.equal(await page.locator('#cloud-auto-enabled').isChecked(), true, 'polling must not overwrite unsaved input');
    cloudAutoFails = true;
    await page.locator('#cloud-auto-form button[type="submit"]').click();
    await page.locator('#cloud-auto-form .form-message.error').waitFor();
    assert.equal(await page.locator('#cloud-auto-modal').isVisible(), true);
    assert.equal(await page.locator('#cloud-auto-enabled').isChecked(), true);
    cloudAutoFails = false;
    await page.locator('#cloud-auto-form button[type="submit"]').click();
    await page.locator('#cloud-auto-modal').waitFor({state: 'hidden'});
    assert.equal(cloudAuto, true);
    assert.match(await page.locator('#cloud-auto-status').innerText(), /aktiv/);

    await page.evaluate(() => { openModal('users-invite-form-modal'); document.querySelector('#users-invite-form [type="submit"]').disabled = true; });
    await page.keyboard.press('Escape');
    await page.locator('#users-invite-form-modal').waitFor({ state: 'hidden' });
    await page.evaluate(() => { document.querySelector('#users-invite-form [type="submit"]').disabled = false; });
    await page.evaluate(() => openModal('clock-appearance-modal'));
    const originalSeconds = await page.locator('#meet-clock-seconds').isChecked();
    await page.locator('#meet-clock-seconds').setChecked(!originalSeconds);
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#clock-appearance-form [data-close-modal]').click();
    assert.equal(await page.locator('#meet-clock-seconds').isChecked(), originalSeconds, 'cancel restores checkbox');
    assert.equal(await page.locator('#device-management').isVisible(), true);
    // Clock source is edited in a modal; a provider error preserves the form,
    // while read-only FastClock disables local time/rate and start/stop.
    clockSourceFails = true;
    await page.locator('[data-open-modal="clock-source-modal"]').click();
    await page.locator('#clock-source').selectOption('fastclock');
    await page.locator('#fastclock-name').fill('Club clock');
    for (const width of [1200, 360]) {
      await page.setViewportSize({width, height: 900});
      const modal = page.locator('#clock-source-modal');
      assert.equal(await modal.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true, 'expanded source form fits');
      await screenshot('fastclock-settings-' + width);
    }
    await page.setViewportSize({width: 1200, height: 900});
    await page.locator('#clock-source-form [type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#clock-source-message').textContent.includes('svarar inte'));
    assert.equal(await page.locator('#fastclock-name').inputValue(), 'Club clock');
    assert.equal(await page.locator('#clock-source-modal').isVisible(), true);
    clockSourceFails = false;
    await page.locator('#clock-source-form [type="submit"]').click();
    await page.locator('#clock-source-modal').waitFor({state:'hidden'});
    assert.equal(JSON.parse(calls.filter(c => c[0] === 'POST' && c[1] === '/v1/clock/source').at(-1)[2]).meet_generation, 7);
    await page.waitForFunction(() => document.querySelector('#clock-adjust').disabled);
    assert.equal(await page.locator('#overview-clock-start').isDisabled(), true);
    assert.equal(await page.locator('#overview-clock-stop').isDisabled(), true);
    await page.locator('[data-open-modal="clock-source-modal"]').click();
    await page.locator('#clock-source').selectOption('internal');
    await page.locator('#clock-source-form [type="submit"]').click();
    await page.locator('#clock-source-modal').waitFor({state:'hidden'});
    await page.waitForFunction(() => !document.querySelector('#clock-adjust').disabled);
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
    await page.locator('#server-identity-form-modal > .modal-close').click();
    const nameLauncher = page.locator('[data-open-modal="server-identity-form-modal"]');
    assert.equal(await nameLauncher.evaluate(el => el === document.activeElement), true, 'focus restored to launcher');
    for (const closeMethod of ['cancel', 'escape', 'cross']) {
      await nameLauncher.click();
      const original = await page.locator('#admin-server-name').inputValue();
      await page.locator('#admin-server-name').fill('Unsaved ' + closeMethod);
      page.once('dialog', dialog => dialog.dismiss());
      if (closeMethod === 'escape') await page.keyboard.press('Escape');
      else await page.locator(closeMethod === 'cross' ? '#server-identity-form-modal > .modal-close' : '#server-identity-form .modal-actions [data-close-modal]').click();
      assert.equal(await page.locator('#server-identity-form-modal').isVisible(), true, 'keep editing after dismissed confirmation');
      assert.equal(await page.locator('#admin-server-name').inputValue(), 'Unsaved ' + closeMethod);
      // Reverting to the original value must no longer prompt.
      await page.locator('#admin-server-name').fill(original);
      await page.keyboard.press('Escape');
      await page.locator('#server-identity-form-modal').waitFor({ state: 'hidden' });
    }
    await nameLauncher.click();
    await page.locator('#admin-server-name').fill('Verified server');
    holdIdentity = true;
    await page.locator('#server-identity-form [type="submit"]').click();
    await page.waitForFunction(() => document.querySelector('#server-identity-form-modal').dataset.busy === 'true');
    assert.equal(await page.locator('#admin-server-name').isDisabled(), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#server-identity-form-modal').isVisible(), true);
    const identityWrites = () => calls.filter(([method, path]) => method === 'POST' && path === '/v1/setup/server').length;
    await page.evaluate(() => document.querySelector('#server-identity-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    assert.equal(identityWrites(), 1, 'duplicate submit ignored');
    assert.equal(typeof releaseIdentity, 'function');
    releaseIdentity();
    await page.locator('#server-identity-message.error').waitFor();
    assert.equal(await page.locator('#admin-server-name').inputValue(), 'Verified server', 'failure preserves input');
    assert.equal(await page.locator('#admin-server-name').isDisabled(), false);
    identityFails = false; holdIdentity = false;
    await page.locator('#admin-server-name').press('Enter');
    await page.locator('#server-identity-form-modal').waitFor({ state: 'hidden' });
    assert.equal(identityWrites(), 2, 'retry submits exactly once');
    assert.equal(serverName, 'Verified server');
    assert.match(await page.locator('#modal-result').innerText(), /Verified server/, 'save receipt remains visible outside dialog');
    await page.locator('#users-rows button').click();
    assert.equal(await page.locator('#user-edit-modal').isVisible(), true);
    await page.locator('#user-edit-password').fill('password1');
    await page.locator('#user-edit-password-confirm').fill('password2');
    await page.locator('#user-edit-form [type="submit"]').click();
    assert.match(await page.locator('#user-edit-form .modal-feedback').innerText(), /inte likadana/);
    assert.equal(calls.some(call => call[1] === '/v1/admin/users/update'), false);
    page.once('dialog', dialog => dialog.accept());
    await page.locator('#user-edit-modal > .modal-close').click();
    await page.locator('#runtime-check-update').click();
    assert.equal(calls.some(call => call[0] === 'POST' && call[1] === '/v1/config/check'), true);
    await page.locator('#workspace-home').click();
    await page.locator('#overview-view').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#overview-view').isVisible(), true);
    await page.setViewportSize({ width: 360, height: 780 });
    await page.goto('http://127.0.0.1:9999/#settings');
    await page.locator('[data-language-picker]').selectOption('de');
    await page.goto('http://127.0.0.1:9999/#workspaces');
    await page.locator('#workspace-picker').waitFor({ state: 'visible' });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await screenshot('workspace-chooser-mobile');
    await page.goto('http://127.0.0.1:9999/#settings');
    await page.locator('#users-rows button').waitFor();
    await page.locator('[data-language-picker]').selectOption('de');
    const overflow = await page.evaluate(() => ({ body: document.documentElement.scrollWidth, width: innerWidth }));
    assert.ok(overflow.body <= overflow.width + 1, JSON.stringify(overflow));
    await page.locator('.device-reconnect > summary').click();
    await page.locator('[data-open-modal="device-form-modal"]').click();
    const rect = await page.locator('#device-form-modal').boundingBox();
    assert.ok(rect.x >= 0 && rect.x + rect.width <= 361, JSON.stringify(rect));
    await screenshot('mobile-device-modal');
    await page.locator('#device-form-modal > .modal-close').click();
    await page.locator('[data-language-picker]').selectOption('sv');
    // Removal is explicit, names the target, handles errors and works on mobile.
    const checkClientReadability = async expected => {
      assert.equal(await page.locator('#device-list .status-row').count(), expected);
      const metrics = await page.locator('#device-list').evaluate(list => ({
        maxHeight: getComputedStyle(list).maxHeight,
        overflow: document.documentElement.scrollWidth > innerWidth + 1,
        rows: [...list.querySelectorAll('.status-row')].map(row => ({
          code: parseFloat(getComputedStyle(row.querySelector('b')).fontSize),
          model: parseFloat(getComputedStyle(row.querySelector('small')).fontSize),
          station: parseFloat(getComputedStyle(row.querySelector(':scope > span')).fontSize),
          buttons: [...row.querySelectorAll('button')].map(button => ({font: parseFloat(getComputedStyle(button).fontSize), height: button.getBoundingClientRect().height})),
        })),
      }));
      assert.equal(metrics.maxHeight, 'none');
      assert.equal(metrics.overflow, false);
      for (const row of metrics.rows) {
        assert.ok(row.code >= 18 && row.model >= 14 && row.station >= 16, JSON.stringify(row));
        assert.ok(row.buttons.every(button => button.font >= 16 && button.height >= 44));
      }
    };
    await checkClientReadability(1);
    const removeDialog = page.locator('#device-remove-modal');
    await page.locator('#device-list .device-remove').click();
    assert.equal(await page.locator('#device-remove-code').innerText(), 'TBX-123');
    assert.equal(await page.locator('#device-remove-station').innerText(), 'A · Alpha');
    await removeDialog.locator('[data-close-modal]').last().click();
    assert.equal(calls.some(call => call[1] === '/v1/devices/remove'), false);
    await page.locator('#device-list .device-remove').click();
    await removeDialog.locator('[type="submit"]').click();
    await page.locator('#device-remove-message').filter({ hasText: 'Tillfälligt fel' }).waitFor();
    assert.equal(await removeDialog.isVisible(), true);
    assert.equal(await page.locator('#device-list .status-row').count(), 1);
    await screenshot('mobile-remove-device-error');
    removeFails = false;
    await removeDialog.locator('[type="submit"]').click();
    await removeDialog.waitFor({ state: 'hidden' });
    assert.equal(await page.locator('#device-list .status-row').count(), 0);
    assert.match(await page.locator('#device-list-message').innerText(), /TMBoxen är borttagen/);
    assert.match(await page.locator('#app-devices').innerText(), /^0 klienter/);
    const removal = JSON.parse(calls.find(call => call[1] === '/v1/devices/remove')[2]);
    assert.equal(removal.device_id, 'esp-1');
    await page.reload();
    await page.locator('#device-list .empty-status').waitFor();
    assert.equal(await page.locator('#device-list .status-row').count(), 0);
    assert.ok(await page.locator('#device-list .empty-status').evaluate(node => parseFloat(getComputedStyle(node).fontSize) >= 16));
    devices = Array.from({length: 6}, (_, index) => ({
      device_id: `esp32-long-device-identity-for-wrapping-${index}`,
      device_code: `TBX-TEST-${index}`, station_id: index % 2 ? null : 'a',
      model: index % 2 ? 'Virtual TMBox test client' : 'NodeMCU ESP8266 PCF8574',
    }));
    await page.reload();
    await page.locator('#device-list .status-row').last().waitFor();
    for (const width of [1200, 760, 390, 360]) {
      await page.setViewportSize({width, height: 900});
      await checkClientReadability(6);
    }
    await screenshot('mobile-connected-clients');
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
