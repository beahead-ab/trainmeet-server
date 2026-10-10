// /v1/display för resten av sidan (web/display-feed.js, förut
// simuleringsbannern): den frågar servern var 2:a sekund utan händelseström
// och var 10:e med. En ändring hämtas direkt, det blir aldrig två timrar som
// frågar parallellt, och varje svar går vidare som "trainmeet:display".
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../src/tmbox_gateway/web/display-feed.js'), 'utf8');

function page({connected = false, live = true} = {}) {
  const timers = new Set(), fetches = [], subscriptions = [], dispatched = [];
  const context = {
    URLSearchParams, Error,
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    location: {pathname: '/drift', search: ''},
    document: {dispatchEvent: (event) => dispatched.push(event), createElement() { throw new Error('no banner any more'); }},
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
  return {timers, fetches, subscriptions, answer, dispatched, context, setStatus};
}

test('listens passively and asks at once on a change it cares about', async () => {
  const {subscriptions, fetches, answer} = page({connected: true});
  assert.equal(subscriptions.length, 1);
  assert.equal(subscriptions[0].options.passive, true, 'it opens no stream of its own');
  await answer({running: false});
  subscriptions[0].callback(new Set(['devices', 'traffic']));
  assert.equal(fetches.length, 0);
  for (const topic of ['automatic', 'runtime', 'clock']) {
    subscriptions[0].callback(new Set([topic]));
    assert.equal(fetches.length, 1, topic);
    await answer({running: false});
  }
});

test('ten seconds with a stream, two without', async () => {
  for (const [connected, ms] of [[true, 10000], [false, 2000]]) {
    const {timers, answer} = page({connected});
    await answer({running: true});
    assert.deepEqual([...timers].map((timer) => timer.ms), [ms]);
  }
});

test('a change while waiting cancels the wait at once', async () => {
  const {timers, subscriptions, answer} = page({connected: true});
  await answer({running: false});
  assert.equal(timers.size, 1);
  subscriptions[0].callback(new Set(['automatic']));
  assert.equal(timers.size, 0, 'no second fetch from the old timer meanwhile');
  await answer({running: false});
  assert.equal(timers.size, 1);
});

test('a change during a fetch still leaves one timer, not two', async () => {
  const {timers, subscriptions, answer, dispatched} = page({connected: true});
  subscriptions[0].callback(new Set(['clock']));  // while the first fetch is out
  await answer({running: true});
  await answer({running: false});
  assert.equal(timers.size, 1);
  // Every answer reaches the rest of the page (day-change.js), the last one last.
  assert.deepEqual(dispatched.map((event) => [event.type, event.detail.clock.running]),
    [['trainmeet:display', true], ['trainmeet:display', false]]);
});

test('the waiting timer takes the new pace when the stream comes up or goes down', async () => {
  const {timers, answer, setStatus} = page({connected: false});
  await answer({running: false});
  assert.deepEqual([...timers].map((timer) => timer.ms), [2000]);
  setStatus(true);
  assert.deepEqual([...timers].map((timer) => timer.ms), [10000]);
  setStatus(false);
  assert.deepEqual([...timers].map((timer) => timer.ms), [2000]);
});

test('without the live script (the virtual TMBox page) it keeps its two seconds', async () => {
  const {timers, answer} = page({live: false});
  await answer({running: false});
  assert.deepEqual([...timers].map((timer) => timer.ms), [2000]);
});
