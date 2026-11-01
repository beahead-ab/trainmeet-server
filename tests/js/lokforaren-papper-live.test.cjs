// Rätta en tjänst efter pappret (#225) mot en riktig TrainMeet Server: ägaren
// fotar två sidor i Lokföraren, avläsningen (här en ersättare för Cloud) visar
// pappret bredvid tjänsten som den är nu, och den sparade versionen gäller.
// Benny, som redan kör tjänsten, byts inte mitt i utan får frågan. En förare
// ser aldrig knapparna. Till sist gäller köruppdragets version igen.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const shots = process.env.LOKFORAREN_SHOTS || '';

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'lokforaren-live-fixture.py')], {
    cwd: root,
    env: { ...process.env, LOKFORAREN_PAPER: '1', PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '', browser;
  fixture.stderr.on('data', (value) => { stderr += value; });
  try {
    const { base } = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: fixture.stdout });
      const timeout = setTimeout(() => reject(new Error('Backend start timeout: ' + stderr)), 15000);
      lines.once('line', (line) => { clearTimeout(timeout); resolve(JSON.parse(line)); });
      fixture.once('exit', (code) => { clearTimeout(timeout); reject(new Error('Backend exited: ' + code + ' ' + stderr)); });
    });
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const errors = [];
    const phone = async (email, password) => {
      const context = await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      page.on('pageerror', (error) => errors.push(error.message));
      await page.goto(base + '/lokforaren/');
      await page.locator('#login-email').fill(email);
      await page.locator('#login-password').fill(password);
      await page.locator('#login-form button[type="submit"]').click();
      await page.locator('#view-choose').waitFor({ state: 'visible' });
      return page;
    };
    const choose = async (page, number) => {
      await page.locator('#duty-query').fill(number);
      await page.locator('#duty-found').waitFor({ state: 'visible' });
      await page.waitForFunction((value) => document.querySelector('#found-title').textContent.endsWith(' ' + value), number);
    };

    // 1. Benny kör tjänst 7 som den kom i paketet, och ser inga knappar för pappret.
    const benny = await phone('benny@example.se', 'forarens-losenord');
    await choose(benny, '7');
    assert.equal(await benny.locator('#found-admin').isVisible(), false);
    await benny.locator('#duty-activate').click();
    await benny.waitForFunction(() => !document.querySelector('#view-countdown').hidden || !document.querySelector('#view-korplan').hidden);

    // 2. Casper (ägare) fotar omslaget och en sida till.
    const casper = await phone('casper@example.se', 'ett-langt-losenord');
    await choose(casper, '7');
    await casper.locator('#paper-open').click();
    await casper.locator('#sheet-paper').waitFor({ state: 'visible' });
    assert.equal(await casper.locator('#paper-camera-text').innerText(), 'Fota omslaget');
    assert.equal(await casper.locator('#paper-read').isDisabled(), true);
    const photo = (name, color) => {
      // Ett ritat "papper": ingen riktig bild behövs för flödet.
      const file = path.join(os.tmpdir(), `lokforaren-${name}-${process.pid}.svg`);
      fs.writeFileSync(file, `<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="2000"><rect width="1500" height="2000" fill="#f4f2ea"/><rect x="200" y="300" width="1100" height="120" fill="${color}"/></svg>`);
      return file;
    };
    await casper.locator('#paper-camera').setInputFiles(photo('omslag', '#5b3fa6'));
    await casper.waitForFunction(() => document.querySelectorAll('#paper-pages .lf-page:not(.is-sending)').length === 1)
      .catch(async (error) => { throw new Error('first page: ' + await casper.locator('#paper-message').innerText() + ' ' + await casper.locator('#paper-pages').innerHTML()); });
    assert.equal(await casper.locator('#paper-camera-text').innerText(), 'Fota sida 2');
    await casper.locator('#paper-camera').setInputFiles(photo('sida2', '#333333'));
    await casper.waitForFunction(() => document.querySelectorAll('#paper-pages .lf-page:not(.is-sending)').length === 2);
    if (shots) await casper.screenshot({ path: path.join(shots, 'papper-1-foton.png') });

    // 3. Avläsningen: pappret bredvid tjänsten som den är nu.
    await casper.locator('#paper-read').click();
    await casper.locator('#paper-step-reading').waitFor({ state: 'visible' });
    await casper.locator('#paper-step-result').waitFor({ state: 'visible', timeout: 20000 });
    const facts = await casper.locator('#paper-facts tbody').innerText();
    assert.match(facts, /17:40–19:10\s+03:01–05:36/);
    assert.match(facts, /P 101\s+G 4007/);
    assert.match(await casper.locator('#paper-warnings').innerText(), /suddig/);
    assert.match(await casper.locator('#paper-summary').innerText(), /på \d+ punkter/);
    assert.equal(await casper.locator('#paper-rows li').count(), 4);
    if (shots) await casper.screenshot({ path: path.join(shots, 'papper-2-jamfor.png'), fullPage: true });
    await casper.locator('#paper-save').click();
    await casper.locator('#sheet-paper').waitFor({ state: 'hidden' });
    await casper.locator('#toast').waitFor({ state: 'visible' });
    assert.match(await casper.locator('#toast').innerText(), /Version 1 av tjänst 7 gäller nu/);
    await casper.waitForFunction(() => !document.querySelector('#found-version').hidden);
    assert.match(await casper.locator('#found-version').innerText(), /Version 1 · rättad efter pappret .+ av Casper/);
    assert.match(await casper.locator('#found-facts').innerText(), /03:01–05:36/);
    assert.equal(await casper.locator('#found-source').innerText(), 'Fotona av pappret · 2 sidor');
    const pdf = await casper.request.get(base + (await casper.locator('#found-source').getAttribute('href')));
    assert.equal(pdf.headers()['content-type'], 'application/pdf');
    if (shots) await casper.screenshot({ path: path.join(shots, 'papper-3-sparad.png') });

    // 4. Benny byts inte mitt i: Lokföraren frågar, och han byter själv.
    await benny.reload();
    await benny.locator('#banner.is-duty').waitFor({ state: 'visible', timeout: 15000 });
    assert.match(await benny.locator('#banner-text').innerText(), /Tjänst 7 har rättats efter pappret/);
    assert.match(await benny.locator('#duty-sub').innerText(), /Provbolaget/);
    if (shots) await benny.screenshot({ path: path.join(shots, 'papper-4-foraren.png') });
    await benny.locator('#banner').click();
    await benny.locator('#sheet-update').waitFor({ state: 'visible' });
    await benny.waitForFunction(() => /Gäller nu/.test(document.querySelector('#update-facts').textContent));
    assert.match(await benny.locator('#update-facts').innerText(), /Du kör nu\s+17:40–19:10 · P 101/);
    assert.match(await benny.locator('#update-facts').innerText(), /Gäller nu\s+03:01–05:36 · G 4007/);
    if (shots) await benny.screenshot({ path: path.join(shots, 'papper-5-fraga.png') });
    await benny.locator('#update-switch').click();
    await benny.locator('#sheet-update').waitFor({ state: 'hidden' });
    await benny.waitForFunction(() => /Green Cargo fjärr/.test(document.querySelector('#duty-sub').textContent));
    assert.equal(await benny.locator('#banner.is-duty').count(), 0);

    // 5. Förare når inte versionerna eller fotona, inte ens direkt.
    const denied = await benny.request.post(base + '/v1/driver-admin/scans', { data: { duty_id: 'duty-7' } });
    assert.equal(denied.status(), 403);

    // 6. Köruppdragets version kan gälla igen; Benny får frågan åt andra hållet.
    await casper.locator('#versions-open').click();
    await casper.locator('#sheet-versions').waitFor({ state: 'visible' });
    await casper.locator('#versions-list .lf-choice[data-version="1"][aria-checked="true"]').waitFor();
    await casper.locator('#versions-list .lf-choice[data-version="0"]').click();
    if (shots) await casper.screenshot({ path: path.join(shots, 'papper-6-versioner.png') });
    await casper.locator('#versions-use').click();
    await casper.locator('#sheet-versions').waitFor({ state: 'hidden' });
    await casper.waitForFunction(() => document.querySelector('#found-version').hidden);
    assert.match(await casper.locator('#found-facts').innerText(), /17:40–19:10/);
    assert.equal(await casper.locator('#versions-open').isVisible(), true, 'versionerna finns kvar att välja');
    await benny.reload();
    await benny.locator('#banner.is-duty').waitFor({ state: 'visible', timeout: 15000 });
    assert.match(await benny.locator('#banner-text').innerText(), /gäller igen som i köruppdraget/);
    await benny.locator('#banner').click();
    await benny.locator('#update-keep').click();
    await benny.waitForFunction(() => !document.querySelector('#banner').classList.contains('is-duty'));

    // 7. Inga style-attribut (serverns CSP) och inga fel på sidorna.
    const html = fs.readFileSync(path.join(root, 'src/tmbox_gateway/web/lokforaren/index.html'), 'utf8');
    assert.equal(/\sstyle=/.test(html), false);
    assert.deepEqual(errors, []);
    console.log('lokforaren-papper-live: ok (photos, reading, comparison, new version, PDF of the photos, driver asked and switches, 403 for drivers, back to the duty sheets)');
  } finally {
    if (browser) await browser.close();
    fixture.stdin.end();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
