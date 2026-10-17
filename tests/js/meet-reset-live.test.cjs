// Farozon → Nollställ träffen, i en riktig webbläsare mot en riktig server med
// tillfällig databas (samma fixtur som server-shell-live). Inget nätverk utåt.
//
// Klockan går en stund från 10:40, sedan nollställs träffen genom dialogen:
// knappen är släckt tills träffens namn är skrivet, frågan måste godkännas,
// och efteråt står klockan still på planens starttid och en säkerhetskopia
// har tillkommit. Servern startar inte om.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

// Kopiera är en liten ikon direkt efter namnet, eller ordet, som ska skrivas
// (Casper 2026-10-17), inte en knapp bredvid fältet. Fältet heter
// fortfarande hela meningen.
async function iconAfter(dialog, nameId, label) {
  const at = await dialog.evaluate((element, id) => {
    const name = element.querySelector('#' + id).getBoundingClientRect();
    const button = element.querySelector(`[data-copy-confirmation="${id}"]`);
    const icon = button.getBoundingClientRect();
    const sentence = button.closest('.confirm-prompt');
    return { gap: icon.left - name.right, middle: Math.abs((icon.top + icon.bottom) / 2 - (name.top + name.bottom) / 2),
      width: icon.width, height: icon.height, text: button.textContent.trim(), name: button.getAttribute('aria-label'),
      beside: element.querySelectorAll('.factory-reset-row button').length, inSentence: Boolean(sentence) };
  }, nameId);
  assert.ok(at.inSentence, `${nameId}: ikonen står i meningen`);
  assert.ok(at.gap >= 0 && at.gap <= 6, `${nameId}: ikonen står direkt efter namnet ${JSON.stringify(at)}`);
  assert.ok(at.middle <= 4, `${nameId}: på samma rad ${JSON.stringify(at)}`);
  assert.ok(at.width <= 24 && at.height <= 24, `${nameId}: ikonen är liten ${JSON.stringify(at)}`);
  assert.equal(at.text, '', `${nameId}: bara en ikon, ingen text`);
  assert.ok(at.name, `${nameId}: ikonen har ett namn för skärmläsare`);
  assert.equal(at.beside, 0, `${nameId}: ingen knapp bredvid fältet`);
  assert.equal(await dialog.getByRole('textbox', { name: label, exact: typeof label === 'string' }).count(), 1, `${nameId}: fältet heter ${label}`);
}

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'server-shell-live-fixture.py')], {
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
    const base = urls.eu;
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const page = await browser.newPage({ locale: 'sv-SE', viewport: { width: 1280, height: 900 } });
    page.setDefaultTimeout(15000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));

    await page.goto(base + '/login');
    await page.locator('#login-email').fill('smoke-admin@example.se');
    await page.locator('#login-password').fill('isolated-browser-test');
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator('#overview-view').waitFor({ state: 'visible' });

    const get = async route => (await page.request.get(base + route)).json();
    const start = (await get('/v1/clock')).time;
    const generation = (await get('/v1/server-context')).selected_meet.generation;
    const started = await page.request.post(base + '/v1/clock', { data: { action: 'start', time: '10:40:00', speed: 4, meet_generation: generation } });
    assert.equal(started.ok(), true, await started.text());
    assert.equal((await get('/v1/clock')).running, true);
    const backups = await get('/v1/server/backups');
    const meet = backups.overwrites;
    assert.ok(meet && meet !== 'TrainMeet Server', 'träffen har ett namn att skriva');

    // Från Kör, på en sida där Inställningar aldrig öppnats: namnet hämtas när
    // rutan öppnas (förut stod det – och knappen gick inte att trycka på), och
    // Kopiera ger namnet att klistra in.
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin: base });
    await page.goto(base + '/');
    await page.locator('#drift-timeline-reset').click();
    const fromDrift = page.locator('#meet-reset-modal');
    await fromDrift.waitFor({ state: 'visible' });
    await page.waitForFunction(name => document.querySelector('#meet-reset-name').textContent.trim() === name, meet);
    const copy = fromDrift.locator('[data-copy-confirmation="meet-reset-name"]');
    assert.equal(await copy.isDisabled(), false, 'Kopiera går att trycka på när namnet finns');
    await iconAfter(fromDrift, 'meet-reset-name', `Skriv ${meet} för att bekräfta`);
    await copy.click();
    await fromDrift.locator('#meet-reset-message').getByText('Kopierat – klistra in det i rutan').waitFor();
    assert.equal(await copy.evaluate(button => button.classList.contains('is-copied')), true, 'ikonen visar en bock när det är kopierat');
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    assert.equal(copied, meet, 'urklippet har träffens namn');
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'meet-reset-confirmation', 'fältet har fokus för att klistra in');
    await page.keyboard.press('ControlOrMeta+V');
    assert.equal(await fromDrift.locator('#meet-reset-confirmation').inputValue(), meet, 'namnet klistrades in');
    assert.equal(await fromDrift.locator('#meet-reset-start').isDisabled(), false, 'inklistrat namn låser upp knappen');
    if (process.env.MEET_RESET_SCREENSHOTS) {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.screenshot({ path: path.join(process.env.MEET_RESET_SCREENSHOTS, 'meet-reset-from-drift-phone.png') });
      await page.setViewportSize({ width: 1280, height: 900 });
    }
    // Utan urklipps-API (en Pi över http) kopieras det ur ett dolt fält i rutan.
    await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true }));
    await fromDrift.locator('#meet-reset-confirmation').fill('');
    await copy.click();
    await fromDrift.locator('#meet-reset-message.notice').waitFor();
    // Fältet är tomt igen, så Avbryt stänger utan att fråga.
    await fromDrift.locator('.modal-actions [data-close-modal]').click();
    await fromDrift.waitFor({ state: 'hidden' });

    await page.goto(base + '/installningar#farozon');
    const row = page.locator('#server-system-settings .kr-rowact').first();
    assert.equal((await row.locator('.kr-k').innerText()).trim(), 'Nollställ träffen', 'först i Farozon');
    await row.locator('#meet-reset-open').click();
    const dialog = page.locator('#meet-reset-modal');
    await dialog.waitFor({ state: 'visible' });
    assert.equal((await dialog.locator('#meet-reset-name').innerText()).trim(), meet);
    const button = dialog.locator('#meet-reset-start');
    assert.equal(await button.isDisabled(), true, 'släckt utan namn');
    await dialog.locator('#meet-reset-confirmation').fill('NOLLSTÄLL');
    assert.equal(await button.isDisabled(), true, 'ett ord att skriva av räcker inte');
    await dialog.locator('#meet-reset-confirmation').fill(meet.toLocaleUpperCase('sv-SE'));
    assert.equal(await button.isDisabled(), false, 'versaler spelar ingen roll');
    if (process.env.MEET_RESET_SCREENSHOTS) {
      await page.screenshot({ path: path.join(process.env.MEET_RESET_SCREENSHOTS, 'meet-reset-dialog.png') });
      await dialog.evaluate(element => element.close());
      await page.screenshot({ path: path.join(process.env.MEET_RESET_SCREENSHOTS, 'meet-reset-farozon.png') });
      await row.locator('#meet-reset-open').click();
      await dialog.locator('#meet-reset-confirmation').fill(meet.toLocaleUpperCase('sv-SE'));
    }

    // Nej på frågan: ingenting händer.
    page.once('dialog', question => question.dismiss());
    await button.click();
    assert.equal((await get('/v1/clock')).running, true, 'ett nej nollställer ingenting');

    page.once('dialog', question => {
      assert.match(question.message(), new RegExp(meet));
      question.accept();
    });
    await button.click();
    await page.locator('#modal-result').getByText(/Träffen är nollställd/).waitFor();
    await dialog.waitFor({ state: 'hidden' });

    const clock = await get('/v1/clock');
    assert.equal(clock.running, false, 'klockan står still');
    assert.equal(clock.time, start, 'på planens starttid');
    assert.equal(clock.speed, 4, 'hastigheten står kvar');
    const after = await get('/v1/server/backups');
    assert.equal(after.backups.length, backups.backups.length + 1, 'en säkerhetskopia togs först');
    assert.ok((await get('/v1/server-context')).selected_meet.generation > generation, 'enheterna får en ny generation');
    // Listan under Återställ säger varför kopian togs och var träffen stod.
    await page.reload();
    await page.waitForFunction(() => document.querySelector('#restore-list .restore-row'));
    const newest = await page.locator('#restore-list .restore-row small').first().textContent();
    assert.match(newest, /^Före nollställningen · Dag 1 · \S+ \d\d:\d\d · /, newest);
    // Återställ har samma ikon efter namnet på det som skrivs över.
    await page.locator('[data-open-modal="restore-modal"]').click();
    const restoreDialog = page.locator('#restore-modal');
    await restoreDialog.waitFor({ state: 'visible' });
    await page.waitForFunction(name => document.querySelector('#restore-overwrites').textContent.trim() === name, meet);
    await iconAfter(restoreDialog, 'restore-overwrites', `Skriv ${meet} för att bekräfta att den datan skrivs över`);
    await restoreDialog.locator('.modal-actions [data-close-modal]').click();
    await restoreDialog.waitFor({ state: 'hidden' });
    // Nollställ träffdata på tyska: ordet som visas låser upp knappen (#129),
    // och NOLLSTÄLL gör det fortfarande. Knappen trycks inte.
    await page.evaluate(() => localStorage.setItem('trainmeet.language', 'de'));
    await page.reload();
    await page.locator('#reset-mode-summary').click();
    const factory = page.locator('#reset-modal');
    await factory.waitFor({ state: 'visible' });
    const word = (await factory.locator('#factory-reset-word').innerText()).trim();
    assert.notEqual(word.toUpperCase(), 'NOLLSTÄLL', 'ordet står på tyska');
    // Ikonen efter ordet kopierar det och sätter markören i fältet.
    await iconAfter(factory, 'factory-reset-word', new RegExp(word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    await factory.locator('[data-copy-confirmation="factory-reset-word"]').click();
    await factory.locator('#factory-reset-message.notice').waitFor();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), word, 'urklippet har ordet');
    assert.equal(await page.evaluate(() => document.activeElement?.id), 'factory-reset-confirmation', 'fältet har fokus');
    const factoryButton = page.locator('#factory-reset-server');
    await factory.locator('#factory-reset-confirmation').fill('fel');
    assert.equal(await factoryButton.isDisabled(), true, 'fel ord låser inte upp');
    await factory.locator('#factory-reset-confirmation').fill(word.toLowerCase());
    assert.equal(await factoryButton.isDisabled(), false, 'det visade ordet låser upp');
    await factory.locator('#factory-reset-confirmation').fill('NOLLSTÄLL');
    assert.equal(await factoryButton.isDisabled(), false, 'NOLLSTÄLL gäller på alla språk');

    assert.deepEqual(errors, []);
    console.log('meet reset live: ok');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
