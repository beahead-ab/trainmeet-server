// Account changes are server-wide; traffic commands belong to the displayed meet.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const app = fs.readFileSync(path.resolve(__dirname, '../../src/tmbox_gateway/web/app.js'), 'utf8');
const start = app.indexOf('function authorizedFetch(');
const source = app.slice(start, app.indexOf('\n}\n', start) + 3);

async function sentBody(route, body) {
  let sent;
  const context = {
    Headers,
    state: { serverContext: { selected_meet: { generation: 7 } } },
    fetch: async (url, options) => { sent = JSON.parse(options.body); return {}; },
    readableReply: response => response,
  };
  vm.runInNewContext(source, context);
  await context.authorizedFetch(route, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  return sent;
}

test('own-account edits contain only account fields even when a meet is selected', async () => {
  const change = { display_name: 'Driver Updated', email: 'driver@example.invalid' };
  assert.deepEqual(await sentBody('/v1/account', change), change);
});

test('traffic commands still carry the generation shown to the operator', async () => {
  assert.deepEqual(await sentBody('/v1/clock', { action: 'start' }), { action: 'start', meet_generation: 7 });
});

test('a command keeps its original generation instead of rebinding to a newer meet', async () => {
  assert.deepEqual(await sentBody('/v1/clock', { action: 'start', meet_generation: 6 }), { action: 'start', meet_generation: 6 });
});
