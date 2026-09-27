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
    const errors = [], posts = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', req => {if (req.method() === 'POST') posts.push(new URL(req.url()).pathname);});
    page.setDefaultTimeout(15000);
    const labURL = urls.eu + '/tmbox-lab/';
    const state = async () => (await page.request.get(labURL + 'api/state')).json();
    const select = (station, connection) => page.locator(`#placement-fields select[data-station="${station}"][data-connection="${connection}"]`);
    const key = (station, value) => page.locator(`[data-device="DEMO-${station}"] [data-key="${value}"]`);
    const lcd = station => page.locator(`[data-device="DEMO-${station}"] .lcd`);
    const save = () => page.locator('#placement-form button[type=submit]').click();
    const open = () => page.locator('#placement-open').click();
    const screenshot = async suffix => {
      if (suffix.startsWith('active-')) {
        await page.waitForFunction(() => !document.querySelector('[data-device="DEMO-CDA"] [data-key="D"]').disabled);
      }
      if (process.env.SERVER_SHELL_SCREENSHOTS) await page.screenshot({path: path.join(process.env.SERVER_SHELL_SCREENSHOTS, `placement-${suffix}.png`)});
    };
    const admin = await browser.newContext();
    assert.equal((await admin.request.post(urls.eu + '/v1/auth/login', {data: {username: 'smoke-admin', password: 'isolated-browser-test'}})).status(), 200);
    const presentation = async () => (await admin.request.get(urls.eu + '/v1/cloud/presentation')).json();
    const realBefore = await presentation();
    await page.goto(labURL);
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
    await key('MUN', '#').click(); await key('MUN', '#').click();
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('93?MUN'));
    const before = await state();
    const beforeTops = await page.locator('.tmbox-case').evaluateAll(elements => elements.map(e => e.getBoundingClientRect().top));
    assert.ok(Math.max(...beforeTops) - Math.min(...beforeTops) < 1, 'Cases remain aligned');
    await open(); await select('cda', 'west').selectOption('default'); await save();
    await page.locator('#placement-dialog').waitFor({state: 'hidden'});
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('MUN?93'));
    assert.deepEqual((await state()).audit, before.audit, 'Placement never mutates traffic');
    assert.deepEqual(await presentation(), realBefore, 'Lab never changes real Server placement');
    await key('CDA', '#').click(); await key('MUN', '#').click(); await key('CDA', '#').click();
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-MUN"] .lcd').textContent.includes('MOTTAGET'));
    await page.locator('#reset-all').click();
    await page.getByText('Alla enheter är nollställda. Inga pågående tågrörelser.', {exact: true}).waitFor();
    const mun = (await state()).placement.stations.find(s => s.station_id === 'mun');
    assert.equal(mun.connections[0].side, 'left', 'Clear traffic keeps test placement');
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
      await key('CDA', '#').click(); await key('CDA', '#').click();
      await page.waitForFunction(s => document.querySelector(`[data-device="DEMO-${s}"] .lcd`).textContent.includes('?'), receiver);
      await key(receiver, '#').click();
    }
    await key('CDA', 'B').click();
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('B:Akt2'));
    assert.match(await lcd('CDA').textContent(), /MUN<17\s+39>VA/);
    await screenshot('active-overview');
    const activeAudit = (await state()).audit;
    await key('CDA', 'B').click();
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('1/2'));
    assert.match(await lcd('CDA').textContent(), /MUN<17/);
    await key('CDA', 'D').click();
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('2/2'));
    assert.match(await lcd('CDA').textContent(), /39>VA/);
    await key('CDA', 'C').click();
    assert.deepEqual((await state()).audit, activeAudit, 'B/C/D does not alter traffic');
    await screenshot('active-selected');
    await page.setViewportSize({width: 390, height: 844});
    await key('CDA', 'B').scrollIntoViewIfNeeded();
    await screenshot('active-mobile');
    await key('CDA', '#').click();
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('MUN◀17'));
    assert.equal(await key('CDA', '#').isDisabled(), true, 'No automatic next departure on doublepress');
    await key('CDA', 'D').click();
    await key('CDA', '#').click();
    await page.waitForFunction(() => document.querySelector('[data-device="DEMO-CDA"] .lcd').textContent.includes('39▶VA'));

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
    assert.deepEqual(errors, []);
    console.log('TMBox browser tests passed: placement, local digits, isolation, real-client frames, two active departures, B/C/D navigation, counters and disabled duplicate departure, desktop/mobile.');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
    if (fixture.exitCode === null) await new Promise(resolve => fixture.once('exit', resolve));
    if (diagnostics) console.error(diagnostics);
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
