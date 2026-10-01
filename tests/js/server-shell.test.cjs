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
          case '/v1/admin/access': data = { username: 'admin', password_configured: true }; break;
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
    // A box finds the server and needs no address or code; the card says so.
    await page.locator('#pv-connect-card').getByText('Boxen hittar servern själv').waitFor();
    assert.equal(await page.locator('#pv-connect-card').getByText(/parningskod/i).count(), 0);
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
    await page.locator('#device-list .status-row').waitFor();
    assert.equal(await page.locator('#application-menu').isVisible(), false);
    // Clients and the left/right setting are two cards: operations and a
    // setting used to share one, and the station table read as client rows.
    assert.equal((await page.locator('#device-management > .section-heading h2').textContent()).trim(),'Klienter');
    assert.equal(await page.locator('#device-management #server-station-rows').count(),0);
    assert.equal((await page.locator('#station-placement > h2').textContent()).trim(),'TMBox-placering');
    assert.equal(await page.locator('#station-placement #server-station-rows').count(),1);
    assert.ok(await page.locator('#device-management').evaluate(card=>card.querySelector('#device-list').compareDocumentPosition(card.querySelector('.device-reconnect'))&Node.DOCUMENT_POSITION_FOLLOWING),'Reconnect comes after the client list');
    // Nothing on Drift folds away (1.23.0): every module and every part of it
    // is open, the timetable and its search included.
    assert.equal(await page.locator('#overview-view details').count(), 0);
    assert.equal(await page.locator('#overview-route-search').isVisible(), true);
    assert.equal(await page.locator('#device-management .device-reconnect button').isVisible(), true);
    assert.equal(await page.locator('#traffic-timeline').isVisible(), true);
    // The design correction keeps 1.23.0's open blocks and updates their
    // real heading, not the removed disclosure summary.
    assert.equal(await page.locator('#traffic-stations-heading').textContent(), 'Inne på stationerna (2)');
    // TMBox-placering lists every station at once, however many there are.
    extraPlacementStations = 6;
    await page.evaluate(() => refreshCloudPresentation());
    await page.locator('#server-station-rows tbody tr').nth(6).waitFor();
    assert.equal(await page.locator('#server-station-rows tbody tr').count(), 7);
    assert.equal(await page.locator('#server-station-rows tbody tr').nth(6).isVisible(), true);
    assert.equal(await page.locator('#station-placement details').count(), 0);
    extraPlacementStations = 0;
    await page.evaluate(() => refreshCloudPresentation());
    await page.locator('#server-station-rows tbody tr').nth(1).waitFor({ state: 'detached' });
    assert.equal(await page.locator('#traffic-only-deviations').isChecked(), true);
    assert.equal(await page.locator('#overview-graph').isVisible(), true);
    // Connecting a signal box (1.22.0): under Klienter, the address and the
    // code to type into TKL, and each paired signal box with its station.
    const connect = page.locator('#connect-terminals');
    assert.equal(await page.locator('#device-management').evaluate(card => card.nextElementSibling.id), 'connect-terminals');
    assert.equal(await connect.locator('h2').textContent(), 'Anslut ställverk och appar');
    await connect.locator('#connect-code').getByText('262-617', { exact: true }).waitFor();
    assert.equal(await connect.locator('#connect-address').textContent(), 'http://192.168.1.20:8787');
    // Behind Caddy on server.trainmeet.app the box comes in where this page did.
    assert.equal(await page.evaluate(() => connectAddress({host: '10.0.0.5', port: 8787}, 'https://server.trainmeet.app')), 'https://server.trainmeet.app');
    assert.equal(await connect.locator('.connect-steps li').count(), 3);
    assert.equal(await connect.locator('#connect-code-note').isVisible(), false);
    const terminalRow = page.locator('#terminal-list .terminal-row[data-client-id="tkl-cda"]');
    assert.equal(await page.locator('#terminal-list h3').textContent(), 'Ställverk (TKL)');
    assert.match(await terminalRow.textContent(), /CDA TKL 1.*A · Alpha.*Online/);
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
    terminals[0] = { ...terminals[0], has_access: false };
    await page.evaluate(() => refreshDevices());
    await terminalRow.getByText('Ny träff: ange den nya koden i ställverket').waitFor();
    await terminalRow.locator('.terminal-remove').click();
    await terminalRow.getByText(/kopplas bort direkt/).waitFor();
    await terminalRow.getByRole('button', { name: 'Ta bort', exact: true }).click();
    await page.locator('#terminal-list').waitFor({ state: 'hidden', timeout: 1500 });
    assert.deepEqual(terminals, []);
    assert.equal(JSON.parse(calls.find(c => c[1] === '/v1/terminals/remove')[2]).client_id, 'tkl-cda');

    // Drift as one whole (1.20.0). The train diagram is drawn without opening
    // the timetable, and nothing is lit until someone picks a train.
    await page.locator('#overview-graph .overview-train-group[data-train-number="421"]').waitFor();
    assert.equal(await page.locator('#overview-graph .overview-train-group.dimmed, #overview-graph .overview-train-group.selected').count(), 0);
    assert.equal(await page.locator('#overview-topology .route-highlight, #overview-topology .dimmed').count(), 0);
    assert.equal(await page.locator('#overview-route-list .route-number.active').count(), 0);
    assert.equal(await page.locator('#topology-head-meta').textContent(), '2 stationer · 1 sträcka');
    // One timetable card. Its head says what is inside; Cloud's check findings
    // are its last part, the count in their heading and the list below.
    assert.equal(await page.locator('#timetable-summary-meta').textContent(), '1 tåg · sök tåg · tågrutter · stationer');
    const findingsRow = page.locator('#drift-timetable > #published-findings');
    const findingsTag = findingsRow.locator('#published-findings-tag');
    await findingsTag.getByText('1 konflikt i version 8', {exact: true}).waitFor();
    assert.match(await findingsTag.getAttribute('class'), /tm-tag--warn/);
    assert.equal(await findingsRow.locator('#published-findings-list').isVisible(), true);
    assert.equal(await page.locator('#drift-timetable details').count(), 0);
    for (const [rows, text, kind] of [
      [[...imported, imported[0], {level: 'observation'}], '2 konflikter · 1 observation i version 8', 'warn'],
      [[{level: 'observation'}, {level: 'observation'}], '2 observationer i version 8', 'neutral'],
      [[], 'Inga konflikter i version 8', 'ok'],
      [null, 'Inga kontrolluppgifter i version 8', 'neutral']]) {
      findings = rows;
      await page.evaluate(() => refreshCloudPresentation());
      await findingsTag.getByText(text, {exact: true}).waitFor();
      assert.match(await findingsTag.getAttribute('class'), new RegExp(`tm-tag--${kind}$`));
    }
    findings = imported;
    await page.evaluate(() => refreshCloudPresentation());
    // Without a version number from Cloud the tag says no version.
    version = undefined;
    await page.evaluate(() => refreshServerContext());
    await findingsTag.getByText('1 konflikt', {exact: true}).waitFor();
    version = 8;
    await page.evaluate(() => refreshServerContext());
    await findingsTag.getByText('1 konflikt i version 8', {exact: true}).waitFor();
    // The diagram is as tall as its stations and meets their names: never
    // stretched on a wide screen, the first hour readable beside the names.
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.evaluate(() => refreshLocalClock());
    const diagram = await page.evaluate(() => {
      const scroller = document.querySelector('#overview-graph-scroll').getBoundingClientRect();
      const axes = [...document.querySelectorAll('#overview-graph .overview-graph-axis')].map(line => line.getBoundingClientRect().y);
      const names = [...document.querySelectorAll('#overview-graph-station-labels .overview-graph-station')].map(text => { const r = text.getBoundingClientRect(); return r.y + r.height / 2; });
      const firstHour = document.querySelector('#overview-graph .overview-graph-time').getBoundingClientRect();
      return { axes, names, firstHourLeft: firstHour.x - scroller.x, height: scroller.height, canvas: document.querySelector('#overview-graph').getBoundingClientRect().height };
    });
    assert.equal(diagram.axes.length, 2);
    diagram.axes.forEach((y, index) => assert.ok(Math.abs(y - diagram.names[index]) < 4, `station ${index}: line ${y}, name ${diagram.names[index]}`));
    assert.ok(diagram.firstHourLeft >= 60, `first hour at ${diagram.firstHourLeft}`);
    assert.ok(diagram.height <= diagram.canvas + 20, `no empty diagram: ${diagram.height} for ${diagram.canvas}`);
    await page.setViewportSize({ width: 1200, height: 900 });

    // Trains on the map. A clear given is an outlined tag a quarter along from
    // the station the train leaves, its triangle towards where it goes; once
    // departed the tag is filled. A request alone draws nothing.
    const mapTrain = page.locator('#overview-topology .topology-train[data-train-number="421"]');
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
      assert.equal(g.tagFill, 'rgb(255, 255, 255)', 'outlined');
      assert.equal(g.arrowFill, 'none');
      const at = along(g, g.tag, from, to);
      assert.ok(at > 0.15 && at < 0.4, `near the station it leaves: ${at}`);
      assert.ok((g.arrow.x - g.number.x) * (g[to].x - g[from].x) > 0, `the triangle leads towards ${to}`);
      assert.ok(pointsTowards(g, from, to), `the triangle points at ${to}: ${JSON.stringify(g.points)}`);
      if (from === 'a') assert.equal(overlaps(g.box, g.ring), false, 'clear of the station it leaves');
      // ...and of the ring drawn round it when its route is lit (radius 12 to
      // the dot's 7): on a short line the tag sat on it (1.23.0).
      const r = g.ring.width / 2 * 12 / 7, c = { x: g.ring.x + g.ring.width / 2, y: g.ring.y + g.ring.height / 2 };
      if (from === 'a') assert.equal(overlaps(g.box, { x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r }), false, 'clear of the ring');
      await showTraffic([line('occupied', from, to)], [{ train_number: '421', status: 'connection', connection_id: 'a-b', from_station_id: from, to_station_id: to }]);
      await page.locator('#overview-topology .topology-train.on-line[data-train-number="421"]').waitFor();
      g = await mapGeometry();
      assert.equal(await mapTrain.count(), 1, 'one tag, not one per source');
      assert.equal(await mapTrain.getAttribute('aria-label'), `Tåg 421 · ${route} · på linjen`);
      assert.equal(g.tagFill, 'rgb(34, 86, 195)', 'filled');
      assert.equal(g.arrowFill, 'rgb(255, 255, 255)');
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
      const g = await mapGeometry();
      assert.ok(Math.abs(g.b.y - g.a.y) > Math.abs(g.b.x - g.a.x), 'portrait: the line is upright');
      assert.ok(pointsTowards(g, from, to), `upright, the triangle points at ${to}: ${JSON.stringify(g.points)}`);
    }
    // Trains inside a station take a side free of names and lines.
    for (const station of ['a', 'b']) {
      await showTraffic([], parked(4).map(train => ({ ...train, station_id: station })));
      await page.locator('#overview-topology .topology-train[data-train-number="+2"]').waitFor();
      const clash = await page.evaluate(() => {
        const rects = selector => [...document.querySelectorAll(selector)].map(element => element.getBoundingClientRect());
        const hit = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
        const tags = rects('#overview-topology .topology-train.at-station .topology-train-tag');
        return tags.some(tag => rects('#overview-topology .topology-name, #overview-topology .topology-track').some(other => hit(tag, other)));
      });
      assert.equal(clash, false, `station ${station}: the row covers a name or a line`);
    }
    // Readable on a phone: the map's box grows with the upright line.
    const smallest = await page.locator('#overview-topology .topology-name, #overview-topology .train-number').evaluateAll(texts => Math.min(...texts.map(text => text.getBoundingClientRect().height)));
    assert.ok(smallest >= 7, `map text ${smallest} px high`);
    for (const heading of await page.locator('.overview-section-heading a').all()) {
      if (await heading.isVisible()) assert.ok((await heading.boundingBox()).height < 22, 'a heading link stays on one line');
    }
    // The chosen train's badge sits under the map: upright on a phone, the
    // line ends at the bottom, where the badge used to cover the last station.
    await page.evaluate(() => selectOverviewTrain('421'));
    const badge = await page.locator('#overview-route-badge').boundingBox();
    const map = await page.locator('#overview-topology').boundingBox();
    assert.ok(badge.y >= map.y + map.height + 8, `the badge (${badge.y}) leaves a gap below the map (${map.y + map.height})`);
    await page.evaluate(() => selectOverviewTrain(null));
    // A long address wraps inside the connect card instead of running out of it.
    const spill = await page.evaluate(() => {
      renderConnectCard({ host: 'trainmeet-server-grimslov-2027.example.org', port: 8787, code: '123-456', code_state: 'valid' });
      const card = document.querySelector('#connect-terminals');
      const inner = card.getBoundingClientRect().right - parseFloat(getComputedStyle(card).paddingRight);
      // The text itself, not just its box: overflowing text leaves the box as it was.
      const right = element => { const range = document.createRange(); range.selectNodeContents(element); return Math.max(element.getBoundingClientRect().right, range.getBoundingClientRect().right); };
      return [...card.querySelectorAll('.connect-fact, .connect-fact > *')].filter(element => right(element) > inner + 0.5).map(element => element.id || element.className);
    });
    assert.deepEqual(spill, [], 'nothing in the connect card runs past its edge');
    // A usual address stays whole on one line; Kopiera moves below it instead.
    await page.evaluate(() => renderConnectCard({ host: '192.168.100.200', port: 8787, code: '123-456', code_state: 'valid' }));
    assert.ok((await page.locator('#connect-address').boundingBox()).height < 30, 'the address is not broken');
    await page.evaluate(() => refreshLocalClock());
    await page.setViewportSize({ width: 1200, height: 900 });
    // Older paths record only the line position; that is on the line too.
    await showTraffic([], [{ train_number: '421', status: 'connection', connection_id: 'a-b', from_station_id: 'a', to_station_id: 'b' }]);
    await page.locator('#overview-topology .topology-train.on-line[data-train-number="421"]').waitFor();
    // Inside a station: a pale tag above it, no triangle. With a clear for the
    // next line it is that outlined tag instead, not both.
    await showTraffic([], [{ train_number: '421', status: 'station', station_id: 'b' }]);
    await page.locator('#overview-topology .topology-train.at-station[data-train-number="421"]').waitFor();
    let g = await mapGeometry();
    assert.equal(g.arrow, null);
    assert.equal(await mapTrain.getAttribute('aria-label'), 'Tåg 421 vid B');
    assert.ok(g.tag.y < g.b.y && Math.abs(g.tag.x - g.b.x) < 2, 'above its station');
    await showTraffic([line('reserved', 'b', 'a')], [{ train_number: '421', status: 'station', station_id: 'b' }]);
    await page.locator('#overview-topology .topology-train.cleared[data-train-number="421"]').waitFor();
    assert.equal(await mapTrain.count(), 1);
    // Three trains fit above a station; more show two and +N.
    await showTraffic([], parked(3));
    await page.locator('#overview-topology .topology-train.at-station[data-train-number="903"]').waitFor();
    await showTraffic([], parked(4));
    await page.locator('#overview-topology .topology-train[data-train-number="+2"]').waitFor();
    assert.deepEqual(await page.locator('#overview-topology .topology-train.at-station').evaluateAll(tags => tags.map(tag => tag.dataset.trainNumber)), ['901', '902', '+2']);
    const row = await page.locator('#overview-topology .topology-train.at-station .topology-train-tag').evaluateAll(tags => tags.map(tag => tag.getBoundingClientRect().toJSON()));
    assert.equal(overlaps(row[0], row[1]) || overlaps(row[1], row[2]), false, 'side by side');
    // Clicking a train on the map lights its route and opens the train panel.
    await showTraffic([line('occupied')], [{ train_number: '421', status: 'connection', connection_id: 'a-b', from_station_id: 'a', to_station_id: 'b' }]);
    await page.locator('#overview-topology .topology-train.on-line[data-train-number="421"]').click();
    await page.locator('#drift-train-detail .train-detail-now').waitFor();
    assert.equal(await page.locator('#drift-train-detail h3').textContent(), 'Tåg 421');
    await page.locator('#overview-topology .topology-train.selected[data-train-number="421"]').waitFor();
    assert.ok(await page.locator('#overview-topology .route-highlight').count() > 0);
    assert.equal(await page.locator('#overview-graph .overview-train-group.selected').getAttribute('data-train-number'), '421');
    // The other trains step back, the parked ones and their +N too.
    await showTraffic([line('occupied')], parked(4));
    await page.locator('#overview-topology .topology-train.dimmed[data-train-number="+2"]').waitFor();
    assert.equal(await page.locator('#overview-topology .topology-train.dimmed[data-train-number="901"]').count(), 1);
    assert.equal(await page.locator('#overview-topology .topology-train.dimmed[data-train-number="421"]').count(), 0);
    await page.locator('#drift-train-detail').getByRole('button', { name: 'Stäng' }).click();
    await showTraffic([], []);

    // A train in "Kommande enligt tidtabell" opens its route and where it is now.
    const upcomingTrain = page.locator('#drift-upcoming button.server-event[data-train-number="421"]').first();
    await upcomingTrain.click();
    const trainPanel = page.locator('#drift-train-detail');
    await trainPanel.locator('.train-detail-now').waitFor();
    assert.equal(await trainPanel.locator('h3').textContent(), 'Tåg 421');
    assert.equal(await trainPanel.locator('.train-detail-now').textContent(), 'Nu: På linjen A → B · avgick 06:05 3 min sen');
    assert.deepEqual(await trainPanel.locator('.route-stop b').allTextContents(), ['A · Alpha', 'B · Beta']);
    assert.deepEqual(await trainPanel.locator('.route-stop span').allTextContents(), ['avg 06:05 · spår 1', 'ank 06:20 · spår 2']);
    assert.equal(await trainPanel.locator('.route-stop.done').count(), 1);
    assert.equal(await trainPanel.locator('.train-detail-between').textContent(), 'På linjen');
    assert.equal(await page.locator('#drift-upcoming button.server-event[aria-pressed="true"]').first().getAttribute('data-train-number'), '421');
    // Tågrutter (1.23.0) follows the same choice: the train is marked in the
    // list, its route is lit on a small map, and its calls say where it is.
    const routeDetail = page.locator('#overview-route-detail');
    assert.equal(await page.locator('#overview-route-list .route-number.active').textContent(), '421');
    await routeDetail.locator('.train-detail-now').waitFor();
    assert.equal(await routeDetail.locator('.train-detail-now').textContent(), 'Nu: På linjen A → B · avgick 06:05 3 min sen');
    assert.deepEqual(await routeDetail.locator('.route-stop b').allTextContents(), ['A · Alpha', 'B · Beta']);
    assert.equal(await routeDetail.locator('.route-stop.done').count(), 1);
    assert.equal(await routeDetail.locator('.train-detail-between').textContent(), 'På linjen');
    assert.ok(await routeDetail.locator('#overview-route-map .topology-track.route-highlight').count() > 0, 'its route is lit');
    // The small map has this train only; Banöversikten above has them all.
    // It moves with the line at once, not when /v1/train next answers.
    let releaseTrain; holdTrain = new Promise(resolve => { releaseTrain = resolve; });
    await showTraffic([line('occupied')], parked(2));
    await routeDetail.locator('#overview-route-map .topology-train.on-line.selected[data-train-number="421"]').waitFor({ timeout: 2000 });
    assert.equal(await routeDetail.locator('#overview-route-map .topology-train').count(), 1);
    assert.equal(await page.locator('#overview-topology .topology-train').count(), 3);
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
    await showTraffic([], []);
    // Without a stream (here a 503) Drift keeps its five seconds.
    assert.ok(calls.some(c => c[1] === '/v1/events'), 'the page asked for the stream');
    assert.equal(await page.evaluate(() => globalThis.TrainMeetLive.connected), false);
    // The choice survives the five-second refresh, then × closes it.
    const trainCalls = calls.filter(c => c[1] === '/v1/train').length;
    await page.waitForTimeout(5600);
    assert.ok(calls.filter(c => c[1] === '/v1/train').length > trainCalls, 'the open panel refreshes with Drift');
    assert.equal(await trainPanel.isVisible(), true);
    await trainPanel.getByRole('button', { name: 'Stäng' }).click();
    assert.equal(await trainPanel.isHidden(), true);
    assert.equal(await page.locator('#drift-upcoming button.server-event[aria-pressed="true"]').count(), 0);
    // Closing it lets go of the train everywhere.
    assert.equal(await page.locator('#overview-route-list .route-number.active').count(), 0);
    assert.equal(await page.locator('#overview-route-map').count(), 0);
    assert.equal(await page.locator('#overview-graph .overview-train-group.selected').count(), 0);
    // Chosen in Tågrutter, the train opens the train panel at once and is lit
    // in the diagram and in Kommande, as when chosen anywhere else.
    await page.locator('#overview-route-list .route-number[data-train-number="421"]').click();
    await trainPanel.locator('.train-detail-now').waitFor({ timeout: 1500 });
    assert.equal(await page.locator('#overview-graph .overview-train-group.selected').getAttribute('data-train-number'), '421');
    assert.equal(await page.locator('#drift-upcoming button.server-event[aria-pressed="true"]').first().getAttribute('data-train-number'), '421');
    // A station picked under Stationer shows that station instead: the train
    // is let go everywhere.
    await page.locator('#overview-station-counts button[data-station-id="b"]').click();
    await trainPanel.waitFor({ state: 'hidden', timeout: 1500 });
    assert.equal(await page.locator('#overview-route-list .route-number.active').count(), 0);
    // Chosen in the diagram, the same as in Tågrutter.
    await page.locator('#overview-graph .overview-train-group[data-train-number="421"]').dispatchEvent('click');
    await trainPanel.locator('.train-detail-now').waitFor({ timeout: 1500 });
    assert.equal(await page.locator('#overview-route-list .route-number.active').textContent(), '421');
    // A click on the map's open ground lets go of it, panel and all.
    await page.locator('#overview-topology').dispatchEvent('click');
    await trainPanel.waitFor({ state: 'hidden', timeout: 1500 });
    assert.equal(await page.locator('#overview-graph .overview-train-group.selected').count(), 0);
    await page.locator('#overview-clock-start').click();
    await page.locator('#stop-local-clock').waitFor({state:'visible'});
    assert.equal(running, true);
    await page.locator('#stop-local-clock').click();
    await page.locator('#overview-clock-start').waitFor({state:'visible'});
    assert.equal(running, false);
    assert.equal(JSON.parse(calls.find(c=>c[0]==='POST'&&c[1]==='/v1/clock')[2]).meet_generation,7);

    // Drafts stay intact when the live state refreshes or a save fails.
    await page.getByRole('link',{name:'Inställningar',exact:true}).click();
    // Settings › Anslutning: the code and how long it holds, out of Skärmar och klocka.
    const connectionSettings = page.locator('#connection-settings');
    assert.equal(await page.locator('.server-settings-nav a[href="/installningar#anslutning"]').textContent(), 'Anslutning');
    assert.equal(await connectionSettings.locator('#connection-badge-code').count(), 1);
    assert.equal(await connectionSettings.locator('#connection-code-form').count(), 1);
    assert.equal(await page.locator('.clock-control-card #connection-badge-code, .clock-control-card #connection-code-form').count(), 0);
    await page.locator('#server-identity-form').waitFor({state:'visible'});
    // The settings kit moves existing controls into rows and card feet;
    // their handlers and the separate TKL connection section stay intact.
    assert.equal(await page.locator('#sync-and-devices .tm-lines > .tm-line').count(), 4);
    assert.equal(await page.locator('#sync-and-devices .tm-card__foot [data-open-modal="runtime-sync-form-modal"]').isVisible(), true);
    assert.equal(await page.locator('#admin-users-settings .section-heading #users-invite-open').isVisible(), true);
    assert.equal(await page.locator('#software-update-settings .update-actions #software-version').count(), 1);
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
    assert.equal(await page.locator('#connect-terminals').isVisible(),false);
    assert.equal(await page.locator('#overview-traffic').isVisible(),false);
    assert.equal(await page.locator('#drift-timetable').isVisible(),false);
    assert.equal(calls.some(c=>c[1].includes('local-configuration')||c[1]==='/v1/operating-mode'),false);
    assert.deepEqual(errors,[]);
    await screenshot('server-design');
    console.log('Server design: routes, fenced inline edits, failure recovery, dialogs, responsive layout, locales and EU/US separation passed.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
