// Egna klockor mot en riktig server med tillfällig databas (samma fixtur som
// server-shell-live). Inget nätverk utåt.
//
// Casper 2026-10-08: "kan du göra så att jag kan ladda upp sbb klockan som en
// egen klocka." Admin laddar upp ett klockpaket under Inställningar →
// Skärmar och klocka och väljer klockan som stil. Skärmarna och deltagarvyn
// ritar den som bilder, med visarna som paketets clock.json säger. Tas
// klockan bort visas den analoga klockan.
// Och: "drag-n-drop fler zip filer på en gång … en modalfönster där
// användaren explicit godkänner respektive klocka … en 'stämpel' på admin om
// att det finns ett godkännande att den får visas." Filerna släpps på
// panelen eller väljs, rutan visar varje klocka med förhandsbild och en egen
// kryssruta, och bara de godkända laddas upp.
const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

(async () => {
  const fixture = spawn('python3', [path.join(__dirname, 'server-shell-live-fixture.py')], {
    cwd: root, env: { ...process.env, PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let diagnostics = '', browser;
  fixture.stderr.on('data', value => { diagnostics += value; });
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'trainmeet-clock-pack-'));
  try {
    const urls = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Test server did not start')), 15000);
      readline.createInterface({ input: fixture.stdout }).once('line', line => { clearTimeout(timer); resolve(JSON.parse(line)); });
      fixture.once('exit', () => { clearTimeout(timer); reject(new Error(diagnostics)); });
    });
    const base = urls.eu;
    browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
    const errors = [], blocked = [];
    const watch = (page) => {
      page.setDefaultTimeout(15000);
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (/Content Security Policy/i.test(message.text())) blocked.push(message.text()); });
    };
    const admin = await browser.newContext({ locale: 'sv-SE', viewport: { width: 1280, height: 960 } });
    const page = await admin.newPage();
    watch(page);
    page.on('dialog', dialog => dialog.accept());
    await page.goto(base + '/login');
    await page.locator('#login-email').fill('smoke-admin@example.se');
    await page.locator('#login-password').fill('isolated-browser-test');
    await page.locator('#login-form button[type="submit"]').click();
    await page.locator('#overview-view').waitFor({ state: 'visible' });

    // Exemplet att börja från laddas ner från servern.
    const example = await page.request.get(base + '/v1/clock-faces/exempelur.tmclock');
    assert.equal(example.status(), 200);
    const file = path.join(work, 'exempelur.tmclock');
    fs.writeFileSync(file, await example.body());

    await page.goto(base + '/installningar#skarmar');
    const panel = page.locator('#clock-faces-panel');
    await panel.waitFor({ state: 'visible' });
    await panel.getByText('Inga egna klockor ännu.').waitFor();
    const message = page.locator('#clock-face-message');
    const modal = page.locator('#clock-faces-modal');
    const approveAll = page.locator('#clock-faces-approve-all');
    const submit = page.locator('#clock-faces-approve-submit');
    const items = modal.locator('.kr-clock-approve__item');
    const choose = async (files) => {
      await page.locator('#clock-face-file').setInputFiles(files);
      await modal.waitFor({ state: 'visible' });
    };
    // Filerna släpps på panelen som från Finder eller Utforskaren.
    const drop = async (files) => {
      const payload = files.map(name => ({ name: path.basename(name), data: fs.readFileSync(name).toString('base64') }));
      const transfer = await page.evaluateHandle(payload => {
        const data = new DataTransfer();
        for (const { name, data: bytes } of payload) data.items.add(new File([Uint8Array.from(atob(bytes), c => c.charCodeAt(0))], name, { type: 'application/zip' }));
        return data;
      }, payload);
      const zone = page.locator('#clock-face-drop');
      await zone.dispatchEvent('dragenter', { dataTransfer: transfer });
      await zone.dispatchEvent('dragover', { dataTransfer: transfer });
      assert.match(await zone.getAttribute('class'), /is-dragover/, 'the drop zone lights up');
      await zone.dispatchEvent('drop', { dataTransfer: transfer });
      await modal.waitFor({ state: 'visible' });
      assert.doesNotMatch(await zone.getAttribute('class'), /is-dragover/);
    };
    const upload = async (files, { via = choose } = {}) => {
      await via(files);
      await approveAll.check();
      await submit.click();
      await modal.waitFor({ state: 'hidden' });
    };

    // Rutan visar klockan innan något är uppladdat, och inget laddas upp
    // utan att klockan godkänts: knappen är av tills den är ikryssad.
    await choose(file);
    await items.first().getByText('Exempelur 1.0').waitFor();
    assert.match(await items.first().textContent(), /exempelur\.tmclock · av TrainMeet/);
    assert.equal(await items.first().locator('svg image').count(), 5, 'a preview of the clock to approve');
    assert.equal(await submit.isDisabled(), true, 'nothing to upload before it is approved');
    assert.equal(await submit.textContent(), 'Ladda upp');
    await items.first().locator('input').check();
    assert.equal(await submit.textContent(), 'Ladda upp 1');
    if (process.env.SHOTS) await modal.screenshot({ path: path.join(process.env.SHOTS, 'clock-faces-approve.png') });
    await modal.getByRole('button', { name: 'Avbryt' }).click();
    await modal.waitFor({ state: 'hidden' });
    assert.deepEqual((await (await page.request.get(base + '/v1/display')).json()).clock.faces, [], 'cancelled: nothing uploaded');
    const unapproved = await page.request.post(base + '/v1/clock-faces', { data: { data: fs.readFileSync(file).toString('base64'), rights_confirmed: true } });
    assert.equal(unapproved.status(), 400, 'the server too wants each clock approved');

    // Ett paket med ett skript nekas med skälet.
    const hostile = path.join(work, 'skript.tmclock');
    fs.writeFileSync(hostile, Buffer.from(require('node:child_process').execFileSync('python3', ['-c', `
import io, sys, zipfile
svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">{}</svg>'
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, "w") as archive:
    archive.writestr("clock.json", '{"format": 1, "name": "Skript", "layers": {"dial": "d.svg", "hour": "h.svg", "minute": "m.svg"}}')
    archive.writestr("d.svg", svg.format("<script>alert(1)</script>"))
    archive.writestr("h.svg", svg.format(""))
    archive.writestr("m.svg", svg.format(""))
sys.stdout.buffer.write(buffer.getvalue())`])));
    await page.locator('#clock-face-file').setInputFiles(hostile);
    await message.getByText(/<script> är inte tillåtet/).waitFor();
    assert.equal(await modal.isVisible(), false, 'a pack that cannot be read never reaches the dialog');

    // Exemplet släpps på panelen och godkänns: det står i listan med en
    // stämpel för godkännandet, vem och när.
    await drop([file]);
    await items.first().locator('input').check();
    await submit.click();
    await modal.waitFor({ state: 'hidden' });
    await message.getByText('Exempelur är uppladdad. Välj den som stil ovan.').waitFor();
    const row = page.locator('#clock-faces-list .kr-line--face');
    await row.getByText('Exempelur 1.0').waitFor();
    assert.match(await row.textContent(), /av TrainMeet/);
    const stamp = row.locator('.kr-stamp');
    assert.equal(await stamp.locator('b').textContent(), 'Godkänd att visas');
    assert.match(await stamp.textContent(), /Smoke · \d{4}-\d\d-\d\d \d\d:\d\d/);
    assert.match(await stamp.getAttribute('title'), /^Godkänd av Smoke \d{4}-\d\d-\d\d \d\d:\d\d: rätt att använda urtavlan\.$/);
    assert.equal(await row.locator('image').count(), 5, 'a small preview of the clock');

    // Den blir en stil bland de andra, med förhandsbild, och väljs för skärmarna.
    const tile = page.locator('#clock-style-tiles [data-value="custom:exempelur"]');
    await tile.getByText('Exempelur').waitFor();
    assert.equal(await tile.locator('image').count(), 5);
    if (process.env.SHOTS) await page.locator('#skarmar').screenshot({ path: path.join(process.env.SHOTS, 'clock-faces-settings.png') });
    await tile.click();
    await page.locator('#clock-appearance-form [type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#clock-appearance-form').dataset.dirty);
    const generation = (await (await page.request.get(base + '/v1/server-context')).json()).selected_meet.generation;
    assert.equal((await page.request.post(base + '/v1/clock', { data: { action: 'start', meet_generation: generation } })).ok(), true);

    // Klockskärmen ritar lagren som bilder och vrider visarna som paketet säger:
    // minutvisaren hoppar och sekundvisaren tar ett steg i sekunden.
    const screen = await (await browser.newContext({ locale: 'sv-SE', viewport: { width: 1280, height: 900 } })).newPage();
    watch(screen);
    const layers = new Map();
    screen.on('response', response => { if (response.url().includes('/v1/clock-faces/')) layers.set(response.url(), [response.status(), response.headers()['content-type']]); });
    await screen.goto(base + '/display/clock');
    const face = screen.locator('#clock-view svg.clock-face--custom');
    await face.waitFor();
    assert.equal(await face.locator('image').count(), 5);
    await screen.waitForFunction(() => document.querySelectorAll('#clock-view image').length === 5);
    await screen.waitForTimeout(1500);
    assert.equal(layers.size, 5, 'all five layers were fetched');
    for (const [url, [status, type]] of layers) assert.deepEqual([status, type], [200, 'image/svg+xml'], url);
    const angle = (hand) => face.locator(`[data-clock-hand="${hand}"]`).evaluate(node => Number(/rotate\(([-\d.e]+)/.exec(node.getAttribute('transform'))[1]));
    const seen = new Set();
    for (let sample = 0; sample < 6; sample += 1) {
      const [minute, second] = [await angle('minute'), await angle('second')];
      assert.equal(minute % 6, 0, `the minute hand jumps (${minute})`);
      assert.equal(second % 6, 0, `the second hand ticks (${second})`);
      seen.add(second);
      await screen.waitForTimeout(400);
    }
    assert.ok(seen.size >= 2, 'and it moves');
    if (process.env.SHOTS) await screen.screenshot({ path: path.join(process.env.SHOTS, 'clock-faces-screen.png') });

    // Klockan fyller skärmen på en stående telefon lika väl som på en liggande:
    // en urtavla på en stående skärm får en stående duk (server-ui.js).
    for (const [width, height] of [[390, 844], [844, 390], [820, 1180]]) {
      const sized = await (await browser.newContext({ locale: 'sv-SE', viewport: { width, height } })).newPage();
      watch(sized);
      await sized.goto(base + '/display/clock');
      const dial = sized.locator('#clock-view svg.clock-face--custom');
      await dial.waitFor();
      await sized.waitForTimeout(300);
      const box = await dial.boundingBox();
      assert.ok(box.width >= 0.85 * Math.min(width, height), `the dial fills ${width}×${height}: ${Math.round(box.width)} px`);
      assert.ok(box.x >= 0 && box.x + box.width <= width && box.y >= 0 && box.y + box.height <= height, 'and stays on the screen');
      assert.equal(await sized.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (process.env.SHOTS && width < height) await sized.screenshot({ path: path.join(process.env.SHOTS, `clock-faces-${width}x${height}.png`) });
      await sized.context().close();
    }

    // Skärmens eget val har klockan med sitt namn, utan "Stil:" framför.
    const offered = await screen.locator('#display-clock-style option').allTextContents();
    assert.deepEqual(offered, ['Som i inställningarna · Exempelur', 'Digital', 'Analog', 'Exempelur']);

    // Deltagarvyn på en telefon ritar samma klocka.
    const phone = await (await browser.newContext({ locale: 'sv-SE', viewport: { width: 390, height: 844 } })).newPage();
    watch(phone);
    await phone.goto(base + '/');
    await phone.locator('#pv-clock svg.clock-face--custom image').first().waitFor();
    assert.equal(await phone.locator('#pv-clock svg.clock-face--custom image').count(), 5);
    if (process.env.SHOTS) await phone.locator('#pv-clock').screenshot({ path: path.join(process.env.SHOTS, 'clock-faces-participant.png') });

    // Inställningarna ryms på en telefon med klockan i listan.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('#clock-faces-panel').scrollIntoViewIfNeeded();
    const wide = await page.evaluate(() => [...document.querySelectorAll('#clock-faces-panel *')].filter(node => node.getBoundingClientRect().right > innerWidth + 0.5).map(node => `${node.tagName}.${node.className?.baseVal ?? node.className} ${Math.round(node.getBoundingClientRect().right)}`).slice(0, 8));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `no sideways scroll on a phone: ${wide.join(', ')}`);
    if (process.env.SHOTS) await page.locator('#clock-faces-panel').screenshot({ path: path.join(process.env.SHOTS, 'clock-faces-phone.png') });
    await page.setViewportSize({ width: 1280, height: 960 });

    // Tas klockan bort visar skärmen den analoga klockan, utan omladdning.
    await row.getByRole('button', { name: 'Ta bort' }).click();
    await message.getByText('Exempelur 1.0 är borttagen.').waitFor();
    await page.locator('#clock-faces-list').getByText('Inga egna klockor ännu.').waitFor();
    await screen.waitForFunction(() => document.querySelector('#clock-view').dataset.clockSignature.startsWith('analog|'));
    assert.equal(await screen.locator('#clock-view svg.clock-face--custom').count(), 0);
    assert.equal(await page.locator('#clock-style-tiles [data-value="custom:exempelur"]').count(), 0);

    // Flera paket på en gång: två filer i samma val, och sedan en zip med båda.
    const second = path.join(work, 'andra.tmclock');
    const zipped = path.join(work, 'klockor.zip');
    require('node:child_process').execFileSync('python3', ['-c', `
import io, json, sys, zipfile
source = zipfile.ZipFile(sys.argv[1])
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, "w") as archive:
    for info in source.infolist():
        data = source.read(info)
        if info.filename == "clock.json":
            manifest = json.loads(data)
            manifest.update(id="andra-uret", name="Andra uret")
            data = json.dumps(manifest).encode()
        archive.writestr(info.filename, data)
open(sys.argv[2], "wb").write(buffer.getvalue())
with zipfile.ZipFile(sys.argv[3], "w") as bundle:
    bundle.write(sys.argv[1], "klockor/exempelur.tmclock")
    bundle.write(sys.argv[2], "klockor/andra.tmclock")`, file, second, zipped]);
    // Tre filer släpps på en gång: två paket och en zip med samma två. Rutan
    // listar varje klocka; samma klocka två gånger kan bara godkännas en gång.
    await drop([file, second, zipped]);
    await items.nth(3).waitFor();
    assert.deepEqual(await items.locator('b').allTextContents(), ['Exempelur 1.0', 'Andra uret 1.0', 'Exempelur 1.0', 'Andra uret 1.0']);
    assert.match(await items.nth(2).textContent(), /Samma klocka som i exempelur\.tmclock\./);
    assert.equal(await items.nth(2).locator('input').isDisabled(), true);
    await approveAll.check();
    assert.equal(await submit.textContent(), 'Ladda upp 2', 'all = the two clocks, once each');
    await items.first().locator('input').uncheck();
    assert.equal(await approveAll.evaluate(node => node.indeterminate), true);
    assert.equal(await submit.textContent(), 'Ladda upp 1');
    await modal.getByRole('button', { name: 'Avbryt' }).click();
    await modal.waitFor({ state: 'hidden' });
    // En zip med två klockor där bara den ena godkänns: bara den laddas upp.
    await choose(zipped);
    assert.deepEqual(await items.locator('b').allTextContents(), ['Exempelur 1.0', 'Andra uret 1.0']);
    await items.nth(1).locator('input').check();
    const oneOfTwo = page.waitForResponse(response => response.url().endsWith('/v1/clock-faces') && response.request().method() === 'POST');
    await submit.click();
    const sent = JSON.parse((await oneOfTwo).request().postData());
    assert.deepEqual([sent.file_name, sent.approved, sent.rights_confirmed], ['klockor.zip', ['andra-uret'], true]);
    assert.deepEqual((await (await oneOfTwo).json()).uploaded.map(face => face.id), ['andra-uret'], 'only the approved clock');
    await message.getByText('Andra uret är uppladdad. Välj den som stil ovan.').waitFor();
    await page.waitForFunction(() => document.querySelectorAll('#clock-faces-list .kr-line--face').length === 1);
    assert.deepEqual(await page.locator('#clock-faces-list .kr-line--face > span:nth-child(2) > b').allTextContents(), ['Andra uret 1.0']);
    await upload([file, second]);
    await message.getByText('2 klockor är uppladdade. Välj en som stil ovan.').waitFor();
    await page.locator('#clock-faces-list .kr-line--face').nth(1).waitFor();
    assert.deepEqual(await page.locator('#clock-faces-list .kr-line--face > span:nth-child(2) > b').allTextContents(), ['Andra uret 1.0', 'Exempelur 1.0']);
    assert.equal(await page.locator('#clock-faces-list .kr-stamp').count(), 2, 'a stamp on each');
    await choose(zipped);
    assert.deepEqual(await items.locator('.kr-clock-approve__note').allTextContents(), ['Finns redan, oförändrad.', 'Finns redan, oförändrad.']);
    await approveAll.check();
    await message.evaluate(node => { node.textContent = ''; });
    const zipUpload = page.waitForResponse(response => response.url().endsWith('/v1/clock-faces') && response.request().method() === 'POST');
    await submit.click();
    assert.deepEqual((await (await zipUpload).json()).uploaded.map(face => [face.id, face.replaced]), [['exempelur', true], ['andra-uret', true]], 'one zip, both packs');
    await message.getByText('2 klockor är uppladdade. Välj en som stil ovan.').waitFor();
    assert.equal(await page.locator('#clock-faces-list .kr-line--face').count(), 2, 'the zip replaced the same two');
    assert.equal(await page.locator('#clock-style-tiles [data-value^="custom:"]').count(), 2);

    // Ett paket som fungerar men skalar sämre än det kunde laddas upp, och
    // inställningarna säger vad som kan bli bättre.
    const texted = path.join(work, 'med-text.tmclock');
    require('node:child_process').execFileSync('python3', ['-c', `
import io, json, sys, zipfile
svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">{}</svg>'
with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.writestr("clock.json", json.dumps({"format": 1, "id": "med-text", "name": "Med text", "layers": {"dial": "d.svg", "hour": "h.svg", "minute": "m.svg"}}))
    archive.writestr("d.svg", svg.format('<circle cx="100" cy="100" r="98" fill="#fff"/><text x="100" y="30">12</text>'))
    archive.writestr("h.svg", svg.format('<rect x="98" y="50" width="4" height="50"/>'))
    archive.writestr("m.svg", svg.format('<rect x="98.5" y="25" width="3" height="75"/>'))`, texted]);
    await choose(texted);
    assert.match(await items.first().locator('.kr-clock-approve__warnings').textContent(), /d\.svg: text ritas/, 'the dialog says it before the upload');
    await approveAll.check();
    await submit.click();
    await message.getByText('Med text är uppladdad. Välj den som stil ovan.').waitFor();
    const warnings = page.locator('#clock-face-warnings');
    await warnings.getByText('Uppladdad, men det här skalar sämre än det kunde:').waitFor();
    assert.match(await warnings.textContent(), /d\.svg: text ritas med det typsnitt som finns på varje skärm/);
    if (process.env.SHOTS) await page.locator('#clock-face-upload-form').screenshot({ path: path.join(process.env.SHOTS, 'clock-faces-warnings.png') });

    // En tavla med mörk variant: skärmar i mörkt läge får den, ljusa den ljusa.
    // Paketets id är en tidigare inbyggd stil, så en skärm som själv valt den
    // stilen förut (sparat i webbläsaren) visar paketet igen.
    const legacy = path.join(work, 'stationsur.tmclock');
    require('node:child_process').execFileSync('python3', ['-c', `
import io, json, sys, zipfile
svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">{}</svg>'
with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.writestr("clock.json", json.dumps({"format": 1, "id": "stationsur", "name": "Stationsur",
        "layers": {"dial": "d.svg", "hour": "h.svg", "minute": "m.svg"}, "dark": {"dial": "d-dark.svg"}}))
    archive.writestr("d.svg", svg.format('<circle cx="100" cy="100" r="96" fill="#fff"/>'))
    archive.writestr("d-dark.svg", svg.format('<circle cx="100" cy="100" r="96" fill="#15181e"/>'))
    archive.writestr("h.svg", svg.format('<rect x="98" y="50" width="4" height="50"/>'))
    archive.writestr("m.svg", svg.format('<rect x="98.5" y="25" width="3" height="75"/>'))`, legacy]);
    await upload([legacy]);
    await message.getByText('Stationsur är uppladdad. Välj den som stil ovan.').waitFor();
    for (const theme of ['dark', 'light']) {
      const context = await browser.newContext({ locale: 'sv-SE', viewport: { width: 1280, height: 900 } });
      await context.addInitScript(theme => {
        localStorage.setItem('trainmeet.displayTheme', theme);
        localStorage.setItem('trainmeet.displayClockStyle', 'stationsur');
      }, theme);
      const tv = await context.newPage();
      watch(tv);
      await tv.goto(base + '/display/clock');
      const dial = tv.locator('#clock-view svg.clock-face--custom[data-face="stationsur"] image').first();
      await dial.waitFor();
      const href = await dial.getAttribute('href');
      assert.ok(theme === 'dark' ? href.endsWith('/dark-dial') : href.endsWith('/dial') && !href.endsWith('/dark-dial'), `${theme}: ${href}`);
      await context.close();
    }

    assert.deepEqual(errors, []);
    assert.deepEqual(blocked, []);
    console.log('PASS egna klockor: dra och släpp, godkännande per klocka, stämpel, skärm, deltagarvy och borttagning');
  } finally {
    await browser?.close();
    fixture.stdin.end();
    fs.rmSync(work, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
