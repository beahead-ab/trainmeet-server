/* Drift · Träffens tid (Casper 2026-10-12).
 *
 * Träffens dygn på rad under klockan. Varje dygn går från sitt dygnsskifte
 * till nästa dags, med staplar för hur många tåg som är i gång. NU-linjen
 * visar var träffen står. Admin drar i tidslinjen (eller trycker på ett
 * snabbval) och ser innan något ändras vart hon hoppar, hur långt och hur
 * tågen kommer att stå där. Inget händer förrän hon trycker Hoppa. Efteråt
 * kan hoppet ångras: servern lägger tillbaka kopian som togs före.
 *
 * Servern räknar dygnen och lägena (GET /v1/runtime/timeline och
 * /v1/runtime/time-machine/preview) med samma regler som tidsmaskinen.
 * Strikt CSP: utseendet ligger i kontrollrummet.css, mått sätts med CSSOM.
 */
(() => {
  "use strict";
  const doc = document;
  const $ = (selector) => doc.querySelector(selector);
  const t = (source, values) => globalThis.TrainMeetI18n?.t
    ? globalThis.TrainMeetI18n.t(source, values || {})
    : String(source).replace(/\{(\w+)\}/g, (match, key) => values?.[key] ?? match);
  const app = () => globalThis.TrainMeetApp;
  const STEP = 300;
  const DAY = 86400;
  const REFRESH_MS = 20000;

  const view = { data: null, supported: null, fetchedAt: 0, triedAt: 0, target: null, preview: null, previewKey: "", previewTimer: 0,
    extraDays: 0, busy: false, undo: null, lang: "", loading: false };

  // ── Tid och läge på tidslinjen ────────────────────────────────────────
  const pad = (n) => String(n).padStart(2, "0");
  const clock = (seconds) => { const m = Math.floor(seconds / 60) % 1440; return `${pad(Math.floor(m / 60))}:${pad(m % 60)}`; };
  const dayLabel = (day) => t("Dag {n} · {day}", { n: day.day_number, day: day.weekday });

  /** Dygnen med sin plats på axeln: offset är sekunder från första dygnets början. */
  function segments() {
    let offset = 0;
    return (view.data?.days || []).map((day) => {
      const seg = { ...day, offset, len: Math.max(STEP, day.end - day.start) };
      offset += seg.len;
      return seg;
    });
  }
  const total = (segs) => segs.reduce((sum, seg) => sum + seg.len, 0) || DAY;
  const segOf = (segs, number) => segs.find((seg) => seg.day_number === number);

  /** Var träffen står nu, i dagens sekunder; klockan går vidare mellan hämtningarna. */
  function nowPoint() {
    const now = view.data?.now;
    if (!now) return null;
    let seconds = Number(now.seconds) || 0;
    if (now.running && !now.external) seconds += (Date.now() - view.fetchedAt) / 1000 * (Number(now.speed) || 1);
    const seg = segOf(segments(), now.day_number);
    // FastClock ger bara tiden på dygnet: före dagens skifte är det natten efter.
    if (now.external && seg && seconds < seg.start) seconds += DAY;
    return { day_number: now.day_number, seconds };
  }
  function toAxis(point, segs = segments()) {
    const seg = point && segOf(segs, point.day_number);
    if (!seg) return 0;
    return seg.offset + Math.max(0, point.seconds - seg.start);
  }
  function fromAxis(x, segs = segments()) {
    let seg = segs[0];
    for (const candidate of segs) if (candidate.offset <= x) seg = candidate;
    if (!seg) return null;
    const seconds = seg.start + Math.min(seg.len - 1, Math.max(0, x - seg.offset));
    return { day_number: seg.day_number, seconds: Math.floor(seconds / 60) * 60 };
  }
  const planning = () => Boolean(view.target);
  const percent = (x, segs) => `${(Math.min(1, Math.max(0, x / total(segs))) * 100).toFixed(3)}%`;

  function span(seconds) {
    const a = Math.round(Math.abs(seconds) / 60);
    const days = Math.floor(a / 1440), hours = Math.floor((a % 1440) / 60), minutes = a % 60;
    const parts = [];
    if (days) parts.push(t("{n} d", { n: days }));
    if (hours) parts.push(t("{n} h", { n: hours }));
    if (minutes || !parts.length) parts.push(t("{n} min", { n: minutes }));
    return parts.join(" ");
  }

  // ── Hämta ─────────────────────────────────────────────────────────────
  /** Bara Drift (läget "kor") frågar: skärmarna och deltagarvyn använder samma sida. */
  function visible() {
    const panel = $("#overview-view");
    return Boolean(doc.body?.dataset.mode === "kor" && panel && !panel.classList.contains("hidden") && !panel.hidden
      && doc.visibilityState !== "hidden");
  }
  async function load(force = false) {
    // Utan svar än (inte inloggad, servern startar) försöker den igen efter några sekunder.
    const wait = view.data ? REFRESH_MS : 3000;
    if (!app() || view.loading || !visible() || (!force && Date.now() - Math.max(view.fetchedAt, view.triedAt) < wait)) return;
    view.loading = true;
    view.triedAt = Date.now();
    try {
      const days = Math.max(0, (view.data?.days?.at(-1)?.day_number || 0) + view.extraDays);
      const response = await app().fetch(`/v1/runtime/timeline${view.extraDays ? `?days=${days}` : ""}`);
      if (!response.ok) return;
      const data = await response.json();
      view.data = data?.supported ? data : null;
      // Bara när servern säger nej göms panelen; ett svar utan tidslinje lämnar knapparna kvar.
      view.supported = data?.supported === false ? false : data?.supported ? true : null;
      view.fetchedAt = Date.now();
      view.extraDays = 0;
      if (view.data && !view.busy) keepUndo(view.data.undo);
      render(true);
    } catch {
      // Servern startar om eller är borta en stund: nästa hämtning försöker igen.
    } finally {
      view.loading = false;
    }
  }

  /** Ångra så länge servern erbjuder det: rutan efter ett hopp behåller sin text, och den som valt Behåll hoppet slipper den. */
  function keepUndo(offer) {
    const shown = view.undo;
    if (offer && offer.backup !== view.dismissed) {
      view.undo = shown?.done && shown.backup === offer.backup ? { ...offer, done: shown.done, shownAt: shown.shownAt } : offer;
    } else if (!(shown?.done && Date.now() - shown.shownAt < 10000)) {
      view.undo = null;
    }
  }

  function schedulePreview() {
    clearTimeout(view.previewTimer);
    if (!planning()) return;
    const key = `${view.target.day_number}:${view.target.seconds}`;
    if (key === view.previewKey) return;
    view.previewTimer = setTimeout(async () => {
      try {
        const response = await app().fetch(`/v1/runtime/time-machine/preview?day_number=${view.target.day_number}&seconds=${view.target.seconds}`);
        const data = await response.json();
        if (!response.ok || !planning() || key !== `${view.target.day_number}:${view.target.seconds}`) return;
        view.preview = data;
        view.previewKey = key;
        renderPlan();
      } catch {
        // Förhandsvisningen är en hjälp: hoppet går att göra ändå.
      }
    }, 180);
  }

  // ── Rita ──────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    const node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function say(node, source, values) {
    node.dataset.tmText = source;
    if (values) node.dataset.tmValues = JSON.stringify(values); else delete node.dataset.tmValues;
    node.textContent = t(source, values);
  }

  function render(all = false) {
    const panel = $("#drift-timeline");
    if (!panel) return;
    const data = view.data;
    // Klockradens knappar för nytt dygn och exakt tid ligger här: panelen göms
    // bara när servern säger att träffen inte har några dygn (US, ingen tidtabell).
    panel.hidden = view.supported === false;
    if (!data) {
      for (const id of ["#drift-timeline-body", "#drift-timeline-plan", "#drift-timeline-undo"]) $(id).hidden = true;
      return;
    }
    const calendar = data.calendar || {};
    const mode = $("#drift-timeline-mode");
    if (calendar.day_change_mode === "auto") say(mode, "Nytt trafikdygn: automatiskt kl. {time}", { time: calendar.change_time || "05:00" });
    else say(mode, "Nytt trafikdygn: manuellt");
    $("#drift-timeline-body").hidden = false;
    const segs = segments();
    const now = nowPoint();
    const nowX = toAxis(now, segs);
    const range = $("#drift-timeline-range");
    range.max = String(total(segs));
    if (all || view.lang !== doc.documentElement.lang) { renderDays(segs, now); renderTicks(segs); view.lang = doc.documentElement.lang; }
    renderBars(segs, nowX);
    renderMarks(segs, now, nowX);
    if (!planning() && doc.activeElement !== range) range.value = String(Math.round(nowX / STEP) * STEP);
    range.setAttribute("aria-valuetext", pointLabel(planning() ? view.target : now));
    const due = $("#drift-timeline-due");
    due.hidden = !(calendar.day_change_mode === "manual" && calendar.due);
    if (!due.hidden) {
      const today = segOf(segs, calendar.day_number);
      say($("#drift-timeline-due-text"), "Dygnsskiftet har passerats. Träffen står kvar på {day}, så tidtabellen visar gårdagens tåg som avgångna och ankomna. Starta nästa dag med Starta ny dag.",
        { day: today ? dayLabel(today) : t("Dag {n} · {day}", { n: calendar.day_number, day: calendar.weekday || "" }) });
    }
    // Samma rad i båda lägena, så att tidslinjen inte hoppar när man börjar dra.
    say($("#drift-timeline-hint"), planning()
      ? "Planerar ett hopp. Det blå fältet är tiden du hoppar över. Inget är ändrat än."
      : "Dra i tidslinjen för att välja en annan dag och tid. Du ser hur tågen kommer att stå innan något ändras – inget händer förrän du trycker Hoppa.");
    if (all && !view.scrolled) {
      // På en smal skärm rullar tidslinjen i sidled: visa där träffen står.
      const scroller = $(".kr-tl__scroll");
      if (scroller && scroller.scrollWidth > scroller.clientWidth) {
        scroller.scrollLeft = Math.max(0, scroller.scrollWidth * (nowX / total(segs)) - scroller.clientWidth / 2);
        view.scrolled = true;
      }
    }
    renderPlan();
    renderUndo();
  }

  function pointLabel(point) {
    if (!point) return "";
    const seg = segOf(segments(), point.day_number);
    return t("{day} kl. {time}", { day: seg ? dayLabel(seg) : String(point.day_number), time: clock(point.seconds) });
  }

  function renderDays(segs, now) {
    const host = $("#drift-timeline-days");
    host.replaceChildren(...segs.map((seg) => {
      const cell = el("div", "kr-tl__day");
      cell.style.width = percent(seg.len, segs);
      const state = !now ? "" : seg.day_number < now.day_number ? t("klar") : seg.day_number === now.day_number ? t("pågår") : t("kommer");
      if (seg.day_number === now?.day_number) cell.classList.add("is-now");
      cell.append(el("b", "", dayLabel(seg)), el("span", "", state));
      return cell;
    }));
  }

  function renderTicks(segs) {
    const host = $("#drift-timeline-ticks");
    const ticks = [];
    for (const seg of segs) {
      for (let hour = 0; hour <= 48; hour += 6) {
        const at = hour * 3600;
        if (at <= seg.start || at >= seg.start + seg.len) continue;
        const tick = el("span", "kr-tl__tick", pad(hour % 24));
        tick.style.left = percent(seg.offset + at - seg.start, segs);
        ticks.push(tick);
      }
    }
    host.replaceChildren(...ticks);
  }

  function renderBars(segs, nowX) {
    const host = $("#drift-timeline-bars");
    const slot = Number(view.data.slot_seconds) || 1800;
    const peak = Math.max(1, ...segs.flatMap((seg) => seg.density || []));
    const targetX = planning() ? toAxis(view.target, segs) : null;
    const lo = targetX === null ? null : Math.min(nowX, targetX), hi = targetX === null ? null : Math.max(nowX, targetX);
    const bars = [];
    for (const seg of segs) {
      (seg.density || []).forEach((count, index) => {
        const from = seg.offset + index * slot, to = Math.min(seg.offset + seg.len, from + slot);
        const bar = el("span", "kr-tl__bar");
        bar.style.flexGrow = String(Math.max(1, to - from));
        bar.style.height = `${count ? 6 + Math.round(count / peak * 58) : 2}px`;
        if (lo !== null && to > lo && from < hi) bar.classList.add("is-span");
        else if (to <= nowX) bar.classList.add("is-past");
        bars.push(bar);
      });
    }
    host.replaceChildren(...bars);
  }

  function renderMarks(segs, now, nowX) {
    const host = $("#drift-timeline-marks");
    const marks = [];
    segs.slice(1).forEach((seg) => {
      const line = el("span", "kr-tl__shift");
      line.style.left = percent(seg.offset, segs);
      const label = el("span", "kr-tl__shift-label", t("dygnsskifte {time}", { time: clock(seg.start) }));
      label.style.left = percent(seg.offset, segs);
      marks.push(line, label);
    });
    const calendar = view.data.calendar || {};
    const today = segOf(segs, calendar.day_number);
    if (today && calendar.day_change_mode === "manual" && calendar.due && nowX > today.offset + today.len) {
      // Dygnsskiftet har passerats men nästa dag är inte startad.
      const zone = el("span", "kr-tl__due-zone");
      zone.style.left = percent(today.offset + today.len, segs);
      zone.style.width = percent(nowX - today.offset - today.len, segs);
      marks.push(zone);
    }
    const nowLine = el("span", "kr-tl__now");
    nowLine.style.left = percent(nowX, segs);
    const nowPill = el("span", "kr-tl__now-pill", t("NU {time}", { time: clock(now?.seconds || 0) }));
    nowPill.style.left = percent(nowX, segs);
    marks.push(nowLine, nowPill);
    if (planning()) {
      const x = toAxis(view.target, segs);
      const line = el("span", "kr-tl__target");
      line.style.left = percent(x, segs);
      const pill = el("span", "kr-tl__target-pill", clock(view.target.seconds));
      pill.style.left = percent(x, segs);
      marks.push(line, pill);
    } else {
      const grip = el("span", "kr-tl__grip");
      grip.style.left = percent(nowX, segs);
      marks.push(grip);
    }
    host.replaceChildren(...marks);
  }

  function renderPlan() {
    const plan = $("#drift-timeline-plan");
    plan.hidden = !planning() || !view.data;
    if (plan.hidden) return;
    const segs = segments();
    const target = view.target;
    const seg = segOf(segs, target.day_number);
    const now = nowPoint();
    const delta = toAxis(target, segs) - toAxis(now, segs);
    $("#drift-timeline-plan-day").textContent = seg ? dayLabel(seg) : "";
    $("#drift-timeline-plan-time").textContent = clock(target.seconds);
    const night = $("#drift-timeline-plan-night");
    const next = segOf(segs, target.day_number + 1);
    night.hidden = target.seconds < DAY;
    if (!night.hidden) say(night, "Natten mot {weekday}: nattåg hör till {day}.", { weekday: next?.weekday || "", day: seg ? dayLabel(seg) : "" });
    say($("#drift-timeline-plan-delta"), delta >= 0 ? "{span} framåt från nu" : "{span} bakåt från nu", { span: span(delta) });
    const preview = view.previewKey === `${target.day_number}:${target.seconds}` ? view.preview : null;
    const count = (id, value) => { $(id).textContent = preview ? String(value) : "…"; };
    count("#drift-timeline-on-line", preview?.on_line);
    count("#drift-timeline-at-station", preview?.at_station);
    count("#drift-timeline-not-started", preview?.not_started);
    count("#drift-timeline-finished", preview?.finished);
    const nextText = $("#drift-timeline-next");
    if (!preview) say(nextText, "Räknar …");
    else if (preview.next) say(nextText, "Nästa avgång {time} · tåg {train} {from} → {to}. Tåg som ska vara ute på linjen ställs på sin avgångsstation.",
      { time: preview.next.time, train: preview.next.train_number, from: preview.next.from, to: preview.next.to });
    else say(nextText, "Inga fler avgångar det dygnet.");
    const label = pointLabel(target);
    say($("#drift-timeline-plan-place"), "Alla tåg ställs där tidtabellen säger {when}.", { when: label });
    say($("#drift-timeline-plan-clock"), view.data.now?.running
      ? "Klockan går vidare från {time}. Boxar mitt i en inmatning börjar om."
      : "Klockan står på {time} tills du startar den. Boxar mitt i en inmatning börjar om.", { time: clock(target.seconds) });
    const calendar = view.data.calendar || {};
    const alt = $("#drift-timeline-plan-newday");
    alt.hidden = !(calendar.day_change_mode === "manual" && now && target.day_number === now.day_number + 1);
    if (!alt.hidden) say($("#drift-timeline-newday"), "Starta {day} i stället…", { day: seg ? dayLabel(seg) : "" });
    say($("#drift-timeline-jump"), "Hoppa till {when}", { when: label });
    $("#drift-timeline-jump").disabled = view.busy;
  }

  function renderUndo() {
    const box = $("#drift-timeline-undo");
    const undo = view.undo;
    box.hidden = !undo;
    if (!undo) return;
    const back = undo.from || {};
    const text = $("#drift-timeline-undo-text");
    if (undo.done) say(text, undo.done.source, undo.done.values);
    else say(text, "Det senaste hoppet kan ångras i {n} minuter till.", { n: Math.max(1, Math.round((undo.seconds_left || 0) / 60)) });
    say($("#drift-timeline-undo-open"), "Ångra – tillbaka till Dag {n} · {day} {time}", { n: back.day_number ?? "", day: back.weekday || "", time: back.time || "" });
  }

  // ── Göra ──────────────────────────────────────────────────────────────
  function setTarget(point) {
    const segs = segments();
    if (!point || !segs.length) return;
    const now = nowPoint();
    const x = Math.min(total(segs) - 1, Math.max(0, toAxis(point, segs)));
    const snapped = fromAxis(x, segs);
    view.target = now && Math.abs(toAxis(snapped, segs) - toAxis(now, segs)) < STEP ? null : snapped;
    if (view.target) $("#drift-timeline-range").value = String(toAxis(view.target, segs));
    setMessage("");
    render();
    schedulePreview();
  }
  function setMessage(text, kind = "", values) {
    const message = $("#drift-timeline-message");
    if (message) app()?.setMessage(message, text, kind, values);
  }

  async function step(kind) {
    const now = nowPoint();
    const from = view.target || now;
    if (!from) return;
    const segs = segments();
    if (kind === "now") { view.target = null; view.preview = null; view.previewKey = ""; render(); return; }
    const seg = segOf(segs, from.day_number);
    if (kind === "start" && seg) return setTarget({ day_number: seg.day_number, seconds: seg.start });
    if (kind === "first" && seg) return setTarget({ day_number: seg.day_number, seconds: seg.first === null || seg.first === undefined ? seg.start : Math.max(seg.start, seg.first - 600) });
    const number = from.day_number + Number(kind);
    if (number < 1) return;
    if (!segOf(segs, number)) {
      // Fler dagar än tidslinjen visar: hämta dem och fortsätt dit.
      view.extraDays = 1;
      await load(true);
    }
    const target = segOf(segments(), number);
    if (target) setTarget({ day_number: number, seconds: Math.max(target.start, Math.min(target.start + target.len - 60, from.seconds)) });
  }

  async function jump() {
    if (!planning() || view.busy) return;
    const target = view.target;
    view.busy = true;
    setMessage("Tågen flyttas …", "notice");
    renderPlan();
    try {
      const response = await app().fetch("/v1/runtime/time-machine", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ day_number: target.day_number, seconds: target.seconds }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || t("Tidsmaskinen kunde inte hoppa."));
      const label = pointLabel(target);
      view.target = null; view.preview = null; view.previewKey = "";
      view.undo = payload.undo ? { ...payload.undo, shownAt: Date.now(),
        done: { source: "Klart: {when}. Alla tåg står där tidtabellen säger. En säkerhetskopia togs först.", values: { when: label } } } : null;
      view.busy = false;
      setMessage("");
      render();
      // Klockan, dagen och tågen på resten av Drift; tidslinjen hämtar sitt nya läge.
      Promise.resolve(app().refresh()).catch(() => {}).finally(() => load(true));
    } catch (error) {
      setMessage(error.message, "error");
    } finally {
      view.busy = false;
      renderPlan();
    }
  }

  async function undo() {
    const undoing = view.undo;
    if (!undoing || view.busy) return;
    view.busy = true;
    const message = $("#drift-timeline-undo-message");
    app().setMessage(message, "Lägger tillbaka kopian …", "notice");
    try {
      const response = await app().fetch("/v1/runtime/undo-jump", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ backup: undoing.backup }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.message || t("Hoppet gick inte att ångra."));
      $("#drift-timeline-undo-confirm").hidden = true;
      app().setMessage(message, payload.message || "Hoppet ångras och servern startar om.", "notice");
      await app().restarting(message);
    } catch (error) {
      view.busy = false;
      app().setMessage(message, error.message, "error");
    }
  }

  // ── Koppla ihop ───────────────────────────────────────────────────────
  function start() {
    const range = $("#drift-timeline-range");
    if (!range) return;
    range.addEventListener("input", () => setTarget(fromAxis(Number(range.value))));
    doc.querySelectorAll("[data-tl-step]").forEach((button) => button.addEventListener("click", () => step(button.dataset.tlStep)));
    $("#drift-timeline-cancel").addEventListener("click", () => step("now"));
    $("#drift-timeline-jump").addEventListener("click", jump);
    $("#drift-timeline-newday").addEventListener("click", (event) => app()?.openModal("new-day-modal", event.currentTarget));
    $("#drift-timeline-undo-open").addEventListener("click", () => { $("#drift-timeline-undo-confirm").hidden = false; $("#drift-timeline-undo-yes").focus(); });
    $("#drift-timeline-undo-no").addEventListener("click", () => { $("#drift-timeline-undo-confirm").hidden = true; view.dismissed = view.undo?.backup; view.undo = null; renderUndo(); });
    $("#drift-timeline-undo-yes").addEventListener("click", undo);
    // Ett nytt dygn, ett hopp eller en ändrad klocka, var det än gjordes: servern säger
    // det i strömmen (/v1/events) och tidslinjen hämtar sitt läge. Utan ström
    // hämtar den själv var tjugonde sekund.
    globalThis.TrainMeetLive?.subscribe((topics) => { if (topics.has("runtime") || topics.has("clock")) load(true); }, { passive: true });
    doc.addEventListener("visibilitychange", () => load());
    // Nya dygn från dialogen Starta ny dag syns direkt.
    $("#new-day-form")?.addEventListener("submit", () => setTimeout(() => load(true), 1500));
    setInterval(() => {
      // En träff utan dygn (US) frågas igen först när servern säger att träffen ändrats.
      if (view.supported !== false && (!view.data || !globalThis.TrainMeetLive?.connected)) load();
      if (!view.data) return;
      if (visible()) { renderBars(segments(), toAxis(nowPoint())); renderMarks(segments(), nowPoint(), toAxis(nowPoint())); if (!planning() && doc.activeElement !== range) range.value = String(Math.round(toAxis(nowPoint()) / STEP) * STEP); }
      if (view.lang !== doc.documentElement.lang) render(true);
    }, 1000);
    load(true);
  }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", start); else start();
  globalThis.TrainMeetTimeline = { reload: () => load(true) };
})();
