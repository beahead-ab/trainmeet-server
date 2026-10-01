// Sidorna hör direkt när något ändras på servern (web/live-events.js mot
// GET /v1/events): flera ändringar inom 250 ms blir en uppdatering, en sida
// som varit utan ström hämtar om allt, och en full server ger reservtakten.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../../src/tmbox_gateway/web/live-events.js'), 'utf8');

function page() {
  const timers = [];
  let now = 0;
  const streams = [];
  class EventSource {
    static CONNECTING = 0; static OPEN = 1; static CLOSED = 2;
    constructor(url) { this.url = url; this.readyState = 0; this.listeners = {}; streams.push(this); }
    addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
    emit(name, data) { for (const listener of this.listeners[name] || []) listener({data: JSON.stringify(data)}); }
    fail(closed) { this.readyState = closed ? 2 : 0; this.onerror?.(); }
  }
  const context = {
    console, JSON, Set,
    EventSource,
    setTimeout: (callback, ms) => { const timer = {at: now + ms, callback}; timers.push(timer); return timer; },
    clearTimeout: (timer) => { const index = timers.indexOf(timer); if (index >= 0) timers.splice(index, 1); },
  };
  context.globalThis = context;
  vm.runInNewContext(source, context);
  const advance = (ms) => {
    now += ms;
    for (let due; (due = timers.filter((timer) => timer.at <= now).sort((a, b) => a.at - b.at)[0]);) {
      timers.splice(timers.indexOf(due), 1);
      due.callback();
    }
  };
  return {live: context.TrainMeetLive, streams, advance};
}

const topics = (calls) => calls.map((set) => [...set].sort());

test('one stream, opened by the first page part that wants it, not by a passive one', () => {
  const {live, streams} = page();
  live.subscribe(() => {}, {passive: true});
  assert.equal(streams.length, 0, 'the banner alone opens no stream');
  live.subscribe(() => {});
  live.subscribe(() => {});
  assert.equal(streams.length, 1);
  assert.equal(streams[0].url, '/v1/events');
});

test('changes within 250 ms reach every subscriber once, together; unknown names are dropped', () => {
  const {live, streams, advance} = page();
  const first = [], passive = [];
  live.subscribe((set) => first.push(set));
  live.subscribe((set) => passive.push(set), {passive: true});
  const stream = streams[0];
  stream.emit('hello', {boot: 'a1', seq: 4});
  assert.equal(live.connected, true);
  stream.emit('change', {seq: 5, topics: ['traffic']});
  advance(100);
  stream.emit('change', {seq: 6, topics: ['clock', 'traffic', 'secrets']});
  advance(149);
  assert.deepEqual(first, [], 'still gathering');
  advance(1);
  assert.deepEqual(topics(first), [['clock', 'traffic']]);
  assert.deepEqual(topics(passive), [['clock', 'traffic']]);
  stream.emit('change', {seq: 7, topics: ['devices']});
  advance(250);
  assert.deepEqual(topics(first), [['clock', 'traffic'], ['devices']]);
});

test('back after a gap: everything again if anything was missed, nothing if not', () => {
  const {live, streams, advance} = page();
  const calls = [];
  live.subscribe((set) => calls.push(set));
  const stream = streams[0];
  stream.emit('hello', {boot: 'a1', seq: 0});
  advance(1000);
  assert.deepEqual(calls, [], 'the page has just loaded what is there');
  stream.emit('change', {seq: 1, topics: ['clock']});
  advance(250);
  stream.fail(false);
  assert.equal(live.connected, false);
  stream.emit('hello', {boot: 'a1', seq: 1});  // the browser reconnects by itself
  advance(250);
  assert.deepEqual(topics(calls), [['clock']], 'nothing happened meanwhile');
  stream.fail(false);
  stream.emit('hello', {boot: 'a1', seq: 3});
  advance(250);
  assert.deepEqual(topics(calls).at(-1), ['clock', 'devices', 'runtime', 'simulation', 'traffic']);
  stream.fail(false);
  stream.emit('hello', {boot: 'b2', seq: 3});  // a restarted server
  advance(250);
  assert.equal(calls.length, 3);
});

test('a full or missing stream: the page keeps its own pace and tries again after 30 s', () => {
  const {live, streams, advance} = page();
  const status = [];
  live.onStatus((value) => status.push(value));
  live.subscribe(() => {});
  streams[0].emit('hello', {boot: 'a1', seq: 0});
  streams[0].fail(true);  // 429: the browser gives up
  assert.deepEqual(status, [true, false]);
  advance(29999);
  assert.equal(streams.length, 1);
  advance(1);
  assert.equal(streams.length, 2, 'one new try');
  streams[1].fail(false);
  advance(60000);
  assert.equal(streams.length, 2, 'while the browser retries itself, no second stream');
});

test('without EventSource (an old browser) nothing breaks', () => {
  const context = {console, JSON, Set, setTimeout, clearTimeout};
  context.globalThis = context;
  vm.runInNewContext(source, context);
  const unsubscribe = context.TrainMeetLive.subscribe(() => {});
  assert.equal(context.TrainMeetLive.connected, false);
  unsubscribe();
});
