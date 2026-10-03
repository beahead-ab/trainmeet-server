// Hur den senaste återställningen gick, i Kontrollrummet. Servern startar om
// efter en återställning, så webbläsaren som bad om den ser bara att den går
// ner och kommer tillbaka - också när bytet misslyckades och den gamla
// databasen ligger kvar. Raden i Farozonen och i återställningsrutan är det
// enda stället ägaren får veta det. API:et är fixturen i kr-fixture.cjs.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const WORKED = { backup: 'trainmeet-20261003-060000.db', taken_at: '2026-10-03T06:00:00+00:00', attempted_at: '2026-10-03T06:12:18+00:00', restored: true, problem: null };
const FAILED = { ...WORKED, restored: false, problem: 'kopian går inte att läsa - filen är skadad eller inte en databas' };

const line = (page, id) => page.evaluate((id) => {
  const element = document.getElementById(id);
  return { text: element.textContent.trim(), kind: element.className, shown: element.getBoundingClientRect().height > 0 };
}, id);

async function farozon(lastRestore) {
  const h = await open({ route: '/installningar#farozon', width: 1280, height: 900, lastRestore });
  h.page.setDefaultTimeout(8000);
  await h.page.locator('#farozon').waitFor({ state: 'visible' });
  await h.page.waitForFunction(() => document.querySelector('#restore-list .restore-row'));
  return h;
}

(async () => {
  // Ingen återställning gjord: ingen rad, varken i Farozonen eller i rutan.
  let h = await farozon(null);
  try {
    for (const id of ['restore-last-farozon', 'restore-last']) {
      assert.deepEqual(await line(h.page, id), { text: '', kind: 'form-message', shown: false }, id);
    }
    assert.deepEqual(h.errors, []);
  } finally { await h.browser.close(); }

  // Lyckad: när, och vilken kopia som lades tillbaka.
  h = await farozon(WORKED);
  try {
    const shown = await line(h.page, 'restore-last-farozon');
    assert.match(shown.text, /^Senaste återställningen .+ lade tillbaka kopian från .+\.$/);
    assert.equal(shown.kind, 'form-message success');
    assert.ok(shown.shown, 'raden syns i Farozonen utan att rutan öppnas');
    await h.page.locator('#farozon [data-open-modal="restore-modal"]').click();
    await h.page.locator('#restore-modal').waitFor({ state: 'visible' });
    const inDialog = await line(h.page, 'restore-last');
    assert.equal(inDialog.text, shown.text);
    assert.ok(inDialog.shown, 'samma rad överst i återställningsrutan');
    assert.deepEqual(h.errors, []);
    assert.deepEqual(h.violations, []);
  } finally { await h.browser.close(); }

  // Misslyckad: skälet syns i felfärg, och att databasen är orörd.
  h = await farozon(FAILED);
  try {
    const shown = await line(h.page, 'restore-last-farozon');
    assert.match(shown.text, /^Senaste återställningen .+ misslyckades: kopian går inte att läsa - filen är skadad eller inte en databas\. Databasen är som före försöket\.$/);
    assert.equal(shown.kind, 'form-message error');
    assert.equal(await h.page.locator('#restore-last-farozon').evaluate(e => getComputedStyle(e).color),
      await h.page.evaluate(() => { const probe = document.createElement('span'); probe.style.color = 'var(--kr-red)'; document.body.append(probe); const c = getComputedStyle(probe).color; probe.remove(); return c; }),
      'felet har Kontrollrummets röda färg');

    // Språkbyte: den fasta texten översätts, serverns skäl står kvar som det är.
    await h.page.evaluate(() => globalThis.TrainMeetI18n.setLanguage('en', false));
    const english = await line(h.page, 'restore-last-farozon');
    assert.match(english.text, /^The last restore .+ failed: kopian går inte att läsa - filen är skadad eller inte en databas\. The database is as it was before the attempt\.$/);
    assert.deepEqual(h.errors, []);
  } finally { await h.browser.close(); }

  console.log('restore-last: ok');
})().catch((error) => { console.error(error); process.exit(1); });
