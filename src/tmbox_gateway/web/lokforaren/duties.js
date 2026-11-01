/* Sök och filtrera träffens tjänstekatalog, utan nya serveranrop per tangent.
 * Tidsfiltren beskriver planen och använder bara den synkade träffklockan. */
(function (root) {
  "use strict";
  function normalize(value) {
    return String(value || "").toLowerCase().replace(/ø/g, "o").replace(/æ/g, "ae").replace(/ß/g, "ss")
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }
  const compact = (value) => normalize(value).replace(/[^a-z0-9]/g, "");
  function minutes(value) {
    const match = /^(\d{1,2}):([0-5]\d)$/.exec(String(value || ""));
    return match && Number(match[1]) < 24 ? Number(match[1]) * 60 + Number(match[2]) : null;
  }
  function filter(duties, options = {}) {
    const terms = normalize(options.query).trim().split(/\s+/).filter(Boolean);
    const collator = new Intl.Collator(options.language || "sv", {numeric: true, sensitivity: "base"});
    const result = duties.filter((duty) => {
      if (options.station && !(duty.route || []).includes(options.station)) return false;
      if (options.operator && duty.operator !== options.operator) return false;
      const fields = [duty.number, duty.operator, duty.vehicle, ...(duty.trains || []), ...(duty.route || [])];
      if (!terms.every((term) => fields.some((field) => normalize(field).includes(term)
        || (compact(term) && compact(field).includes(compact(term)))))) return false;
      if (options.time && options.time !== "all") {
        if (!Number.isFinite(options.now)) return false;
        const start = minutes(duty.start), end = minutes(duty.end);
        const now = ((options.now % 1440) + 1440) % 1440;
        if (start === null) return false;
        if (options.time === "soon" && (start - now + 1440) % 1440 > 60) return false;
        if (options.time === "ongoing" && (end === null || (start <= end
          ? now < start || now >= end : now < start && now >= end))) return false;
      }
      return true;
    });
    const rank = (duty) => !options.query ? 0 : normalize(duty.number) === normalize(options.query).trim() ? 0
      : normalize(duty.number).startsWith(normalize(options.query).trim()) ? 1 : 2;
    return result.sort((a, b) => {
      if (options.sort === "start") {
        const difference = (minutes(a.start) ?? Infinity) - (minutes(b.start) ?? Infinity);
        if (difference && Number.isFinite(difference)) return difference;
      } else if (rank(a) !== rank(b)) return rank(a) - rank(b);
      return collator.compare(a.number, b.number) || collator.compare(a.id, b.id);
    });
  }
  const api = {filter, normalize};
  root.LokforarenDuties = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(globalThis);
