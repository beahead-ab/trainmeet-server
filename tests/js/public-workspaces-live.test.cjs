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
    await page.locator('#pv-virtual-card a[href="/tmbox/"]').click();
    await page.locator('.box-code').getByText(/^WEB/).waitFor();
    const box = await page.evaluate(() => JSON.parse(localStorage.getItem('trainmeet.browser-tmbox')));
    assert.equal(await page.locator('input, select').count(), 0, 'No station or address controls');
    assert.equal(await page.locator('.keypad button').count(), 16);
    assert.equal(await page.locator('.keypad button:not(:disabled)').count(), 0);
    assert.equal((await page.request.get(urls.eu + '/v1/devices')).status(), 401);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await screenshot('box-awaiting-admin');
    await page.reload();
    await page.locator('.box-code').getByText(box.device_code, {exact:true}).waitFor();
    assert.equal(posts.filter(path=>path==='/v1/browser-clients').length, 1);
    assert.equal((await admin.request.post(urls.eu+'/v1/devices/assign',{data:{device_code:box.device_code,station_id:'station-a'}})).status(),200);
    await page.locator('.box h2').getByText('Charlottendahl',{exact:true}).waitFor();
    await screenshot('box-assigned');
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
    await page.locator('.keypad [data-key="*"]').click();
    await page.waitForFunction(()=>document.querySelector('.lcd').textContent.includes('Deutsch'));
    await page.locator('.keypad [data-key="D"]').click();
    await page.waitForFunction(()=>document.querySelector('.lcd').textContent.includes('Svenska'));
    await page.locator('.keypad [data-key="#"]').click();
    await page.waitForFunction(()=>document.querySelector('.lcd').textContent.includes('Språk'));
    assert.equal((await page.request.get(urls.eu+'/v1/admin/users',{headers:{Authorization:`Bearer ${box.access_token}`}})).status(),403);
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
    assert.equal(await page.locator('.keypad button:not(:disabled)').count(), 0);
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
