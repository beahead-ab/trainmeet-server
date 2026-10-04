// Stationer och boxar på Drift: firmware per box, och efter att en TMBox tagits bort meddelandet
// under listan får inte trycka ihop den. Förut tog det hela panelens höjd, och
// listan krympte till några rader tills sidan laddades om (#107). API:et är
// fixturen i kr-fixture.cjs.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

(async () => {
  const h = await open({ route: '/drift', width: 1440, height: 900 });
  const { page } = h;
  page.setDefaultTimeout(10000);
  try {
    await page.waitForFunction(() => document.querySelectorAll('#device-list tr').length > 3);
    // The list can be drawn before the route is ready, while #app-view is still
    // hidden, and innerText of hidden text is empty.
    await page.locator('body[data-route-ready]').waitFor({ state: 'attached' });
    await page.locator('#device-list').waitFor({ state: 'visible' });
    const list = () => page.evaluate(() => {
      const scroll = document.querySelector('#drift-stations .kr-scroll-x');
      const table = document.querySelector('#drift-station-table');
      const message = document.querySelector('#device-list-message');
      return { shown: Math.round(scroll.getBoundingClientRect().height), table: Math.round(table.getBoundingClientRect().height),
        message: message.textContent, messageHeight: Math.round(message.getBoundingClientRect().height) };
    });
    // Firmware per box: a physical box says which it runs, a web box has none.
    const boxCell = (code) => page.locator('#device-list td.mono', { hasText: code }).first().innerText();
    assert.match(await boxCell('TBX-3C11'), /^TBX-3C11\s*ver\. 0\.7\.5$/);
    assert.equal((await boxCell('WEB-K3M9')).trim(), 'WEB-K3M9', 'no version for a browser box');
    assert.equal((await boxCell('TBX-77A0')).trim(), 'TBX-77A0', 'no version until the box has said one');

    const before = await list();
    assert.equal(before.shown, before.table, 'the whole list shows before');
    assert.equal(before.messageHeight, 0, 'an empty message takes no room');

    await page.locator('#device-list tr', { hasText: 'Redigera' }).first().getByRole('button', { name: 'Redigera' }).click();
    await page.locator('#device-form-modal').waitFor({ state: 'visible' });
    await page.locator('#device-remove-open').click();
    await page.locator('#device-remove-modal').waitFor({ state: 'visible' });
    await page.locator('#device-remove-form [type="submit"]').click();
    await page.locator('#device-remove-modal').waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.querySelector('#device-list-message').textContent.trim() !== '');

    const after = await list();
    assert.equal(after.message, 'TMBoxen är borttagen.');
    assert.equal(after.shown, after.table, `the list keeps its size after a box is removed: ${JSON.stringify(after)}`);
    assert.ok(after.messageHeight > 0 && after.messageHeight < 60, `the message is one line under the list: ${after.messageHeight}`);
    assert.deepEqual(h.errors, []);
    assert.deepEqual(h.violations, []);
  } finally { await h.browser.close(); }
  console.log('drift-station-list: ok');
})().catch((error) => { console.error(error); process.exit(1); });
