/* Dygnsskiftet i alla vyer (Casper 2026-10-08).
 *
 * Vid dygnsskiftet (förval 05:00) går träffen till nästa dag: alla tåg
 * flyttas till sin utgångspunkt och statusarna nollställs. En toast säger det
 * på Drift, i deltagarvyn, på skärmarna och i webb-TMBoxen, också för den som
 * öppnar sidan strax efter skiftet. Medan skiftet väntar på ett tåg som är
 * ute på linjen syns det också. Bilden kommer från display-feed.js, som
 * redan hämtar /v1/display (händelsen "trainmeet:display").
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TrainMeetDayChange = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  /** Så länge efter skiftet en sida som öppnas visar toasten, i sekunder. */
  const SHOWN_SECONDS = 90;

  /**
   * Vad vyn ska säga om dygnsskiftet ur /v1/display, eller null.
   * `seen` är skiftet sidan redan har visat (dess tid), så att samma skifte
   * inte visas två gånger.
   */
  function notice(snapshot, seen = null) {
    const calendar = snapshot?.calendar;
    if (!calendar) return null;
    const last = calendar.last_change;
    if (last?.kind === "day_change" && last.at && last.at !== seen) {
      const age = (Date.parse(snapshot.server_time) - Date.parse(last.at)) / 1000;
      if (Number.isFinite(age) && age >= -5 && age <= SHOWN_SECONDS) {
        return { kind: "changed", key: last.at, dayNumber: last.day_number, weekday: last.weekday };
      }
    }
    if (calendar.waiting) return { kind: "waiting", key: "waiting" };
    return null;
  }

  return { notice, SHOWN_SECONDS };
});

(function () {
  "use strict";
  if (typeof document === "undefined" || !globalThis.TrainMeetDayChange) return;
  const SEEN = "trainmeet.dayChangeSeen";
  const VISIBLE_MS = 12000;
  // Sidor utan språkstöd visar svenskan.
  const t = (source, values = {}) => globalThis.TrainMeetI18n?.t ? globalThis.TrainMeetI18n.t(source, values)
    : String(source).replace(/\{(\w+)\}/g, (match, name) => (name in values ? String(values[name]) : match));
  const toast = document.createElement("div");
  toast.className = "tm-day-change";
  toast.setAttribute("role", "status");
  toast.setAttribute("aria-live", "polite");
  toast.hidden = true;
  // Samma utseende i ljust och mörkt, på alla sidor (inga egna stilmallar behövs).
  toast.style.cssText = "position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:1000;"
    + "width:max-content;max-width:min(560px,calc(100vw - 32px));box-sizing:border-box;padding:12px 16px;"
    + "border-radius:10px;background:#1d3b7a;color:#fff;box-shadow:0 8px 24px #0005;cursor:pointer;"
    + "font:600 15px/1.4 Inter,-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;text-align:center";
  document.body.append(toast);
  let current = null, timer = null;

  const text = (item) => item.kind === "changed"
    ? t("Nytt trafikdygn: Dag {n} · {day}. Alla tåg står på sin utgångspunkt och statusarna är nollställda.",
      { n: item.dayNumber, day: item.weekday })
    : t("Dygnsskiftet väntar på tåg som är ute på linjen.");
  function hide() { clearTimeout(timer); toast.hidden = true; current = null; }
  function show(item) {
    clearTimeout(timer);
    current = item;
    toast.dataset.kind = item.kind;
    toast.textContent = text(item);
    toast.hidden = false;
    if (item.kind === "changed") timer = setTimeout(hide, VISIBLE_MS);
  }
  toast.addEventListener("click", hide);
  globalThis.TrainMeetI18n?.subscribe?.(() => { if (current) toast.textContent = text(current); });

  document.addEventListener("trainmeet:display", (event) => {
    let seen = null;
    try { seen = sessionStorage.getItem(SEEN); } catch {}
    const item = globalThis.TrainMeetDayChange.notice(event.detail, seen);
    if (item?.kind === "changed") {
      try { sessionStorage.setItem(SEEN, item.key); } catch {}
      show(item);
    } else if (item?.kind === "waiting") {
      if (current?.kind !== "changed") show(item);
    } else if (current?.kind === "waiting") {
      hide();
    }
  });
})();
