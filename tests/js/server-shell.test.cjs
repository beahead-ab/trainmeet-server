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
    const imported = [{level: 'conflict', rule: 'A', message: '<img src=x onerror=alert(1)> Exempel'}];
    let findings = imported;
    let version = 8;
    // The line A–B as /v1/display has it: its directed channels and the
    // recorded positions, set by each step of the test.
    let lineChannels = [];
    let positions = [];
    // Connecting a signal box (TKL): the code, why it is missing, and the paired ones.
    let connectionCode = '262-617', codeState = 'valid', renewals = 0;
    let terminals = [{ client_id: 'tkl-cda', name: 'CDA TKL 1', station: { id: 'a', code: 'A', name: 'Alpha' }, has_access: true,
      connection: { state: 'online', last_seen: null } }];
    const connection = () => ({ host: '192.168.1.20', port: 8787, code: connectionCode, code_state: codeState, screens: [],
      validity_hours: 0, wifi: { name: '', password: '' }, web_client_ttl_minutes: 30 });
    // A larger meet for TMBox-placering: stations without lines, after Alpha.
    let extraPlacementStations = 0;
    // Set to a promise to keep /v1/train from answering until it resolves.
    let holdTrain = null;
    const presentation = () => ({supported: region === 'eu', publication_id: 'pub-1', config_version: 2, findings,
      stations: [{station_id: 'a', code: 'A', name: 'Alpha', connections: [
        {connection_id: 'a-b', other_station_code: 'B', other_station_name: 'Beta', default_side: 'left', side: placementSide, overridden: placementOverride}]},
        ...Array.from({length: extraPlacementStations}, (_, i) => ({station_id: `x${i}`, code: `X${i}`, name: `Extra ${i}`, connections: []}))]});
    const user = { user_id: 'u-1', display_name: 'Admin', email: 'admin@example.se', role: 'owner', invitation_pending: false };
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
          case '/v1/auth/status': data = signedIn ? { authenticated: true, at_the_machine: false } : { authenticated: false }; break;
          case '/v1/workspaces': data = { selected_meet: { id: 'meet-1', name: runtime.meet_name, publication_id: runtime.publication_id, operating_region: region, generation: 7 }, operating_region: region, available_workspaces: ['administration', 'tmbox'], public_clients_enabled: true }; break;
          case '/v1/browser-clients': case '/v1/browser-clients/self': data = { client_id: 'browser-tmbox-test', workspace: 'tmbox', device_code: 'WEB-TEST', access_token: 'test-only' }; break;
          case '/v1/tmbox-v2/assignment': data = { status: 'unassigned' }; break;
          case '/v1/server-context': data = { selected_meet: { id: 'meet-1', name: runtime.meet_name, publication_id: runtime.publication_id, operating_region: region, generation: 7, version_number: version }, operating_region: region, available_workspaces: region === 'eu' ? ['administration', 'tkl', 'tmbox'] : ['administration', 'dispatcher', 'conductor'], cloud_update: { linked: cloudLinked, auto_sync: cloudAuto, state: 'current', current_publication_id: 'pub-1' } }; break;
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
          case '/v1/devices': data = { devices, terminals, stations: [{ id: 'a', code: 'A', name: 'Alpha' }] }; break;
          case '/v1/display/connection': data = connection(); break;
          case '/v1/display/connection/code': renewals += 1; connectionCode = '777-111'; codeState = 'valid'; data = connection(); break;
          case '/v1/terminals/remove':
            terminals = terminals.filter(terminal => terminal.client_id !== JSON.parse(request.postData()).client_id);
            data = { removed: true }; break;
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
          case '/v1/display': data = { clock: clock(), meet: { id: 'meet-1', name: 'Demo meet' }, active_day: 'Dagl', publication_id: 'pub-1', stations, connections: [{ id: 'a-b', station_a_id: 'a', station_b_id: 'b', track_type: 'single' }], routes: [{ train_number: '421', station_id: 'a', departure_time: '06:05' }, { train_number: '421', station_id: 'b', arrival_time: '06:20' }], services: [{ id: 's-421', train_number: '421', days: 'Dagl', stops: [{ station_id: 'a', stop_order: 0, departure_time: '06:05' }, { station_id: 'b', stop_order: 1, arrival_time: '06:20' }] }], train_positions: positions, connection_states: [{ id: 'a-b', state: lineChannels.length ? 'occupied' : 'free', channels: lineChannels }], connection: { screens: [] } }; break;
          case '/v1/config/check': data = { message: 'Senaste config används.' }; break;
          // An older server, or one that is full: no stream; pages keep their timers.
          case '/v1/events': return route.fulfill({ status: 503, contentType: 'application/json', body: '{}' });
          case '/v1/train':
            assert.equal(url.searchParams.get('number'), '421');
            if (holdTrain) await holdTrain;
            data = { train_number: '421', active_day: 'Dagl', services: [{ service_id: 's-421', train_type: 'Godståg', delay_minutes: 3,
              now: { state: 'on_line', from_station_id: 'a', to_station_id: 'b', since: '06:05' },
              stops: [
                { station_id: 'a', station_code: 'A', station_name: 'Alpha', arrival_time: null, departure_time: '06:05', movement_id: 'm-a', planned_track: '1', actual_track: null, arrival: 'none', departure: 'departed' },
                { station_id: 'b', station_code: 'B', station_name: 'Beta', arrival_time: '06:20', departure_time: null, movement_id: 'm-b', planned_track: '2', actual_track: null, arrival: 'none', departure: 'none' }] }] };
            break;
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
    // The connect steps sit behind one button and open as a sheet from the
    // bottom (Deltagare.dc). A box finds the server and needs no address or
    // code; the sheet says so.
    assert.equal(await page.locator('#pv-connect-card').isVisible(), false);
    await page.locator('#pv-connect-open').click();
    await page.locator('#pv-connect-card').getByText('Boxen hittar servern själv').waitFor();
    assert.equal(await page.locator('#pv-connect-card').getByText(/parningskod/i).count(), 0);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#pv-connect-card').isVisible(), false);
    // The summary under the map, and what comes next: a filled badge for a
    // train on its way in, a hollow one for a departure (Deltagare.dc).
    assert.equal(await page.locator('#pv-stat-line').textContent(), '0');
    const events = await page.locator('#pv-events .pv-item--event').evaluateAll(rows => rows.map(row => ({ train: row.querySelector('.pv-badge-train').textContent, hollow: row.querySelector('.pv-badge-train').classList.contains('is-hollow') })));
    assert.deepEqual(events.slice(0, 2), [{ train: '421', hollow: true }, { train: '421', hollow: false }]);
    // Search in the timetable: a train number or a station.
    await page.locator('#pv-timetable-search').fill('zzz-no-such-train');
    await page.locator('#pv-timetable .pv-empty').waitFor();
    await page.locator('#pv-timetable-search').fill('');
    assert.equal(await page.locator('#participant-view a[href="/tmbox/"]').count(), 1, 'One button starts a virtual TMBox');
    assert.equal(await page.locator('#pv-login').getAttribute('href'), '/login');
    // No stream here (503): the participant view keeps asking every five seconds.
    positions = [{ train_number: '421', status: 'connection', connection_id: 'a-b', from_station_id: 'a', to_station_id: 'b' }];
    await page.locator('#pv-topology .topology-train.on-line[data-train-number="421"]').waitFor({ timeout: 6500 });
    positions = [];
    signedIn = true;
    await page.goto('http://127.0.0.1:9999/');
    await page.waitForURL('**/drift');
    assert.equal(await page.locator('#participant-view').isVisible(), false);
    await page.locator('#device-list tr[data-station-id="a"]').waitFor();
    assert.equal(await page.locator('#application-menu').isVisible(), false);
    // Kontrollrummet: Drift is one screen. The clock, the line, the stations
    // with their boxes, what comes next and the diagram are each there once,
    // and nothing folds away.
    for (const id of ['drift-clock', 'drift-map', 'drift-stations', 'overview-traffic', 'drift-graph']) assert.equal(await page.locator('#' + id).isVisible(), true, id);
    assert.equal(await page.locator('#overview-view details').count(), 0);
    assert.equal((await page.locator('#drift-stations .kr-ph > span').first().textContent()).trim(), 'Stationer och boxar');
    assert.equal(await page.locator('#device-management, #station-placement, #traffic-timeline, #overview-route-search:visible').count(), 0, 'the old modules are gone');
    // One row per station: its box, its status, what stands there.
    assert.equal(await page.locator('#device-list tr').count(), 2);
    assert.match(await page.locator('#device-list tr[data-station-id="a"]').textContent(), /Alpha.*TBX-123/);
    assert.match(await page.locator('#device-list tr[data-station-id="b"]').textContent(), /Beta.*Obemannad/);
    assert.equal(await page.locator('#drift-stations-meta').textContent(), '0 av 2 bemannade');
    assert.equal(await page.locator('#drift-graph').isVisible(), true);
    assert.equal(await page.locator('#overview-graph').isVisible(), true);
    // The theme is a switch in the header: dark first, remembered by the browser.
    assert.equal(await page.evaluate(() => document.documentElement.dataset.krTheme), 'dark');
    await page.locator('#kr-theme-toggle').click();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.krTheme), 'light');
    assert.equal(await page.evaluate(() => localStorage.getItem('trainmeet.theme')), 'light');
    await page.locator('#kr-theme-toggle').click();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.krTheme), 'dark');
    // Connecting a signal box: "Anslut en box" opens a popover with the
    // address and the code to type into TKL; each paired signal box is listed
    // under the stations with its station.
    const connect = page.locator('#connect-terminals');
    await page.locator('#drift-connect-open').click();
    await connect.waitFor({ state: 'visible' });
    assert.equal(await connect.locator('h3').textContent(), 'Anslut en box');
    await connect.locator('#connect-code').getByText('262-617', { exact: true }).waitFor();
    assert.equal(await connect.locator('#connect-address').textContent(), 'http://192.168.1.20:8787');
    // Behind Caddy on server.trainmeet.app the box comes in where this page did.
    assert.equal(await page.evaluate(() => connectAddress({host: '10.0.0.5', port: 8787}, 'https://server.trainmeet.app')), 'https://server.trainmeet.app');
    assert.equal(await connect.locator('.kr-steps li').count(), 3);
    assert.equal(await connect.locator('#connect-code-note').isVisible(), false);
    for (const [state, note, renew] of [
      ['used_up', /använts 50 gånger/, true], ['expired', /gått ut/, true],
      ['no_panels', /inga stationspaneler/, false], ['no_meet', /ingen aktiv träff/, false]]) {
      codeState = state;
      await page.evaluate(() => refreshLocalClock());
      await connect.locator('#connect-code-note').getByText(note).waitFor();
      assert.equal(await connect.locator('#connect-code').textContent(), '–', state);
      assert.equal(await connect.locator('#connect-new-code').isVisible(), renew, state);
    }
    codeState = 'used_up';
    await page.evaluate(() => refreshLocalClock());
    await connect.locator('#connect-new-code').click();
    // At once, from the answer: not on Drift's next refresh.
    await connect.locator('#connect-code').getByText('777-111', { exact: true }).waitFor({ timeout: 1500 });
    assert.equal(renewals, 1);
    assert.equal(await connect.locator('#connect-code-note').isVisible(), false);
    // A long address stays inside the popover.
    await page.evaluate(() => renderConnectCard({ host: 'trainmeet-server-grimslov-2027.example.org', port: 8787, code: '123-456', code_state: 'valid' }));
    assert.ok(await connect.evaluate(card => card.scrollWidth <= card.clientWidth + 1), 'nothing in the connect popover runs past its edge');
    await page.evaluate(() => refreshLocalClock());
    await page.evaluate(() => document.querySelector('#connect-terminals').hidePopover());
    const terminalRow = page.locator('#terminal-list .terminal-row[data-client-id="tkl-cda"]');
    assert.equal(await page.locator('#terminal-list h3').textContent(), 'Ställverk (TKL)');
    assert.match(await terminalRow.textContent(), /CDA TKL 1.*A · Alpha.*Online/);
    terminals[0] = { ...terminals[0], has_access: false };
    await page.evaluate(() => refreshDevices());
    await terminalRow.getByText('Ny träff: ange den nya koden i ställverket').waitFor();
    await terminalRow.locator('.terminal-remove').click();
    await terminalRow.getByText(/kopplas bort direkt/).waitFor();
    await terminalRow.getByRole('button', { name: 'Ta bort', exact: true }).click();
    await page.locator('#terminal-list').waitFor({ state: 'hidden', timeout: 1500 });
    assert.deepEqual(terminals, []);
    assert.equal(JSON.parse(calls.find(c => c[1] === '/v1/terminals/remove')[2]).client_id, 'tkl-cda');

    // Drift as one whole. The train diagram is drawn without opening the
    // timetable, and nothing is lit until someone picks a train.
    await page.locator('#overview-graph .tr-line[data-train-number="421"]').waitFor();
    assert.equal(await page.locator('#overview-graph .pl.lit, #overview-graph .pl.sel').count(), 0);
    assert.equal(await page.locator('#overview-topology .route-highlight, #overview-topology .dimmed').count(), 0);
    assert.equal(await page.locator('#overview-route-list .route-number.active').count(), 0);
    assert.equal(await page.locator('#topology-head-meta').textContent(), '2 stationer · 1 sträcka · tryck på en station för att filtrera');
    // The timetable is a dialog away; its head says what is inside.
    assert.equal(await page.locator('#timetable-summary-meta').textContent(), '1 tåg · sök tåg · tågrutter · stationer');
    // Cloud's check findings belong under Inställningar → Träff och Cloud,
    // where configurations are fetched, and nowhere else: no chip in the
    // header on any page. Imported text is never HTML.
    assert.equal(await page.locator('#header-findings').count(), 0, 'no findings in the header');
    await page.evaluate(() => { history.pushState(null, '', '/installningar#fynd'); applyWorkspaceRoute(); });
    await page.locator('#published-findings').waitFor({state: 'visible'});
    const summary = page.locator('#published-findings-summary');
    await summary.getByText('1 noterade uppgifter', {exact: true}).waitFor();
    assert.match(await summary.getAttribute('class'), /warn/);
    assert.equal(await page.locator('#published-findings-list img').count(), 0);
    // A small flag on the menu item says a conflict waits there; none without one.
    const flag = page.locator('.kr-nav[data-section="traff"] .kr-navflag');
    assert.equal(await flag.textContent(), '1');
    assert.equal(await flag.getAttribute('aria-label'), '1 konflikt i tidtabellen');
    findings = [{level: 'observation'}];
    await page.evaluate(() => refreshCloudPresentation());
    await flag.waitFor({state: 'hidden'});
    findings = imported;
    await page.evaluate(() => refreshCloudPresentation());
    await flag.waitFor({state: 'visible'});
    assert.match(await page.locator('#published-findings-list').textContent(), /<img/);
    await page.locator('#app-chrome a[href="/drift"]').first().click();
    await page.locator('#app-chrome a[href="/drift"]').first().click();
    await page.locator('#overview-graph').waitFor({state: 'visible'});
    // The diagram is as tall as its stations and meets their names: never
    // stretched on a wide screen, the first hour readable beside the names.
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.evaluate(() => refreshLocalClock());
    const diagram = await page.evaluate(() => {
      const svg = document.querySelector('#overview-graph'), box = svg.getBoundingClientRect(), holder = svg.parentElement.getBoundingClientRect();
      const rows = [...svg.querySelectorAll('line.gl:not(.h):not(.d)')].map(line => line.getBoundingClientRect().y);
      const names = [...svg.querySelectorAll('text.lbl.s')].map(text => { const r = text.getBoundingClientRect(); return r.y + r.height / 2; });
      const firstHour = svg.querySelector('text.ax').getBoundingClientRect();
      return { rows, names, labels: [...svg.querySelectorAll('text.lbl.s')].map(text => text.textContent), firstHourLeft: firstHour.x - box.x, width: box.width, holder: holder.width };
    });
    assert.equal(diagram.rows.length, 2);
    assert.deepEqual(diagram.labels, ['Alpha', 'Beta']);
    diagram.rows.forEach((y, index) => assert.ok(Math.abs(y - diagram.names[index]) < 8, `station ${index}: line ${y}, name ${diagram.names[index]}`));
    assert.ok(diagram.firstHourLeft >= 60, `first hour at ${diagram.firstHourLeft}`);
    assert.ok(diagram.width <= diagram.holder + 1, 'the diagram fits its panel');
    await page.setViewportSize({ width: 1200, height: 900 });

    // Trains on the map. A clear given is an outlined tag a quarter along from
    // the station the train leaves, its triangle towards where it goes; once
    // departed the tag is filled. A request alone draws nothing.
    const mapTrain = page.locator('#overview-topology .topology-train[data-train-number="421"]');
    // Colours come from the theme's tokens, so the test asks the page what they are.
    const paint = name => page.evaluate(token => { const probe = document.createElement('i'); probe.style.color = `var(${token})`; document.body.append(probe); const colour = getComputedStyle(probe).color; probe.remove(); return colour; }, name);
    const [panel, blue, blueInk] = [await paint('--kr-panel'), await paint('--kr-blue'), await paint('--kr-blue-ink')];
    const showTraffic = async (channels, recorded) => {
      lineChannels = channels; positions = recorded;
      await page.evaluate(() => refreshLocalClock());
    };
    const line = (state, from = 'a', to = 'b') => ({ state, from_station_id: from, to_station_id: to, train_number: '421' });
    const parked = count => Array.from({ length: count }, (_, i) => ({ train_number: String(901 + i), status: 'station', station_id: 'a' }));
    const mapGeometry = () => page.evaluate(() => {
      const centre = element => { const r = element.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; };
      const station = name => centre(document.querySelector(`#overview-topology .topology-node[aria-label^="${name}"] .topology-station`));
      const train = document.querySelector('#overview-topology .topology-train[data-train-number="421"]');
      const tag = train.querySelector('.topology-train-tag'), arrow = train.querySelector('.topology-train-arrow');
      // The triangle's tip and the middle of its base, on screen.
      const matrix = arrow?.getScreenCTM(), tip = arrow && new DOMPoint(10, 6).matrixTransform(matrix), base = arrow && new DOMPoint(2, 6).matrixTransform(matrix);
      const ring = document.querySelector('#overview-topology .topology-node[aria-label^="Alpha"] .topology-station').getBoundingClientRect();
      return { a: station('Alpha'), b: station('Beta'), tag: centre(tag), number: centre(train.querySelector('.train-number')),
        arrow: arrow && centre(arrow), points: arrow && { x: tip.x - base.x, y: tip.y - base.y }, box: tag.getBoundingClientRect().toJSON(), ring: ring.toJSON(),
        tagFill: getComputedStyle(tag).fill, arrowFill: arrow && getComputedStyle(arrow).fill };
    });
    const pointsTowards = (g, from, to) => {
      const dx = g[to].x - g[from].x, dy = g[to].y - g[from].y;
      return (g.points.x * dx + g.points.y * dy) / Math.hypot(g.points.x, g.points.y) / Math.hypot(dx, dy) > 0.95;
    };
    const overlaps = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
    const along = (g, point, from, to) => ((point.x - g[from].x) * (g[to].x - g[from].x) + (point.y - g[from].y) * (g[to].y - g[from].y))
      / ((g[to].x - g[from].x) ** 2 + (g[to].y - g[from].y) ** 2);
    await showTraffic([line('requested')], []);
    assert.equal(await mapTrain.count(), 0, 'a request is not a train on the line');
    for (const [from, to] of [['a', 'b'], ['b', 'a']]) {
      await showTraffic([line('reserved', from, to)], []);
      await page.locator('#overview-topology .topology-train.cleared[data-train-number="421"]').waitFor();
      let g = await mapGeometry();
      const route = `${from.toUpperCase()} → ${to.toUpperCase()}`;
      assert.equal(await mapTrain.getAttribute('aria-label'), `Tåg 421 · ${route} · klart, inte avgått`);
      assert.equal(g.tagFill, panel, 'outlined');
      assert.equal(g.arrowFill, 'none');
      const at = along(g, g.tag, from, to);
      assert.ok(at > 0.15 && at < 0.4, `near the station it leaves: ${at}`);
      assert.ok((g.arrow.x - g.number.x) * (g[to].x - g[from].x) > 0, `the triangle leads towards ${to}`);
      assert.ok(pointsTowards(g, from, to), `the triangle points at ${to}: ${JSON.stringify(g.points)}`);
      if (from === 'a') assert.equal(overlaps(g.box, g.ring), false, 'clear of the station it leaves');
      // ...and of the ring drawn round it when its route is lit (radius 19 to
      // the dot's 11): on a short line the tag sat on it (1.23.0).
      const r = g.ring.width / 2 * 19 / 11, c = { x: g.ring.x + g.ring.width / 2, y: g.ring.y + g.ring.height / 2 };
      if (from === 'a') assert.equal(overlaps(g.box, { x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r }), false, 'clear of the ring');
      await showTraffic([line('occupied', from, to)], [{ train_number: '421', status: 'connection', connection_id: 'a-b', from_station_id: from, to_station_id: to }]);
      await page.locator('#overview-topology .topology-train.on-line[data-train-number="421"]').waitFor();
      g = await mapGeometry();
      assert.equal(await mapTrain.count(), 1, 'one tag, not one per source');
      assert.equal(await mapTrain.getAttribute('aria-label'), `Tåg 421 · ${route} · på linjen`);
      assert.equal(g.tagFill, blue, 'filled');
      assert.equal(g.arrowFill, blueInk);
    }
    // Two trains the same way on one line: both between the stations, apart.
    await showTraffic([line('occupied'), { ...line('occupied'), train_number: '4221' }], []);
    await page.locator('#overview-topology .topology-train.on-line[data-train-number="4221"]').waitFor();
    const pair = await page.evaluate(() => {
      const box = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
      return { first: box('#overview-topology .topology-train[data-train-number="421"] .topology-train-tag'),
        second: box('#overview-topology .topology-train[data-train-number="4221"] .topology-train-tag'),
        a: box('#overview-topology .topology-node[aria-label^="Alpha"] .topology-station'), b: box('#overview-topology .topology-node[aria-label^="Beta"] .topology-station') };
    });
    assert.equal(overlaps(pair.first, pair.second), false, 'two trains apart');
    for (const tag of [pair.first, pair.second]) for (const ring of [pair.a, pair.b]) assert.equal(overlaps(tag, ring), false, 'both clear of the stations');
    // On a phone the line runs top to bottom, and the triangle with it.
    await page.setViewportSize({ width: 390, height: 844 });
    for (const [from, to] of [['a', 'b'], ['b', 'a']]) {
      await showTraffic([line('occupied', from, to)], []);
      await page.waitForFunction(([from]) => document.querySelector('#overview-topology .topology-train[data-train-number="421"]')?.getAttribute('aria-label').includes(`${from.toUpperCase()} →`), [from]);
      // The map is redrawn when its box changes width, a frame after the resize.
      await page.waitForFunction(() => {
        const at = name => document.querySelector(`#overview-topology .topology-node[aria-label^="${name}"] .topology-station`)?.getBoundingClientRect();
        const a = at('Alpha'), b = at('Beta');
        return Boolean(a && b) && Math.abs(b.y - a.y) > Math.abs(b.x - a.x);
      });
      const g = await mapGeometry();
      assert.ok(Math.abs(g.b.y - g.a.y) > Math.abs(g.b.x - g.a.x), 'portrait: the line is upright');
      assert.ok(pointsTowards(g, from, to), `upright, the triangle points at ${to}: ${JSON.stringify(g.points)}`);
    }
    // Trains inside a station are not drawn: the number in the station's brick
    // says how many stand there (SkarmBana: "siffran = tåg inne"). No codes on the map.
    for (const station of ['a', 'b']) {
      await showTraffic([], parked(4).map(train => ({ ...train, station_id: station })));
      const name = station === 'a' ? 'Alpha' : 'Beta';
      await page.waitForFunction(name => document.querySelector(`#overview-topology .topology-node[aria-label^="${name}"] .topology-count`)?.textContent === '4', name);
      assert.equal(await page.locator('#overview-topology .topology-code').count(), 0, 'only names on the map');
      assert.equal(await page.locator('#overview-topology .topology-train.at-station').count(), 0, `station ${station}: no row of tags`);
    }
    // Readable on a phone: the map's box grows with the upright line.
    // Wait and measure in one step: the map may be redrawn (new nodes) between two separate calls.
    const heights = await (await page.waitForFunction(() => {
      const texts = [...document.querySelectorAll('#overview-topology .topology-name, #overview-topology .train-number')];
      const names = texts.filter(text => text.matches('.topology-name'));
      if (!names.length || names.some(text => text.getBoundingClientRect().height === 0)) return null;
      return texts.map(text => [text.textContent, text.getBoundingClientRect().height, getComputedStyle(text).display]);
    })).jsonValue();
    const smallest = Math.min(...heights.map(([, height]) => height));
    assert.ok(smallest >= 7, `map text ${smallest} px high: ${JSON.stringify(heights)}`);
    for (const heading of await page.locator('#overview-view .kr-ph a, #overview-view .kr-ph .kr-linkbtn').all()) {
      if (await heading.isVisible()) assert.ok((await heading.boundingBox()).height < 22, 'a heading link stays on one line');
    }
    await page.evaluate(() => refreshLocalClock());
    await page.setViewportSize({ width: 1200, height: 900 });
    // Older paths record only the line position; that is on the line too.
    await showTraffic([], [{ train_number: '421', status: 'connection', connection_id: 'a-b', from_station_id: 'a', to_station_id: 'b' }]);
    await page.locator('#overview-topology .topology-train.on-line[data-train-number="421"]').waitFor();
    // Inside a station: no tag, only the number in the station's brick. With a clear
    // for the next line the train is that outlined tag on the line instead.
    await showTraffic([], [{ train_number: '421', status: 'station', station_id: 'b' }]);
    await page.waitForFunction(() => document.querySelector('#overview-topology .topology-node[aria-label^="Beta"] .topology-count')?.textContent === '1');
    assert.equal(await mapTrain.count(), 0);
    await showTraffic([line('reserved', 'b', 'a')], [{ train_number: '421', status: 'station', station_id: 'b' }]);
    await page.locator('#overview-topology .topology-train.cleared[data-train-number="421"]').waitFor();
    assert.equal(await mapTrain.count(), 1);
    // Many trains inside one station: still only the number.
    await showTraffic([], parked(4));
    await page.waitForFunction(() => [...document.querySelectorAll('#overview-topology .topology-count')].some(count => count.textContent === '4'));
    assert.equal(await page.locator('#overview-topology .topology-train').count(), 0);
    // Clicking a train on the map lights its route and opens the train panel.
    await showTraffic([line('occupied')], [{ train_number: '421', status: 'connection', connection_id: 'a-b', from_station_id: 'a', to_station_id: 'b' }]);
    await page.locator('#overview-topology .topology-train.on-line[data-train-number="421"]').click();
    const trainPanel = page.locator('#drift-train-detail');
    await trainPanel.locator('.kr-aside__now').waitFor();
    assert.equal((await trainPanel.locator('.kr-aside__row .train-number, .kr-aside__row .kr-badge').first().textContent()).trim(), '421');
    await page.locator('#overview-topology .topology-train.selected[data-train-number="421"]').waitFor();
    assert.ok(await page.locator('#overview-topology .route-highlight').count() > 0);
    assert.equal(await page.locator('#overview-graph .tr-line[data-train-number="421"] .pl.sel').count(), 1);
    // The other trains step back; the ones standing inside a station are not drawn at all.
    await showTraffic([line('occupied')], parked(4));
    await page.waitForFunction(() => document.querySelector('#overview-topology .topology-train.on-line') !== null);
    assert.equal(await page.locator('#overview-topology .topology-train[data-train-number="901"]').count(), 0);
    assert.equal(await page.locator('#overview-topology .topology-train.dimmed[data-train-number="421"]').count(), 0);
    await trainPanel.getByRole('button', { name: 'Stäng tågpanelen' }).click();
    await showTraffic([], []);

    // A train under "Nästa händelser" opens its route and where it is now.
    const upcomingTrain = page.locator('#drift-upcoming button.kr-ev[data-train-number="421"]').first();
    await upcomingTrain.click();
    await trainPanel.locator('.kr-aside__now').waitFor();
    assert.equal((await trainPanel.locator('.kr-aside__route').textContent()).trim(), 'Alpha → Beta');
    assert.equal(await trainPanel.locator('.kr-aside__now').textContent(), 'På linjen A → B · avgick 06:05');
    assert.equal(await trainPanel.locator('.kr-aside__status .kr-tag').textContent(), '3 min sen');
    assert.deepEqual(await trainPanel.locator('.kr-stop:not(.between) .kr-stoplink > span').allTextContents(), ['Alpha', 'Beta']);
    assert.deepEqual(await trainPanel.locator('.kr-stop:not(.between) .kr-stoplink small').allTextContents(), ['spår 1', 'spår 2']);
    assert.deepEqual(await trainPanel.locator('.kr-stop:not(.between) .t').allTextContents(), ['—', '06:05', '06:20', '—']);
    assert.equal(await trainPanel.locator('.kr-stop.done').count(), 1);
    assert.equal(await trainPanel.locator('.kr-stop.between .kr-between__text').textContent(), 'På linjen · 421 →');
    assert.equal(await page.locator('#drift-upcoming button.kr-ev[aria-pressed="true"]').first().getAttribute('data-train-number'), '421');
    // The timetable dialog follows the same choice: the train is marked in
    // the list, its route is lit on a small map, and its calls say where it is.
    await page.locator('#drift-timetable-open').click();
    const routeDetail = page.locator('#overview-route-detail');
    await routeDetail.locator('.train-detail-now').waitFor();
    assert.equal(await page.locator('#overview-route-list .route-number.active').textContent(), '421');
    assert.equal(await routeDetail.locator('.train-detail-now').textContent(), 'Nu: På linjen A → B · avgick 06:05 3 min sen');
    assert.deepEqual(await routeDetail.locator('.route-stop b').allTextContents(), ['A · Alpha', 'B · Beta']);
    assert.equal(await routeDetail.locator('.route-stop.done').count(), 1);
    assert.equal(await routeDetail.locator('.train-detail-between').textContent(), 'På linjen');
    assert.ok(await routeDetail.locator('#overview-route-map .topology-track.route-highlight').count() > 0, 'its route is lit');
    // The small map has this train only; Banöversikten has the ones on the line (those inside a station are a number).
    // It moves with the line at once, not when /v1/train next answers.
    let releaseTrain; holdTrain = new Promise(resolve => { releaseTrain = resolve; });
    await showTraffic([line('occupied')], parked(2));
    await routeDetail.locator('#overview-route-map .topology-train.on-line.selected[data-train-number="421"]').waitFor({ timeout: 2000 });
    assert.equal(await routeDetail.locator('#overview-route-map .topology-train').count(), 1);
    // Banöversikten is redrawn on the next animation frame; count once it has its train.
    await page.locator('#overview-topology .topology-train[data-train-number="421"]').waitFor({ timeout: 2000 });
    assert.equal(await page.locator('#overview-topology .topology-train').count(), 1);
    holdTrain = null; releaseTrain();
    // A call is a button: its station is lit, the train stays chosen. So
    // does a station on the small map.
    await routeDetail.locator('.route-stop button[data-station-id="b"]').click();
    await routeDetail.locator('.route-stop.selected button[data-station-id="b"]').waitFor();
    assert.equal(await trainPanel.isVisible(), true);
    await routeDetail.locator('#overview-route-map .topology-node[aria-label^="Alpha"]').dispatchEvent('click');
    await routeDetail.locator('.route-stop.selected button[data-station-id="a"]').waitFor();
    assert.equal(await page.locator('#overview-route-list .route-number.active').textContent(), '421');
    assert.equal(await trainPanel.isVisible(), true);
    await page.locator('#drift-timetable-dialog [data-close-modal]').click();
    await showTraffic([], []);
    // Without a stream (here a 503) Drift keeps its five seconds.
    assert.ok(calls.some(c => c[1] === '/v1/events'), 'the page asked for the stream');
    assert.equal(await page.evaluate(() => globalThis.TrainMeetLive.connected), false);
    // The choice survives the five-second refresh, then × closes it.
    const trainCalls = calls.filter(c => c[1] === '/v1/train').length;
    await page.waitForTimeout(5600);
    assert.ok(calls.filter(c => c[1] === '/v1/train').length > trainCalls, 'the open panel refreshes with Drift');
    assert.equal(await trainPanel.isVisible(), true);
    await trainPanel.getByRole('button', { name: 'Stäng tågpanelen' }).click();
    await trainPanel.waitFor({ state: 'hidden', timeout: 1500 });
    assert.equal(await page.locator('#drift-upcoming button.kr-ev[aria-pressed="true"]').count(), 0);
    // Closing it lets go of the train everywhere.
    assert.equal(await page.locator('#overview-route-list .route-number.active').count(), 0);
    assert.equal(await page.locator('#overview-route-map').count(), 0);
    assert.equal(await page.locator('#overview-graph .pl.sel').count(), 0);
    // Chosen in the timetable dialog, the train opens the train panel at once
    // and is lit in the diagram and under Nästa händelser, as when chosen anywhere else.
    await page.locator('#drift-timetable-open').click();
    await page.locator('#overview-route-list .route-number[data-train-number="421"]').click();
    await trainPanel.locator('.kr-aside__now').waitFor({ timeout: 1500 });
    assert.equal(await page.locator('#overview-graph .tr-line[data-train-number="421"] .pl.sel').count(), 1);
    assert.equal(await page.locator('#drift-upcoming button.kr-ev[aria-pressed="true"]').first().getAttribute('data-train-number'), '421');
    // A station picked under Stationer there shows that station instead: the train
    // is let go everywhere.
    await page.locator('#overview-station-counts button[data-station-id="b"]').click();
    await trainPanel.waitFor({ state: 'hidden', timeout: 1500 });
    assert.equal(await page.locator('#overview-route-list .route-number.active').count(), 0);
    assert.equal(await page.locator('#overview-station-inspector').isVisible(), true);
    await page.locator('#drift-timetable-dialog [data-close-modal]').click();
    // A station chosen in the table keeps its row lit and opens its panel.
    await page.locator('#device-list tr.sel[data-station-id="b"]').waitFor({ timeout: 1500 });
    await page.locator('#overview-station-inspector .kr-aside__name').getByText('Beta', { exact: true }).waitFor({ timeout: 1500 });
    await page.locator('#overview-station-inspector').getByRole('button', { name: 'Stäng stationspanelen' }).click();
    await page.locator('#overview-station-inspector').waitFor({ state: 'hidden', timeout: 1500 });
    assert.equal(await page.locator('#device-list tr.sel').count(), 0);
    // Chosen in the diagram, the same as under Nästa händelser.
    await page.locator('#overview-graph .tr-line[data-train-number="421"]').dispatchEvent('click');
    await trainPanel.locator('.kr-aside__now').waitFor({ timeout: 1500 });
    assert.equal(await page.locator('#drift-upcoming button.kr-ev[aria-pressed="true"]').first().getAttribute('data-train-number'), '421');
    // A click on the map's open ground lets go of it, panel and all.
    await page.locator('#overview-topology').dispatchEvent('click');
    await trainPanel.waitFor({ state: 'hidden', timeout: 1500 });
    assert.equal(await page.locator('#overview-graph .pl.sel').count(), 0);
    // The search in the header finds a train or a station, and Esc lets go.
    await page.locator('#kr-search').fill('421');
    await page.locator('#kr-search-results [role="option"]').first().click();
    await trainPanel.locator('.kr-aside__now').waitFor({ timeout: 1500 });
    await page.keyboard.press('Escape');
    await trainPanel.waitFor({ state: 'hidden', timeout: 1500 });
    await page.locator('#overview-clock-start').click();
    await page.locator('#overview-clock-stop').waitFor({state:'visible'});
    assert.equal(running, true);
    await page.locator('#overview-clock-stop').click();
    await page.locator('#overview-clock-start').waitFor({state:'visible'});
    assert.equal(running, false);
    assert.equal(JSON.parse(calls.find(c=>c[0]==='POST'&&c[1]==='/v1/clock')[2]).meet_generation,7);


    // Every text Drift draws is translated in all five languages: the panels, the
    // train and station panels, the popover and the dialogs.
    for (const language of ['en', 'da', 'nb', 'de']) {
      await page.evaluate(code => TrainMeetI18n.setLanguage(code), language);
      await page.evaluate(() => { TrainMeetDrift.hooks.selectTrain('421'); });
      await page.locator('#drift-train-detail .kr-aside__now').waitFor();
      await page.evaluate(() => { TrainMeetDrift.hooks.selectStation('a', true); });
      await page.locator('#overview-station-inspector .kr-aside__name').waitFor();
      await page.evaluate(() => document.querySelector('#connect-terminals').showPopover());
      await page.evaluate(() => document.querySelector('#connect-terminals').hidePopover());
      for (const id of ['drift-timetable-dialog']) {
        await page.evaluate(id => TrainMeetDrift.openDialog(id), id);
        await page.keyboard.press('Escape');
      }
      for (const id of ['new-day-modal', 'device-form-modal', 'display-placement-modal', 'device-language-modal', 'device-remove-modal', 'clock-control-form-modal', 'clock-source-modal']) {
        await page.evaluate(id => openModal(id), id);
        await page.evaluate(id => document.getElementById(id).close(), id);
      }
      assert.deepEqual(await page.evaluate(() => TrainMeetI18n.missing()), [], `untranslated in ${language}`);
      await page.evaluate(() => { TrainMeetDrift.hooks.clear(); });
    }
    await page.evaluate(() => TrainMeetI18n.setLanguage('sv'));

    // Drafts stay intact when the live state refreshes or a save fails.
    await page.getByRole('link',{name:'Inställningar',exact:true}).click();
    // Settings is one section at a time, with a side menu in three groups.
    // Every form that can be changed has its own Avbryt and Spara, both dark
    // until something differs from what was last saved.
    const nav = page.locator('#settings-nav');
    assert.equal(await nav.locator('a[href="/installningar#kod"]').textContent(), 'Anslutningskod');
    assert.deepEqual(await nav.locator('a.kr-nav').evaluateAll(links => links.map(link => link.getAttribute('href').split('#')[1])),
      ['traff', 'skarmar', 'wifi', 'obemannade', 'server', 'kod', 'anvandare', 'uppdatering', 'sprak', 'visning', 'farozon']);
    assert.equal(await page.locator('#admin-view .kr-setsec').count(), 11);
    assert.equal(await page.locator('#admin-view .kr-setsec:not([hidden])').count(), 1, 'one section at a time');
    await nav.locator('a[href="/installningar#kod"]').click();
    await page.locator('#connection-code-form').waitFor({state:'visible'});
    assert.equal(await page.locator('#connection-code-form #kod-value').count(), 1);
    assert.equal(await page.locator('#connection-code-form #connection-badge-validity').count(), 1);
    assert.equal(await page.locator('#traff').isVisible(), false);
    const savebar = form => page.evaluate(selector => { const f = document.querySelector(selector); return { cancel: !f.querySelector('[data-save-cancel]').disabled, save: !f.querySelector('[data-save-submit]').disabled, text: f.querySelector('[data-save-state]').textContent }; }, form);
    assert.deepEqual(await savebar('#connection-code-form'), { cancel: false, save: false, text: 'Inget ändrat' });
    await page.locator('#connection-badge-validity').selectOption('24');
    assert.deepEqual(await savebar('#connection-code-form'), { cancel: true, save: true, text: 'Ändrat: Giltighet' });
    await page.locator('#connection-code-form [data-save-cancel]').click();
    assert.equal(await page.locator('#connection-badge-validity').inputValue(), '0');
    assert.deepEqual(await savebar('#connection-code-form'), { cancel: false, save: false, text: 'Inget ändrat' });
    // The old addresses still land in the right section.
    await page.evaluate(() => { history.pushState(null, '', '/installningar#anslutning'); applyWorkspaceRoute(); });
    await page.locator('#connection-code-form').waitFor({state:'visible'});
    await page.evaluate(() => { history.pushState(null, '', '/installningar#traff'); applyWorkspaceRoute(); });
    await page.locator('#cloud-auto-form').waitFor({state:'visible'});
    assert.equal(await page.locator('#cloud-auto-form .kr-kv').count(), 6);
    assert.equal(await page.locator('#cloud-auto-form a[href="/tidtabell"]').isVisible(), true, 'the timetable is reachable from Settings, also on a phone');
    assert.equal(await page.locator('#traff [data-open-modal="runtime-sync-form-modal"]').isVisible(), true);
    await nav.locator('a[href="/installningar#anvandare"]').click();
    assert.equal(await page.locator('#admin-users-settings #users-invite-open').isVisible(), true);
    await nav.locator('a[href="/installningar#uppdatering"]').click();
    assert.equal(await page.locator('#software-update-settings #software-version').count(), 1);
    await nav.locator('a[href="/installningar#server"]').click();
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
    await nav.locator('a[href="/installningar#traff"]').click();
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
    assert.ok(modalIds.includes('time-machine-modal'));
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
    // Language preference lives in Settings, not an extra toolbar: tiles to pick
    // from, saved with Spara like everything else.
    for(const language of ['sv','da','nb','en','de']){
      await page.goto('http://127.0.0.1:9999/installningar#sprak');
      const tile = page.locator(`#language-tiles [data-value="${language}"]`);
      await tile.waitFor();
      if (await tile.getAttribute('aria-checked') !== 'true') {
        await tile.click();
        assert.equal(await page.locator('#language-form [data-save-submit]').isDisabled(), false);
        await page.locator('#language-form [data-save-submit]').click();
      }
      await page.waitForFunction(code => TrainMeetI18n.getLanguage() === code, language);
      assert.equal(await page.locator('#settings-nav a[href="/installningar#farozon"]').textContent(),{sv:'Farozon',da:'Farezone',nb:'Faresone',en:'Danger zone',de:'Gefahrenbereich'}[language]);
      assert.equal(await page.locator('#language-form [data-save-submit]').isDisabled(), true);
      // Signed in, the old picker address lands on Drift, whatever the language.
      await page.goto('http://127.0.0.1:9999/#workspaces');
      await page.waitForURL('**/drift');
      assert.equal(await page.locator('#participant-view').isVisible(), false);
    }
    await page.goto('http://127.0.0.1:9999/installningar#sprak');
    await page.locator('#language-tiles [data-value="sv"]').click();
    await page.locator('#language-form [data-save-submit]').click();
    await page.waitForFunction(() => TrainMeetI18n.getLanguage() === 'sv');
    await page.goto('http://127.0.0.1:9999/installningar#traff');
    // Linking to Cloud never reports "undefined", even without a message.
    await page.locator('[data-open-modal="runtime-sync-form-modal"]').click();
    const syncBoxes=page.locator('#runtime-sync-code-boxes input');
    for(let index=0;index<6;index++) await syncBoxes.nth(index).fill(String(index+1));
    await page.locator('#runtime-sync-form [type=submit]').click();
    // The dialog closes on success; its message is shown in #modal-result.
    await page.locator('#modal-result').getByText('3/3 · Cloud-kopplingen är sparad på servern.',{exact:true}).waitFor();
    assert.doesNotMatch(await page.locator('#modal-result').textContent(),/undefined/);
    await page.setViewportSize({width: 1200, height: 900});
    await page.goto('http://127.0.0.1:9999/drift');
    // A box is removed from the dialog its row opens, after a confirmation;
    // a failure leaves it where it was.
    const aRow = page.locator('#device-list tr[data-station-id="a"]');
    await aRow.getByRole('button', {name: 'Redigera', exact: true}).click();
    await page.locator('#device-remove-open').click();
    const removal = page.locator('#device-remove-modal');
    assert.equal(await removal.locator('#device-remove-code').textContent(), 'TBX-123');
    await removal.getByRole('button', {name: 'Ta bort klient', exact: true}).click();
    await removal.getByText('Tillfälligt fel').waitFor();
    assert.match(await aRow.textContent(), /TBX-123/);
    removeFails = false;
    await removal.getByRole('button', {name: 'Ta bort klient', exact: true}).click();
    await removal.waitFor({state: 'hidden'});
    await aRow.getByText('Obemannad', {exact: true}).waitFor();
    assert.equal(JSON.parse(calls.find(c=>c[1]==='/v1/devices/remove')[2]).device_id,'esp-1');

    // Placement retains publication/config CAS and never interprets imported HTML.
    await aRow.locator('button[title="Ändra vänster och höger"]').click();
    const form = page.locator('#display-placement-modal');
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
    await aRow.locator('button[title="Ändra vänster och höger"]').getByText('— · B', {exact: true}).waitFor();

    region='us';
    await page.goto('http://127.0.0.1:9999/drift');
    await page.locator('#server-region').filter({hasText:'US'}).waitFor();
    // The shell shows the US summary at once; Drift hides its panels on its next drawing.
    await page.locator('#us-runtime-summary').waitFor({state:'visible'});
    for (const id of ['drift-map', 'drift-stations', 'drift-graph', 'overview-traffic', 'drift-stats', 'connect-terminals']) await page.locator('#' + id).waitFor({state:'hidden', timeout: 2000});
    assert.equal(calls.some(c=>c[1].includes('local-configuration')||c[1]==='/v1/operating-mode'),false);
    assert.deepEqual(errors,[]);
    await screenshot('server-design');
    console.log('Server design: routes, fenced inline edits, failure recovery, dialogs, responsive layout, locales and EU/US separation passed.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
