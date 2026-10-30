/* Shared monotonic presentation clock for Drift, public views and fullscreen.
 * Keep time advancing at the provider's speed. Small clock corrections are
 * slewed into the display; a clock/source/stop change is immediate.
 * Traffic decisions always use the authoritative server, never this clock. */
(function(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TrainMeetClockSync = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function() {
  "use strict";
  const DAY = 86400;
  function parse(value) {
    const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(String(value || ""));
    return match ? Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3] || 0) : null;
  }
  function wrap(seconds) { return ((seconds % DAY) + DAY) % DAY; }
  function delta(target, current) { return wrap(target - current + DAY / 2) - DAY / 2; }
  function create() {
    let anchor = null, correction = 0;
    function seconds(at) {
      if (!anchor) return null;
      const age = anchor.running ? Math.max(0, (at - anchor.at) / 1000) : 0;
      const travel = age * anchor.speed;
      // At most a 60% rate correction. Even when the provider is behind, the
      // hands always move forward; never jump backwards on routine polling.
      const adjustment = Math.sign(correction) * Math.min(Math.abs(correction), age * anchor.speed * 0.6);
      return wrap(anchor.seconds + travel + adjustment);
    }
    function accept(clock, at) {
      const reported = parse(clock?.time);
      if (reported === null) return;
      const speed = Math.max(0.01, Number(clock.speed) || 1);
      const running = Boolean(clock.running), source = clock.source || "internal";
      const key = clock.external_name || "";
      if (!anchor || anchor.running !== running || anchor.speed !== speed || anchor.source !== source || anchor.key !== key) {
        anchor = {seconds: reported, at, speed, running, source, key};
        correction = 0;
        return;
      }
      const current = seconds(at);
      const error = delta(reported, current);
      if (Math.abs(error) > 120 || !running) {
        // Explicit clock reposition or pause: obey the authoritative value.
        anchor = {...anchor, seconds: reported, at};
        correction = 0;
      } else {
        anchor = {...anchor, seconds: current, at};
        correction = Math.abs(error) > 0.75 ? error : 0;
      }
    }
    return {accept, seconds, reset: () => { anchor = null; correction = 0; }};
  }
  return {create, parse, delta};
});
