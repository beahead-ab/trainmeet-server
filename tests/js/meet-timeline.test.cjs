// Träffens tid på Drift (Casper 2026-10-12): dygnen på rad under klockan. Admin
// drar i tidslinjen och ser vart hon hoppar och hur tågen kommer att stå där;
// inget skickas förrän hon trycker Hoppa. Efteråt finns Ångra.
// Kör: node tests/js/meet-timeline.test.cjs
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const at = (h, m = 0) => h * 3600 + m * 60;
const calendar = { start_day: 'Fre', day_number: 2, weekday: 'Lör', week: ['Mån', 'Tis', 'Ons', 'Tor', 'Fre', 'Lör', 'Sön'],
  change_time: '05:20', change_auto: true, change_time_set: null, day_change_mode: 'manual', due: false, waiting: false,
  next_day: { day_number: 3, weekday: 'Sön', time: '05:20' } };
const day = (n, weekday) => ({ day_number: n, weekday, start: at(5, 20), end: 86400 + at(5, 20), first: at(6, 20), last: at(23, 40),
  density: Array.from({ length: 48 }, (_, i) => (i > 1 && i < 38 ? 1 + (i % 5) : 0)) });
const timeline = (over = {}) => ({ supported: true, slot_seconds: 1800, meet_generation: 7,
  days: [day(1, 'Fre'), day(2, 'Lör'), day(3, 'Sön')],
  now: { day_number: 2, seconds: at(10, 33), running: false, speed: 1, external: false }, calendar, undo: null, ...over });

async function setRange(page, dayIndex, seconds) {
  // Dag 1 börjar på axelns noll; varje dag är ett dygn långt här.
  const x = dayIndex * 86400 + seconds - at(5, 20);
  await page.locator('#drift-timeline-range').evaluate((range, value) => {
    range.value = String(value);
    range.dispatchEvent(new Event('input', { bubbles: true }));
  }, x);
}

