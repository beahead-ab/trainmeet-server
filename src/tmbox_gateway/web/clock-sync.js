/* Monotonic presentation clock: display-only, never makes traffic decisions.
 * Small corrections are absorbed by changing the hand speed rather than
 * jumping them backwards at each poll. Major user clock changes are immediate.
 */
(function(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TrainMeetClockSync = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function() {
  "use strict";
  const DAY = 86400;
  const MAX_SLEW_RATE = 0.4;
  const RESET_THRESHOLD = 120;
  const wrap = seconds => ((seconds % DAY) + DAY) % DAY;
  const delta = (target, current) => wrap(target - current + DAY / 2) - DAY / 2;
  function parse(value) {
    const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(String(value || ""));
    return match ? Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3] || 0) : null;
  }
  function create() {
    let anchor = null, correction = 0;
    function seconds(at) {
      if (!anchor) return null;
      const age = anchor.running ? Math.max(0, (at - anchor.at) / 1000) : 0;
      const travel = age * anchor.speed;
      // Applying a negative correction can only slow the clock, never turn
      // its hands backwards. Positive corrections accelerate it temporarily.
      const adjust = Math.sign(correction) * Math.min(Math.abs(correction), travel * MAX_SLEW_RATE);
      return wrap(anchor.seconds + travel + adjust);
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
      if (!running || Math.abs(error) > RESET_THRESHOLD) {
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
