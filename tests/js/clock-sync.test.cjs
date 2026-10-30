const test = require('node:test');
const assert = require('node:assert/strict');
const {create} = require('../../src/tmbox_gateway/web/clock-sync.js');

const status = (time, speed = 5, running = true, source = 'fastclock') => ({time, speed, running, source, external_name: 'Test'});

test('a 5x external clock advances smoothly and does not rewind on repeated samples', () => {
  const clock = create();
  clock.accept(status('09:15:00'), 1000);
  let last = clock.seconds(1000);
  for (const now of [3000, 5000, 7000, 9000, 11000]) {
    const seconds = Math.floor((now - 1000) * 5 / 1000);
    clock.accept(status('09:15:' + String(seconds).padStart(2, '0')), now);
    for (let at = now; at <= now + 900; at += 100) {
      const next = clock.seconds(at);
      assert.ok(next >= last, `clock rewound from ${last} to ${next}`);
      last = next;
    }
  }
});

test('a minute-only clock correcting from a one-minute lag catches up without a backward jump', () => {
  const clock = create();
  clock.accept(status('09:15:00'), 0);
  clock.accept(status('09:15:10'), 2000);
  const before = clock.seconds(2000);
  clock.accept(status('09:16:05'), 4000);
  assert.ok(clock.seconds(4000) >= before);
  let previous = clock.seconds(4000);
  for (let at = 4250; at <= 25000; at += 250) {
    const now = clock.seconds(at);
    assert.ok(now >= previous, 'hands should only progress during gradual correction');
    previous = now;
  }
  assert.ok(clock.seconds(25000) >= (9 * 3600 + 16 * 60 + 5 + 21 * 5) % 86400);
});

test('small negative corrections slow the hands instead of resetting the second hand', () => {
  const clock = create();
  clock.accept(status('10:00:00'), 0);
  clock.accept(status('10:00:07'), 2000); // server is three simulated seconds behind
  const before = clock.seconds(2000);
  assert.equal(clock.seconds(2000), before);
  for (let at = 2250, previous = before; at <= 12000; at += 250) {
    const now = clock.seconds(at);
    assert.ok(now >= previous);
    previous = now;
  }
});

test('stop, start and explicit time jumps follow server immediately', () => {
  const clock = create();
  clock.accept(status('06:00:00'), 0);
  clock.accept(status('06:00:15', 5, false), 2000);
  assert.equal(clock.seconds(5000), 6 * 3600 + 15);
  clock.accept(status('06:00:15'), 6000);
  assert.equal(clock.seconds(8000), 6 * 3600 + 25);
  clock.accept(status('12:00:00'), 9000);
  assert.equal(clock.seconds(9000), 12 * 3600);
});
