// Inloggningen i Kontrollrummets formspråk: samma botten och panel som resten,
// i mörkt och ljust läge, och på en telefon utan sidledes rullning. Inloggning
// och inbjudan visas aldrig samtidigt. API:et är fixturen i kr-fixture.cjs.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const look = (page) => page.evaluate(() => {
  const card = document.querySelector('#login');
  const box = card.getBoundingClientRect();
  const shown = (selector) => getComputedStyle(document.querySelector(selector)).display !== 'none';
  return { body: getComputedStyle(document.body).backgroundColor, card: getComputedStyle(card).backgroundColor,
    left: Math.round(box.left), right: Math.round(innerWidth - box.right), overflow: document.documentElement.scrollWidth > innerWidth + 1,
    login: shown('#login-form'), redeem: shown('#redeem-form'), error: Math.round(document.querySelector('#login-error').getBoundingClientRect().height) };
});

(async () => {
  for (const [theme, width, bg, panel] of [['dark', 1280, 'rgb(13, 15, 19)', 'rgb(21, 24, 30)'], ['light', 1280, 'rgb(236, 235, 230)', 'rgb(255, 255, 255)'],
                                          ['dark', 390, 'rgb(13, 15, 19)', 'rgb(21, 24, 30)']]) {
    const h = await open({ route: '/login', width, height: 860, theme, loggedOut: true });
    const { page } = h;
    page.setDefaultTimeout(10000);
    try {
      await page.locator('#login').waitFor({ state: 'visible' });
      const before = await look(page);
      assert.equal(before.body, bg, `${theme}: the page has the control room background`);
      assert.equal(before.card, panel, `${theme}: the card is a control room panel`);
      assert.equal(before.overflow, false, `${width}: no sideways scrolling`);
      assert.ok(Math.abs(before.left - before.right) <= 1 && before.left >= 15, `${width}: the card is centred with room around it`);
      assert.deepEqual([before.login, before.redeem, before.error], [true, false, 0], 'login shown, invite hidden, no empty error line');
      await page.locator('#redeem-open').click();
      const invite = await look(page);
      assert.deepEqual([invite.login, invite.redeem], [false, true], 'the invite replaces the login');
      assert.deepEqual(h.errors, []);
      assert.deepEqual(h.violations, []);
    } finally { await h.browser.close(); }
  }
  console.log('login-look: ok');
})().catch((error) => { console.error(error); process.exit(1); });
