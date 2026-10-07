// När servern inte svarar med JSON. Casper såg "The string did not match the
// expected pattern." på klockraden i Drift (2026-10-07): Safaris text när ett
// svar inte är JSON, här Cloudflares felsida medan servern startade om. Nu står
// vad som hände och vad man gör, på sidans språk, och inget av webbläsarens eget.
// Kör: node tests/js/server-unreachable.test.cjs
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const PAGE = '<!DOCTYPE html><html><head><title>502 Bad gateway</title></head><body>Bad gateway · Cloudflare</body></html>';
const clockPost = (reply) => (request) => (request.method() === 'POST' ? reply : undefined);

async function pressStart({ reply, lang = 'sv' }) {
  const drift = await open({ route: '/drift', running: false, lang, api: { '/v1/clock': clockPost(reply) } });
  try {
    const start = drift.page.locator('#overview-clock-start');
    await start.waitFor({ state: 'visible' });
    await start.click();
    const message = drift.page.locator('#overview-clock-message');
    await drift.page.waitForFunction(() => {
      const text = document.querySelector('#overview-clock-message')?.textContent || '';
      return text && !/Uppdaterar|Updating/.test(text);
    });
    return { text: (await message.textContent()).trim(), kind: await message.getAttribute('class'), errors: drift.errors };
  } finally {
    await drift.browser.close();
  }
}

(async () => {
  const expected = 'Servern svarade inte. Den kan hålla på att starta om – försök igen om en stund.';
  // Proxyns HTML-sida i stället för JSON.
  const page = await pressStart({ reply: { status: 502, raw: PAGE } });
  assert.equal(page.text, expected);
  assert.match(page.kind, /error/);
  // Inget svar alls.
  const dropped = await pressStart({ reply: { abort: true } });
  assert.equal(dropped.text, expected);
  // Ett 200-svar som inte är JSON är lika obegripligt för sidan.
  const odd = await pressStart({ reply: { status: 200, raw: 'OK' } });
  assert.equal(odd.text, expected);
  // På engelska.
  const english = await pressStart({ reply: { status: 502, raw: PAGE }, lang: 'en' });
  assert.equal(english.text, 'The server did not respond. It may be restarting – try again in a moment.');
  for (const run of [page, dropped, odd, english]) {
    assert.doesNotMatch(run.text, /pattern|JSON|token|Load failed|Failed to fetch/i);
  }
  console.log('server-unreachable: ok');
})().catch((error) => { console.error(error); process.exitCode = 1; });
