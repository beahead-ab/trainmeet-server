// All three account roles, using real login/SQLite/HTTP on desktop and mobile.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'account-mail-live-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let browser, stderr = '';
  fixture.stderr.on('data', value => stderr += value);
  try {
    const urls = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: fixture.stdout });
      const timeout = setTimeout(() => reject(new Error(stderr)), 15000);
      lines.once('line', line => { clearTimeout(timeout); resolve(JSON.parse(line)); });
      fixture.once('exit', code => { clearTimeout(timeout); reject(new Error('Fixture exited ' + code + stderr)); });
    });
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const base = urls.offline;
    const setup = await browser.newContext();
    assert.equal((await setup.request.post(base + '/v1/auth/login', { data: { email: 'casper@example.se', password: 'ett-langt-losenord' } })).status(), 200);
    const accounts = { owner: ['casper@example.se', 'ett-langt-losenord', 'Casper'], admin: ['benny@example.se', 'bennys-losenord', 'Benny'] };
    for (const [name, role] of [['Driver', 'user'], ['Other admin', 'admin'], ['Pending user', 'user'], ['Pending owner', 'owner']]) {
      const response = await setup.request.post(base + '/v1/admin/users', { data: { display_name: name, email: name.replaceAll(' ', '').toLowerCase() + '@example.se', role } });
      assert.equal(response.status(), 201);
      const user = (await response.json()).user;
      if (role === 'user' && name === 'Driver') {
        assert.equal((await setup.request.post(base + '/v1/admin/users/update', { data: { user_id: user.user_id, password: 'driver-password' } })).status(), 200);
        accounts.user = [user.email, 'driver-password', name];
      }
    }
    for (const width of [1280, 390]) {
      for (const role of ['owner', 'admin', 'user']) {
        const context = await browser.newContext({ locale: 'sv-SE', viewport: { width, height: 960 } });
        const page = await context.newPage();
        page.setDefaultTimeout(15000);
        page.on('dialog', dialog => dialog.accept());
        const errors = [], denied = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('response', response => { if (response.status() === 403) denied.push(response.url()); });
        const [email, password, name] = accounts[role];
        await page.goto(base + '/login');
        await page.locator('#login-email').fill(email);
        await page.locator('#login-password').fill(password);
        await page.locator('#login-form button[type="submit"]').click();
        if (role === 'user') {
          await page.locator('#users-rows tr').waitFor();
          assert.equal(await page.locator('#anvandare-title').innerText(), 'Eget konto');
          assert.equal(await page.locator('#users-rows tr').count(), 1);
          assert.equal(await page.locator('#settings-nav .kr-nav:visible').count(), 1);
          assert.equal(await page.locator('#header-data').isVisible(), false);
          assert.equal(await page.locator('#header-help').isVisible(), false);
          assert.deepEqual(denied, [], 'profile UI should never request administrative APIs');
        } else {
          await page.locator('#overview-view').waitFor({ state: 'visible' });
          await page.goto(base + '/installningar#anvandare');
          await page.locator('#users-rows tr').first().waitFor();
          await page.locator('#users-invite-open').click();
          const choices = await page.locator('#users-invite-role option').evaluateAll(nodes => nodes.map(node => node.value));
          assert.deepEqual(choices, role === 'owner' ? ['owner', 'admin', 'user'] : ['admin', 'user']);
          await page.locator('#users-invite-form [data-close-modal]').click();
          // Owners can edit owners; an administrator cannot even open an owner account.
          const ownerRow = page.locator('#users-rows tr').filter({ hasText: 'Pending owner' });
          assert.equal(await ownerRow.getByRole('button').count(), role === 'admin' ? 0 : 2);
          const other = page.locator('#users-rows tr').filter({ hasText: 'Pending user' });
          await other.getByRole('button', { name: 'Redigera', exact: true }).click();
          assert.equal(await page.locator('#user-edit-role').isEnabled(), true);
          assert.deepEqual(await page.locator('#user-edit-role option').evaluateAll(nodes => nodes.map(node => node.value)), choices);
          assert.equal(await page.locator('#user-edit-removal').isVisible(), true);
          assert.equal(await page.locator('#user-edit-reissue').isVisible(), true);
          // Actually regenerate the invitation through the editor.
          const reissue = page.waitForResponse(r => r.url().endsWith('/v1/admin/users/reissue'));
          await page.locator('#user-edit-reissue').click();
          assert.equal((await reissue).status(), 200);
          await page.locator('#user-edit-modal').waitFor({ state: 'hidden' });
        }
        assert.equal(await page.locator('#users-invite-open').isVisible(), role !== 'user');
        const selfRow = page.locator('#users-rows tr').filter({ hasText: email });
        await selfRow.getByRole('button', { name, exact: true }).click();
        await page.locator('#user-edit-modal').waitFor({ state: 'visible' });
        assert.equal(await page.locator('#user-edit-role').isDisabled(), true);
        assert.equal(await page.locator('#user-edit-role').inputValue(), role);
        assert.equal(await page.locator('#user-edit-password').inputValue(), '');
        assert.equal(await page.locator('#user-edit-removal').isVisible(), false);
        assert.equal(await page.locator('#user-edit-reissue').isVisible(), false);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
        const box = await page.locator('#user-edit-modal').boundingBox();
        assert.ok(box.x >= 0 && box.x + box.width <= width + 1);
        if (process.env.USER_EDIT_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.USER_EDIT_SCREENSHOTS, `role-${role}-${width}.png`), fullPage: true });
        await page.locator('#user-edit-form [data-close-modal]').click();
        if (role === 'user') {
          await selfRow.getByRole('button', { name, exact: true }).click();
          await page.locator('#user-edit-display-name').fill('Driver Updated');
          const saved = page.waitForResponse(r => r.url().endsWith('/v1/account') && r.request().method() === 'POST');
          await page.locator('#user-edit-form button[type="submit"]').click();
          assert.equal((await saved).status(), 200);
          await page.locator('#user-edit-modal').waitFor({ state: 'hidden' });
          await selfRow.getByRole('button', { name: 'Driver Updated', exact: true }).waitFor();
          accounts.user[2] = 'Driver Updated';
          assert.equal((await page.request.post(base + '/v1/admin/users/update', { data: { user_id: 'anything', role: 'owner' } })).status(), 403);
          await page.goto(base + '/drift');
          await page.locator('#anvandare-title').waitFor({ state: 'visible' });
          assert.equal(await page.locator('#overview-view').isVisible(), false);
          await page.goto(base + '/');
          await page.locator('#participant-view').waitFor({ state: 'visible' });
          await page.locator('#pv-login').waitFor({ state: 'visible' });
          assert.equal(await page.locator('#pv-login').innerText(), 'Eget konto');
          assert.match(await page.locator('#pv-login').getAttribute('href'), /installningar/);
          assert.equal((await page.request.get(base + '/v1/display')).status(), 200);
        }
        assert.deepEqual(errors, []);
        console.log(`user-roles-live: ${role} ${width}px ok`);
        await context.close();
      }
    }
    await setup.close();
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
    await new Promise(resolve => { fixture.once('exit', resolve); setTimeout(() => { fixture.kill(); resolve(); }, 5000).unref(); });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
