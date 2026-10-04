// Glömt lösenord och inbjudan med e-post, mot RIKTIGA servrar och en låtsad
// TrainMeet Cloud (account-mail-live-fixture.py). Servern skickar till Cloud
// över HTTP; provet läser koden ur det Cloud fick, som en användare läser sin
// e-post. Inget riktigt brev skickas.
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
  let stderr = '';
  fixture.stderr.on('data', value => { stderr += value; });
  let browser;
  try {
    const urls = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: fixture.stdout });
      const timeout = setTimeout(() => reject(new Error('Backend start timeout: ' + stderr)), 15000);
      lines.once('line', line => { clearTimeout(timeout); resolve(JSON.parse(line)); });
      fixture.once('exit', code => { clearTimeout(timeout); reject(new Error('Backend exited: ' + code + ' ' + stderr)); });
    });
    const mails = async () => (await fetch(urls.cloud + '/captured')).json();
    const waitForMails = async (count) => {
      for (let i = 0; i < 50; i++) { const list = await mails(); if (list.length >= count) return list; await new Promise(r => setTimeout(r, 100)); }
      throw new Error('no mail reached the fake Cloud');
    };
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const errors = [];
    const page = await browser.newPage({ locale: 'sv-SE', viewport: { width: 390, height: 844 } });
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => errors.push(error.message));
    const shown = (selector) => page.locator(selector).isVisible();
    const shot = async (target, name) => { if (process.env.ACCOUNT_MAIL_SCREENSHOTS) await target.screenshot({ path: path.join(process.env.ACCOUNT_MAIL_SCREENSHOTS, name + '.png'), fullPage: true }); };
    const login = async (base, username, password) => {
      await page.goto(base + '/login');
      await page.locator('#login-form').waitFor({ state: 'visible' });
      await page.locator('#login-username').fill(username);
      await page.locator('#login-password').fill(password);
      await page.locator('#login-form button[type="submit"]').click();
    };

    // 1. Glömt lösenordet? på en kopplad server: koden går till kontots e-post.
    await page.goto(urls.linked + '/login');
    await page.locator('#login-form').waitFor({ state: 'visible' });
    await page.locator('#login-username').fill('benny');
    await page.locator('#forgot-open').click();
    assert.deepEqual([await shown('#forgot-form'), await shown('#login-form'), await shown('#login-links')], [true, false, false]);
    assert.equal(await page.locator('#forgot-username').inputValue(), 'benny', 'the name typed for login follows along');
    await shot(page, 'forgot');
    await page.locator('#forgot-form button.primary').click();
    await page.locator('#redeem-form').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#redeem-username').inputValue(), 'benny');
    assert.match(await page.locator('#redeem-message').innerText(), /kod på väg/);
    await shot(page, 'code');
    const [reset] = await waitForMails(1);
    assert.equal(reset.path, '/api/server-mail');
    assert.equal(reset.authorization, 'Bearer kopplingsnyckel-i-provet');
    assert.deepEqual([reset.body.kind, reset.body.to, reset.body.username, reset.body.server_url],
      ['password_reset', 'benny@example.se', 'benny', ''], 'no address from the request in a reset mail');
    assert.match(reset.body.code, /^[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}$/);

    // 2. Koden och ett nytt lösenord, sedan inloggning med det.
    await page.locator('#redeem-code-boxes input').first().click();
    await page.keyboard.type(reset.body.code.replace('-', ''));
    await page.locator('#redeem-password').fill('bennys-nya-losenord');
    await page.locator('#redeem-form button.primary').click();
    await page.locator('#login-form').waitFor({ state: 'visible' });
    assert.match(await page.locator('#login-error').innerText(), /Lösenordet är satt/);
    await login(urls.linked, 'benny', 'bennys-losenord');
    await page.locator('#login-error').getByText(/./).waitFor();
    await login(urls.linked, 'benny', 'bennys-nya-losenord');
    await page.locator('#overview-view').waitFor({ state: 'visible' });

    // 3. Ett okänt konto ger samma besked och inget brev.
    const context = await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 } });
    const guest = await context.newPage();
    guest.on('pageerror', error => errors.push(error.message));
    await guest.goto(urls.linked + '/login');
    await guest.locator('#forgot-open').click();
    await guest.locator('#forgot-username').fill('finns-inte');
    await guest.locator('#forgot-form button.primary').click();
    await guest.locator('#redeem-form').waitFor({ state: 'visible' });
    assert.match(await guest.locator('#redeem-message').innerText(), /kod på väg/);
    await new Promise(r => setTimeout(r, 300));
    assert.equal((await mails()).length, 1, 'nothing is sent for an unknown account');

    // 4. Utan koppling säger rutan hur man annars kommer in.
    await guest.goto(urls.offline + '/login');
    await guest.locator('#forgot-open').click();
    await guest.locator('#forgot-username').fill('benny');
    await guest.locator('#forgot-form button.primary').click();
    await guest.locator('#forgot-message.error').waitFor();
    assert.match(await guest.locator('#forgot-message').innerText(), /inte kopplad till TrainMeet Cloud.*tmbox_gateway\.recover/);
    assert.equal(await guest.locator('#forgot-form').isVisible(), true);
    await shot(guest, 'offline');
    assert.equal(await guest.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, 'no sideways scrolling on a phone');
    await context.close();

    // 5. Ägaren bjuder in med e-post: brevet går via Cloud och koden står kvar på skärmen.
    // Ett eget fönster: benny är fortfarande inloggad i det första.
    await page.context().clearCookies();
    await page.setViewportSize({ width: 1280, height: 900 });
    await login(urls.linked, 'casper', 'ett-langt-losenord');
    await page.locator('#overview-view').waitFor({ state: 'visible' });
    await page.goto(urls.linked + '/installningar#anvandare');
    await page.waitForFunction(() => !document.getElementById('anvandare').hidden);
    await page.locator('#users-rows').getByText('benny@example.se').waitFor();
    await page.locator('#users-invite-open').click();
    await page.locator('#users-invite-name').fill('lars');
    await page.locator('#users-invite-email').fill('Lars@Example.se');
    await shot(page, 'invite');
    await page.locator('#users-invite-form button.primary').click();
    await page.locator('#users-invite-code').waitFor({ state: 'visible' });
    const code = (await page.locator('#users-code-value').innerText()).trim();
    assert.match(await page.locator('#users-message').innerText(), /också skickad till lars@example\.se/);
    const invite = (await waitForMails(2))[1];
    assert.deepEqual([invite.body.kind, invite.body.to, invite.body.username, invite.body.code, invite.body.server_url],
      ['invite', 'lars@example.se', 'lars', code, urls.linked]);
    await page.locator('#users-rows').getByText('lars@example.se').waitFor();
    await shot(page, 'users');

    // 6. Adressen går att ändra i Redigera.
    await page.locator('#users-rows tr').filter({ hasText: 'lars' }).getByRole('button', { name: 'Redigera' }).click();
    assert.equal(await page.locator('#user-edit-email').inputValue(), 'lars@example.se');
    await page.locator('#user-edit-email').fill('lars@annan.example');
    await page.locator('#user-edit-form button[type="submit"]').click();
    await page.locator('#users-rows').getByText('lars@annan.example').waitFor();

    assert.deepEqual(errors, []);
    console.log('account-mail-live: ok');
  } finally {
    await browser?.close();
    fixture.stdin.end();
  }
})().catch((error) => { console.error(error); process.exit(1); });
