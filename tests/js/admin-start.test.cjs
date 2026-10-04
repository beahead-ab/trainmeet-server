// När admin startar visas ingen annan vy än den man bad om. Förut stod HTML:ens
// utgångsläge, Drift på ljus botten, framme tills alla anrop svarat, också på
// Inställningar. Nu är sidan kontrollrummets tomma botten tills rätt vy är
// vald. API:et är fixturen i kr-fixture.cjs, med långsamma svar som på ett nät.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

(async () => {
  for (const [route, mode] of [['/installningar', 'installningar'], ['/drift', 'kor']]) {
    const h = await open({ route: '/__start', width: 1280, height: 800, apiDelay: 250 });
    const { page } = h;
    try {
      await page.goto('http://127.0.0.1:9999' + route, { waitUntil: 'commit' });
      const seen = [];
      for (let i = 0; i < 80; i++) {
        // Bara bilder som ritas räknas. requestAnimationFrame körs inte medan
        // stilmallarna i <head> laddas, och det läget målas aldrig. Ett prov
        // direkt efter 'commit' kunde annars se en genomskinlig botten.
        const frame = await page.evaluate(() => new Promise((resolve) => {
          const timer = setTimeout(() => resolve(null), 1000);
          requestAnimationFrame(() => {
            clearTimeout(timer);
            resolve(document.body ? {
              mode: document.body.dataset.mode, shown: getComputedStyle(document.querySelector('#app-view') || document.body).visibility === 'visible'
                && !document.querySelector('#app-view')?.classList.contains('hidden'),
              bg: getComputedStyle(document.documentElement).backgroundColor } : null);
          });
        })).catch(() => null);
        if (frame) seen.push(frame);
        if (frame?.mode === mode && frame.shown) break;
        await page.waitForTimeout(40);
      }
      const wrong = seen.filter((frame) => frame.shown && frame.mode !== mode);
      assert.deepEqual(wrong, [], `${route}: nothing but ${mode} is ever shown`);
      assert.ok(seen.length > 3 && seen.at(-1).mode === mode && seen.at(-1).shown, `${route}: the view arrives (${JSON.stringify(seen.at(-1))})`);
      assert.ok(seen.every((frame) => frame.bg === 'rgb(13, 15, 19)'), `${route}: the control room background from the first frame`);
      assert.deepEqual(h.errors, []);
    } finally { await h.browser.close(); }
  }
  console.log('admin-start: ok');
})().catch((error) => { console.error(error); process.exit(1); });
