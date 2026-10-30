const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

(async () => {
  const snapshot = {
    clock: { configured: true, source: 'fastclock', available: true, last_sync: '2026-10-30T15:00:00Z', running: false, time: '12:30:00', speed: 4, style: 'digital', stopped_reason: 'Lunch & information <alla>' },
    meet: { id: 'meet-1', name: 'Testträff' }, stations: [], connections: [], train_positions: [], connection_states: [], connection: { screens: [] },
  };
  const tv = await open({ route: '/display/clock', api: { '/v1/display': () => ({ data: snapshot }) } });
  const { page } = tv;
  const update = async clock => {
    Object.assign(snapshot.clock, clock);
    await page.evaluate(s => { displaySnapshot = s; syncDisplayClock(s); renderClock(s); }, snapshot);
  };
  try {
    const message = page.locator('.sc-clock-message');
    await message.waitFor();
    assert.equal(await message.locator('span').first().textContent(), snapshot.clock.stopped_reason);
    assert.equal(await message.locator('span[aria-hidden="true"]').count(), 1);
    assert.equal(await message.locator('alla').count(), 0, 'provider text is escaped');
    const before = await page.evaluate(() => {
      window.pauseTrack = document.querySelector('.sc-clock-message__track');
      return pauseTrack.getAnimations()[0].currentTime;
    });
    await page.waitForTimeout(350);
    await update({});
    assert.ok(await page.evaluate(time => pauseTrack === document.querySelector('.sc-clock-message__track') && pauseTrack.getAnimations()[0].currentTime > time, before), 'polls preserve the animation and its progress');

    await update({ stopped_reason: 'Vi väntar på att alla stationer ska bli klara. '.repeat(4), style: 'analog' });
    await page.locator('.clock-face').waitFor();
    assert.ok(await page.evaluate(() => {
      const track = document.querySelector('.sc-clock-message__track');
      const duration = track.getAnimations()[0].effect.getTiming().duration;
      return Math.abs(track.scrollWidth / 2 / (duration / 1000) - 48) < 1;
    }), 'long messages keep a steady reading speed');
    const box = await message.boundingBox();
    const canvas = await page.locator('#display-stage').boundingBox();
    assert.ok(box.x >= canvas.x - 1 && box.x + box.width <= canvas.x + canvas.width + 1, 'ticker fits the clock canvas');
    assert.ok(box.y >= canvas.y && box.y + box.height <= canvas.y + canvas.height, 'ticker stays inside the screen');
    await page.screenshot({ path: '/tmp/trainmeet-clock-pause.png' });

    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(await page.locator('.sc-clock-message__track').evaluate(el => getComputedStyle(el).animationName), 'none');
    assert.equal(await message.locator('span[aria-hidden="true"]').isVisible(), false);
    assert.equal(await message.locator('span').first().evaluate(el => el.scrollWidth <= el.clientWidth), true, 'static text wraps');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await update({ stopped_reason: '   ' });
    assert.equal(await message.count(), 0, 'no empty ticker');
    await page.locator('.sc-stopped__title').waitFor();
    await update({ running: true, stopped_reason: 'Old reason' });
    assert.equal(await message.count(), 0, 'resume removes the message even if a provider retains the reason');
    assert.equal(await page.locator('.sc-stopped').count(), 0);
    assert.deepEqual(tv.errors, []);
    assert.deepEqual(tv.violations, []);
    console.log('PASS clock pause messages: motion, escaping, stable polls, long text, reduced motion, resume');
  } finally { await tv.browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
