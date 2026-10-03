const { open } = require('./kr-fixture.cjs');
(async () => {
  for (const [theme, width, height] of [['dark', 1280, 860], ['light', 1280, 860], ['dark', 390, 844]]) {
    const h = await open({ route: '/login', width, height, theme, loggedOut: true });
    await h.page.locator('#login').waitFor({ state: 'visible', timeout: 10000 });
    await h.page.waitForTimeout(400);
    await h.page.screenshot({ path: `${process.argv[2]}-${theme}-${width}.png` });
    console.log(theme, width, JSON.stringify(h.errors));
    await h.browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
