// Träffklockans meddelanden som glas (#223): de ligger ovanpå klockan och tar
// aldrig höjd från den. NoReason och andra platshållare syns aldrig.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

(async () => {
  const snapshot = {
    clock: { configured: true, source: 'fastclock', available: true, last_sync: '2026-10-30T15:00:00Z', running: true, time: '12:15:48', speed: 4, style: 'analog', stopped_reason: null, message: 'NoReason' },
    meet: { id: 'meet-1', name: 'Testträff' }, stations: [], connections: [], train_positions: [], connection_states: [], connection: { screens: [] },
  };
  const tv = await open({ route: '/display/clock', api: { '/v1/display': () => ({ data: snapshot }) } });
  const { page } = tv;
  const update = async clock => {
    Object.assign(snapshot.clock, clock);
    await page.evaluate(s => { displaySnapshot = s; syncDisplayClock(s); renderClock(s); }, snapshot);
  };
  const faceSize = () => page.locator('.clock-face').evaluate(face => { const box = face.getBoundingClientRect(); return Math.round(box.width) + 'x' + Math.round(box.height); });
  const inside = async selector => {
    const box = await page.locator(selector).boundingBox();
    const canvas = await page.locator('#display-stage').boundingBox();
    return box.x >= canvas.x - 1 && box.x + box.width <= canvas.x + canvas.width + 1 && box.y >= canvas.y - 1 && box.y + box.height <= canvas.y + canvas.height + 1;
  };
  try {
    await page.locator('.clock-face').waitFor();
    const fullSize = await faceSize();
    assert.equal(await page.locator('.sc-notice').count(), 0, 'NoReason under a running clock shows nothing');
    for (const placeholder of ['None', 'SelectStopReason', '   ', 'N/A']) {
      await update({ message: placeholder });
      assert.equal(await page.locator('.sc-notice').count(), 0, `${JSON.stringify(placeholder)} shows nothing`);
    }
    await update({ message: null, stopped_reason: 'Derailment' });
    assert.equal(await page.locator('.sc-notice').count(), 0, 'a stop reason is never shown while the clock runs');

    await update({ message: 'Tåg 4711 är inställt i dag <alla>' });
    const info = page.locator('.sc-notice--info');
    await info.waitFor();
    assert.equal(await info.locator('.sc-notice__text').textContent(), 'Tåg 4711 är inställt i dag <alla>');
    assert.equal(await info.locator('alla').count(), 0, 'provider text is escaped');
    assert.equal(await faceSize(), fullSize, 'information takes no room from the clock');
    assert.ok(await inside('.sc-notice--info'), 'information stays inside the screen');
    assert.ok(await info.evaluate(card => {
      const a = card.getBoundingClientRect();
      const face = document.querySelector('.clock-face').getBoundingClientRect();
      const r = face.width / 2, cx = face.left + r, cy = face.top + r;
      // The card's corner nearest the centre of the face lies outside the dial.
      return Math.hypot(Math.max(0, cx - a.right), Math.max(0, a.top - cy)) > r * 0.9;
    }), 'information sits in the corner, beside the clock face');
    assert.equal(await info.evaluate(card => getComputedStyle(card).position), 'absolute');
    assert.match(await info.evaluate(card => getComputedStyle(card).backgroundColor), /rgba\(26, 28, 34, 0\.74\)/, 'dark glass');
    const polled = await info.elementHandle();
    await update({});
    assert.ok(await page.evaluate(node => node.isConnected, polled), 'polls do not rebuild an unchanged notice');

    await update({ running: false, stopped_reason: 'Lunch', message: null });
    const stop = page.locator('.sc-notice--stop');
    await stop.waitFor();
    assert.equal(await stop.locator('.sc-notice__text').textContent(), 'Lunchpaus', 'known FastClock codes are translated');
    assert.equal(await page.locator('.sc-notice--info').count(), 0);
    assert.equal(await faceSize(), fullSize, 'a stopped clock keeps its size');
    assert.match(await stop.evaluate(card => getComputedStyle(card).backgroundColor), /rgba\(190, 30, 36, 0\.82\)/, 'red glass');
    assert.ok(await stop.evaluate(card => {
      const a = card.getBoundingClientRect(), face = document.querySelector('.clock-face').getBoundingClientRect();
      return a.top > face.top + face.height / 2 && a.bottom < face.bottom && a.left > face.left && a.right < face.right;
    }), 'the stop notice lies over the lower half of the face');

    await update({ stopped_reason: 'NoReason' });
    await page.locator('.sc-notice--stop.is-short').waitFor();
    assert.equal(await page.locator('.sc-notice--stop .sc-notice__text').textContent(), 'Klockan är stoppad', 'a stop without reason still says the clock is stopped');
    await update({ stopped_reason: 'SectionBlocked' });
    assert.equal(await page.locator('.sc-notice--stop.is-short').count(), 1, 'an unknown code is not shown');
    await update({ stopped_reason: 'Vi väntar på att alla stationer ska bli klara' });
    assert.equal(await page.locator('.sc-notice--stop .sc-notice__text').textContent(), 'Vi väntar på att alla stationer ska bli klara', 'free text from the administrator is shown');
    assert.equal(await page.locator('.sc-notice--stop.is-long').count(), 1);
    await update({ message: 'Samling vid tågledningen' });
    assert.equal(await page.locator('.sc-notice').count(), 2, 'stop and information have places of their own');

    await update({ stopped_reason: 'Closing', message: null });
    const quiet = page.locator('.sc-notice--quiet');
    await quiet.waitFor();
    assert.equal(await quiet.locator('.sc-notice__text').textContent(), 'Dagens spel slut');
    assert.equal(await page.locator('.sc-notice--stop').count(), 0, 'the end of the day is quiet, not an alarm');
    assert.equal(await page.locator('.sc-clock-layout.is-quiet').count(), 1);
    await update({ stopped_reason: '', completed: true });
    assert.equal(await quiet.locator('.sc-notice__text').textContent(), 'Dagens spel slut', 'a completed FastClock day is quiet too');

    await update({ completed: false, available: false });
    assert.equal(await page.locator('.sc-notice--stop .sc-notice__label').textContent(), 'Kontakt saknas');

    await update({ available: true, running: false, stopped_reason: 'Derailment', style: 'digital' });
    await page.locator('.clock-digital').waitFor();
    assert.equal(await page.locator('.sc-notice--stop .sc-notice__text').textContent(), 'Urspårning');
    assert.ok(await page.locator('.sc-notice--stop').evaluate(card => card.getBoundingClientRect().top >= document.querySelector('.clock-digital').getBoundingClientRect().bottom - 1), 'under the digits, never over them');
    assert.ok(await inside('.sc-notice--stop'));
    await page.screenshot({ path: '/tmp/trainmeet-clock-notice.png' });

    await page.emulateMedia({ reducedMotion: 'reduce' });
    assert.equal(await page.locator('.sc-notice--stop').evaluate(el => getComputedStyle(el).animationName), 'none');
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await update({ running: true, stopped_reason: null, message: null });
    assert.equal(await page.locator('.sc-notice').count(), 0, 'nothing is left when the clock runs without a message');
    assert.deepEqual(tv.errors, []);
    assert.deepEqual(tv.violations, []);
    console.log('PASS clock notices: placeholders hidden, glass layers, codes translated, quiet day end, clock keeps its size');
  } finally { await tv.browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
