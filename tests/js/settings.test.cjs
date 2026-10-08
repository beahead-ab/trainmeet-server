// Inställningar i Kontrollrummet: en sidomeny, ett avsnitt i taget, och varje
// panel som går att ändra har egen Avbryt/Spara som är släckta tills något är
// ändrat. Riktig webbkod med en strikt CSP; API:et är fixturen i kr-fixture.cjs.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const SECTIONS = ['traff', 'skarmar', 'wifi', 'obemannade', 'server', 'kod', 'anvandare', 'uppdatering', 'sprak', 'visning', 'farozon'];
const FORMS = { traff: 'cloud-auto-form', skarmar: 'clock-appearance-form', wifi: 'connection-wifi-form', server: 'server-identity-form', kod: 'connection-code-form', sprak: 'language-form', visning: 'browser-deviation-form' };

const bar = (page, form) => page.evaluate((id) => {
  const f = document.getElementById(id);
  return { cancel: !f.querySelector('[data-save-cancel]').disabled, save: !f.querySelector('[data-save-submit]').disabled, text: f.querySelector('[data-save-state]').textContent.trim() };
}, form);

(async () => {
  const h = await open({ route: '/installningar', width: 1280, height: 900 });
  const { page } = h;
  page.setDefaultTimeout(8000);
  try {
    await page.locator('#admin-view').waitFor({ state: 'visible' });

    // Nine sections, in three groups, one at a time.
    assert.deepEqual(await page.locator('#settings-nav a.kr-nav').evaluateAll(links => links.map(l => l.dataset.section)), SECTIONS);
    assert.deepEqual(await page.locator('#settings-nav .kr-grp').allTextContents(), ['Träffen', 'Den här servern', 'Webbläsaren']);
    assert.equal(await page.locator('#admin-view .kr-setsec').count(), 11);
    for (const section of SECTIONS) {
      await page.locator(`#settings-nav a[data-section="${section}"]`).click();
      await page.waitForFunction(id => !document.getElementById(id).hidden, section);
      assert.equal(await page.locator('#admin-view .kr-setsec:not([hidden])').count(), 1, section);
      assert.equal(await page.locator(`#settings-nav a[data-section="${section}"]`).getAttribute('aria-current'), 'page');
      assert.equal(await page.locator('#settings-nav [aria-current="page"]').count(), 1);
      assert.equal(new URL(page.url()).pathname + new URL(page.url()).hash, '/installningar#' + section);
    }
    // Old addresses and unknown ones land on a section that exists.
    for (const [hash, section] of [['anslutning', 'kod'], ['fynd', 'traff'], ['klocka', 'skarmar'], ['nonsense', 'traff']]) {
      await page.evaluate(h => { location.hash = h; }, hash);
      await page.waitForFunction(id => !document.getElementById(id).hidden, section);
    }

    // Every form that can be changed starts dark and says nothing is changed.
    for (const [section, id] of Object.entries(FORMS)) {
      await page.evaluate(s => { location.hash = s; }, section);
      assert.deepEqual(await bar(page, id), { cancel: false, save: false, text: 'Inget ändrat' }, id);
    }

    // A text field: lit by a change, named by its label, dark again after Avbryt.
    await page.evaluate(() => { location.hash = 'wifi'; });
    const name = page.locator('#connection-wifi-name');
    const before = await name.inputValue();
    await name.fill(before + ' 2');
    assert.deepEqual(await bar(page, 'connection-wifi-form'), { cancel: true, save: true, text: 'Ändrat: Nätverksnamn' });
    assert.equal(await page.locator('#connection-wifi-form').getAttribute('data-dirty'), 'true');
    // The neighbouring form on the same page is untouched.
    assert.deepEqual(await bar(page, 'connection-badge-form'), { cancel: false, save: false, text: 'Inget ändrat' });
    await page.locator('#connection-wifi-form [data-save-cancel]').click();
    assert.equal(await name.inputValue(), before);
    assert.deepEqual(await bar(page, 'connection-wifi-form'), { cancel: false, save: false, text: 'Inget ändrat' });
    // Changing a value back by hand is also "not changed".
    await name.fill(before + 'x'); await name.fill(before);
    assert.equal((await bar(page, 'connection-wifi-form')).save, false);
    // Two changes are both named; a save posts, then reports Sparat and goes dark.
    await name.fill(before + ' 3');
    await page.locator('#connection-wifi-password').fill('hemligt-123');
    assert.equal((await bar(page, 'connection-wifi-form')).text, 'Ändrat: Nätverksnamn, Lösenord');
    await page.locator('#connection-wifi-form [data-save-submit]').click();
    await page.waitForFunction(() => document.querySelector('#connection-wifi-form [data-save-state]').textContent.trim() === 'Sparat');
    assert.deepEqual(await bar(page, 'connection-wifi-form'), { cancel: false, save: false, text: 'Sparat' });
    // The password is hidden until asked for.
    assert.equal(await page.locator('#connection-wifi-password').getAttribute('type'), 'password');
    await page.locator('#connection-wifi-show').click();
    assert.equal(await page.locator('#connection-wifi-password').getAttribute('type'), 'text');
    await page.locator('#connection-wifi-show').click();
    assert.equal(await page.locator('#connection-wifi-password').getAttribute('type'), 'password');

    // A switch counts too.
    await page.evaluate(() => { location.hash = 'skarmar'; });
    const seconds = page.locator('#meet-clock-seconds');
    const secondsBefore = await seconds.isChecked();
    await seconds.setChecked(!secondsBefore);
    assert.deepEqual(await bar(page, 'clock-appearance-form'), { cancel: true, save: true, text: 'Ändrat: Sekunder' });

    // Clock styles are one choice among four, by mouse and by arrow keys.
    const tiles = page.locator('#clock-style-tiles [role=radio]');
    assert.equal(await tiles.count(), 4);
    assert.equal(await page.locator('#clock-style-tiles').getAttribute('role'), 'radiogroup');
    assert.equal(await page.locator('#clock-style-tiles [aria-checked="true"]').count(), 1);
    const selected = await page.locator('#clock-style-tiles [aria-checked="true"]').getAttribute('data-value');
    const other = (await tiles.evaluateAll(nodes => nodes.map(n => n.dataset.value))).find(value => value !== selected);
    await page.locator(`#clock-style-tiles [data-value="${other}"]`).click();
    assert.equal(await page.locator('#clock-style-tiles [aria-checked="true"]').getAttribute('data-value'), other);
    assert.equal(await page.locator('#meet-clock-style').inputValue(), other);
    assert.match((await bar(page, 'clock-appearance-form')).text, /Klockstil/);
    await page.keyboard.press('ArrowRight');
    assert.notEqual(await page.locator('#clock-style-tiles [aria-checked="true"]').getAttribute('data-value'), other);
    await page.locator('#clock-appearance-form [data-save-cancel]').click();
    assert.equal(await page.locator('#clock-style-tiles [aria-checked="true"]').getAttribute('data-value'), selected, 'Avbryt restores the tile');
    assert.equal(await seconds.isChecked(), secondsBefore, 'and the switch');
    assert.equal((await bar(page, 'clock-appearance-form')).save, false);
    // The previews are drawn, not images from somewhere.
    assert.equal(await page.locator('#clock-style-tiles svg').count() >= 3, true);

    // QR codes are made on the spot: Wi-Fi first (when there is one), then the meet.
    await page.evaluate(() => { location.hash = 'wifi'; });
    await page.waitForFunction(() => document.querySelectorAll('#settings-qrs .kr-qr').length >= 1);
    assert.ok(await page.locator('#settings-qrs .kr-qr svg').count() >= 1);
    assert.equal(await page.locator('#settings-qrs .kr-qr').last().locator('.kr-qr__cap span').textContent(), 'Träffen');

    // Language: tiles, saved with Spara, and the whole page follows.
    await page.evaluate(() => { location.hash = 'sprak'; });
    assert.equal(await page.locator('#language-tiles [role=radio]').count(), 5);
    assert.equal(await page.locator('#language-tiles [aria-checked="true"]').getAttribute('data-value'), 'sv');
    await page.locator('#language-tiles [data-value="de"]').click();
    assert.equal((await bar(page, 'language-form')).save, true);
    assert.equal(await page.evaluate(() => TrainMeetI18n.getLanguage()), 'sv', 'nothing changes before Spara');
    await page.locator('#language-form [data-save-submit]').click();
    await page.waitForFunction(() => TrainMeetI18n.getLanguage() === 'de');
    assert.equal(await page.locator('#settings-nav a[data-section="farozon"]').textContent(), 'Gefahrenbereich');
    assert.equal((await bar(page, 'language-form')).save, false);
    await page.locator('#language-tiles [data-value="sv"]').click();
    await page.locator('#language-form [data-save-submit]').click();
    await page.waitForFunction(() => TrainMeetI18n.getLanguage() === 'sv');

    // Visning: this browser's own level for delays, saved here only. Empty follows the meet.
    await page.evaluate(() => { location.hash = 'visning'; });
    const level = page.locator('#browser-deviation-level');
    assert.deepEqual(await level.evaluate((select) => [...select.options].map((option) => option.value)), ['', '1', '2', '3', '4', '5']);
    assert.match(await level.locator('option').first().textContent(), /^Som träffen: 2 · När det inträffar$/);
    await level.selectOption('4');
    assert.deepEqual(await bar(page, 'browser-deviation-form'), { cancel: true, save: true, text: 'Ändrat: Förseningar i den här webbläsaren' });
    assert.match(await page.locator('#browser-deviation-note').textContent(), /Röd bricka från 3 min/);
    await page.locator('#browser-deviation-form [data-save-submit]').click();
    assert.equal(await page.evaluate(() => localStorage.getItem('trainmeet.deviationLevel')), '4');
    assert.equal((await bar(page, 'browser-deviation-form')).save, false);
    await level.selectOption('');
    await page.locator('#browser-deviation-form [data-save-submit]').click();
    assert.equal(await page.evaluate(() => localStorage.getItem('trainmeet.deviationLevel')), null, 'empty follows the meet again');

    // The menu can be searched: the groups without a hit go, Enter opens the first.
    const search = page.locator('#settings-search');
    await search.fill('wi-fi');
    assert.deepEqual(await page.locator('#settings-nav a.kr-nav:not([hidden])').evaluateAll(l => l.map(a => a.dataset.section)), ['wifi']);
    assert.deepEqual(await page.locator('#settings-nav .kr-grp:not([hidden])').allTextContents(), ['Träffen']);
    await search.fill('zzz');
    assert.equal(await page.locator('#settings-nav a.kr-nav:not([hidden])').count(), 0);
    await search.fill('använd');
    await search.press('Enter');
    await page.waitForFunction(() => !document.getElementById('anvandare').hidden);
    assert.equal(await search.inputValue(), '');
    assert.equal(await page.locator('#settings-nav a.kr-nav:not([hidden])').count(), 11);

    // The update section: nothing is offered that cannot run, and the steps
    // only show while an update is going on.
    await page.evaluate(() => { location.hash = 'uppdatering'; });
    await page.locator('#software-version').getByText('2.2.0').waitFor();
    assert.equal(await page.locator('#update-progress').isVisible(), false);
    // A message with a value in it is translated again when the language changes.
    await page.locator('#software-check').click();
    await page.locator('#software-update-message').getByText('Version 2.3.0 finns tillgänglig.').waitFor();
    await page.evaluate(() => TrainMeetI18n.setLanguage('en'));
    assert.equal(await page.locator('#software-update-message').textContent(), 'Version 2.3.0 is available.');
    await page.evaluate(() => TrainMeetI18n.setLanguage('sv'));
    assert.equal(await page.locator('#software-update-message').textContent(), 'Version 2.3.0 finns tillgänglig.');

    // Both themes, and the page is made of tokens: nothing is white on dark.
    for (const theme of ['dark', 'light']) {
      await page.evaluate(t => document.documentElement.setAttribute('data-kr-theme', t), theme);
      const colours = await page.evaluate(() => {
        const color = selector => getComputedStyle(document.querySelector(selector));
        return { page: color('#admin-view').backgroundColor, nav: color('#settings-nav').backgroundColor, field: color('#web-client-ttl').backgroundColor, ink: color('#settings-nav .kr-nav').color };
      });
      const luminance = rgb => { const [r, g, b] = rgb.match(/\d+(\.\d+)?/g).map(Number); return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255; };
      if (theme === 'dark') { assert.ok(luminance(colours.nav) < 0.3, 'nav is dark'); assert.ok(luminance(colours.field) < 0.3, 'fields are dark'); assert.ok(luminance(colours.ink) > 0.5, 'text is light'); }
      else { assert.ok(luminance(colours.nav) > 0.7, 'nav is light'); assert.ok(luminance(colours.ink) < 0.5, 'text is dark'); }
    }

    // Nothing in Inställningar uses an inline style (the CSP would drop it).
    assert.equal(await page.locator('#admin-view [style]:not(tm-text)').count(), 0);

    // Every section fits a phone, with the menu as a strip along the top.
    await page.setViewportSize({ width: 360, height: 800 });
    for (const section of SECTIONS) {
      await page.evaluate(s => { location.hash = s; }, section);
      await page.waitForFunction(id => !document.getElementById(id).hidden, section);
      const overflow = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, width: innerWidth }));
      assert.ok(overflow.document <= overflow.width + 1, `${section}: ${JSON.stringify(overflow)}`);
    }
    assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('#settings-nav')).flexDirection), 'row');

    // Every text is translated in all five languages, in every section.
    await page.setViewportSize({ width: 1280, height: 900 });
    for (const language of ['en', 'da', 'nb', 'de', 'sv']) {
      await page.evaluate(code => TrainMeetI18n.setLanguage(code), language);
      for (const section of SECTIONS) {
        await page.evaluate(s => { location.hash = s; }, section);
        await page.waitForFunction(id => !document.getElementById(id).hidden, section);
      }
      await page.evaluate(() => { for (const id of ['restore-modal', 'reset-modal', 'users-invite-form-modal', 'runtime-sync-form-modal']) { openModal(id); document.getElementById(id).close(); } });
      assert.deepEqual(await page.evaluate(() => TrainMeetI18n.missing()), [], `untranslated in ${language}`);
    }

    assert.deepEqual(h.errors, []);
    assert.deepEqual(h.violations, []);
    console.log('Settings: nine sections, per-panel Avbryt/Spara, tiles, language, search, QR, themes, phone layout and translations passed.');
  } finally {
    await h.browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
