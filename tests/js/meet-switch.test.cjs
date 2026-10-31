const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');
(async () => {
  const selection = { region: 'eu', meet_id: 'old', meet_name: 'Grimslöv 2027', publication_id: 'pub-9', generation: 7 };
  const calls = [];
  const ui = await open({ route: '/installningar', api: {
    '/v1/runtime/sync': req => {
      const data = JSON.parse(req.postData()); calls.push(data);
      if (!data.confirm_meet_change) return { status: 409, data: { error: 'meet_change_required', message: 'Bekräfta', confirmation: { selection, name: 'Grimslöv 2027', target_name: 'Nästa träff' } } };
      return { data: { activated: true, message: 'Servern kör nu Nästa träff.' } };
    },
  } });
  const { page } = ui;
  try {
    await page.evaluate(() => openModal('runtime-sync-form-modal'));
    await page.locator('#runtime-sync-code-boxes input').first().fill('1');
    await page.evaluate(() => { document.querySelector('#runtime-sync-code').value = '123456'; });
    await page.locator('#runtime-sync-form button[type="submit"]').click();
    const dialog = page.locator('#meet-switch-modal');
    await dialog.waitFor({ state: 'visible' });
    assert.equal(await dialog.locator('#meet-switch-name').textContent(), 'Grimslöv 2027');
    assert.ok((await dialog.locator('#meet-switch-description').textContent()).includes('Nästa träff'));
    assert.equal(await dialog.locator('#meet-switch-start').isDisabled(), true);
    await dialog.locator('#meet-switch-confirmation').fill('Fel träff');
    assert.equal(await dialog.locator('#meet-switch-start').isDisabled(), true);
    await dialog.getByRole('button', { name: 'Avbryt', exact: true }).click();
    assert.equal(calls.length, 1, 'cancel never submits a destructive confirmation');
    await page.evaluate(() => openModal('runtime-sync-form-modal'));
    await page.evaluate(() => { document.querySelector('#runtime-sync-code').value = '123456'; });
    await page.locator('#runtime-sync-form button[type="submit"]').click();
    await dialog.waitFor({ state: 'visible' });
    assert.equal(await dialog.locator('#meet-switch-confirmation').inputValue(), '');
    await dialog.locator('#meet-switch-confirmation').fill('Grimslöv 2027');
    await dialog.locator('#meet-switch-start').click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(calls.length, 3);
    assert.equal(calls[2].leaving_name, 'Grimslöv 2027');
    assert.deepEqual(calls[2].leaving_selection, selection);
    assert.equal(calls[2].sync_code, '123456');
    assert.deepEqual(ui.errors, []);
    assert.deepEqual(ui.violations, []);
    console.log('PASS meet switch: danger dialog, wrong name, cancel, fresh confirmation and scoped request');
  } finally { await ui.browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
