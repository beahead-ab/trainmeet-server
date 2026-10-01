// Public entry points against disposable real HTTP/SQLite backends, never a LAN meet.
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'server-shell-live-fixture.py')], {
    cwd: root, env: {...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter)},
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let diagnostics = '', browser, page;
  fixture.stderr.on('data', value => diagnostics += value);
  try {
    const urls = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Test server did not start')), 15000);
      readline.createInterface({input: fixture.stdout}).once('line', line => {clearTimeout(timer); resolve(JSON.parse(line));});
      fixture.once('exit', () => {clearTimeout(timer); reject(new Error(diagnostics));});
    });
    browser = await chromium.launch({headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {})});
    const visitor = await browser.newContext({locale: 'sv-SE', viewport: {width: 390, height: 844}});
    page = await visitor.newPage();
    const errors = [], posts = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {requests.push(new URL(request.url()).pathname); if (request.method() === 'POST') posts.push(new URL(request.url()).pathname);});
    page.setDefaultTimeout(15000);
    const screenshot = async name => {
      if (process.env.SERVER_SHELL_SCREENSHOTS) await page.screenshot({path: path.join(process.env.SERVER_SHELL_SCREENSHOTS, 'public-' + name + '.png'), fullPage: true});
    };
    // A guest at "/" sees the participant view, never a login wall or a picker.
    await page.goto(urls.eu);
    await page.locator('#participant-view').waitFor({state: 'visible'});
    assert.equal(await page.locator('#login').isVisible(), false);
    assert.equal(await page.locator('#workspace-options').count(), 0);
    assert.equal((await page.request.get(urls.eu + '/v1/devices')).status(), 401);
    await screenshot('participant');
    const admin = await browser.newContext({locale:'sv-SE'});
    assert.equal((await admin.request.post(urls.eu+'/v1/auth/login',{data:{username:'smoke-admin',password:'isolated-browser-test'}})).status(),200);
    // The participant view shows the typed Wi-Fi as text, never as a QR: the
    // reader is already on the network and a TMBox cannot scan. The Wi-Fi QR,
    // with the password in it, belongs on the screens.
    const wifi = {screens: ['clock'], wifi_name: 'Träff; ÅÄÖ', wifi_password: 'test-only:secret'};
    assert.equal((await admin.request.post(urls.eu+'/v1/display/connection',{data:wifi})).status(),200);
    await page.reload();
    await page.locator('#pv-wifi').getByText(wifi.wifi_name, {exact:true}).waitFor();
    await page.locator('#pv-wifi').getByText(wifi.wifi_password, {exact:true}).waitFor();
    assert.match(await page.locator('#pv-wifi-note').innerText(), /som boxen frågar efter/);
    assert.equal(await page.locator('#pv-connect-card svg').count(), 0);
    const clockPage = await visitor.newPage();
    await clockPage.setViewportSize({width:1920,height:1080});
    await clockPage.goto(urls.eu+'/display/clock');
    await clockPage.locator('#screen-qr svg').first().waitFor();
    assert.equal(await clockPage.locator('#screen-qr svg').count(), 2);
    const signature = await clockPage.locator('#screen-qr').getAttribute('data-signature');
    assert.ok(signature.endsWith('|'+urls.eu+'/'), 'QR keeps the actual scheme, host and port');
    assert.ok(signature.startsWith('WIFI:T:WPA;S:Träff\\; ÅÄÖ;P:test-only\\:secret;;|'), 'The Wi-Fi QR carries the password, escaped');
    assert.ok(await clockPage.locator('.sc-stopped').evaluate(stopped => {
      const a = stopped.getBoundingClientRect(), b = document.querySelector('#screen-qr').getBoundingClientRect();
      return a.right <= b.left || a.bottom <= b.top || a.left >= b.right || a.top >= b.bottom;
    }), 'Two QR codes do not overlap the stopped-clock message');
    await clockPage.locator('#display-clock-style').selectOption('digital');
    assert.equal((await (await page.request.get(urls.eu+'/v1/display')).json()).clock.style, 'swiss', 'A screen preference never changes the shared clock');
    await clockPage.close();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Participant page fits a phone');
    await page.locator('#pv-topology .topology-station').first().click();
    assert.equal(await page.locator('#pv-clear-station').isVisible(), true);
    await page.locator('#pv-clear-station').click();
    assert.equal(await page.locator('#pv-clear-station').isVisible(), false);
    // Phone widths show the login link in the foot, wider ones in the top row.
    await page.locator('#pv-login:visible, #pv-foot-login:visible').first().click();
    await page.locator('#login-form').waitFor({state: 'visible'});
    await page.locator('#login a[href="/"]').click();
    await page.locator('#participant-view').waitFor({state: 'visible'});
    // Guests find the test bench next to the virtual box, and the two pages
    // link to each other.
    // The card appears once the participant view has loaded its data:
    // wait for it rather than asking straight after the navigation.
    await page.locator('#pv-virtual-card a[href="/tmbox-lab/"]').waitFor();
    await page.locator('#pv-virtual-card a[href="/tmbox/"]').click();
    await page.locator('.box-code').getByText(/^WEB/).waitFor();
    assert.equal(await page.locator('.tm-top a[href="/tmbox-lab/"]').textContent(), 'Provbänk');
    await page.waitForFunction(() => document.querySelector('#connection-rate').textContent === ' · uppdateras 2 gånger i sekunden');
    const box = await page.evaluate(() => JSON.parse(localStorage.getItem('trainmeet.browser-tmbox')));
    assert.equal(await page.locator('input, select').count(), 0, 'No station or address controls');
    assert.equal(await page.locator('.keypad button').count(), 16);
    // As on the physical box, every key can be pressed; without a station
    // none of them does anything.
    assert.equal(await page.locator('.keypad button:disabled').count(), 0);
    const unassigned = posts.length;
    for (const value of ['#', '1', 'A', 'D']) await page.locator(`.keypad [data-key="${value}"]`).click();
    await page.waitForTimeout(300);
    assert.equal(posts.length, unassigned, 'An unassigned box sends nothing');
    assert.equal((await page.request.get(urls.eu + '/v1/devices')).status(), 401);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await screenshot('box-awaiting-admin');
    await page.reload();
    await page.locator('.box-code').getByText(box.device_code, {exact:true}).waitFor();
    assert.equal(posts.filter(path=>path==='/v1/browser-clients').length, 1);
    assert.equal((await admin.request.post(urls.eu+'/v1/devices/assign',{data:{device_code:box.device_code,station_id:'station-a'}})).status(),200);
    await page.locator('.box h2').getByText('Charlottendahl',{exact:true}).waitFor();
    await screenshot('box-assigned');
    // A polling browser box is online in the admin's client list.
    const listed=(await (await admin.request.get(urls.eu+'/v1/devices')).json()).devices.find(d=>d.device_id===box.client_id);
    assert.deepEqual(listed.connection,{state:'online',last_seen:null});
    const beforeDigits = posts.filter(path=>path==='/v1/tmbox/terminal').length;
    await page.locator('.keypad [data-key="1"]').click();
    await page.locator('.keypad [data-key="2"]').click();
    assert.equal(posts.filter(path=>path==='/v1/tmbox/terminal').length,beforeDigits);
    assert.match(await page.locator('.lcd').textContent(),/12___/);
    assert.equal((await admin.request.post(urls.eu+'/v1/devices/language',{data:{device_id:box.client_id,language:'de'}})).status(),200);
    await page.waitForFunction(()=>document.querySelector('.lcd').textContent.includes('ZUG:'));
    assert.match(await page.locator('.lcd').textContent(),/12___/,'Admin language preserves unsent digits');
    await page.locator('.keypad [data-key="*"]').click();
    assert.equal(posts.filter(path=>path==='/v1/tmbox/terminal').length,beforeDigits);
    // No language menu on the box: * on the start screen does nothing, and
    // only the administrator changes the language.
    await page.waitForFunction(()=>document.querySelector('.lcd').textContent.includes('Nr# A:Q'));
    await page.locator('.keypad [data-key="*"]').click();
    await page.waitForTimeout(300);
    assert.equal(posts.filter(path=>path==='/v1/tmbox/terminal').length,beforeDigits,'* sends nothing on the start screen');
    assert.equal((await admin.request.post(urls.eu+'/v1/devices/language',{data:{device_id:box.client_id,language:'sv'}})).status(),200);
    await page.waitForFunction(()=>document.querySelector('.lcd').textContent.includes('Nr# A:Kö'));
    assert.equal((await page.request.get(urls.eu+'/v1/admin/users',{headers:{Authorization:`Bearer ${box.access_token}`}})).status(),403);
    // Traffic with two virtual boxes, then Drift: clicking the train in
    // "Kommande enligt tidtabell" shows its route and that it is on the line.
    const lekContext = await browser.newContext({locale:'sv-SE'});
    const lek = await lekContext.newPage();
    lek.setDefaultTimeout(15000);
    await lek.goto(urls.eu + '/tmbox/');
    await lek.locator('.box-code').getByText(/^WEB/).waitFor();
    const lekBox = await lek.evaluate(() => JSON.parse(localStorage.getItem('trainmeet.browser-tmbox')));
    assert.equal((await admin.request.post(urls.eu+'/v1/devices/assign',{data:{device_code:lekBox.device_code,station_id:'station-b'}})).status(),200);
    await lek.locator('.box h2').getByText('Lekeberg',{exact:true}).waitFor();
    const lcd = (target, text) => target.waitForFunction(value => document.querySelector('.lcd').textContent.includes(value), text);
    const press = async (target, key) => { await target.waitForTimeout(600); await target.locator(`.keypad [data-key="${key}"]`).click(); };
    for (const key of ['1', '0', '1', '#']) await page.locator(`.keypad [data-key="${key}"]`).click();
    await lcd(page, '#Beg');
    await press(page, '#');
    await lcd(lek, '#Ja *Nej');
    await press(lek, '#');
    await lcd(page, '#Avg');
    // Drift's map, from the real traffic: LEK's clear is an outlined tag on
    // CDA–LEK, nearer CDA, its triangle towards LEK; departed, it is filled.
    const drift = await admin.newPage();
    await drift.goto(urls.eu + '/drift');
    const mapTag = state => drift.locator(`#overview-topology .topology-train.${state}[data-train-number="101"]`);
    await mapTag('cleared').waitFor();
    const geometry = await drift.evaluate(() => {
      const centre = element => { const r = element.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; };
      const station = name => centre(document.querySelector(`#overview-topology .topology-node[aria-label^="${name}"] .topology-station`));
      const train = document.querySelector('#overview-topology .topology-train[data-train-number="101"]');
      return { cda: station('Charlottendahl'), lek: station('Lekeberg'), tag: centre(train.querySelector('.topology-train-tag')),
        arrow: centre(train.querySelector('.topology-train-arrow')), number: centre(train.querySelector('.train-number')) };
    });
    const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    assert.ok(distance(geometry.tag, geometry.cda) < distance(geometry.tag, geometry.lek), 'nearer the station it leaves');
    assert.ok((geometry.arrow.x - geometry.number.x) * (geometry.lek.x - geometry.cda.x) > 0, 'the triangle leads towards LEK');
    assert.equal(await mapTag('cleared').getAttribute('aria-label'), 'Tåg 101 · CDA → LEK · klart, inte avgått');
    // Everything updates at once (1.21.0). With the stream up, Drift, the TV
    // and the participant view hear the departure from the server and fetch
    // again at once; their timers are only a slow fallback.
    const tv = await browser.newPage({viewport: {width: 1920, height: 1080}});
    await tv.goto(urls.eu + '/display/topology');
    const guest = await (await browser.newContext({locale: 'sv-SE'})).newPage();
    await guest.goto(urls.eu + '/');
    await guest.locator('#pv-topology .topology-train.cleared[data-train-number="101"]').waitFor();
    for (const viewer of [drift, tv, guest]) await viewer.waitForFunction(() => globalThis.TrainMeetLive?.connected === true);
    await tv.locator('#topology-svg .topology-train.cleared[data-train-number="101"]').waitFor();
    await drift.waitForTimeout(1500);  // let the first fetches settle
    const fetched = new Map([[drift, []], [tv, []], [guest, []]]);
    const watchStart = Date.now();
    for (const [viewer, list] of fetched) viewer.on('request', request => {
      const where = new URL(request.url()).pathname; if (where.startsWith('/v1/')) list.push(where);
      if (process.env.DEBUG_LIVE) console.log([drift, tv, guest].indexOf(viewer), Date.now() - watchStart, where, request.headers()['referer'] || '');
    });
    await drift.waitForTimeout(11000);
    // Drift asks nothing on its own; only the simulation banner looks every ten seconds.
    assert.deepEqual(fetched.get(drift).filter(where => where !== '/v1/display'), [], 'with the stream up, Drift does not poll every five seconds');
    assert.ok(fetched.get(drift).length <= 2, fetched.get(drift).join(' '));
    // In eleven seconds: the TV's own five-second fetch and the banner's ten
    // (not one and two); the guest only the banner's (not every five seconds).
    assert.ok(fetched.get(tv).length <= 5, fetched.get(tv).join(' '));
    assert.ok(fetched.get(guest).length <= 2, fetched.get(guest).join(' '));
    await drift.waitForTimeout(600);
    await page.locator('.keypad [data-key="#"]').click();  // LEK departs
    const departed = Date.now();
    const seen = async (viewer, selector) => { await viewer.locator(selector).waitFor({timeout: 5000}); return Date.now() - departed; };
    const delays = await Promise.all([
      seen(drift, '#overview-topology .topology-train.on-line[data-train-number="101"]'),
      seen(tv, '#topology-svg.topology-tv .topology-train.on-line[data-train-number="101"] .topology-train-arrow'),
      seen(guest, '#pv-topology .topology-train.on-line[data-train-number="101"]'),
    ]);
    for (const delay of delays) assert.ok(delay < 1500, `seen after ${delays.join(' / ')} ms`);
    console.log(`Departure seen in Drift, on the TV and by a guest after ${delays.join(' / ')} ms`);
    assert.equal(await mapTag('on-line').getAttribute('aria-label'), 'Tåg 101 · CDA → LEK · på linjen');
    // A box given a side, and the clock stopped and started elsewhere, show at once too.
    const within = async (selector, action) => {
      const started = Date.now(); await action();
      await drift.locator(selector).first().waitFor({state: 'visible', timeout: 5000});
      assert.ok(Date.now() - started < 1500, `${selector} after ${Date.now() - started} ms`);
    };
    await within('#device-list .status-row span:text("· höger")', () =>
      admin.request.post(urls.eu + '/v1/devices/assign', {data: {device_code: lekBox.device_code, station_id: 'station-b', side: 'right'}}));
    await within('#overview-clock-start', () => admin.request.post(urls.eu + '/v1/clock', {data: {action: 'stop'}}));
    await within('#stop-local-clock', () => admin.request.post(urls.eu + '/v1/clock', {data: {action: 'start'}}));
    await guest.context().close();
    // The same tag on the TV's map, in its size.
    // It rides on the line, clear of the large names under it.
    const ride = await tv.evaluate(() => {
      const tag = document.querySelector('#topology-svg .topology-train[data-train-number="101"] .topology-train-tag').getBoundingClientRect();
      const node = document.querySelector('#topology-svg .topology-node[aria-label^="Charlottendahl"] .topology-station').getBoundingClientRect();
      return { top: tag.y, bottom: tag.y + tag.height, middle: tag.y + tag.height / 2, line: node.y + node.height / 2 };
    });
    assert.ok(ride.top < ride.line && ride.line < ride.bottom && ride.middle < ride.line - 5, JSON.stringify(ride));
    await tv.close();
    await drift.locator('#drift-upcoming button.server-event[data-train-number="101"]').first().click();
    const trainPanel = drift.locator('#drift-train-detail');
    await trainPanel.locator('.train-detail-now').getByText('Nu: På linjen CDA → LEK', {exact: false}).waitFor();
    assert.deepEqual(await trainPanel.locator('.route-stop b').allTextContents(), ['CDA · Charlottendahl', 'LEK · Lekeberg']);
    assert.equal(await trainPanel.locator('.train-detail-between').count(), 1);
    assert.ok(await drift.locator('#overview-topology .route-highlight').count() > 0, 'Banöversikten marks the train');
    assert.ok(await drift.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await drift.close();
    await lekContext.close();
    await page.getByRole('link',{name:'Träffens sida',exact:true}).click();
    await page.locator('#participant-view').waitFor({state:'visible'});
    // TKL is a separately hosted application. Its backend contract stays,
    // but bundled pages and assets must not survive a Server release.
    assert.equal(await page.getByRole('link', {name: 'TKL', exact: true}).count(), 0);
    for (const path of ['/tkl', '/tkl/', '/tkl/index.html']) assert.equal((await page.request.get(urls.eu + path)).status(), 404);
    assert.equal(posts.filter(path => path === '/v1/auth/login').length, 0, 'TMBox never uses administrator login');
    assert.equal((await visitor.cookies()).length, 0, 'Guests receive no administrator cookies');
    await page.locator('#pv-virtual-card a[href="/tmbox/"]').click();
    await page.locator('.box h2').getByText('Charlottendahl',{exact:true}).waitFor();
    await admin.request.post(urls.eu + '/v1/devices/remove', {data: {device_id: box.client_id}});
    await page.waitForFunction(()=>document.querySelector('#connection').textContent.includes('inte ansluten'));
    assert.equal(posts.filter(path => path === '/v1/browser-clients').length, 1, 'Revoked box does not recreate itself automatically');
    const revoked = posts.length;
    for (const value of ['#', '1', '*']) await page.locator(`.keypad [data-key="${value}"]`).click();
    await page.waitForTimeout(300);
    assert.equal(posts.length, revoked, 'A revoked box sends nothing');
    assert.deepEqual(errors, []);
    console.log('Participant tests passed: guest page, protected admin, TMBox assignment/revocation, local input and retired TKL pages.');
  } catch (error) {
    console.error(diagnostics);
    if (page) {
      console.error('Page:', page.url(), await page.locator('body').innerText());
      if (process.env.SERVER_SHELL_SCREENSHOTS) await page.screenshot({path: path.join(process.env.SERVER_SHELL_SCREENSHOTS, 'public-failure.png'), fullPage: true});
    }
    throw error;
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
    if (fixture.exitCode === null) await new Promise(resolve => fixture.once('exit', resolve));
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
