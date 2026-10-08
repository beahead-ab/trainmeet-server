/* Urtavlor: hur visarna går, och uppladdade klockor (klockpaket).
 *
 * Inbyggda tavlor ritas av app.js. En uppladdad klocka (Inställningar →
 * Klockan, clock_pack.py på servern) är bilder: tavlan, tim-, minut- och
 * sekundvisaren och ett lager ovanpå, alla lika stora och med visarna pekande
 * mot 12. Här läggs de på varandra som <image> i en SVG, så att de visas som
 * bilder: en SVG i ett klockpaket kan inte köra något i sidan. Hur visarna
 * går (jämnt, i hopp, sekundvisarens svep) står i paketets clock.json.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TrainMeetClockFace = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const PREFIX = "custom:";
  const DEFAULT_MOTION = Object.freeze({ hour: "smooth", minute: "smooth", minute_bounce: false, second: "smooth", sweep_seconds: 60 });

  /**
   * Visarnas vinklar i grader för en tid i sekunder efter midnatt.
   * hour: smooth (glider) eller minute (flyttar sig varje minut);
   * minute: smooth eller jump (hoppar vid varje hel minut), med `bounce`
   * grader studs just efter hoppet; second: smooth, tick (ett steg i
   * sekunden) eller sweep (varvet på sweep_seconds, sedan väntar den vid 12).
   */
  function handAngles(seconds, motion = DEFAULT_MOTION, bounce = 0) {
    const m = { ...DEFAULT_MOTION, ...(motion || {}) };
    const day = ((Number(seconds) % 86400) + 86400) % 86400;
    const hour = Math.floor(day / 3600) % 12;
    const minute = Math.floor((day % 3600) / 60);
    const second = day % 60;
    const hourAngle = m.hour === "minute" ? (hour + minute / 60) * 30 : (hour + minute / 60 + second / 3600) * 30;
    const minuteAngle = m.minute === "jump" ? minute * 6 + bounce : (minute + second / 60) * 6;
    const secondAngle = m.second === "sweep" ? Math.min(second / Number(m.sweep_seconds || 60), 1) * 360
      : m.second === "tick" ? Math.floor(second) * 6
        : second * 6;
    return { hour: hourAngle, minute: minuteAngle, second: secondAngle };
  }

  /**
   * Minutvisarens studs efter ett hopp, som på ett elektriskt stationsur:
   * en dämpad svängning på en knapp grad som klingar av på en halv sekund.
   * Ett spår per sida; `now` i millisekunder.
   */
  function bounceTracker() {
    let key = null, startedAt = null;
    return function bounce(minuteKey, running, now) {
      if (key === null) key = minuteKey;
      if (minuteKey !== key) {
        key = minuteKey;
        startedAt = running ? now : null;
      }
      if (startedAt === null) return 0;
      const elapsed = (now - startedAt) / 1000;
      const decay = Math.exp(-6 * elapsed);
      if (decay <= 0.01) { startedAt = null; return 0; }
      return 1.8 * decay * Math.sin(8 * Math.PI * 2 * elapsed);
    };
  }

  // De uppladdade klockorna, ur clock.faces i /v1/display eller /v1/clock.
  const faces = new Map();
  function remember(list) {
    if (!Array.isArray(list)) return;
    faces.clear();
    for (const face of list) if (face && typeof face.style === "string") faces.set(face.style, face);
  }
  const isCustom = (style) => typeof style === "string" && style.startsWith(PREFIX);
  const find = (style) => faces.get(style) || null;
  const all = () => [...faces.values()];

  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[c]);
  // Bara serverns egna adresser till lagren; aldrig något annat i href.
  const layerUrl = (url) => (typeof url === "string" && /^\/v1\/clock-faces\/[a-z0-9-]+\/[0-9a-f]{16}\/(?:dark-)?[a-z]+$/.test(url) ? url : "");
  // Sidans tema (kr-theme.js): skärmar och deltagarvyn i mörkt läge använder
  // paketets mörka variant när det har en.
  const pageIsDark = () => (typeof document === "undefined" ? true : document.documentElement.dataset.krTheme !== "light");
  /** Lagrets adress, ur den mörka varianten när skärmen är mörk och paketet har en. */
  const layerFor = (face, layer, dark) => layerUrl((dark && face?.dark_layers?.[layer]) || face?.layers?.[layer]);

  /** En uppladdad klocka som SVG-markup, med visarna där app.js vrider dem. */
  function markup(face, { showSeconds = true, stopped = false, dark = pageIsDark() } = {}) {
    const image = (layer, hand) => {
      const url = layerFor(face, layer, dark);
      if (!url) return "";
      const handAttributes = hand ? ` data-clock-hand="${hand}" transform="rotate(0 100 100)"` : "";
      return `<image href="${escape(url)}" x="0" y="0" width="200" height="200" preserveAspectRatio="xMidYMid meet"${handAttributes}/>`;
    };
    return `<svg class="clock-face clock-face--custom${stopped ? " stopped" : ""}" viewBox="0 0 200 200" role="img" aria-label="${escape(face?.name)}" data-face="${escape(face?.id)}">`
      + image("dial") + image("hour", "hour") + image("minute", "minute") + (showSeconds ? image("second", "second") : "") + image("top")
      + "</svg>";
  }

  return { PREFIX, DEFAULT_MOTION, handAngles, bounceTracker, remember, isCustom, find, all, markup, layerUrl, layerFor, pageIsDark };
});
