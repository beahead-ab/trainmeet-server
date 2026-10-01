// Simuleringsbannern (web/simulation-banner.js) frågar servern var 2:a
// sekund utan händelseström och var 10:e med. En ändring hämtas direkt, och
// det blir aldrig två timrar som frågar parallellt.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../src/tmbox_gateway/web/simulation-banner.js'), 'utf8');

function page({connected = false, live = true} = {}) {
  const timers = new Set(), fetches = [], subscriptions = [];
  const element = () => ({style: {}, setAttribute() {}, hidden: false, textContent: ''});
  const banner = element();
  const context = {
    URLSearchParams, Error,
    location: {pathname: '/drift', search: ''},
    document: {createElement: () => banner, body: {prepend() {}}},
    fetch: () => new Promise((resolve) => fetches.push(resolve)),
    setTimeout: (callback, ms) => { const timer = {callback, ms}; timers.add(timer); return timer; },
    clearTimeout: (timer) => timers.delete(timer),
  };
  const statusListeners = [];
  if (live) context.TrainMeetLive = {connected, subscribe: (callback, options) => subscriptions.push({callback, options}),
    onStatus: (listener) => statusListeners.push(listener)};
  const setStatus = (value) => { context.TrainMeetLive.connected = value; statusListeners.forEach((listener) => listener(value)); };
  context.globalThis = context;
  vm.runInNewContext(source, context);
  const answer = async (clock) => {
    fetches.shift()({ok: true, json: async () => ({clock})});
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  };
  return {timers, fetches, subscriptions, answer, banner, context, setStatus};
}

test('listens passively and asks at once on a change it cares about', async () => {
  const {subscriptions, fetches, answer} = page({connected: true});
  assert.equal(subscriptions.length, 1);
  assert.equal(subscriptions[0].options.passive, true, 'it opens no stream of its own');
  await answer({simulation: false});
  subscriptions[0].callback(new Set(['devices', 'traffic']));
  assert.equal(fetches.length, 0);
  for (const topic of ['simulation', 'runtime', 'clock']) {
    subscriptions[0].callback(new Set([topic]));
    assert.equal(fetches.length, 1, topic);
    await answer({simulation: false});
  }
});

test('ten seconds with a stream, two without', async () => {
  for (const [connected, ms] of [[true, 10000], [false, 2000]]) {
    const {timers, answer} = page({connected});
    await answer({simulation: true, running: true});
    assert.deepEqual([...timers].map((timer) => timer.ms), [ms]);
  }
});

test('a change while waiting cancels the wait at once', async () => {
  const {timers, subscriptions, answer} = page({connected: true});
  await answer({simulation: false});
  assert.equal(timers.size, 1);
  subscriptions[0].callback(new Set(['simulation']));
  assert.equal(timers.size, 0, 'no second fetch from the old timer meanwhile');
  await answer({simulation: false});
  assert.equal(timers.size, 1);
});

test('a change during a fetch still leaves one timer, not two', async () => {
  const {timers, subscriptions, answer, banner} = page({connected: true});
  subscriptions[0].callback(new Set(['simulation']));  // while the first fetch is out
  await answer({simulation: true, running: true});
  await answer({simulation: true, running: false});
  assert.equal(timers.size, 1);
  assert.match(banner.textContent, /Pausad/);
});

test('the waiting timer takes the new pace when the stream comes up or goes down', async () => {
  const {timers, answer, setStatus} = page({connected: false});
  await answer({simulation: false});
  assert.deepEqual([...timers].map((timer) => timer.ms), [2000]);
  setStatus(true);
  assert.deepEqual([...timers].map((timer) => timer.ms), [10000]);
  setStatus(false);
  assert.deepEqual([...timers].map((timer) => timer.ms), [2000]);
});

test('without the live script (the virtual TMBox page) it keeps its two seconds', async () => {
  const {timers, answer} = page({live: false});
  await answer({simulation: false});
  assert.deepEqual([...timers].map((timer) => timer.ms), [2000]);
});
