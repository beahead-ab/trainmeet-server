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
    const page = await browser.newPage({ locale: 'sv-SE', viewport: { width: 1200, height: 900 } });
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
    let placementSide = 'left';
    let placementOverride = false;
    let placementFails = true;
    let signedIn = false;
    const presentation = () => ({supported: region === 'eu', publication_id: 'pub-1', config_version: 2,
      findings: [{level: 'conflict', rule: 'A', message: '<img src=x onerror=alert(1)> Exempel'}],
      stations: [{station_id: 'a', code: 'A', name: 'Alpha', connections: [
        {connection_id: 'a-b', other_station_code: 'B', other_station_name: 'Beta', default_side: 'left', side: placementSide, overridden: placementOverride}]}]});
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
          case '/v1/auth/status': data = signedIn ? { authenticated: true, at_the_machine: false, username: 'admin' } : { authenticated: false }; break;
          case '/v1/workspaces': data = { selected_meet: { id: 'meet-1', name: runtime.meet_name, publication_id: runtime.publication_id, operating_region: region, generation: 7 }, operating_region: region, available_workspaces: ['administration', 'tmbox'], public_clients_enabled: true }; break;
          case '/v1/browser-clients': case '/v1/browser-clients/self': data = { client_id: 'browser-tmbox-test', workspace: 'tmbox', device_code: 'WEB-TEST', access_token: 'test-only' }; break;
          case '/v1/tmbox-v2/assignment': data = { status: 'unassigned' }; break;
          case '/v1/server-context': data = { selected_meet: { id: 'meet-1', name: runtime.meet_name, publication_id: runtime.publication_id, operating_region: region, generation: 7 }, operating_region: region, available_workspaces: region === 'eu' ? ['administration', 'tkl', 'tmbox'] : ['administration', 'dispatcher', 'conductor'], cloud_update: { linked: cloudLinked, auto_sync: cloudAuto, state: 'current', current_publication_id: 'pub-1' } }; break;
          case '/v1/cloud/auto-sync':
            if (cloudAutoFails) return route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({message: 'Kunde inte spara testinställningen'})});
            cloudAuto = JSON.parse(request.postData()).enabled;
            data = {enabled: cloudAuto}; break;
          case '/v1/runtime': data = runtime; break;
          case '/v1/cloud/presentation': data = presentation(); break;
          case '/v1/cloud/display-placement':
            if (placementFails) return route.fulfill({status: 409, contentType: 'application/json', body: JSON.stringify({message: 'Test: konfigurationen ändrades'})});
            const placementBody = JSON.parse(request.postData());
            assert.equal(placementBody.publication_id, 'pub-1');
            assert.equal(placementBody.station_id, 'a');
            placementSide = placementBody.sides['a-b'] || 'left';
            placementOverride = Boolean(placementBody.sides['a-b']);
            data = presentation(); break;
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
          // An older server answered a link to the same meet without a message.
          case '/v1/runtime/sync': return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ linked: true, restart_required: false }) });
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

    // "/" is the participant view for guests: the meet, the clock, how to
    // connect a box – no picker. Signed in, "/" is Drift.
    await page.goto('http://127.0.0.1:9999/');
    await page.locator('#participant-view').waitFor();
    assert.equal(await page.locator('#application-menu').isVisible(), false);
    assert.equal(await page.locator('#pv-meet-name').textContent(), 'Demo meet');
    assert.equal(await page.locator('#workspace-options').count(), 0);
    // A box finds the server and needs no address or code; the card says so.
    await page.locator('#pv-connect-card').getByText('Boxen hittar servern själv').waitFor();
    assert.equal(await page.locator('#pv-connect-card').getByText(/parningskod/i).count(), 0);
    assert.equal(await page.locator('#participant-view a[href="/tmbox/"]').count(), 1, 'One button starts a virtual TMBox');
    assert.equal(await page.locator('#pv-login').getAttribute('href'), '/login');
    signedIn = true;
    await page.goto('http://127.0.0.1:9999/');
    await page.waitForURL('**/drift');
    assert.equal(await page.locator('#participant-view').isVisible(), false);
    await page.locator('#device-list .status-row').waitFor();
    assert.equal(await page.locator('#application-menu').isVisible(), false);
    // Clients and the left/right setting are two cards: operations and a
    // setting used to share one, and the station table read as client rows.
    assert.equal((await page.locator('#device-management > .section-heading h2').textContent()).trim(),'Klienter');
    assert.equal(await page.locator('#device-management #server-station-rows').count(),0);
    assert.equal((await page.locator('#station-placement > h2').textContent()).trim(),'TMBox-placering');
    assert.equal(await page.locator('#station-placement #server-station-rows').count(),1);
    assert.ok(await page.locator('#device-management').evaluate(card=>card.querySelector('#device-list').compareDocumentPosition(card.querySelector('.device-reconnect'))&Node.DOCUMENT_POSITION_FOLLOWING),'Reconnect comes after the client list');
    assert.equal(await page.locator('#overview-timetable').getAttribute('open'), null);
    assert.equal(await page.locator('#traffic-only-deviations').isChecked(), true);
    assert.equal(await page.locator('#overview-graph').isVisible(), true);
    await page.locator('#overview-clock-start').click();
    await page.locator('#stop-local-clock').waitFor({state:'visible'});
    assert.equal(running, true);
    await page.locator('#stop-local-clock').click();
    await page.locator('#overview-clock-start').waitFor({state:'visible'});
    assert.equal(running, false);
    assert.equal(JSON.parse(calls.find(c=>c[0]==='POST'&&c[1]==='/v1/clock')[2]).meet_generation,7);

    // Drafts stay intact when the live state refreshes or a save fails.
    await page.getByRole('link',{name:'Inställningar',exact:true}).click();
    await page.locator('#server-identity-form').waitFor({state:'visible'});
    await page.locator('#admin-server-name').fill('Verified server');
    await page.evaluate(()=>refreshInfo());
    assert.equal(await page.locator('#admin-server-name').inputValue(),'Verified server');
    holdIdentity=true;
    await page.locator('#server-identity-form [type=submit]').click();
    await page.waitForFunction(()=>document.querySelector('#server-identity-form').dataset.busy==='true');
    assert.equal(await page.locator('#admin-server-name').isDisabled(),true);
    await page.evaluate(()=>document.querySelector('#server-identity-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
    assert.equal(calls.filter(c=>c[0]==='POST'&&c[1]==='/v1/setup/server').length,1);
    releaseIdentity();
    await page.locator('#server-identity-message.error').waitFor();
    assert.equal(await page.locator('#admin-server-name').inputValue(),'Verified server');
    identityFails=false;holdIdentity=false;
    await page.locator('#server-identity-form [type=submit]').click();
    await page.waitForFunction(()=>!document.querySelector('#server-identity-form').dataset.dirty);
    assert.equal(serverName,'Verified server');

    cloudAuto=false;
    await page.evaluate(()=>refreshServerContext());
    await page.locator('#cloud-auto-enabled').check();
    await page.evaluate(()=>refreshServerContext());
    assert.equal(await page.locator('#cloud-auto-enabled').isChecked(),true);
    cloudAutoFails=true;
    await page.locator('#cloud-auto-form [type=submit]').click();
    await page.locator('#cloud-auto-form .form-message.error').waitFor();
    assert.equal(await page.locator('#cloud-auto-enabled').isChecked(),true);
    cloudAutoFails=false;
    await page.locator('#cloud-auto-form [type=submit]').click();
    await page.waitForFunction(()=>!document.querySelector('#cloud-auto-form').dataset.dirty);
    assert.equal(cloudAuto,true);

    // Retained dialogs still trap focus and cannot be dismissed during writes.
    const modalIds=await page.locator('dialog.admin-modal').evaluateAll(nodes=>nodes.filter(n=>n.querySelector('form')).map(n=>n.id));
    assert.ok(modalIds.includes('simulation-start-modal'));
    assert.ok(modalIds.includes('device-language-modal'));
    for(const width of [1200,360]){
      await page.setViewportSize({width,height:900});
      for(const id of modalIds){
        await page.evaluate(id=>openModal(id),id);
        const dialog=page.locator('#'+id), close=dialog.locator(':scope > .modal-close');
        assert.equal(await close.count(),1);
        assert.equal(await close.getAttribute('aria-label'),'Stäng');
        assert.equal(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth),true,id);
        assert.equal(await dialog.evaluate(el=>el.contains(document.activeElement)),true,id);
        await page.evaluate(id=>beginModalAction(document.getElementById(id)),id);
        assert.equal(await close.isDisabled(),true);
        await page.keyboard.press('Escape');
        assert.equal(await dialog.isVisible(),true);
        await page.evaluate(id=>endModalAction(document.getElementById(id)),id);
        await close.click(); await dialog.waitFor({state:'hidden'});
      }
      const overflow=await page.evaluate(()=>({document:document.documentElement.scrollWidth,width:innerWidth}));
      assert.ok(overflow.document<=overflow.width+1,JSON.stringify(overflow));
    }
    // Language preference lives in Settings, not an extra toolbar.
    for(const language of ['sv','da','nb','en','de']){
      await page.goto('http://127.0.0.1:9999/installningar');
      await page.locator('[data-language-picker]').selectOption(language);
      assert.equal(await page.locator('.server-settings-nav a[href="/installningar#farozon"]').textContent(),{sv:'Farozon',da:'Farezone',nb:'Faresone',en:'Danger zone',de:'Gefahrenbereich'}[language]);
      // Signed in, the old picker address lands on Drift, whatever the language.
      await page.goto('http://127.0.0.1:9999/#workspaces');
      await page.waitForURL('**/drift');
      assert.equal(await page.locator('#participant-view').isVisible(), false);
    }
    await page.goto('http://127.0.0.1:9999/installningar');
    await page.locator('[data-language-picker]').selectOption('sv');
    // Linking to Cloud never reports "undefined", even without a message.
    await page.locator('[data-open-modal="runtime-sync-form-modal"]').click();
    const syncBoxes=page.locator('#runtime-sync-code-boxes input');
    for(let index=0;index<6;index++) await syncBoxes.nth(index).fill(String(index+1));
    await page.locator('#runtime-sync-form [type=submit]').click();
    // The dialog closes on success; its message is shown in #modal-result.
    await page.locator('#modal-result').getByText('3/3 · Cloud-kopplingen är sparad på servern.',{exact:true}).waitFor();
    assert.doesNotMatch(await page.locator('#modal-result').textContent(),/undefined/);
    await page.goto('http://127.0.0.1:9999/drift');
    await page.locator('#device-list .device-remove').click();
    const confirmation=page.locator('.device-inline-edit');
    await confirmation.getByRole('button',{name:'Ta bort',exact:true}).click();
    await confirmation.getByText('Tillfälligt fel').waitFor();
    assert.equal(await page.locator('#device-list .status-row').count(),1);
    removeFails=false;
    await confirmation.getByRole('button',{name:'Ta bort',exact:true}).click();
    await page.locator('#device-list .empty-status').waitFor();
    assert.equal(JSON.parse(calls.find(c=>c[1]==='/v1/devices/remove')[2]).device_id,'esp-1');

    // Placement retains publication/config CAS and never interprets imported HTML.
    await page.locator('#server-station-rows button').click();
    const form=page.locator('.placement-inline-edit');
    await form.locator('select').selectOption('right');
    await page.evaluate(()=>refreshCloudPresentation());
    assert.equal(await form.locator('select').inputValue(),'right');
    await form.locator('[type=submit]').click();
    await form.getByText('Test: konfigurationen ändrades').waitFor();
    assert.equal(await form.locator('select').inputValue(),'right');
    placementFails=false;
    await form.locator('[type=submit]').click();
    await form.waitFor({state:'hidden'});
    assert.equal(placementSide,'right');
    assert.equal(await page.locator('#published-findings-list img').count(),0);
    assert.match(await page.locator('#published-findings-list').textContent(),/<img/);

    region='us';
    await page.goto('http://127.0.0.1:9999/drift');
    await page.locator('#server-region').filter({hasText:'US'}).waitFor();
    assert.equal(await page.locator('#drift-simulation').isVisible(),false);
    assert.equal(await page.locator('#device-management').isVisible(),false);
    assert.equal(await page.locator('#station-placement').isVisible(),false);
    assert.equal(await page.locator('#overview-traffic').isVisible(),false);
    assert.equal(calls.some(c=>c[1].includes('local-configuration')||c[1]==='/v1/operating-mode'),false);
    assert.deepEqual(errors,[]);
    await screenshot('server-design');
    console.log('Server design: routes, fenced inline edits, failure recovery, dialogs, responsive layout, locales and EU/US separation passed.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
