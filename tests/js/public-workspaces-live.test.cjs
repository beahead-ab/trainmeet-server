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
    const visitor = await browser.newContext({viewport: {width: 390, height: 844}});
    page = await visitor.newPage();
    const errors = [], posts = [], requests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {requests.push(new URL(request.url()).pathname); if (request.method() === 'POST') posts.push(new URL(request.url()).pathname);});
    page.setDefaultTimeout(15000);
    const screenshot = async name => {
      if (process.env.SERVER_SHELL_SCREENSHOTS) await page.screenshot({path: path.join(process.env.SERVER_SHELL_SCREENSHOTS, 'public-' + name + '.png'), fullPage: true});
    };
    await page.goto(urls.eu);
    await page.locator('#workspace-picker').waitFor({state: 'visible'});
    assert.equal(await page.locator('#login').isVisible(), false);
    assert.equal(await page.locator('#workspace-options button').count(), 3);
    await screenshot('picker');
    await page.getByRole('button', {name: 'Drift och administration', exact: true}).click();
    await page.locator('#login-form').waitFor({state: 'visible'});
    await page.locator('#login a[href="#workspaces"]').click();
    await page.getByRole('button', {name: 'TMBox', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('#tmbox-v2-device').value.startsWith('WEB')).catch(async error => {
      console.error('Guest TMBox state:', page.url(), await page.locator('body').innerText(), errors);
      await screenshot('box-start-failure');
      throw error;
    });
    const box = await page.evaluate(() => JSON.parse(localStorage.getItem('trainmeet.browser-tmbox')));
    assert.equal(await page.locator('#login').isVisible(), false);
    assert.equal(await page.locator('#tmbox-v2-station').isDisabled(), true);
    assert.equal(await page.locator('#tmbox-v2-device').getAttribute('readonly'), '');
    assert.equal(await page.locator('#keypad-v2 button').count(), 16);
    assert.equal((await page.request.get(urls.eu + '/v1/devices')).status(), 401);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await screenshot('box-awaiting-admin');
    await page.reload();
    await page.waitForFunction(() => document.querySelector('#tmbox-v2-device').value.startsWith('WEB'));
    assert.equal(await page.locator('#tmbox-v2-device').inputValue(), box.device_code);
    assert.equal(posts.filter(path => path === '/v1/browser-clients').length, 1, 'Reload reuses the box identity');

    const admin = await browser.newContext();
    assert.equal((await admin.request.post(urls.eu + '/v1/auth/login', {data: {username: 'smoke-admin', password: 'isolated-browser-test'}})).status(), 200);
    assert.equal((await admin.request.post(urls.eu + '/v1/devices/assign', {data: {device_code: box.device_code, station_id: 'station-a'}})).status(), 200);
    await page.waitForFunction(() => document.querySelector('#tmbox-v2-station').value === 'station-a');
    await screenshot('box-assigned');
    await page.waitForFunction(() => tmboxV2.nav.view.screen === 'StationOverview' && !tmboxV2.nav.locked(v2Now()));
    const beforeDigits = posts.filter(path => path === '/v1/tmbox-v2/command').length;
    await page.locator('#keypad-v2 [data-key="1"]').click();
    await page.waitForFunction(() => !tmboxV2.nav.locked(v2Now()));
    await page.locator('#keypad-v2 [data-key="2"]').click();
    assert.equal(posts.filter(path => path === '/v1/tmbox-v2/command').length, beforeDigits, 'Digits remain local');
    await page.locator('#keypad-v2 [data-key="A"]').click();
    await page.waitForFunction(() => document.querySelector('#tmbox-v2-ack').textContent.includes('status'));
    assert.equal(posts.filter(path => path === '/v1/tmbox-v2/command').length, beforeDigits + 1);
    assert.equal((await page.request.get(urls.eu + '/v1/admin/users', {headers: {Authorization: `Bearer ${box.access_token}`}})).status(), 403);
    await page.locator('#application-menu summary').click();
    await page.locator('#open-settings').click();
    await page.locator('#login-form').waitFor({state: 'visible'});
    await page.locator('#login a[href="#workspaces"]').click();
    await page.getByRole('button', {name: 'TKL', exact: true}).click();
    await page.getByRole('heading', {name: 'Väntar på administratören'}).waitFor();
    assert.equal(await page.locator('main input, main select').count(), 0, 'Unassigned TKL cannot choose its station or login');
    const tkl = await page.evaluate(() => JSON.parse(localStorage.getItem('trainmeet-tkl.managed-client')));
    const registrationCount = posts.filter(path => path === '/v1/browser-clients').length;
    await page.reload();
    await page.getByText(tkl.device_code, {exact:true}).waitFor();
    assert.equal(posts.filter(path => path === '/v1/browser-clients').length, registrationCount);
    assert.equal((await admin.request.post(urls.eu + '/v1/devices/assign', {data: {device_code:tkl.device_code,station_id:'station-a'}})).status(),200);
    await page.locator('.operator-field input').waitFor();
    await screenshot('tkl-assigned-by-admin');
    assert.equal(await page.locator('input[type=password]').count(),0);
    const oldContext = await visitor.request.get(urls.eu + '/v1/tkl/context?station_id=station-a', {headers:{Authorization:'Bearer '+tkl.access_token}});
    assert.equal(oldContext.status(),200);
    await admin.request.post(urls.eu + '/v1/devices/assign', {data: {device_code:tkl.device_code,station_id:'station-b'}});
    await page.waitForFunction(() => document.body.textContent.includes('Lekeberg'));
    assert.equal((await visitor.request.get(urls.eu+'/v1/tkl/context?station_id=station-a',{headers:{Authorization:'Bearer '+tkl.access_token}})).status(),403);
    await admin.request.post(urls.eu + '/v1/devices/remove', {data:{device_id:tkl.client_id}});
    await page.getByRole('heading', {name:'Väntar på administratören'}).waitFor();
    await page.waitForTimeout(2300);
    assert.equal(posts.filter(path=>path==='/v1/browser-clients').length,registrationCount,'Removed TKL must not re-enroll itself');
    const trafficBefore = await (await admin.request.get(urls.eu + '/v1/tkl/context?station_id=station-a')).json();
    const beforeDemo = requests.length;
    await page.goto(urls.eu + '/tkl/?mode=demo');
    await page.getByRole('button', {name: /^ALP/}).click();
    assert.equal(await page.getByPlaceholder('Lösenord', {exact: true}).count(), 0);
    await page.getByRole('button', {name: 'Bekräfta station och fortsätt', exact: true}).click();
    await page.locator('.operator-field input').fill('Guest test operator');
    await page.getByRole('button', {name: 'Starta trafikpass', exact: true}).click();
    await page.getByRole('button', {name: 'Hem', exact: true}).waitFor();
    await page.locator('#train-demo-101-a .train-card-summary').click();
    await page.getByRole('button', {name: 'Ställ upp tåg', exact: true}).click();
    await page.locator('#train-demo-101-a .train-card-summary[aria-expanded="false"]').waitFor();
    await page.locator('#train-demo-101-a .train-card-summary').click();
    await page.getByRole('button', {name: 'Klart för avgång', exact: true}).click();
    await page.locator('#train-demo-101-a .train-card-summary[aria-expanded="false"]').waitFor();
    await page.locator('#train-demo-101-a .train-card-summary').click();
    await page.getByRole('button', {name: 'Tåg ut', exact: true}).click();
    await screenshot('tkl-guest');
    assert.equal(await page.locator('.demo-notice').isVisible(), true);
    await page.reload();
    await page.getByRole('button', {name: 'Hem', exact: true}).waitFor();
    const demoState = await page.evaluate(() => JSON.parse(sessionStorage.getItem('trainmeet-tkl.demo.v1')));
    assert.equal(demoState.runtime.connection_states[0].state, 'occupied');
    assert.ok(!requests.slice(beforeDemo).some(path => path.startsWith('/v1/') || path.startsWith('/terminal/')), 'Demo must not call server APIs, even on reload');
    const trafficAfter = await (await admin.request.get(urls.eu + '/v1/tkl/context?station_id=station-a')).json();
    for (const key of ['shift', 'movements', 'connection_states']) assert.deepEqual(trafficAfter[key], trafficBefore[key]);
    assert.equal(posts.filter(path => path === '/v1/auth/login').length, 0, 'Neither client has used admin login');
    assert.equal((await visitor.cookies()).length, 0, 'Guest clients do not receive administrator cookies');
    await page.locator('.demo-notice a[href="/#workspaces"]').click();
    await page.locator('#workspace-picker').waitFor({state: 'visible'});
    await page.getByRole('button', {name: 'TMBox', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('#tmbox-v2-station').value === 'station-a');
    await admin.request.post(urls.eu + '/v1/devices/remove', {data: {device_id: box.client_id}});
    await page.locator('#tmbox-browser-start').waitFor({state: 'visible'});
    assert.equal(posts.filter(path => path === '/v1/browser-clients').length, 2, 'Revoked box does not recreate itself automatically');
    assert.equal(await page.locator('#tmbox-v2-station').inputValue(), '');
    assert.deepEqual(errors, []);
    console.log('Public workspace tests passed: guest picker, protected admin, unique TMBox + admin assignment + revocation, local input, managed TKL assignment/revocation and isolated explicit demo with no server API calls.');
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
