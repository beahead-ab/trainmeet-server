// Account editing against real HTTP, authentication and temporary SQLite.
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
  let stderr = '', browser;
  fixture.stderr.on('data', value => { stderr += value; });
  try {
    const urls = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: fixture.stdout });
      const timeout = setTimeout(() => reject(new Error('Backend start timeout: ' + stderr)), 15000);
      lines.once('line', line => { clearTimeout(timeout); resolve(JSON.parse(line)); });
      fixture.once('exit', code => { clearTimeout(timeout); reject(new Error('Backend exited: ' + code + ' ' + stderr)); });
    });
    const base = urls.offline;
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage({ locale: 'sv-SE', viewport: { width: 1280, height: 960 } });
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.accept());
    const post = (route, data) => page.request.post(base + route, { data });
    const list = async () => (await (await page.request.get(base + '/v1/admin/users')).json()).users;
    const row = name => page.locator('#users-rows tr').filter({ hasText: name });
    const shot = async name => {
      if (process.env.USER_EDIT_SCREENSHOTS) await page.screenshot({ path: path.join(process.env.USER_EDIT_SCREENSHOTS, name + '.png'), fullPage: true });
    };
    const login = async (email, password) => {
      await page.context().clearCookies();
      await page.goto(base + '/login');
      await page.locator('#login-form').waitFor({ state: 'visible' });
      await page.locator('#login-email').fill(email);
      await page.locator('#login-password').fill(password);
      await page.locator('#login-form button[type="submit"]').click();
      await page.locator('#overview-view').waitFor({ state: 'visible' });
      await page.goto(base + '/installningar#anvandare');
      await page.locator('#users-rows tr').first().waitFor();
    };
    const open = async name => {
      await row(name).getByRole('button', { name, exact: true }).click();
      await page.locator('#user-edit-modal').waitFor({ state: 'visible' });
      assert.equal(await page.locator('#user-edit-password').inputValue(), '');
      assert.equal(await page.locator('#user-edit-password-confirm').inputValue(), '');
    };
    const save = async status => {
      const response = page.waitForResponse(r => r.url().endsWith('/v1/admin/users/update') && r.request().method() === 'POST');
      await page.locator('#user-edit-form button[type="submit"]').click();
      assert.equal((await response).status(), status);
      if (status === 200) await page.locator('#user-edit-modal').waitFor({ state: 'hidden' });
      else await page.locator('#user-edit-form .modal-feedback.error').waitFor();
    };
    const cancel = async () => {
      await page.locator('#user-edit-form [data-close-modal]').click();
      await page.locator('#user-edit-modal').waitFor({ state: 'hidden' });
    };
    const noOverflow = async () => {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      const box = await page.locator('#user-edit-modal').boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= (await page.viewportSize()).width + 1);
    };

    await login('casper@example.se', 'ett-langt-losenord');
    // Seed a second administrator and an active second owner through the real API.
    for (const [name, role] of [['Lars', 'admin'], ['Sven', 'owner']]) {
      const response = await post('/v1/admin/users', { display_name: name, email: name.toLowerCase() + '@example.se', role });
      assert.equal(response.status(), 201);
      const user = (await response.json()).user;
      assert.equal((await post('/v1/admin/users/update', { user_id: user.user_id, password: 'ursprungligt-losenord' })).status(), 200);
    }
    await page.reload();
    await open('Sven');
    assert.equal(await page.locator('#user-edit-role').inputValue(), 'owner');
    assert.equal(await page.locator('#user-edit-role').isEnabled(), true);
    await page.locator('#user-edit-display-name').fill('Sven Ny');
    await page.locator('#user-edit-email').fill('sven.ny@example.se');
    await page.locator('#user-edit-password').fill('svens-nya-losenord');
    await page.locator('#user-edit-password-confirm').fill('svens-nya-losenord');
    await page.locator('#user-edit-role').selectOption('admin');
    await shot('owner-desktop');
    await save(200);
    await row('Sven Ny').getByText('sven.ny@example.se').waitFor();

    // A duplicate address fails without committing the name first.
    const before = (await list()).find(user => user.display_name === 'Benny');
    await open('Benny');
    await page.locator('#user-edit-display-name').fill('Osparat namn');
    await page.locator('#user-edit-email').fill('casper@example.se');
    await save(400);
    assert.deepEqual((await list()).find(user => user.user_id === before.user_id), before);
    await cancel();
    await open('Benny');
    assert.equal(await page.locator('#user-edit-display-name').inputValue(), 'Benny');
    await page.locator('#user-edit-role').selectOption('owner');
    await save(200);
    assert.equal((await list()).find(user => user.user_id === before.user_id).role, 'owner');

    // Save without a new password, then verify the original password still signs in.
    await open('Sven Ny');
    await page.locator('#user-edit-display-name').fill('Sven Tomt');
    await save(200);
    const targetContext = await browser.newContext();
    assert.equal((await targetContext.request.post(base + '/v1/auth/login', {
      data: { email: 'sven.ny@example.se', password: 'svens-nya-losenord' },
    })).status(), 200);

    await login('lars@example.se', 'ursprungligt-losenord');
    assert.equal(await page.locator('#users-invite-open').isVisible(), false);
    for (const name of ['Casper', 'Benny']) assert.equal(await row(name).getByRole('button').count(), 0);
    await open('Sven Tomt');
    assert.equal(await page.locator('#user-edit-role').isDisabled(), true);
    assert.equal(await page.locator('#user-edit-role option[value="owner"]').isDisabled(), true);
    assert.equal(await page.locator('#user-edit-removal').isVisible(), false);
    assert.equal(await page.locator('#user-edit-reissue').isVisible(), false);
    await page.setViewportSize({ width: 390, height: 844 });
    await noOverflow();
    await shot('admin-mobile');
    await page.locator('#user-edit-display-name').fill('Sven Admin');
    await page.locator('#user-edit-email').fill('sven.admin@example.se');
    await page.locator('#user-edit-password').fill('admin-satt-losenord');
    await page.locator('#user-edit-password-confirm').fill('admin-satt-losenord');
    await save(200);
    await row('Sven Admin').getByText('sven.admin@example.se').waitFor();
    assert.equal((await targetContext.request.get(base + '/v1/admin/users')).status(), 401, 'old sessions ended');
    assert.equal((await targetContext.request.post(base + '/v1/auth/login', {
      data: { email: 'sven.admin@example.se', password: 'admin-satt-losenord' },
    })).status(), 200);

    // Direct requests must not bypass the owner's boundary or promote an administrator.
    for (const target of (await list()).filter(user => ['Benny', 'Sven Admin'].includes(user.display_name))) {
      const response = await post('/v1/admin/users/update', { user_id: target.user_id,
        display_name: 'Kapad', email: 'kapad@example.se', role: 'owner', password: 'kapat-losenord' });
      assert.equal(response.status(), 403);
      assert.deepEqual((await list()).find(user => user.user_id === target.user_id), target);
    }
    await open('Sven Admin');
    await page.locator('#user-edit-display-name').fill('Avbruten ändring');
    await cancel();
    await open('Sven Admin');
    assert.equal(await page.locator('#user-edit-display-name').inputValue(), 'Sven Admin');
    await cancel();
    await open('Lars');
    assert.equal(await page.locator('#user-edit-removal').isVisible(), false);
    await page.locator('#user-edit-password').fill('mitt-nya-losenord');
    await page.locator('#user-edit-password-confirm').fill('mitt-nya-losenord');
    await save(200);
    await page.locator('#login-form').waitFor({ state: 'visible' });
    assert.match(await page.locator('#login-error').innerText(), /Lösenordet är ändrat/);
    assert.equal((await post('/v1/auth/login', { email: 'lars@example.se', password: 'ursprungligt-losenord' })).status(), 401);
    await login('lars@example.se', 'mitt-nya-losenord');
    await targetContext.close();
    assert.deepEqual(errors, []);
    console.log('user-editing-live: ok (owner/admin permissions, atomic errors, passwords, desktop/mobile)');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
    await new Promise(resolve => { fixture.once('exit', resolve); setTimeout(() => { fixture.kill(); resolve(); }, 5000).unref(); });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
