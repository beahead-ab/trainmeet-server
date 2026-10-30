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
        const anchors = page.locator('#pv-screens-list a');
        await page.waitForFunction((n) => document.querySelectorAll('#pv-screens-list a').length === n, expected.length);
        const hrefs = await anchors.evaluateAll((items) => items.map((link) => new URL(link.href).pathname));
        assert.deepEqual(hrefs, expected.map((name) => '/display/' + name));
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
          await page.locator('#pv-screens-list a[href="/display/' + name + '"]').click();
          await page.locator('#display-app').waitFor({ state: 'visible' });
          await page.locator('#display-switch-list a[href="/display/' + name + '"]').waitFor({ state: 'attached' });
          assert.equal(await page.locator('#login').isVisible(), false, 'screen ' + name + ' stays public');
        }
        assert.deepEqual(errors, [], 'browser errors');
        assert.deepEqual(violations, [], 'CSP violations');
      } finally {
        await browser.close();
      }
    }
  }
  console.log('guest-share: ok');
})().catch(error => { console.error(error); process.exit(1); });
