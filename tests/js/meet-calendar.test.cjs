// Träffens dagar och tidsmaskinen (Casper 2026-10-08): Drift visar "Dag 2 · Lör",
// knappen Tidsmaskin… väljer dag och tid och skickar dem med träffens
// generation, och startdagen och dygnsskiftets tid väljs under Inställningar →
// Träff och Cloud. Vid dygnsskiftet säger en toast att alla tåg står på sin
// utgångspunkt; den visas en gång, och medan skiftet väntar säger den det.
// Kör: node tests/js/meet-calendar.test.cjs
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const calendar = { start_day: 'Fre', day_number: 2, weekday: 'Lör', week: ['Mån', 'Tis', 'Ons', 'Tor', 'Fre', 'Lör', 'Sön'],
  change_time: '05:20', change_auto: true, change_time_set: null };

(async () => {
  const sent = [];
  const view = await open({ route: '/drift', calendar, api: {
    '/v1/runtime/time-machine': (request) => { sent.push(['time-machine', JSON.parse(request.postData())]); return { data: { day_number: 3, active_day: 'Sön', clock: { time: '14:00:00' } } }; },
    '/v1/runtime/calendar': (request) => { sent.push(['calendar', JSON.parse(request.postData())]); return { data: { ...calendar, start_day: 'Lör', weekday: 'Sön', changed: true } }; },
  } });
  const { page } = view;
  page.setDefaultTimeout(8000);
  try {
    await page.waitForFunction(() => document.querySelector('#overview-day')?.textContent === 'Dag 2 · Lör');
    // Tidsmaskinen: dagarna med veckodag, dagens dag och tid förvalda.
    await page.locator('#time-machine-open').click();
    await page.locator('#time-machine-modal[open]').waitFor();
    const days = await page.locator('#time-machine-day option').allTextContents();
    assert.deepEqual(days.slice(0, 4), ['Dag 1 · Fre', 'Dag 2 · Lör', 'Dag 3 · Sön', 'Dag 4 · Mån']);
    assert.ok(days.length >= 16, 'two weeks ahead of today');
    assert.equal(await page.locator('#time-machine-day').inputValue(), '2');
    assert.match(await page.locator('#time-machine-time').inputValue(), /^\d\d:\d\d$/);
    // Spola dygn: ett dygn fram och bak, och till nästa dygnsskifte (Dag 3 kl. 05:20).
    const step = (name) => page.locator('#time-machine-form .time-machine-steps button', { hasText: name }).click();
    await step('+1 dygn');
    await step('+1 dygn');
    assert.equal(await page.locator('#time-machine-day').inputValue(), '4');
    await step('−1 dygn');
    assert.equal(await page.locator('#time-machine-day').inputValue(), '3');
    await page.locator('#time-machine-day').selectOption('1');
    await step('−1 dygn');
    assert.equal(await page.locator('#time-machine-day').inputValue(), '1', 'not before the first day');
    await step('Nästa dygnsskifte');
    assert.deepEqual([await page.locator('#time-machine-day').inputValue(), await page.locator('#time-machine-time').inputValue()], ['3', '05:20']);
    await page.locator('#time-machine-day').selectOption('16');
    for (let n = 0; n < 3; n += 1) await step('+1 dygn');
    assert.equal(await page.locator('#time-machine-day').inputValue(), '19', 'the list grows as days are stepped');
    assert.equal(sent.length, 0, 'the buttons only fill in; Hoppa dit jumps');
    await page.locator('#time-machine-day').selectOption('3');
    await page.locator('#time-machine-time').fill('14:00');
    await page.locator('#time-machine-form button[type=submit]').click();
    await page.waitForFunction(() => !document.querySelector('#time-machine-modal').open);
    assert.deepEqual(sent[0], ['time-machine', { day_number: 3, time: '14:00', meet_generation: 7 }]);
    await page.waitForFunction(() => /Dag 3 · Sön kl\. 14:00/.test(document.querySelector('#overview-clock-message')?.textContent || ''));

    // Startdagen under Inställningar → Träff och Cloud.
    await page.goto(new URL('/installningar#traff', page.url()).href);
    await page.locator('#meet-calendar-form').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#meet-start-day').inputValue(), 'Fre');
    assert.deepEqual(await page.locator('#meet-start-day option').allTextContents(), ['Alla dagar (Dagl)', 'Mån', 'Tis', 'Ons', 'Tor', 'Fre', 'Lör', 'Sön']);
    assert.equal(await page.locator('#meet-calendar-note').textContent(), 'I dag: Dag 2 · Lör');
    assert.equal(await page.locator('#meet-day-change').inputValue(), '', 'empty: automatic');
    assert.equal(await page.locator('#meet-day-change').getAttribute('placeholder'), '05:20');
    assert.equal(await page.locator('#meet-day-change-note').textContent(), 'Tomt: automatiskt, en timme före första tåget (05:20)');
    await page.locator('#meet-start-day').selectOption('Lör');
    await page.locator('#meet-day-change').fill('04:15');
    await page.locator('#meet-calendar-form [data-save-submit]').click();
    await page.waitForFunction(() => document.querySelector('#meet-calendar-form [data-save-state]')?.textContent.trim() === 'Sparat');
    assert.equal(await page.locator('#meet-day-change-mode').inputValue(), 'manual', 'manual is the default');
    assert.match(await page.locator('#meet-day-change-mode-note').textContent(), /^Admin startar nästa dag med Starta ny dag på Drift/);
    assert.deepEqual(sent[1], ['calendar', { start_day: 'Lör', change_time: '04:15', day_change_mode: 'manual', meet_generation: 7 }]);
    assert.equal(await page.locator('.tm-day-change').isVisible(), false, 'no day change, no toast');
    assert.deepEqual(view.errors, []);
    assert.deepEqual(view.violations, []);
  } finally { await view.browser.close(); }

  // Dygnsskiftet nyss: toasten på Drift och i deltagarvyn, en gång.
  const changed = { ...calendar, change_time: '05:00', last_change: { kind: 'day_change', day_number: 2, weekday: 'Lör', at: new Date(Date.now() - 10000).toISOString() } };
  for (const route of ['/drift', '/']) {
    const fresh = await open({ route, calendar: changed });
    try {
      const toast = fresh.page.locator('.tm-day-change');
      await toast.waitFor({ state: 'visible', timeout: 8000 });
      assert.equal(await toast.textContent(), 'Nytt trafikdygn: Dag 2 · Lör. Alla tåg står på sin utgångspunkt och statusarna är nollställda.');
      assert.equal(await toast.getAttribute('role'), 'status');
      const box = await toast.boundingBox(), width = fresh.page.viewportSize().width;
      assert.ok(box.x >= 0 && box.x + box.width <= width, 'inside the screen');
      await fresh.page.reload();
      await fresh.page.waitForTimeout(1500);
      assert.equal(await fresh.page.locator('.tm-day-change').isVisible(), false, 'the same change is shown once');
      assert.deepEqual(fresh.errors, []);
      assert.deepEqual(fresh.violations, [], 'no CSP violations');
    } finally { await fresh.browser.close(); }
  }

  // En sida som öppnas långt efter skiftet visar ingenting.
  const old = await open({ route: '/drift', calendar: { ...changed, last_change: { ...changed.last_change, at: new Date(Date.now() - 600000).toISOString() } } });
  try {
    await old.page.waitForFunction(() => document.querySelector('#overview-day')?.textContent === 'Dag 2 · Lör');
    await old.page.waitForTimeout(1500);
    assert.equal(await old.page.locator('.tm-day-change').isVisible(), false, 'ten minutes later: no toast');
  } finally { await old.browser.close(); }

  // Skiftet väntar på ett tåg ute på linjen.
  const waiting = await open({ route: '/', calendar: { ...calendar, waiting: true } });
  try {
    const toast = waiting.page.locator('.tm-day-change');
    await toast.waitFor({ state: 'visible', timeout: 8000 });
    assert.equal(await toast.textContent(), 'Dygnsskiftet väntar på tåg som är ute på linjen.');
  } finally { await waiting.browser.close(); }

  // Starta ny dag (Casper 2026-10-09): i manuellt läge finns knappen på Drift,
  // och när dygnsskiftet har passerats påminner Drift om det.
  const manual = { ...calendar, day_change_mode: 'manual', due: true, next_day: { day_number: 3, weekday: 'Sön', time: '00:35' } };
  const started = [];
  let refuse = true;
  const newDay = await open({ route: '/drift', calendar: manual, api: {
    '/v1/runtime/new-day': (request) => {
      started.push(JSON.parse(request.postData()));
      if (refuse) { refuse = false; return { status: 409, data: { error: 'day_still_running', message: 'Tåg 101 är ute på linjen. Vänta tills de har kommit fram, eller använd Tidsmaskinen.' } }; }
      return { data: { day_number: 3, active_day: 'Sön', clock: { time: '05:40:00' } } };
    },
    '/v1/runtime/calendar': (request) => { sent.push(['calendar', JSON.parse(request.postData())]); return { data: { ...manual, day_change_mode: 'auto' } }; },
  } });
  try {
    const { page } = newDay;
    page.setDefaultTimeout(8000);
    const due = page.locator('#new-day-due');
    await due.waitFor({ state: 'visible' });
    assert.equal(await due.textContent(), 'Dygnsskiftet har passerats · Starta Dag 3 · Sön');
    assert.equal(await page.locator('#new-day-open').isVisible(), true);
    await due.click();
    await page.locator('#new-day-modal[open]').waitFor();
    assert.match(await page.locator('#new-day-text').textContent(), /^Träffen går till Dag 3 · Sön med den dagens tidtabell\..*En säkerhetskopia tas först\.$/);
    assert.equal(await page.locator('#new-day-submit').textContent(), 'Starta Dag 3 · Sön');
    assert.equal(await page.locator('#new-day-time').inputValue(), '00:35', 'the server suggests the time, not the day change');
    await page.locator('#new-day-time').fill('05:40');
    await page.locator('#new-day-submit').click();
    // Ett tåg ute på linjen: dialogen står kvar med skälet.
    await page.waitForFunction(() => /Tåg 101 är ute på linjen/.test(document.querySelector('#new-day-message')?.textContent || ''));
    assert.equal(await page.locator('#new-day-modal').evaluate((dialog) => dialog.open), true);
    await page.locator('#new-day-submit').click();
    await page.waitForFunction(() => !document.querySelector('#new-day-modal').open);
    assert.deepEqual(started, [{ time: '05:40', meet_generation: 7 }, { time: '05:40', meet_generation: 7 }]);
    await page.waitForFunction(() => /Dag 3 · Sön har börjat kl\. 05:40\./.test(document.querySelector('#overview-clock-message')?.textContent || ''));
    const box = await due.boundingBox(), width = page.viewportSize().width;
    assert.ok(box.x >= 0 && box.x + box.width <= width, 'the reminder fits');

    // Inställningen: Automatiskt vid dygnsskiftet (dygnet runt).
    await page.goto(new URL('/installningar#traff', page.url()).href);
    await page.locator('#meet-calendar-form').waitFor({ state: 'visible' });
    assert.deepEqual(await page.locator('#meet-day-change-mode option').allTextContents(), ['Manuellt (Starta ny dag)', 'Automatiskt vid dygnsskiftet (dygnet runt)']);
    await page.locator('#meet-day-change-mode').selectOption('auto');
    assert.equal(await page.locator('#meet-day-change-mode-note').textContent(), 'Träffen går till nästa dag av sig själv vid dygnsskiftet, när inga tåg är ute på linjen.');
    await page.locator('#meet-calendar-form [data-save-submit]').click();
    await page.waitForFunction(() => document.querySelector('#meet-calendar-form [data-save-state]')?.textContent.trim() === 'Sparat');
    assert.equal(sent.at(-1)[1].day_change_mode, 'auto');
    assert.deepEqual(newDay.errors, []);
    assert.deepEqual(newDay.violations, []);
  } finally { await newDay.browser.close(); }

  // Automatiskt läge: ingen knapp och ingen påminnelse.
  const auto = await open({ route: '/drift', calendar: { ...manual, day_change_mode: 'auto', due: false } });
  try {
    await auto.page.waitForFunction(() => document.querySelector('#overview-day')?.textContent === 'Dag 2 · Lör');
    // Drift syns (Tidsmaskin finns alltid), men inte Starta ny dag.
    await auto.page.locator('#time-machine-open').waitFor({ state: 'visible', timeout: 8000 });
    assert.equal(await auto.page.locator('#new-day-open').isVisible(), false);
    assert.equal(await auto.page.locator('#new-day-due').isVisible(), false);
  } finally { await auto.browser.close(); }

  // Manuellt men före dygnsskiftet: knappen men ingen påminnelse.
  const early = await open({ route: '/drift', calendar: { ...manual, due: false } });
  try {
    await early.page.locator('#new-day-open').waitFor({ state: 'visible', timeout: 8000 });
    assert.equal(await early.page.locator('#new-day-due').isVisible(), false);
  } finally { await early.browser.close(); }

  // Telefon: med fyra knappar bryts raden, och Tidsmaskin klipps inte bort.
  const phone = await open({ route: '/drift', width: 390, height: 844, calendar: manual });
  try {
    await phone.page.locator('#new-day-due').waitFor({ state: 'visible', timeout: 8000 });
    const clipped = await phone.page.evaluate(() => {
      const panel = document.querySelector('#drift-clock').getBoundingClientRect();
      return [...document.querySelectorAll('.kr-clock-actions button:not([hidden]), #new-day-due')]
        .filter((node) => { const box = node.getBoundingClientRect(); return box.left < panel.left || box.right > panel.right; }).map((node) => node.id);
    });
    assert.deepEqual(clipped, [], 'every clock button and the reminder inside the panel');
    assert.equal(await phone.page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  } finally { await phone.browser.close(); }
  console.log('meet-calendar: ok');
})().catch((error) => { console.error(error); process.exitCode = 1; });
