// Försök igen under Programuppdatering. The button posted with no body, the
// server reads every POST as JSON and answered 400, and the button never
// looked: it re-read the old failure and blinked. With Installera hidden while
// the status says failed, there was no way out of the page (2026-10-06).
//
// The API here answers like the real server: an empty POST is a 400, and the
// status file keeps the old failure for a moment after the updater is started.
const assert = require('node:assert/strict');
const { open } = require('./kr-fixture.cjs');

const STAGES = [['checking', 'Söker efter uppdatering'], ['downloading', 'Hämtar'], ['verifying', 'Verifierar'], ['installing', 'Installerar'], ['restarting', 'Startar om'], ['health_check', 'Kontrollerar att tjänsten fungerar'], ['complete', 'Klart']];
const steps = (status, failedStage) => {
  const reached = STAGES.findIndex(([stage]) => stage === (failedStage || status));
  return STAGES.map(([stage, label], index) => ({ stage, label, state: failedStage && index === reached ? 'failed' : index < reached || (status === 'complete' && stage === 'complete') ? 'done' : index === reached ? 'active' : 'pending' }));
};

(async () => {
  const api = { posts: [], pollsAfterStart: 0, installed: '3.4.0' };
  const base = () => ({ supported: true, installed_version: api.installed, installed_build: api.installed === '3.4.0' ? '26ac80b3' : 'a615f93a', latest_version: '3.5.2', latest_build: 'a615f93a', update_available: api.installed === '3.4.0', releases: [] });
  const failed = () => ({ ...base(), status: 'failed', failed_stage: 'installing', message: 'Installationen misslyckades, återställde föregående version', updated_at: '2026-10-06T01:35:12Z', steps: steps('failed', 'installing') });
  const h = await open({
    route: '/installningar#uppdatering', width: 390, height: 844,
    api: {
      '/v1/server/update': (request) => {
        if (request.method() === 'POST') {
          const body = request.postData();
          api.posts.push(body);
          // http_server.py _read_json: no body is "Tom eller för stor begäran".
          if (!body) return { status: 400, data: { error: 'invalid_body', message: 'Tom eller för stor begäran' } };
          api.started = true;
          return { status: 202, data: { status: 'started', message: 'Uppdateringen har startat i bakgrunden.' } };
        }
        if (!api.started) return { data: failed() };
        api.pollsAfterStart += 1;
        // The updater has not written its first step yet: still the old failure.
        if (api.pollsAfterStart <= 2) return { data: failed() };
        if (api.pollsAfterStart <= 4) return { data: { ...base(), status: 'downloading', failed_stage: null, message: 'Hämtar TrainMeet Server från GitHub', updated_at: '2026-10-06T03:01:02Z', steps: steps('downloading') } };
        api.installed = '3.5.2';
        api.started = false;
        return { data: { ...base(), status: 'complete', failed_stage: null, message: 'Version 3.5.2 är installerad och svarar.', updated_at: '2026-10-06T03:02:40Z', steps: steps('complete') } };
      },
    },
  });
  const { page } = h;
  page.setDefaultTimeout(8000);
  const dialogs = [];
  page.on('dialog', (dialog) => { dialogs.push(dialog.message()); dialog.accept(); });
  try {
    await page.locator('#software-update-message').getByText('Installationen misslyckades, återställde föregående version').waitFor();
    assert.equal(await page.locator('#software-retry').isVisible(), true);
    assert.equal(await page.locator('#software-install').isVisible(), false);
    assert.match(await page.locator('#update-banner').getAttribute('class'), /danger/);

    await page.locator('#software-retry').click();

    // It asks first, like Installera: the server restarts and traffic stops.
    assert.equal(dialogs.length, 1);
    // The request is a JSON object, the only kind the server accepts.
    assert.equal(api.posts.length, 1);
    assert.equal(typeof JSON.parse(api.posts[0]), 'object');

    // While the old failure is still in the status file, the page does not
    // draw it again: it says the update is under way.
    await page.locator('#software-update-message').getByText('Uppdaterar i bakgrunden. Sidan ansluter igen efter omstart.').waitFor();
    assert.doesNotMatch(await page.locator('#update-banner').getAttribute('class'), /danger/);
    assert.equal(await page.locator('#software-retry').isVisible(), false);

    // Then the real steps, and after the health check the page reloads on the new version.
    await page.locator('#update-progress .update-step.active').getByText('Hämtar').waitFor({ timeout: 15000 });
    await page.locator('#software-version').getByText('3.5.2').waitFor({ timeout: 15000 });
    assert.equal(api.posts.length, 1, 'one press, one start');

    assert.deepEqual(h.errors.filter((e) => !/status of 400|status of 202/.test(e)), []);
    assert.deepEqual(h.violations, []);
    console.log('Försök igen: posts JSON, waits out the old failure, follows the steps and lands on the new version.');
  } finally {
    await h.browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
