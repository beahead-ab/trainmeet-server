// Issue #209: the public guest can browse all read-only screens and share a meet.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

(async () => {
  for (const [region, expected] of [
    ['eu', ['clock', 'dashboard', 'topology', 'graph']],
    ['us', ['clock', 'territories']],
  ]) {
    for (const width of [390, 1440]) {
      const { page, browser, errors, violations } = await open({
        route: '/', width, height: 900, loggedOut: true, region,
      });
      try {
        await page.locator('#participant-view').waitFor({ state: 'visible' });
        page.setDefaultTimeout(10000);
        const writes = [];
        page.on('request', request => {
          if (request.url().includes('/v1/') && request.method() !== 'GET') writes.push(request.url());
        });
        const anchors = page.locator('#pv-screens-list a');
        await page.waitForFunction((n) => document.querySelectorAll('#pv-screens-list a').length === n, expected.length);
        const hrefs = await anchors.evaluateAll((items) => items.map((link) => new URL(link.href).pathname));
        assert.deepEqual(hrefs, expected.map((name) => '/display/' + name));
        assert.equal(await page.locator('#pv-screens-menu').getAttribute('open'), null, 'compact menu starts closed');
        await page.locator('#pv-screens-menu summary').click();
        assert.equal(await anchors.first().isVisible(), true);
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#pv-screens-menu').getAttribute('open'), null);
        assert.equal(await page.locator('#login').isVisible(), false);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);

        await page.locator('#pv-share-open').click();
        await page.locator('#pv-share-card[open]').waitFor();
        assert.equal(await page.locator('#pv-share-codes svg').count(), 1, 'No configured meet Wi-Fi: link QR only');
        assert.match(await page.locator('#pv-share-codes').innerText(), /Wi-Fi har inte lagts in/);
        assert.equal(await page.locator('#pv-share-link').inputValue(), 'http://127.0.0.1:9999/');
        await page.locator('#pv-share-close').click();
        assert.equal(await page.locator('#pv-share-card[open]').count(), 0);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);

        // Browse every link via the same public routes. No login or admin writes.
        for (const name of expected) {
          await page.goto('http://127.0.0.1:9999/');
          await page.locator('#pv-screens-menu summary').click();
          await page.locator('#pv-screens-list a[href="/display/' + name + '"]').click();
          await page.locator('#display-app').waitFor({ state: 'visible' });
          await page.locator('#display-switch-list a[href="/display/' + name + '"]').waitFor({ state: 'attached' });
          assert.equal(await page.locator('#login').isVisible(), false, 'screen ' + name + ' stays public');
          assert.equal(await page.locator('#display-app').getAttribute('data-read-only'), 'true');
          assert.equal(await page.locator('#display-app').getAttribute('data-chrome'), 'fullscreen');
          await page.waitForFunction(() => document.fullscreenElement?.id === 'display-app');
          for (const setting of ['clock-style', 'clock-seconds', 'graph-window', 'graph-tracks', 'deviation-level', 'train-select', 'theme', 'fullscreen']) {
            assert.equal(await page.locator('#display-' + setting).isVisible(), false, name + ': no ' + setting + ' setting');
          }
          assert.equal(await page.locator('#display-guest-back').isVisible(), true);
          if (width === 1440 && ['topology', 'graph'].includes(name)) {
            assert.equal(await page.evaluate(() => document.querySelector('#display-guest-back').getBoundingClientRect().bottom
              <= document.querySelector('#screen-meet').getBoundingClientRect().top), true, 'navigation does not cover the meet title');
          }
          // Screen switches stay in the document and keep native fullscreen.
          await page.locator('#display-switch summary').click();
          const next = expected[(expected.indexOf(name) + 1) % expected.length];
          await page.locator('#display-switch-list a[href="/display/' + next + '"]').click();
          await page.waitForURL('**/display/' + next);
          assert.equal(await page.evaluate(() => document.fullscreenElement?.id), 'display-app');
          // Browser Back returns to the previous view, then to the participant page.
          await page.goBack();
          await page.waitForURL('**/display/' + name);
          await page.goBack();
          await page.locator('#participant-view').waitFor({ state: 'visible' });
          assert.equal(await page.locator('#display-app').isVisible(), false);
          assert.equal(await page.evaluate(() => document.fullscreenElement), null);
          await page.goForward();
          await page.locator('#display-app').waitFor({ state: 'visible' });
          await page.locator('#display-guest-back').click();
          await page.locator('#participant-view').waitFor({ state: 'visible' });
        }
        // A bookmarked public screen also follows the meet, ignoring old local choices.
        await page.evaluate(() => {
          localStorage.setItem('trainmeet.displayClockStyle', 'analog');
          localStorage.setItem('trainmeet.displayClockSeconds', 'off');
          localStorage.setItem('trainmeet.displayGraphWindow', '1440');
        });
        await page.goto('http://127.0.0.1:9999/display/clock');
        await page.locator('#clock-view .clock-digital').waitFor();
        await page.locator('#clock-view').click();
        assert.equal(await page.locator('#clock-view .clock-digital').getAttribute('data-seconds'), 'true');
        assert.equal(await page.evaluate(() => localStorage.getItem('trainmeet.displayClockSeconds')), 'off');
        assert.equal(await page.locator('#display-clock-style').isVisible(), false);
        await page.locator('#display-guest-back').click();
        await page.locator('#participant-view').waitFor({ state: 'visible' });
        assert.deepEqual(writes, [], 'guest browsing sends no write requests');
        assert.equal(await page.locator('#login').isVisible(), false);
        assert.deepEqual(errors, [], 'browser errors');
        assert.deepEqual(violations, [], 'CSP violations');
      } finally {
        await browser.close();
      }
    }
  }
  // Local HTTP and phones may reject native fullscreen. Fill the viewport
  // anyway, keep navigation, and let the guest return without a second action.
  const fallback = await open({ route: '/', width: 320, height: 844, loggedOut: true });
  try {
    const {page} = fallback;
    await page.locator('#pv-screens-list a').first().waitFor({state:'attached'});
    await page.evaluate(() => { Element.prototype.requestFullscreen = () => Promise.reject(new Error('Unavailable')); });
    await page.locator('#pv-screens-menu summary').click();
    await page.locator('#pv-screens-list a[href="/display/clock"]').click();
    await page.locator('#clock-view .clock-digital').waitFor();
    assert.equal(await page.evaluate(() => document.fullscreenElement), null);
    assert.equal(await page.locator('#display-app').getAttribute('data-chrome'), 'fullscreen');
    assert.equal(await page.locator('#display-app').evaluate(el => {
      const r = el.getBoundingClientRect();
      return r.width === innerWidth && r.height === innerHeight;
    }), true);
    assert.equal(await page.locator('#display-switch summary').isVisible(), true);
    await page.locator('#display-guest-back').click();
    await page.locator('#participant-view').waitFor({state:'visible'});
    assert.deepEqual(fallback.errors, []);
    assert.deepEqual(fallback.violations, []);
  } finally { await fallback.browser.close(); }
  console.log('guest-share: ok');
})().catch(error => { console.error(error); process.exit(1); });
