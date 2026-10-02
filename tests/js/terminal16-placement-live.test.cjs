// Real HTTP + disposable SQLite; never touches Cloud, a LAN meet or hardware.
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
  fixture.stderr.on('data', data => diagnostics += data);
  try {
    const urls = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('Fixture timeout')), 15000);
      readline.createInterface({input: fixture.stdout}).once('line', line => {clearTimeout(timer); resolve(JSON.parse(line));});
      fixture.once('exit', () => {clearTimeout(timer); reject(Error(diagnostics));});
    });
    browser = await chromium.launch({headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel: process.env.PLAYWRIGHT_CHANNEL} : {})});
    const context = await browser.newContext({viewport: {width: 1280, height: 1000}});
    page = await context.newPage();
    // Lets the test break the test bench's event stream the way a network
    // drop does (the browser then reconnects by itself), or close it for
    // good. Everything else is the real EventSource.
    await page.addInitScript(() => {
      const Real = window.EventSource;
      window.EventSource = class extends Real {
        constructor(...args) { super(...args); window.labStream = this; }
        get readyState() { return this.simulated ?? super.readyState; }
      };
    });
    const errors = [], posts = [];
    page.on('pageerror', error => errors.push(error.message));
    let inflight = 0;
    page.on('request', req => {if (req.method() === 'POST') {posts.push(new URL(req.url()).pathname); inflight++;}});
    for (const event of ['requestfinished', 'requestfailed']) page.on(event, req => {if (req.method() === 'POST') inflight--;});
    page.setDefaultTimeout(15000);
    const labURL = urls.eu + '/tmbox-lab/';
    const state = async () => (await page.request.get(labURL + 'api/state')).json();
    const select = (station, connection) => page.locator(`#placement-fields select[data-station="${station}"][data-connection="${connection}"]`);
    const key = (station, value) => page.locator(`[data-device="DEMO-${station}"] [data-key="${value}"]`);
    // Like the physical box, no key is ever disabled: a press that cannot do
    // anything does nothing. So the test presses like a person - reads the
    // new screen first - instead of leaning on a button waiting to be enabled.
    const settle = async () => { while (inflight) await page.waitForTimeout(20); await page.waitForTimeout(600); };
    const press = async (station, value) => { await settle(); await key(station, value).click(); };
    const noneDisabled = async () => assert.equal(await page.locator('.keypad button:disabled').count(), 0, 'No key is ever disabled');
    const lcd = station => page.locator(`[data-device="DEMO-${station}"] .lcd`);
    const save = () => page.locator('#placement-form button[type=submit]').click();
    const open = () => page.locator('#placement-open').click();
    const screenshot = async suffix => {
      if (suffix.startsWith('active-')) await settle();
      if (process.env.SERVER_SHELL_SCREENSHOTS) await page.screenshot({path: path.join(process.env.SERVER_SHELL_SCREENSHOTS, `placement-${suffix}.png`)});
    };
    const admin = await browser.newContext();
    assert.equal((await admin.request.post(urls.eu + '/v1/auth/login', {data: {username: 'smoke-admin', password: 'isolated-browser-test'}})).status(), 200);
    const presentation = async () => (await admin.request.get(urls.eu + '/v1/cloud/presentation')).json();
    const realBefore = await presentation();
    await page.goto(labURL);
    // Same page frame as /tmbox/: the way back is the meet's page, not the
    // administrators' /drift, and the footer names the server version.
    assert.equal(await page.locator('.tm-top a[href="/drift"]').count(), 0);
    assert.equal(await page.locator('.tm-top a[href="/tmbox/"]').textContent(), 'Virtuell TMBox');
    assert.equal(await page.locator('.tm-top a[href="/"]').textContent(), 'Träffens sida');
    await page.waitForFunction(() => /^TrainMeet Server \d+\.\d+\.\d+/.test(document.querySelector('#server-version').textContent));
    assert.equal(await page.locator('#key-help').evaluate(d => d.tagName === 'DETAILS' && d.open), true, 'Key help open on a desktop');
    await page.locator('.box').nth(2).waitFor();
    await open();
    await select('cda', 'west').selectOption('right');
    await page.locator('#placement-cancel').click();
    assert.equal((await state()).placement.revision, 0);
    assert.equal(posts.length, 0, 'Cancel does not send placement or traffic');
    await key('MUN', '9').click(); await key('MUN', '3').click();
    assert.equal(posts.length, 0, 'Digits remain local');
    await open();
    await select('cda', 'west').selectOption('right');
    await select('mun', 'west').selectOption('left');
    await screenshot('desktop');
    await save();
    await page.locator('#placement-dialog').waitFor({state: 'hidden'});
    assert.match(await lcd('MUN').textContent(), /93___/, 'Placement preserves unsent digits');
    assert.equal(posts.filter(p => p.endsWith('/api/key')).length, 0);
    await press('MUN', '#'); await press('MUN', '#');
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('93?MUN'));
    await noneDisabled();
    const before = await state();
    const beforeTops = await page.locator('.tmbox-case').evaluateAll(elements => elements.map(e => e.getBoundingClientRect().top));
    assert.ok(Math.max(...beforeTops) - Math.min(...beforeTops) < 1, 'Cases remain aligned');
    await open(); await select('cda', 'west').selectOption('default'); await save();
    await page.locator('#placement-dialog').waitFor({state: 'hidden'});
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('MUN?93'));
    assert.deepEqual((await state()).audit, before.audit, 'Placement never mutates traffic');
    assert.deepEqual(await presentation(), realBefore, 'Lab never changes real Server placement');
    await press('CDA', '#'); await press('MUN', '#'); await press('CDA', '#');
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-MUN"] .lcd').textContent.includes('MOTTAGET'));
    await page.locator('#reset-all').click();
    await page.getByText('Alla enheter är nollställda. Inga pågående tågrörelser.', {exact: true}).waitFor();
    const mun = (await state()).placement.stations.find(s => s.station_id === 'mun');
    assert.equal(mun.connections[0].side, 'left', 'Clear traffic keeps test placement');
    await page.setViewportSize({width: 390, height: 844});
    for (const width of [390, 320]) {
      await page.setViewportSize({width, height: 844});
      const fit = await page.evaluate(() => ({name: document.querySelector('.tm-top__name').getBoundingClientRect().width,
        links: [...document.querySelectorAll('.tm-top a')].map(a => a.getBoundingClientRect().right), inner: innerWidth}));
      assert.ok(fit.name > 40 && fit.links.every(right => right <= fit.inner), `Header fits ${width} px: ${JSON.stringify(fit)}`);
    }
    await page.setViewportSize({width: 390, height: 844});
    await open();
    await screenshot('mobile');
    assert.ok(await page.locator('#placement-dialog').evaluate(d => d.getBoundingClientRect().left >= 0 && d.getBoundingClientRect().right <= innerWidth && d.scrollWidth <= d.clientWidth + 1));
    await page.locator('#placement-default').click();
    await page.locator('#placement-close').click();
    assert.equal((await state()).placement.stations.find(s => s.station_id === 'mun').connections[0].side, 'left', 'Reset default remains a draft until saved');
    await open();
    // A reset from a second tab invalidates the open draft, not the real meet.
    const response = await page.request.post(labURL + 'api/reset-devices', {headers: {Origin: urls.eu}, data: {}});
    assert.equal(response.status(), 200);
    await save();
    await page.locator('#placement-error').getByText('Provbänken ändrades. Stäng och öppna placeringen igen.', {exact: true}).waitFor();
    await page.keyboard.press('Escape');
    await open(); await page.locator('#placement-default').click(); await save();
    await page.locator('#placement-dialog').waitFor({state: 'hidden'});
    assert.equal((await state()).placement.stations.find(s => s.station_id === 'mun').connections[0].side, 'right');

    // Two approvals must remain accessible from overview, without typing again.
    await page.setViewportSize({width: 1600, height: 1100});
    for (const [number, receiver] of [['17', 'MUN'], ['39', 'VA']]) {
      for (const digit of number) await key('CDA', digit).click();
      await press('CDA', '#'); await press('CDA', '#');
      await page.waitForFunction(s => document.querySelector(`[data-device="DEMO-${s}"] .lcd`).textContent.includes('?'), receiver);
      await press(receiver, '#');
    }
    await press('CDA', 'B');
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('B:Akt2'));
    assert.match(await lcd('CDA').textContent(), /MUN<17\s+39>VA/);
    await screenshot('active-overview');
    const activeAudit = (await state()).audit;
    await press('CDA', 'B');
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('1/2'));
    assert.match(await lcd('CDA').textContent(), /MUN<17/);
    // Browsing answers as soon as the server has: D straight after D, with no
    // half-second wait after each screen change. This was most of what felt slow.
    await settle();
    const browsing = posts.length;
    await key('CDA', 'D').click();
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('2/2'));
    await key('CDA', 'D').click();
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('1/2'));
    await key('CDA', 'D').click();
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('2/2'));
    assert.equal(posts.length, browsing + 3, 'Every browse press was sent');
    assert.match(await lcd('CDA').textContent(), /39>VA/);
    await press('CDA', 'C');
    assert.deepEqual((await state()).audit, activeAudit, 'B/C/D does not alter traffic');
    await screenshot('active-selected');
    await page.setViewportSize({width: 390, height: 844});
    await key('CDA', 'B').scrollIntoViewIfNeeded();
    await screenshot('active-mobile');
    // A departure offered on a new screen waits; pressed at once it does nothing.
    // Pressed from inside the page the moment the new screen is drawn, so the
    // check does not depend on how fast the test itself gets there.
    await settle();
    await key('CDA', 'D').click();
    const guardedPosts = posts.length;
    await page.evaluate(() => new Promise(resolve => {
      const box = document.querySelector('[data-device="DEMO-CDA"]');
      const check = () => box.querySelector('.lcd').textContent.includes('2/2')
        ? (box.querySelector('[data-key="#"]').click(), resolve()) : requestAnimationFrame(check);
      check();
    }));
    await page.waitForTimeout(150);
    assert.equal(posts.length, guardedPosts, 'An acting key right after a screen change is not sent');
    await press('CDA', 'C');
    await press('CDA', '#');
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('MUN◀17'));
    // The departed train stays selected without '#'. Pressing it anyway, even
    // twice and after the guard, sends nothing - no automatic next departure.
    await settle();
    const departed = posts.length, departedScreen = await lcd('CDA').textContent();
    await key('CDA', '#').click(); await key('CDA', '#').click();
    await page.waitForTimeout(300);
    assert.equal(posts.length, departed, 'No automatic next departure on doublepress');
    assert.equal(await lcd('CDA').textContent(), departedScreen);
    await noneDisabled();
    await press('CDA', 'D');
    await press('CDA', '#');
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('39▶VA'));

    // A person flicking through trains as fast as anyone does - seven presses
    // a second - is never refused. The old limit (30 per 10 s) refused the 31st.
    await settle();
    const refused = [];
    const onResponse = response => { if (response.status() === 429) refused.push(response.url()); };
    page.on('response', onResponse);
    const flicked = posts.length;
    await page.evaluate(() => new Promise(resolve => {
      const key = document.querySelector('[data-device="DEMO-CDA"] [data-key="D"]');
      let count = 0;
      const timer = setInterval(() => { key.click(); if (++count === 42) { clearInterval(timer); resolve(); } }, 1000 / 7);
    }));
    await settle();
    page.off('response', onResponse);
    assert.deepEqual(refused, [], 'No press refused');
    assert.ok(posts.length - flicked >= 40, `${posts.length - flicked} of 42 presses sent`);

    // A dropped stream is not a lost server until 15 s have passed, as on the
    // box; a stream the server closed for good is lost at once.
    const labConnection = () => page.locator('#connection').textContent();
    const ready = await labConnection();
    const droppedAt = Date.now();
    await page.evaluate(() => { labStream.simulated = 0; labStream.close(); labStream.onerror(new Event('error')); });
    await page.waitForTimeout(5000);
    assert.equal(await labConnection(), ready, 'Five seconds without the stream are not a lost server');
    await page.waitForFunction(text => document.querySelector('#connection').textContent !== text, ready, {timeout: 20000});
    const droppedFor = Date.now() - droppedAt;
    assert.ok(droppedFor >= 14500 && droppedFor < 18000, `Lost after ${droppedFor} ms`);
    await page.reload();
    await page.waitForFunction(text => document.querySelector('#connection').textContent === text, ready);
    await page.evaluate(() => { labStream.simulated = 2; labStream.close(); labStream.onerror(new Event('error')); });
    assert.notEqual(await labConnection(), ready, 'A closed stream is lost at once');
    await noneDisabled();

    // The operational browser client has no simulator configuration controls.
    await page.goto(urls.eu + '/tmbox/');
    await page.locator('.box-code').getByText(/^WEB/).waitFor();
    assert.equal(await page.locator('#placement-dialog, #placement-open, select').count(), 0);
    const identity = await page.evaluate(() => JSON.parse(localStorage.getItem('trainmeet.browser-tmbox')));
    await admin.request.post(urls.eu + '/v1/devices/assign', {data: {device_code: identity.device_code, station_id: 'station-a'}});
    await page.locator('.box h2').getByText('Charlottendahl', {exact: true}).waitFor();
    await page.locator('.keypad [data-key="1"]').click(); await page.locator('.keypad [data-key="2"]').click();
    const current = await presentation();
    const auth = {Authorization: `Bearer ${identity.access_token}`};
    const frameBefore = await (await page.request.get(urls.eu + '/v1/tmbox/terminal', {headers: auth})).json();
    const station = current.stations.find(s => s.station_id === 'station-a'), connection = station.connections[0];
    assert.equal((await admin.request.post(urls.eu + '/v1/cloud/display-placement', {data: {
      publication_id: current.publication_id, config_version: current.config_version, station_id: 'station-a',
      sides: {[connection.connection_id]: connection.side === 'left' ? 'right' : 'left'},
    }})).status(), 200);
    await page.waitForTimeout(1200);
    assert.match(await page.locator('.lcd').textContent(), /12___/, 'Admin placement preserves live unsent digits');
    const frameAfter = await (await page.request.get(urls.eu + '/v1/tmbox/terminal', {headers: auth})).json();
    assert.equal(frameAfter.entry.context, frameBefore.entry.context);
    assert.ok(frameAfter.view_revision > frameBefore.view_revision);

    // A slow answer and no answer look as on the box: the second row says so
    // after 1.5 s, and a command that fails keeps the digits for another try.
    const row2 = async () => (await page.locator('.lcd .lcd-row').nth(1).textContent());
    await page.route('**/v1/tmbox/terminal', async route => {
      if (route.request().method() !== 'POST') return route.fallback();
      await new Promise(resolve => setTimeout(resolve, 2500)); await route.fallback();
    });
    await page.locator('.keypad [data-key="#"]').click();
    await page.waitForTimeout(800);
    assert.doesNotMatch(await row2(), /VANTAR PA SVAR/, 'A quick answer shows nothing');
    await page.waitForFunction(() => document.querySelectorAll('.lcd .lcd-row')[1].textContent.startsWith('VANTAR PA SVAR'));
    await page.waitForFunction(() => !document.querySelectorAll('.lcd .lcd-row')[1].textContent.startsWith('VANTAR PA SVAR'));
    await page.unrouteAll({behavior: 'wait'});
    await settle();
    await page.locator('.keypad [data-key="*"]').click();  // stänger "INGET TÅG"
    await settle();
    await page.locator('.keypad [data-key="1"]').click(); await page.locator('.keypad [data-key="2"]').click();
    await page.route('**/v1/tmbox/terminal', route => route.request().method() === 'POST' ? route.abort() : route.fallback());
    await page.locator('.keypad [data-key="#"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.lcd .lcd-row')[1].textContent.startsWith('INGET SVAR'));
    assert.match(await page.locator('.lcd').textContent(), /12___/, 'Digits stay after no answer');
    await page.waitForFunction(() => !document.querySelectorAll('.lcd .lcd-row')[1].textContent.startsWith('INGET SVAR'), null, {timeout: 5000});
    await page.unrouteAll({behavior: 'wait'});

    // Silence is not a lost server until it has lasted 15 s, as on the box.
    await page.route('**/v1/tmbox/terminal', route => route.request().method() === 'GET' ? route.abort() : route.fallback());
    const silentSince = Date.now();
    await page.waitForTimeout(5000);
    assert.equal(await page.locator('#connection').textContent(), 'Ansluten till servern', 'Five silent seconds are not a lost server');
    await page.waitForFunction(() => document.querySelector('#connection').textContent.includes('inte ansluten'), null, {timeout: 20000});
    const silentFor = Date.now() - silentSince;
    assert.ok(silentFor >= 14500 && silentFor < 18000, `Lost after ${silentFor} ms`);
    await page.unrouteAll({behavior: 'wait'});
    await page.waitForFunction(() => document.querySelector('#connection').textContent === 'Ansluten till servern');
    await noneDisabled();

    // The flows page: every picture from the 16x2 engine, drawn by the same
    // lcd.js as the boxes, readable on a desktop and on a phone.
    await page.goto(labURL);
    await page.locator('.tm-top a[href="./floden"]').click();
    await page.waitForURL(/\/tmbox-lab\/floden$/);
    for (const width of [1280, 390]) {
      await page.setViewportSize({width, height: 900});
      await page.locator('.flow').nth(11).waitFor();
      assert.equal(await page.locator('.flow').count(), 12);
      assert.equal(await page.locator('#flow-index a').count(), 12);
      const drawn = await page.evaluate(() => [...document.querySelectorAll('.flow .lcd')].map(lcd =>
        [...lcd.querySelectorAll('.lcd-row')].map(row => row.querySelectorAll('.lcd-cell').length)));
      assert.ok(drawn.length > 100, `${drawn.length} displays`);
      assert.ok(drawn.every(rows => rows.length === 2 && rows[0] === 16 && rows[1] === 16), 'Every display is 16 x 2');
      const shipped = await page.evaluate(() => TMBoxFlows.flows[0].steps[0].screens[0].lines.join(''));
      assert.equal(await page.locator('#flow-klarera .flow-step').nth(1).locator('.lcd').first().textContent(), shipped);
      assert.match(shipped, /^TÅG: 39___/);
      // What the step changed stands out; the other box is drawn dimmed.
      assert.deepEqual(await page.locator('#flow-klarera .flow-step').nth(1).locator('.flow-screen')
        .evaluateAll(figures => figures.map(f => f.classList.contains('is-changed'))), [true, false]);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `No sideways scroll at ${width} px`);
      const cell = await page.locator('#flow-klarera .lcd-cell').first().boundingBox();
      assert.ok(cell.width >= 10, `LCD cells readable at ${width} px (${cell.width})`);
      await screenshot(`flows-${width}`);
    }
    assert.deepEqual(errors, []);
    console.log('TMBox browser tests passed: placement, local digits, isolation, real-client frames, two active departures, B/C/D navigation, counters and disabled duplicate departure, desktop/mobile, flows page.');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
    if (fixture.exitCode === null) await new Promise(resolve => fixture.once('exit', resolve));
    if (diagnostics) console.error(diagnostics);
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
