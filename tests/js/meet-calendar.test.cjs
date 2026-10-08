// Träffens dagar och tidsmaskinen (Casper 2026-10-08): Drift visar "Dag 2 · Lör",
// knappen Tidsmaskin… väljer dag och tid och skickar dem med träffens
// generation, och startdagen väljs under Inställningar → Träff och Cloud.
// Kör: node tests/js/meet-calendar.test.cjs
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const calendar = { start_day: 'Fre', day_number: 2, weekday: 'Lör', week: ['Mån', 'Tis', 'Ons', 'Tor', 'Fre', 'Lör', 'Sön'] };

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
    assert.ok(days.length >= 9, 'a week ahead of today');
    assert.equal(await page.locator('#time-machine-day').inputValue(), '2');
    assert.match(await page.locator('#time-machine-time').inputValue(), /^\d\d:\d\d$/);
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
    await page.locator('#meet-start-day').selectOption('Lör');
    await page.locator('#meet-calendar-form [data-save-submit]').click();
    await page.waitForFunction(() => document.querySelector('#meet-calendar-form [data-save-state]')?.textContent.trim() === 'Sparat');
    assert.deepEqual(sent[1], ['calendar', { start_day: 'Lör', meet_generation: 7 }]);
    assert.deepEqual(view.errors, []);
    assert.deepEqual(view.violations, []);
    console.log('meet-calendar: ok');
  } finally { await view.browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