(async () => {
  const sent = [];
  const view = await open({ route: '/drift', calendar, running: false, api: {
    '/v1/runtime/timeline': () => ({ data: timeline() }),
    '/v1/runtime/time-machine/preview': (request, url) => {
      sent.push(['preview', url.searchParams.get('day_number'), url.searchParams.get('seconds')]);
      return { data: { day_number: Number(url.searchParams.get('day_number')), weekday: 'Lör', time: '14:20', on_line: 4, at_station: 11, not_started: 6, finished: 9, trains: 30,
        next: { train_number: '1207', time: '14:22', from: 'Björkvik', to: 'Västerhamn' } } };
    },
    '/v1/runtime/time-machine': (request) => {
      sent.push(['time-machine', JSON.parse(request.postData())]);
      return { data: { day_number: 2, active_day: 'Lör', clock: { time: '05:20:00' }, backup: 'trainmeet-20261012-101500-tidsmaskin.db',
        undo: { backup: 'trainmeet-20261012-101500-tidsmaskin.db', kind: 'tidsmaskin', from: { day_number: 2, weekday: 'Lör', time: '10:33' }, seconds_left: 1800 } } };
    },
    '/v1/runtime/undo-jump': (request) => {
      sent.push(['undo', JSON.parse(request.postData())]);
      return { status: 202, data: { status: 'restoring', message: 'Hoppet ångras: Dag 2 · Lör kl. 10:33 läggs tillbaka och servern startar om.' } };
    },
  } });
  const { page, errors, violations } = view;
  page.setDefaultTimeout(8000);
  try {
    // Dygnen på rad, var träffen står och hur nytt dygn går till.
    await page.locator('#drift-timeline-days .kr-tl__day').first().waitFor();
    assert.deepEqual(await page.locator('#drift-timeline-days .kr-tl__day b').allTextContents(), ['Dag 1 · Fre', 'Dag 2 · Lör', 'Dag 3 · Sön']);
    assert.deepEqual(await page.locator('#drift-timeline-days .kr-tl__day span').allTextContents(), ['klar', 'pågår', 'kommer']);
    assert.equal(await page.locator('#drift-timeline-mode').textContent(), 'Nytt trafikdygn: manuellt');
    assert.equal(await page.locator('.kr-tl__now-pill').textContent(), 'NU 10:33');
    assert.equal(await page.locator('#drift-timeline-plan').isVisible(), false, 'nothing planned yet');
    assert.equal(await page.locator('#drift-timeline-hint').isVisible(), true);
    assert.equal(await page.locator('#time-machine-open').isVisible(), true, 'exact day and time still there');
    assert.equal(await page.locator('#new-day-open').isVisible(), true, 'manual: Starta ny dag in the timeline');
    assert.equal(await page.locator('.kr-tl__shift-label').count(), 2, 'a day change between each day');

    // Dra till Dag 2 kl. 14:20: kortet säger vart, hur långt och hur tågen står.
    await setRange(page, 1, at(14, 20));
    await page.locator('#drift-timeline-plan').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#drift-timeline-plan-day').textContent(), 'Dag 2 · Lör');
    assert.equal(await page.locator('#drift-timeline-plan-time').textContent(), '14:20');
    assert.equal(await page.locator('#drift-timeline-plan-delta').textContent(), '3 h 47 min framåt från nu');
    await page.waitForFunction(() => document.querySelector('#drift-timeline-on-line').textContent === '4');
    assert.deepEqual(await Promise.all(['#drift-timeline-at-station', '#drift-timeline-not-started', '#drift-timeline-finished']
      .map((id) => page.locator(id).textContent())), ['11', '6', '9']);
    assert.match(await page.locator('#drift-timeline-next').textContent(), /^Nästa avgång 14:22 · tåg 1207 Björkvik → Västerhamn\./);
    assert.equal(await page.locator('#drift-timeline-jump').textContent(), 'Hoppa till Dag 2 · Lör kl. 14:20');
    assert.equal(await page.locator('#drift-timeline-plan-newday').isVisible(), false, 'same day: no new-day shortcut');
    assert.equal(await page.locator('.kr-tl__target-pill').textContent(), '14:20');
    assert.ok(await page.locator('.kr-tl__bar.is-span').count() > 5, 'the hours jumped over are marked');
    assert.equal(sent.filter(([kind]) => kind === 'time-machine').length, 0, 'dragging only plans');

    // Snabbval: ett dygn fram föreslår Starta ny dag i stället (manuellt läge).
    await page.locator('[data-tl-step="1"]').click();
    assert.equal(await page.locator('#drift-timeline-plan-day').textContent(), 'Dag 3 · Sön');
    assert.equal(await page.locator('#drift-timeline-plan-time').textContent(), '14:20');
    assert.equal(await page.locator('#drift-timeline-newday').textContent(), 'Starta Dag 3 · Sön i stället…');
    await page.locator('[data-tl-step="-1"]').click();
    await page.locator('[data-tl-step="-1"]').click();
    assert.equal(await page.locator('#drift-timeline-plan-day').textContent(), 'Dag 1 · Fre');
    assert.match(await page.locator('#drift-timeline-plan-delta').textContent(), /^1 dygn \d+ h \d+ min bakåt från nu$|^20 h 13 min bakåt från nu$/);
    await page.locator('#drift-timeline-cancel').click();
    assert.equal(await page.locator('#drift-timeline-plan').isVisible(), false, 'Avbryt goes back to now');
    await page.locator('[data-tl-step="start"]').click();
    assert.equal(await page.locator('#drift-timeline-plan-time').textContent(), '05:20', 'the start of the day is its day change');
    await page.locator('[data-tl-step="first"]').click();
    assert.equal(await page.locator('#drift-timeline-plan-time').textContent(), '06:10', 'ten minutes before the first train');
    await page.locator('[data-tl-step="start"]').click();

    // Hoppa: dag och sekunder skickas med träffens generation; sedan finns Ångra.
    await page.locator('#drift-timeline-jump').click();
    await page.locator('#drift-timeline-undo').waitFor({ state: 'visible' });
    assert.deepEqual(sent.find(([kind]) => kind === 'time-machine')[1], { day_number: 2, seconds: at(5, 20), meet_generation: 7 });
    assert.equal(await page.locator('#drift-timeline-undo-text').textContent(),
      'Klart: Dag 2 · Lör kl. 05:20. Alla tåg står där tidtabellen säger. En säkerhetskopia togs först.');
    assert.equal(await page.locator('#drift-timeline-undo-open').textContent(), 'Ångra – tillbaka till Dag 2 · Lör 10:33');
    assert.equal(await page.locator('#drift-timeline-plan').isVisible(), false);
    // Ångra frågar först och säger att servern startar om.
    await page.locator('#drift-timeline-undo-open').click();
    await page.locator('#drift-timeline-undo-confirm').waitFor({ state: 'visible' });
    assert.equal(sent.filter(([kind]) => kind === 'undo').length, 0, 'asks first');
    await page.locator('#drift-timeline-undo-yes').click();
    await page.waitForFunction(() => /Hoppet ångras/.test(document.querySelector('#drift-timeline-undo-message')?.textContent || ''));
    assert.deepEqual(sent.find(([kind]) => kind === 'undo')[1], { backup: 'trainmeet-20261012-101500-tidsmaskin.db', meet_generation: 7 });
    assert.deepEqual(errors, []);
    assert.deepEqual(violations, []);
  } finally {
    await view.browser.close();
  }

  // Dygnsskiftet har passerats i manuellt läge: tidslinjen säger varför tågen ser ut som i går.
  const due = await open({ route: '/drift', calendar: { ...calendar, day_number: 1, weekday: 'Fre', due: true }, running: false, width: 390, height: 844, api: {
    '/v1/runtime/timeline': () => ({ data: timeline({ calendar: { ...calendar, day_number: 1, weekday: 'Fre', due: true },
      now: { day_number: 1, seconds: 86400 + at(10, 33), running: false, speed: 1, external: false } }) }),
  } });
  try {
    const { page } = due;
    page.setDefaultTimeout(8000);
    await page.locator('#drift-timeline-due').waitFor({ state: 'visible' });
    assert.match(await page.locator('#drift-timeline-due-text').textContent(), /^Dygnsskiftet har passerats\. Träffen står kvar på Dag 1 · Fre/);
    assert.equal(await page.locator('.kr-tl__due-zone').count(), 1, 'the day not started is hatched');
    assert.equal(await page.locator('.kr-tl__now-pill').textContent(), 'NU 10:33');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 0, `no sideways scroll on a phone (${overflow}px)`);
    assert.deepEqual(due.errors, []);
    assert.deepEqual(due.violations, []);
  } finally {
    await due.browser.close();
  }

  // Ett svar utan tidslinje (en US-träff) gömmer panelen.
  const us = await open({ route: '/drift', calendar, api: { '/v1/runtime/timeline': () => ({ data: { supported: false } }) } });
  try {
    await us.page.locator('#drift-timeline').waitFor({ state: 'hidden', timeout: 5000 });
    await us.page.waitForTimeout(3500);
    assert.equal(await us.page.locator('#drift-timeline').isVisible(), false, 'and stays hidden');
  } finally {
    await us.browser.close();
  }
  console.log('meet-timeline: ok');
})().catch((error) => { console.error(error); process.exit(1); });
