/* Lokföraren (#225, #226): förarens app på TrainMeet Server.
 *
 * Träffklockan kommer bara från servern (/v1/driver/clock). Tappar telefonen
 * kontakten står klockan och NU-linjen still på senaste servertid; telefonens
 * egen klocka används aldrig i stället. Incheckningar och ikoner är socialt
 * läge, aldrig trafikbeslut: ingenting här styr tåg, klarering eller klocka.
 */
(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const K = globalThis.LokforarenKorplan;
  const app = $("#app");
  const LANGUAGES = ["sv", "da", "nb", "en", "de"];
  const CLOCK_POLL = 2000;
  const CLOCK_LOST_AFTER = 12000;
  const OFFLINE_HOURS = 12;
  const FOLLOW_AGAIN_AFTER = 4000;

  // ── Inställningar i telefonen ──────────────────────────────────────
  const store = {
    get(key, fallback) {
      try { const value = localStorage.getItem("lf." + key); return value === null ? fallback : JSON.parse(value); } catch { return fallback; }
    },
    set(key, value) { try { localStorage.setItem("lf." + key, JSON.stringify(value)); } catch { /* privat läge */ } },
  };
  const guessLanguage = () => {
    for (const tag of navigator.languages || [navigator.language || "sv"]) {
      const code = String(tag).toLowerCase().slice(0, 2);
      if (code === "no" || code === "nn") return "nb";
      if (LANGUAGES.includes(code)) return code;
    }
    return "sv";
  };
  let language = store.get("language", guessLanguage());

  function t(source, values = {}) {
    const row = (globalThis.LokforarenMessages || {})[source];
    const text = language === "sv" || !row ? source : (row[language] || source);
    return text.replace(/\{(\w+)\}/g, (match, key) => (key in values ? String(values[key]) : match));
  }

  function translateStatic() {
    document.documentElement.lang = language;
    for (const element of document.querySelectorAll("[data-t]")) {
      element.textContent = t(element.dataset.t);
    }
    $("#timeline").setAttribute("aria-label", t("Körplan"));
  }

  // ── Tillstånd ──────────────────────────────────────────────────────
  const state = {
    session: null,
    offline: false,
    tab: "duty",
    choosing: false,
    forceKorplan: false,
    plan: null,
    planFor: null,
    clock: null,
    clockOkAt: 0,
    sync: globalThis.TrainMeetClockSync.create(),
    frozenSeconds: null,
    presence: null,
    presenceAt: 0,
    seenCheckins: new Set(),
    popUntil: new Map(),
    chosenDuty: null,
    checkin: {station: "", stationId: "", track: ""},
    endArmedUntil: 0,
  };

  // ── Server ─────────────────────────────────────────────────────────
  class Unauthorized extends Error {}

  async function api(path, options = {}) {
    const init = {credentials: "same-origin", cache: "no-store", headers: {}};
    if (options.body !== undefined) {
      init.method = "POST";
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, init);
    if (response.status === 401) throw new Unauthorized();
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || t("Det gick inte. Försök igen."));
    return payload;
  }

  // ── Klockan ────────────────────────────────────────────────────────
  async function pollClock() {
    try {
      const payload = await api("/v1/driver/clock");
      state.clock = payload.clock;
      if (payload.clock && payload.clock.time) state.sync.accept(payload.clock, performance.now());
      state.clockOkAt = performance.now();
      state.offline = false;
    } catch (error) {
      if (error instanceof Unauthorized) { showLogin(); return; }
    } finally {
      clearTimeout(state.clockTimer);
      state.clockTimer = setTimeout(pollClock, CLOCK_POLL);
    }
  }

  function clockMode() {
    if (!state.clock || !state.clockOkAt || performance.now() - state.clockOkAt > CLOCK_LOST_AFTER) return "lost";
    if (!state.clock.time) return "idle";
    return state.clock.running ? "running" : "stopped";
  }

  // Sekunder på dygnet. Utan kontakt: senaste tid, fryst.
  function clockSeconds() {
    const mode = clockMode();
    if (mode === "lost") return state.frozenSeconds;
    const seconds = state.sync.seconds(performance.now());
    if (seconds !== null) state.frozenSeconds = seconds;
    return seconds;
  }

  const pad = (value) => String(value).padStart(2, "0");
  function hms(seconds, withSeconds = true) {
    if (seconds === null || seconds === undefined) return "--:--" + (withSeconds ? ":--" : "");
    const s = Math.floor(seconds);
    return pad(Math.floor(s / 3600) % 24) + ":" + pad(Math.floor(s / 60) % 60) + (withSeconds ? ":" + pad(s % 60) : "");
  }
  function left(minutes) {
    const total = Math.max(0, Math.ceil(minutes * 60));
    const h = Math.floor(total / 3600), m = Math.floor(total / 60) % 60, s = total % 60;
    return h > 0 ? h + ":" + pad(m) + ":" + pad(s) : m + ":" + pad(s);
  }

  // ── Ikoner ─────────────────────────────────────────────────────────
  const AVATAR_COLORS = ["#475569", "#0f5d57", "#7c3aed", "#b45309", "#1d4ed8", "#be123c", "#3f6212"];
  function fillAvatar(element, person) {
    if (!element) return;
    element.replaceChildren();
    if (person && person.avatar_url) {
      const image = document.createElement("img");
      image.alt = "";
      image.src = person.avatar_url;
      element.append(image);
      element.style.background = "transparent";
    } else {
      const name = person ? person.name : "?";
      let hash = 0;
      for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
      element.style.background = AVATAR_COLORS[hash % AVATAR_COLORS.length];
      element.textContent = person ? person.initials : "?";
    }
  }
  function me() { return state.session ? state.session.user : null; }

  // Tjänstens väg i korthet: tågens från- och tillstationer, annars stationerna.
  function routeOf(duty) {
    const route = [];
    const add = (name) => { if (name && route[route.length - 1] !== name) route.push(name); };
    if (duty.trains && duty.trains.some((train) => train.from && train.to)) {
      for (const train of duty.trains) { add(train.from); add(train.to); }
    } else {
      for (const event of duty.events) add(event.station);
    }
    return route;
  }

  // ── Skärmarna ──────────────────────────────────────────────────────
  const VIEWS = ["login", "choose", "countdown", "korplan", "drivers", "me"];
  function show(view) {
    for (const name of VIEWS) $("#view-" + name).hidden = name !== view;
    app.dataset.screen = view;
    $("#tabs").hidden = view === "login";
  }

  function activeDuty() {
    return state.session && state.session.active ? state.session.active.duty : null;
  }

  function showLogin() {
    clearTimeout(state.clockTimer);
    clearTimeout(state.presenceTimer);
    state.session = null;
    show("login");
    $("#login-email").focus();
  }

  function setTab(tab) {
    state.tab = tab;
    for (const button of document.querySelectorAll(".lf-tab")) {
      if (button.dataset.tab === tab) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    if (tab === "drivers") refreshPresence();
    route();
  }

  function route() {
    if (!state.session) return;
    if (state.tab === "drivers") { show("drivers"); renderDrivers(); return; }
    if (state.tab === "me") { show("me"); renderMe(); return; }
    const duty = activeDuty();
    if (!duty || state.choosing) { show("choose"); return; }
    ensurePlan();
    const seconds = clockSeconds();
    const now = seconds === null || !state.plan ? null : K.axisMinutes(seconds, state.plan);
    if (now !== null && now < state.plan.start - 10 && !state.forceKorplan) show("countdown");
    else show("korplan");
  }

  // ── Sidhuvud och remsa ─────────────────────────────────────────────
  function renderHeader(seconds) {
    const duty = activeDuty();
    $("#duty-title").textContent = duty ? t("Tjänst {number}", {number: duty.number}) : t("Lokföraren");
    $("#duty-demo").hidden = !(duty && duty.demo);
    let sub = state.session && state.session.meet ? state.session.meet.name : "";
    if (duty) sub = routeOf(duty).join(" → ") + (duty.operator ? " · " + duty.operator : "");
    $("#duty-sub").textContent = sub;
    const mode = clockMode();
    const clock = $("#clock");
    clock.textContent = hms(seconds);
    clock.classList.toggle("is-frozen", mode === "lost");
    const status = $("#clock-status");
    status.className = "lf-status" + ({running: " is-running", stopped: " is-stopped", lost: " is-lost"}[mode] || "");
    const speed = state.clock && state.clock.speed ? Number(state.clock.speed) : 1;
    $("#clock-status-text").textContent = {
      running: t("GÅR") + " · ×" + (Number.isInteger(speed) ? speed : speed.toFixed(1)),
      stopped: t("STOPPAD"),
      lost: t("INGEN KONTAKT"),
      idle: t("EJ IGÅNG"),
    }[mode];
    app.classList.toggle("is-stopped", mode === "stopped");
    app.classList.toggle("is-lost", mode === "lost");

    const banner = $("#banner");
    let text = "", kind = "", icon = "";
    if (mode === "lost" && state.session) {
      text = seconds === null ? t("Ingen kontakt med träffklockan") : t("Ingen kontakt med träffklockan · fryst {time}", {time: hms(seconds)});
      kind = "is-lost"; icon = "M12 3.5 2.5 20h19zM12 10v4.2M12 17.4v.01";
    } else if (mode === "stopped") {
      text = t("Träffklockan står still. NU-linjen väntar på den.");
      kind = "is-stopped"; icon = "M9 5v14M15 5v14";
    } else if (mode === "idle" && duty) {
      text = t("Träffklockan har inte startat än.");
      kind = "is-info"; icon = "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17zM12 7.5V12l3 2";
    } else if (duty && state.plan && seconds !== null && app.dataset.screen === "korplan") {
      const now = K.axisMinutes(seconds, state.plan);
      if (now < state.plan.start) {
        text = state.plan.start - now <= 10 ? t("10 min kvar · körplanen öppnades av sig själv") : t("Tjänsten börjar om {time}", {time: left(state.plan.start - now)});
        kind = "is-info"; icon = "M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15zM10 20.5a2 2 0 0 0 4 0";
      }
    }
    const showBanner = Boolean(text) && ["korplan", "countdown", "drivers", "me"].includes(app.dataset.screen);
    banner.hidden = !showBanner;
    if (showBanner) {
      banner.className = "lf-banner " + kind;
      $("#banner-text").textContent = text;
      $("#banner-icon").setAttribute("d", icon);
    }
  }

  // ── Välj tjänst ────────────────────────────────────────────────────
  let searchTimer = null;
  async function searchDuties() {
    const query = $("#duty-query").value.trim();
    try {
      const payload = await api("/v1/driver/duties?q=" + encodeURIComponent(query));
      renderResults(payload.duties || [], query);
    } catch (error) {
      if (error instanceof Unauthorized) showLogin();
    }
  }

  function renderResults(duties, query) {
    const list = $("#duty-results");
    list.replaceChildren();
    const shown = query ? duties : duties.slice(0, 6);
    for (const duty of shown) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "lf-result";
      button.setAttribute("aria-pressed", String(state.chosenDuty && state.chosenDuty.id === duty.id));
      const number = document.createElement("span");
      number.className = "lf-result__nr";
      number.textContent = duty.number;
      const text = document.createElement("span");
      text.className = "lf-result__text";
      const route = document.createElement("span");
      route.className = "lf-result__line";
      route.textContent = (duty.route || []).join(" → ");
      const trains = document.createElement("span");
      trains.className = "lf-result__line";
      trains.textContent = [duty.operator, (duty.trains || []).join(" · "), duty.demo ? "DEMO" : ""].filter(Boolean).join(" · ");
      text.append(route, trains);
      const time = document.createElement("span");
      time.className = "lf-result__time";
      time.textContent = duty.start + "–" + duty.end;
      button.append(number, text, time);
      button.addEventListener("click", () => chooseDuty(duty));
      list.append(button);
    }
    if (query && duties.length === 1) chooseDuty(duties[0]);
    if (!duties.length && query) {
      const empty = document.createElement("p");
      empty.className = "lf-empty";
      empty.textContent = t("Ingen tjänst {number} i träffens köruppdrag.", {number: query});
      list.append(empty);
    }
  }

  async function chooseDuty(summary) {
    try {
      const payload = await api("/v1/driver/duties/" + encodeURIComponent(summary.id));
      state.chosenDuty = payload.duty;
    } catch (error) {
      if (error instanceof Unauthorized) { showLogin(); return; }
      return;
    }
    const duty = state.chosenDuty;
    for (const button of document.querySelectorAll(".lf-result")) {
      button.setAttribute("aria-pressed", String(button.querySelector(".lf-result__nr").textContent === duty.number));
    }
    $("#duty-found").hidden = false;
    $("#found-title").textContent = t("Tjänst {number}", {number: duty.number});
    $("#found-demo").hidden = !duty.demo;
    const facts = $("#found-facts");
    facts.replaceChildren();
    const fact = (label, value) => {
      if (!value) return;
      const row = document.createElement("div");
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      row.append(dt, dd);
      facts.append(row);
    };
    const route = routeOf(duty);
    fact(t("Tid"), duty.start + "–" + duty.end);
    fact(t("Tåg"), duty.trains.map((train) => train.number).join(" · "));
    fact(t("Rutt"), route.join(" → "));
    fact(t("Bolag och fordon"), [duty.operator, duty.vehicle, duty.vmax ? "vmax " + duty.vmax : ""].filter(Boolean).join(" · "));
    $("#found-note").hidden = !duty.notes.length;
    $("#found-note").textContent = duty.notes.join(" ");
    const button = $("#duty-activate");
    button.disabled = false;
    button.textContent = t("Aktivera tjänst {number}", {number: duty.number});
  }

  async function activateDuty() {
    if (!state.chosenDuty) return;
    const button = $("#duty-activate");
    button.disabled = true;
    try {
      const payload = await api("/v1/driver/active", {body: {duty_id: state.chosenDuty.id}});
      state.session.active = payload.active;
      saveOffline();
      state.choosing = false;
      state.forceKorplan = false;
      state.chosenDuty = null;
      $("#duty-found").hidden = true;
      $("#duty-query").value = "";
      setTab("duty");
    } catch (error) {
      if (error instanceof Unauthorized) { showLogin(); return; }
      toast(error.message);
      button.disabled = false;
    }
  }

  function openChoose() {
    state.choosing = true;
    state.chosenDuty = null;
    $("#duty-found").hidden = true;
    $("#duty-activate").disabled = true;
    $("#duty-activate").textContent = t("Aktivera tjänsten");
    $("#duty-cancel").hidden = !activeDuty();
    $("#duty-query").value = "";
    state.tab = "duty";
    for (const button of document.querySelectorAll(".lf-tab")) {
      if (button.dataset.tab === "duty") button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    route();
    searchDuties();
  }

  // ── Körplanen ──────────────────────────────────────────────────────
  const timeline = {elements: null, follow: true, interactedAt: 0, touching: false, lastNowY: null, lastPast: -1};

  function ensurePlan() {
    const duty = activeDuty();
    const key = duty ? duty.id + "|" + (state.session.active.activated_at || "") : null;
    if (key === state.planFor) return;
    state.planFor = key;
    state.plan = duty ? K.build(duty) : null;
    if (state.plan) buildTimeline(state.plan);
  }

  const KIND = {
    duty_start: () => t("Tjänsten börjar"),
    duty_end: () => t("Tjänsten slutar"),
    departure: () => t("Avgång"),
    arrival: () => t("Ankomst"),
    stop: () => t("Ankomst"),
    pass: () => t("Passerar"),
    note: () => "",
  };

  function el(className, parent, text) {
    const element = document.createElement("div");
    element.className = className;
    if (text !== undefined) element.textContent = text;
    parent.append(element);
    return element;
  }

  function chip(text, parent, extra = "") {
    const element = document.createElement("span");
    element.className = "lf-chip" + extra;
    element.textContent = text;
    parent.append(element);
    return element;
  }

  function buildTimeline(plan) {
    const content = $("#timeline-content");
    content.replaceChildren();
    content.style.height = plan.height + "px";
    const y = plan.y;
    const parts = {segments: [], times: [], rows: [], deps: [], tint: null, me: null, metag: null};
    parts.tint = el("tl-tint", content);
    parts.tint.hidden = true;
    // Linjen ligger under texten; stationsnamnen har en kant i bakgrundsfärg.
    parts.line = el("tl-nowline", content);
    for (const segment of plan.segments) {
      const past = el("tl-rail is-" + segment.kind + " is-past", content);
      const future = el("tl-rail is-" + segment.kind, content);
      parts.segments.push({...segment, y0: y(segment.from), y1: y(segment.to), past, future});
    }
    for (const gap of plan.gaps) {
      const mark = el("tl-break", content);
      mark.style.top = (gap.y - 6) + "px";
      mark.innerHTML = '<svg viewBox="0 0 18 12" aria-hidden="true"><path d="M2 9 8 3M10 9l6-6"></path></svg>';
      const hours = Math.floor(gap.minutes / 60), minutes = Math.round(gap.minutes % 60);
      const length = hours ? t("{h} h {m} min", {h: hours, m: minutes}) : t("{m} min", {m: minutes});
      const label = el("tl-gap", content, gap.station ? t("Uppehåll i {station} · {length}", {station: gap.station, length}) : t("Uppehåll · {length}", {length}));
      label.style.top = (gap.y - 9) + "px";
    }
    for (const train of plan.trains) {
      const row = plan.rows[train.row];
      const box = el("tl-train", content);
      box.style.top = (y(row.t) - 44) + "px";
      const number = document.createElement("span");
      number.className = "tl-train__nr";
      number.textContent = train.number;
      const route = document.createElement("span");
      route.className = "tl-train__route";
      route.textContent = [train.from && train.to ? train.from + " → " + train.to : "", train.vmax ? "vmax " + train.vmax : ""].filter(Boolean).join(" · ");
      box.append(number, route);
    }
    plan.rows.forEach((row, index) => {
      for (const [t0, first] of row.t2 === undefined ? [[row.t, true]] : [[row.t, true], [row.t2, false]]) {
        const node = el("tl-node" + (row.kind.startsWith("duty") ? " is-duty" : ""), content);
        node.style.top = (y(t0) - (row.kind.startsWith("duty") ? 6 : 7)) + "px";
        const time = el("tl-time", content, K.label(t0));
        time.style.top = (y(t0) - 10) + "px";
        parts.times.push({t: t0, y: y(t0), node, time, first, index});
      }
      const box = el("tl-row", content);
      box.style.top = (y(row.t) - 11) + "px";
      const isDuty = row.kind.startsWith("duty");
      el("tl-row__name", box, isDuty ? KIND[row.kind]() : row.station);
      const sub = [isDuty ? row.station : KIND[row.kind](), row.track ? t("spår {track}", {track: row.track}) : ""].filter(Boolean).join(" · ");
      el("tl-row__sub", box, sub);
      if (row.notes.length && !isDuty) {
        const chips = el("lf-chips", box);
        for (const note of row.notes) chip(note, chips);
      } else if (row.notes.length && isDuty) {
        box.querySelector(".tl-row__sub").textContent = [row.station, ...row.notes].join(" · ");
      }
      parts.rows.push({row, box, end: row.t2 ?? row.t});
      if (row.t2 !== undefined) {
        const dep = el("tl-dep", content, [t("Avgång"), row.track ? t("spår {track}", {track: row.track}) : ""].filter(Boolean).join(" · "));
        dep.style.top = (y(row.t2) - 9) + "px";
        parts.deps.push({t: row.t2, dep});
      }
    });
    parts.me = el("tl-me lf-avatar", content);
    parts.me.hidden = true;
    parts.metag = el("tl-metag", content);
    parts.metag.hidden = true;
    parts.ring = el("tl-nowring", content);
    parts.chip = document.createElement("span");
    parts.chip.className = "lf-nowchip tl-nowchip";
    content.append(parts.chip);
    timeline.elements = parts;
    timeline.lastNowY = null;
    timeline.lastPast = -1;
    timeline.follow = true;
    timeline.jump = true;
    renderMyCheckin();
  }

  function updateTimeline(seconds) {
    const plan = state.plan, parts = timeline.elements;
    if (!plan || !parts) return;
    const box = $("#timeline");
    const haveTime = seconds !== null && seconds !== undefined;
    const now = haveTime ? K.axisMinutes(seconds, plan) : null;
    const nowY = haveTime ? plan.y(now) : null;
    for (const item of [parts.line, parts.ring, parts.chip]) item.hidden = !haveTime;
    if (haveTime) {
      parts.line.style.transform = `translateY(${nowY}px)`;
      parts.ring.style.transform = `translateY(${nowY}px)`;
      parts.chip.style.transform = `translateY(${nowY}px)`;
      const stopped = clockMode() === "stopped";
      parts.chip.innerHTML = stopped ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5v14M15 5v14"></path></svg>' : "";
      parts.chip.append(document.createTextNode(hms(seconds, !stopped)));
      parts.chip.setAttribute("aria-label", t("Nu, träfftid {time}", {time: hms(seconds)}));
    }
    if (haveTime && (timeline.lastNowY === null || Math.abs(nowY - timeline.lastNowY) > 0.2)) {
      timeline.lastNowY = nowY;
      for (const segment of parts.segments) {
        const cut = Math.min(Math.max(nowY, segment.y0), segment.y1);
        segment.past.style.top = segment.y0 + "px";
        segment.past.style.height = Math.max(0, cut - segment.y0) + "px";
        segment.future.style.top = cut + "px";
        segment.future.style.height = Math.max(0, segment.y1 - cut) + "px";
      }
      for (const item of parts.times) {
        const past = item.t < now;
        item.time.classList.toggle("is-past", past);
        item.node.classList.toggle("is-past", past);
        item.time.classList.toggle("is-hidden", Math.abs(item.y - nowY) < 17);
      }
      let tinted = null;
      for (const item of parts.rows) {
        item.box.classList.toggle("is-past", item.end < now);
        if (item.row.t2 !== undefined && item.row.t <= now && now < item.row.t2) tinted = item.row;
      }
      for (const item of parts.deps) item.dep.classList.toggle("is-past", item.t < now);
      parts.tint.hidden = !tinted;
      if (tinted) {
        parts.tint.style.top = (plan.y(tinted.t) - 20) + "px";
        parts.tint.style.height = (plan.y(tinted.t2) - plan.y(tinted.t) + 40) + "px";
      }
    }
    // Autofölj: linjen får vandra nedåt på skärmen och tas tillbaka till en
    // tredjedel när den passerat mitten, så att man ser att den rör sig.
    const height = box.clientHeight;
    if (!timeline.follow && !timeline.touching && performance.now() - timeline.interactedAt > FOLLOW_AGAIN_AFTER) {
      timeline.follow = true;
    }
    if (haveTime && timeline.follow && height > 0) {
      const target = Math.max(0, nowY - height * 0.33);
      const offset = nowY - box.scrollTop;
      if (timeline.jump) {
        box.scrollTop = target;
        timeline.jump = false;
      } else if (offset > height * 0.5 || offset < height * 0.12) {
        box.scrollTo({top: target, behavior: "smooth"});
      }
    }
    $("#to-now").hidden = timeline.follow || !haveTime;
    const above = haveTime && nowY < box.scrollTop + 2;
    const below = haveTime && nowY > box.scrollTop + height - 2;
    $("#edge-top").hidden = !above;
    $("#edge-bottom").hidden = !below;
    if (above || below) {
      const arrow = above ? "M12 19V5M6 11l6-6 6 6" : "M12 5v14M6 13l6 6 6-6";
      const target = above ? $("#edge-top-chip") : $("#edge-bottom-chip");
      target.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${arrow}"></path></svg>`;
      target.append(document.createTextNode(hms(seconds)));
    }
    renderNext(now);
  }

  function momentTitle(moment) {
    const station = moment.row.station;
    switch (moment.kind) {
      case "duty_start": return t("Tjänsten börjar i {station}", {station});
      case "duty_end": return t("Tjänsten slutar i {station}", {station});
      case "departure": return t("Avgång {station}", {station});
      case "pass": return t("Passerar {station}", {station});
      default: return t("Ankomst {station}", {station});
    }
  }

  let lastNextKey = "";
  function renderNext(now) {
    const plan = state.plan;
    if (!plan) return;
    const moment = now === null ? null : K.next(plan, now);
    const key = moment ? moment.t + moment.kind : "end";
    if (key !== lastNextKey) {
      lastNextKey = key;
      $("#next-label").textContent = moment ? t("NÄSTA") : t("KLART");
      $("#next-title").textContent = moment ? momentTitle(moment) : t("Tjänsten är slut");
      $("#next-time").textContent = moment ? K.label(moment.t) : K.label(plan.end);
      const chips = $("#next-chips");
      chips.replaceChildren();
      if (moment && moment.row.track) chip(t("spår {track}", {track: moment.row.track}), chips, " lf-chip--track");
      if (moment && moment.row.train && ["departure"].includes(moment.kind)) chip(moment.row.train, chips);
      if (moment) for (const note of moment.row.notes.slice(0, 3)) chip(note, chips);
    }
    const remaining = $("#next-left");
    if (moment && now !== null) {
      const minutes = moment.t - now;
      remaining.textContent = t("om {time}", {time: left(minutes)});
      remaining.classList.toggle("is-soon", minutes <= 5 && clockMode() === "running");
    } else {
      remaining.textContent = "";
    }
    const suggestion = now === null ? null : K.nearestStation(plan, now);
    const station = suggestion ? suggestion.station : "";
    const text = station ? t("Checka in · {station}", {station}) : t("Checka in");
    if ($("#open-checkin-text").textContent !== text) $("#open-checkin-text").textContent = text;
  }

  function renderMyCheckin() {
    const parts = timeline.elements;
    if (!parts || !state.plan) return;
    const mine = state.presence && state.presence.drivers.find((driver) => driver.me);
    const checkin = mine && mine.checkin;
    const seconds = clockSeconds();
    const now = seconds === null ? null : K.axisMinutes(seconds, state.plan);
    let row = null;
    if (checkin) {
      for (const candidate of state.plan.rows) {
        if (candidate.station !== checkin.station) continue;
        if (!row || (now !== null && Math.abs(candidate.t - now) < Math.abs(row.t - now))) row = candidate;
      }
    }
    parts.me.hidden = parts.metag.hidden = !row;
    if (!row) return;
    fillAvatar(parts.me, me());
    parts.me.style.top = (state.plan.y(row.t) - 12) + "px";
    parts.metag.style.top = (state.plan.y(row.t) - 12) + "px";
    parts.metag.textContent = checkin.meet_time ? t("Incheckad {time}", {time: checkin.meet_time.slice(0, 5)}) : t("Incheckad");
  }

  // ── Nedräkning ─────────────────────────────────────────────────────
  function renderCountdown(seconds) {
    const plan = state.plan;
    if (!plan) return;
    const duty = activeDuty();
    const now = seconds === null ? null : K.axisMinutes(seconds, plan);
    const remaining = now === null ? null : plan.start - now;
    const value = $("#countdown-value");
    value.textContent = remaining === null ? "–" : left(remaining);
    value.classList.toggle("is-soon", remaining !== null && remaining <= 10);
    const first = plan.rows[0];
    $("#countdown-sub").textContent = t("{time} träfftid · {station}", {time: K.label(plan.start), station: first.station}) + (first.notes.length ? " · " + first.notes[0].toLowerCase() : "");
    $("#mark-start").textContent = t("Start {time}", {time: K.label(plan.start)});
    const pct = remaining === null ? 0 : Math.max(0, Math.min(1, (30 - remaining) / 30));
    $("#countdown-fill").style.width = (pct * 100) + "%";
    $("#countdown-now").style.left = (pct * 100) + "%";
    $("#mark-20").classList.toggle("is-done", remaining !== null && remaining <= 20);
    $("#mark-10").classList.toggle("is-done", remaining !== null && remaining <= 10);
    const list = $("#first-moments");
    if (list.dataset.duty !== duty.id) {
      list.dataset.duty = duty.id;
      list.replaceChildren();
      for (const row of plan.rows.slice(0, 3)) {
        const item = document.createElement("li");
        const time = document.createElement("span");
        time.className = "lf-first__time";
        time.textContent = K.label(row.t);
        const what = document.createElement("span");
        what.className = "lf-first__what";
        const isDuty = row.kind.startsWith("duty");
        what.textContent = isDuty ? KIND[row.kind]() : (row.train && row.kind === "departure" ? t("{train} avgår", {train: row.train}) : KIND[row.kind]() + " " + row.station);
        const small = document.createElement("small");
        small.textContent = [row.station, ...row.notes].filter(Boolean).join(" · ");
        what.append(small);
        item.append(time, what);
        list.append(item);
      }
    }
  }

  // ── Påminnelser 20 och 10 min före start (i appen) ─────────────────
  function remind(seconds) {
    const plan = state.plan, duty = activeDuty();
    if (!plan || !duty || seconds === null || clockMode() !== "running" || !store.get("reminders", true)) return;
    const remaining = plan.start - K.axisMinutes(seconds, plan);
    const fired = store.get("reminded", {});
    for (const threshold of [20, 10]) {
      const key = duty.id + "|" + state.session.active.activated_at + "|" + threshold;
      if (remaining <= threshold && remaining > threshold - 2 && !fired[key]) {
        fired[key] = Date.now();
        store.set("reminded", fired);
        toast(threshold === 20 ? t("20 min kvar till tjänst {number}", {number: duty.number}) : t("10 min kvar · körplanen öppnas"));
        if (store.get("sound", true)) beep();
      }
    }
  }

  function beep() {
    try { navigator.vibrate && navigator.vibrate([200, 100, 200]); } catch { /* inget stöd */ }
    try {
      const context = new (window.AudioContext || window.webkitAudioContext)();
      for (const [offset, frequency] of [[0, 880], [0.25, 1175]]) {
        const tone = context.createOscillator(), gain = context.createGain();
        tone.frequency.value = frequency;
        gain.gain.setValueAtTime(0.0001, context.currentTime + offset);
        gain.gain.exponentialRampToValueAtTime(0.25, context.currentTime + offset + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + offset + 0.2);
        tone.connect(gain).connect(context.destination);
        tone.start(context.currentTime + offset);
        tone.stop(context.currentTime + offset + 0.22);
      }
    } catch { /* ljud kräver att sidan använts */ }
  }

  let toastTimer = null;
  function toast(text) {
    const element = $("#toast");
    element.textContent = text;
    element.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { element.hidden = true; }, 5000);
  }

  // ── Förare och banan ───────────────────────────────────────────────
  async function refreshPresence() {
    clearTimeout(state.presenceTimer);
    try {
      state.presence = await api("/v1/driver/presence");
      state.presenceAt = Date.now();
      for (const driver of state.presence.drivers) {
        if (!driver.checkin) continue;
        const key = driver.user_id + "|" + driver.checkin.created_at;
        if (!state.seenCheckins.has(key)) {
          state.seenCheckins.add(key);
          if (driver.checkin.age_seconds < 60 && state.presenceSeeded) state.popUntil.set(driver.user_id, Date.now() + 1600);
        }
      }
      state.presenceSeeded = true;
      renderDrivers();
      renderMyCheckin();
    } catch (error) {
      if (error instanceof Unauthorized) { showLogin(); return; }
    } finally {
      state.presenceTimer = setTimeout(refreshPresence, state.tab === "drivers" ? 8000 : 30000);
    }
  }

  function ageOf(driver) {
    return driver.checkin ? driver.checkin.age_seconds + (Date.now() - state.presenceAt) / 1000 : null;
  }
  function agoText(age) {
    return age < 60 ? t("nyss") : t("för {n} min", {n: Math.floor(age / 60)});
  }

  function pill(driver) {
    const element = document.createElement("span");
    element.className = "lf-pill" + (driver.me ? " is-me" : "");
    const avatar = document.createElement("span");
    avatar.className = "lf-avatar lf-avatar--p";
    fillAvatar(avatar, driver);
    const name = document.createElement("span");
    name.textContent = driver.me ? t("Du") : driver.name;
    element.append(avatar, name);
    element.dataset.user = driver.user_id;
    return element;
  }

  function renderDrivers() {
    if (app.dataset.screen !== "drivers") return;
    const presence = state.presence;
    const duty = activeDuty();
    const myCheckinText = (() => {
      const seconds = clockSeconds();
      const row = state.plan && seconds !== null ? K.nearestStation(state.plan, K.axisMinutes(seconds, state.plan)) : null;
      return row ? t("Checka in · {station}", {station: row.station}) : t("Checka in");
    })();
    $("#drivers-checkin-text").textContent = myCheckinText;
    fillAvatar($("#drivers-checkin-avatar"), me());
    const bana = $("#bana");
    bana.replaceChildren();
    const people = $("#people");
    people.replaceChildren();
    if (!presence) return;
    // Stationerna som spelar roll: min tjänsts stationer och där någon är.
    const order = (state.session.stations || []).map((station) => station.name);
    const mine = state.plan ? K.stations(state.plan) : [];
    const here = new Map();
    for (const driver of presence.drivers) {
      if (!driver.checkin) continue;
      const age = ageOf(driver);
      if (age > presence.checkin_visible_seconds + presence.checkin_fade_seconds) continue;
      if (!here.has(driver.checkin.station)) here.set(driver.checkin.station, []);
      here.get(driver.checkin.station).push(driver);
    }
    const others = [...here.keys()].filter((name) => !mine.includes(name))
      .sort((a, b) => (order.indexOf(a) === -1 ? 999 : order.indexOf(a)) - (order.indexOf(b) === -1 ? 999 : order.indexOf(b)));
    const stations = [...mine, ...others];
    $("#bana-empty").hidden = here.size > 0;
    for (const name of stations) {
      const item = document.createElement("li");
      item.className = "lf-station" + (mine.includes(name) ? " is-mine" : "");
      const node = document.createElement("span");
      node.className = "lf-station__node";
      const body = document.createElement("div");
      body.className = "lf-station__body";
      const title = document.createElement("span");
      title.className = "lf-station__name";
      title.textContent = name;
      body.append(title);
      const drivers = here.get(name) || [];
      if (drivers.length) {
        const pills = document.createElement("div");
        pills.className = "lf-station__pills";
        drivers.sort((a, b) => (b.me - a.me) || (ageOf(a) - ageOf(b)));
        for (const driver of drivers) pills.append(pill(driver));
        body.append(pills);
      }
      item.append(node, body);
      bana.append(item);
    }
    for (const driver of presence.drivers) {
      const item = document.createElement("li");
      item.className = "lf-person";
      item.dataset.user = driver.user_id;
      const avatar = document.createElement("span");
      avatar.className = "lf-avatar lf-avatar--row";
      fillAvatar(avatar, driver);
      const text = document.createElement("span");
      text.className = "lf-person__text";
      const name = document.createElement("span");
      name.className = "lf-person__name";
      name.textContent = driver.me ? t("Du") : driver.name;
      const line = document.createElement("span");
      line.className = "lf-person__duty";
      line.textContent = driver.duty_number
        ? [t("Tjänst {number}", {number: driver.duty_number}), ...driver.trains, driver.duty_demo ? "DEMO" : ""].filter(Boolean).join(" · ")
        : t("Ingen tjänst vald");
      text.append(name, line);
      const where = document.createElement("span");
      where.className = "lf-person__where";
      item.append(avatar, text, where);
      people.append(item);
    }
    if (!presence.drivers.length) {
      const empty = document.createElement("li");
      empty.className = "lf-empty lf-person";
      empty.textContent = t("Ingen förare har en aktiv tjänst än.");
      people.append(empty);
    }
    fadeDrivers();
  }

  // Varje sekund: uttoning på banan och "för N min" i listan.
  function fadeDrivers() {
    if (app.dataset.screen !== "drivers" || !state.presence) return;
    const visible = state.presence.checkin_visible_seconds, fadeSeconds = state.presence.checkin_fade_seconds;
    const byUser = new Map(state.presence.drivers.map((driver) => [driver.user_id, driver]));
    for (const element of document.querySelectorAll("#bana .lf-pill")) {
      const driver = byUser.get(element.dataset.user);
      const look = K.fade(ageOf(driver), visible, fadeSeconds);
      element.style.opacity = look.opacity.toFixed(3);
      element.querySelector(".lf-avatar").style.filter = `grayscale(${look.gray.toFixed(3)})`;
      const pop = state.popUntil.get(driver.user_id);
      element.classList.toggle("is-pop", Boolean(pop && pop > Date.now()));
    }
    for (const element of document.querySelectorAll("#people .lf-person[data-user]")) {
      const driver = byUser.get(element.dataset.user);
      const where = element.querySelector(".lf-person__where");
      const avatar = element.querySelector(".lf-avatar");
      const age = ageOf(driver);
      if (age === null || age > visible + fadeSeconds) {
        where.textContent = t("Inte incheckad");
        where.classList.add("is-faded");
        avatar.style.opacity = "0.55";
        avatar.style.filter = "grayscale(.7)";
      } else {
        const look = K.fade(age, visible, fadeSeconds);
        where.textContent = driver.checkin.station + " · " + agoText(age);
        where.classList.toggle("is-faded", age > visible);
        avatar.style.opacity = Math.max(0.4, look.opacity).toFixed(3);
        avatar.style.filter = `grayscale(${look.gray.toFixed(3)})`;
      }
    }
  }

  // ── Checka in ──────────────────────────────────────────────────────
  function openCheckin() {
    const sheet = $("#sheet-checkin");
    fillAvatar($("#sheet-avatar"), me());
    const plan = state.plan;
    const seconds = clockSeconds();
    const now = plan && seconds !== null ? K.axisMinutes(seconds, plan) : null;
    const choices = $("#checkin-stations");
    choices.replaceChildren();
    const nearest = new Map();
    if (plan) {
      for (const row of plan.rows) {
        if (!row.station) continue;
        for (const [moment, kind] of row.t2 === undefined ? [[row.t, row.kind]] : [[row.t, "arrival"], [row.t2, "departure"]]) {
          const best = nearest.get(row.station);
          if (!best || (now !== null && Math.abs(moment - now) < Math.abs(best.t - now))) nearest.set(row.station, {t: moment, kind, row});
        }
      }
    }
    const ranked = [...nearest.entries()].sort((a, b) => now === null ? 0 : Math.abs(a[1].t - now) - Math.abs(b[1].t - now)).slice(0, 4);
    ranked.forEach(([station, info], index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "lf-choice";
      button.setAttribute("role", "radio");
      const dot = document.createElement("span");
      dot.className = "lf-choice__dot";
      const text = document.createElement("span");
      text.className = "lf-choice__text";
      const name = document.createElement("span");
      name.className = "lf-choice__name";
      name.textContent = station;
      const sub = document.createElement("span");
      sub.className = "lf-choice__sub";
      const label = {departure: t("Avgång"), arrival: t("Ankomst"), duty_start: t("Tjänsten börjar"), duty_end: t("Tjänsten slutar"), pass: t("Passerar")}[info.kind] || t("Ankomst");
      sub.textContent = label + " " + K.label(info.t) + (index === 0 && now !== null ? " · " + t("närmast NU") : "");
      text.append(name, sub);
      button.append(dot, text);
      button.addEventListener("click", () => pickStation(station, "", info.row.track));
      choices.append(button);
    });
    const other = $("#checkin-other");
    other.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = t("Välj …");
    other.append(placeholder);
    for (const station of state.session.stations || []) {
      if (nearest.has(station.name)) continue;
      const option = document.createElement("option");
      option.value = station.id;
      option.textContent = station.name;
      other.append(option);
    }
    other.parentElement.hidden = other.options.length <= 1;
    $("#checkin-message").textContent = "";
    if (ranked.length) pickStation(ranked[0][0], "", ranked[0][1].row.track);
    else pickStation("", "", "");
    sheet.hidden = false;
  }

  function pickStation(station, stationId, plannedTrack) {
    state.checkin = {station, stationId, track: plannedTrack || ""};
    for (const button of document.querySelectorAll("#checkin-stations .lf-choice")) {
      button.setAttribute("aria-checked", String(button.querySelector(".lf-choice__name").textContent === station));
    }
    if (!stationId) $("#checkin-other").value = "";
    const tracks = $("#checkin-tracks");
    tracks.replaceChildren();
    $("#checkin-tracks-field").hidden = !plannedTrack;
    if (plannedTrack) {
      for (const [value, label] of [[plannedTrack, t("{track} · planerat", {track: plannedTrack})], ["", t("Vet ej")]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "lf-trackbtn";
        button.textContent = label;
        button.setAttribute("aria-pressed", String(state.checkin.track === value));
        button.addEventListener("click", () => {
          state.checkin.track = value;
          for (const other of tracks.children) other.setAttribute("aria-pressed", String(other === button));
          labelCheckin();
        });
        tracks.append(button);
      }
    }
    labelCheckin();
  }

  function labelCheckin() {
    const button = $("#checkin-submit");
    const {station, track} = state.checkin;
    button.disabled = !station;
    button.textContent = !station ? t("Checka in") : track ? t("Checka in i {station}, spår {track}", {station, track}) : t("Checka in i {station}", {station});
  }

  async function submitCheckin() {
    const {station, stationId, track} = state.checkin;
    if (!station) return;
    const button = $("#checkin-submit");
    button.disabled = true;
    try {
      await api("/v1/driver/checkins", {body: {station, station_id: stationId || undefined, track: track || undefined}});
      $("#sheet-checkin").hidden = true;
      toast(t("Incheckad i {station}", {station}));
      state.popUntil.set(me().user_id, Date.now() + 1600);
      await refreshPresence();
    } catch (error) {
      if (error instanceof Unauthorized) { showLogin(); return; }
      $("#checkin-message").textContent = error.message;
      $("#checkin-message").className = "lf-message is-error";
    } finally {
      button.disabled = false;
    }
  }

  // ── Jag ────────────────────────────────────────────────────────────
  const ROLE = {owner: "Ägare", admin: "Administratör", user: "Lokförare"};
  function renderMe() {
    const person = me();
    if (!person) return;
    fillAvatar($("#me-avatar"), person);
    $("#me-name").textContent = person.name;
    $("#me-role").textContent = [t(ROLE[person.role] || "Lokförare"), state.session.meet ? state.session.meet.name : ""].filter(Boolean).join(" · ");
    const duty = activeDuty();
    $("#me-duty").textContent = duty ? t("Tjänst {number} är aktiv på den här telefonen", {number: duty.number}) : t("Ingen tjänst vald");
    $("#end-duty").hidden = !duty;
    $("#change-duty").textContent = duty ? t("Byt tjänst") : t("Välj tjänst");
    $("#me-visible").checked = person.visible;
    $("#me-reminders").checked = store.get("reminders", true);
    $("#me-sound").checked = store.get("sound", true);
    $("#me-language").value = language;
    $("#me-theme").value = store.get("theme", "dark");
    $("#me-meet").textContent = state.session.meet ? state.session.meet.name : t("Ingen träff vald på servern");
    renderMeClock();
  }

  function renderMeClock() {
    const mode = clockMode();
    const element = $("#me-clock");
    element.textContent = {running: t("Synkad med servern"), stopped: t("Står still"), lost: t("Ingen kontakt"), idle: t("Inte igång")}[mode];
    element.classList.toggle("is-ok", mode === "running");
  }

  async function endDuty() {
    const button = $("#end-duty");
    if (Date.now() > state.endArmedUntil) {
      state.endArmedUntil = Date.now() + 4000;
      button.textContent = t("Tryck igen för att avsluta");
      setTimeout(() => { if (Date.now() > state.endArmedUntil) button.textContent = t("Avsluta tjänsten"); }, 4100);
      return;
    }
    state.endArmedUntil = 0;
    try {
      await api("/v1/driver/active/end", {body: {}});
      state.session.active = null;
      state.planFor = null;
      state.plan = null;
      saveOffline();
      button.textContent = t("Avsluta tjänsten");
      renderMe();
    } catch (error) {
      if (error instanceof Unauthorized) { showLogin(); return; }
      toast(error.message);
    }
  }

  // ── Ikonen: beskär i webbläsaren, skicka en liten bild ─────────────
  const crop = {image: null, zoom: 1, x: 0, y: 0, drag: null};
  const CROP_SIZE = 560, CIRCLE = 440;

  function openIcon() {
    crop.image = null;
    crop.zoom = 1;
    crop.x = crop.y = 0;
    $("#crop-zoom").value = "1";
    $("#icon-save").disabled = true;
    $("#icon-remove").hidden = !me().avatar_url;
    $("#icon-message").textContent = "";
    $("#preview-name-s").textContent = t("Du");
    fillAvatar($("#preview-s"), me());
    fillAvatar($("#preview-l"), me());
    if (me().avatar_url) {
      const image = new Image();
      image.addEventListener("load", () => { crop.image = image; drawCrop(false); });
      image.src = me().avatar_url;
    }
    drawCrop(false);
    $("#sheet-icon").hidden = false;
  }

  function cropRect() {
    const image = crop.image;
    const base = CIRCLE / Math.min(image.naturalWidth || image.width, image.naturalHeight || image.height);
    const scale = base * crop.zoom;
    const width = (image.naturalWidth || image.width) * scale, height = (image.naturalHeight || image.height) * scale;
    return {scale, width, height, left: (CROP_SIZE - width) / 2 + crop.x, top: (CROP_SIZE - height) / 2 + crop.y};
  }

  function clampCrop() {
    if (!crop.image) return;
    const {width, height} = cropRect();
    const limitX = Math.max(0, (width - CIRCLE) / 2), limitY = Math.max(0, (height - CIRCLE) / 2);
    crop.x = Math.max(-limitX, Math.min(limitX, crop.x));
    crop.y = Math.max(-limitY, Math.min(limitY, crop.y));
  }

  function drawCrop(updatePreview = true) {
    const canvas = $("#crop-canvas");
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, CROP_SIZE, CROP_SIZE);
    $("#crop-empty").hidden = Boolean(crop.image);
    if (!crop.image) return;
    clampCrop();
    const rect = cropRect();
    context.drawImage(crop.image, rect.left, rect.top, rect.width, rect.height);
    if (updatePreview) {
      const url = renderIcon(96, "image/jpeg", 0.85);
      for (const target of [$("#preview-s"), $("#preview-l")]) fillAvatar(target, {...me(), avatar_url: url});
    }
  }

  function renderIcon(size, type, quality) {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    const context = canvas.getContext("2d");
    context.fillStyle = "#475569";
    context.fillRect(0, 0, size, size);
    const rect = cropRect();
    const factor = size / CIRCLE;
    const offset = (CROP_SIZE - CIRCLE) / 2;
    context.imageSmoothingQuality = "high";
    context.drawImage(crop.image, (rect.left - offset) * factor, (rect.top - offset) * factor, rect.width * factor, rect.height * factor);
    return canvas.toDataURL(type, quality);
  }

  function loadIconFile(file) {
    if (!file) return;
    if (!/^image\//.test(file.type)) {
      $("#icon-message").textContent = t("Välj en bild.");
      return;
    }
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const image = new Image();
      image.addEventListener("load", () => {
        crop.image = image;
        crop.zoom = 1;
        crop.x = crop.y = 0;
        $("#crop-zoom").value = "1";
        $("#icon-save").disabled = false;
        drawCrop();
      });
      image.addEventListener("error", () => { $("#icon-message").textContent = t("Bilden gick inte att öppna."); });
      image.src = reader.result;
    });
    reader.readAsDataURL(file);
  }

  async function saveIcon() {
    if (!crop.image) return;
    const button = $("#icon-save");
    button.disabled = true;
    let image = renderIcon(256, "image/jpeg", 0.86);
    if (image.length > 120000) image = renderIcon(256, "image/jpeg", 0.7);
    try {
      const payload = await api("/v1/driver/avatar", {body: {image}});
      state.session.user = payload.user;
      saveOffline();
      $("#sheet-icon").hidden = true;
      toast(t("Ikonen är sparad"));
      refreshEverywhere();
    } catch (error) {
      if (error instanceof Unauthorized) { showLogin(); return; }
      $("#icon-message").textContent = error.message;
      button.disabled = false;
    }
  }

  async function removeIcon() {
    try {
      const payload = await api("/v1/driver/avatar/remove", {body: {}});
      state.session.user = payload.user;
      saveOffline();
      $("#sheet-icon").hidden = true;
      refreshEverywhere();
    } catch (error) {
      if (error instanceof Unauthorized) { showLogin(); return; }
      $("#icon-message").textContent = error.message;
    }
  }

  function refreshEverywhere() {
    fillAvatar($("#checkin-avatar"), me());
    renderMe();
    refreshPresence();
  }

  // ── Uppstart ───────────────────────────────────────────────────────
  function saveOffline() {
    if (state.session) store.set("cache", {session: state.session, at: Date.now()});
  }

  function applyTheme() {
    const choice = store.get("theme", "dark");
    const light = choice === "light" || (choice === "system" && window.matchMedia("(prefers-color-scheme: light)").matches);
    app.dataset.theme = light ? "light" : "dark";
    document.querySelector('meta[name="theme-color"]').setAttribute("content", light ? "#f5f4f0" : "#0d0f13");
  }

  async function loadSession() {
    try {
      state.session = await api("/v1/driver/session");
      state.offline = false;
      saveOffline();
    } catch (error) {
      if (error instanceof Unauthorized) { showLogin(); return false; }
      // Ingen kontakt: senaste sparade tjänsten i högst tolv timmar.
      const cached = store.get("cache", null);
      if (cached && Date.now() - cached.at < OFFLINE_HOURS * 3600 * 1000) {
        state.session = cached.session;
        state.offline = true;
      } else {
        show("login");
        $("#login-message").textContent = t("Servern svarar inte. Kontrollera att telefonen är på träffens nät.");
        return false;
      }
    }
    if (state.session.clock) {
      state.clock = state.session.clock;
      if (!state.offline) {
        if (state.clock.time) state.sync.accept(state.clock, performance.now());
        state.clockOkAt = performance.now();
      }
    }
    fillAvatar($("#checkin-avatar"), me());
    setTab(state.tab);
    pollClock();
    refreshPresence();
    return true;
  }

  function frame() {
    const seconds = state.session ? clockSeconds() : null;
    if (state.session) {
      renderHeader(seconds);
      if (activeDuty() && !state.choosing && state.tab === "duty") {
        ensurePlan();
        const now = seconds === null || !state.plan ? null : K.axisMinutes(seconds, state.plan);
        const wanted = now !== null && now < state.plan.start - 10 && !state.forceKorplan ? "countdown" : "korplan";
        if (app.dataset.screen !== wanted) show(wanted);
        if (wanted === "korplan") updateTimeline(seconds);
        else renderCountdown(seconds);
        remind(seconds);
      }
    }
    requestAnimationFrame(frame);
  }

  function bind() {
    for (const button of document.querySelectorAll(".lf-tab")) {
      button.addEventListener("click", () => {
        state.choosing = false;
        if (button.dataset.tab === "duty" && state.tab === "duty") {
          timeline.follow = true;
          timeline.jump = true;
        }
        setTab(button.dataset.tab);
      });
    }
    $("#login-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const message = $("#login-message");
      message.textContent = "";
      message.className = "lf-message";
      const button = $("#login-form button");
      button.disabled = true;
      try {
        const response = await fetch("/v1/auth/login", {
          method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({email: $("#login-email").value, password: $("#login-password").value}),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.message || t("Inloggningen misslyckades"));
        $("#login-password").value = "";
        await loadSession();
      } catch (error) {
        message.textContent = error.message;
        message.className = "lf-message is-error";
      } finally {
        button.disabled = false;
      }
    });
    $("#duty-query").addEventListener("input", () => {
      clearTimeout(searchTimer);
      state.chosenDuty = null;
      $("#duty-found").hidden = true;
      $("#duty-activate").disabled = true;
      searchTimer = setTimeout(searchDuties, 200);
    });
    $("#duty-activate").addEventListener("click", activateDuty);
    $("#duty-cancel").addEventListener("click", () => { state.choosing = false; route(); });
    $("#show-korplan").addEventListener("click", () => { state.forceKorplan = true; timeline.jump = true; route(); });
    $("#to-now").addEventListener("click", () => {
      timeline.follow = true;
      timeline.interactedAt = 0;
      const plan = state.plan, seconds = clockSeconds();
      if (plan && seconds !== null) $("#timeline").scrollTo({top: Math.max(0, plan.y(K.axisMinutes(seconds, plan)) - $("#timeline").clientHeight * 0.33), behavior: "smooth"});
    });
    const box = $("#timeline");
    const interacted = () => { timeline.follow = false; timeline.interactedAt = performance.now(); };
    box.addEventListener("wheel", interacted, {passive: true});
    box.addEventListener("touchstart", () => { timeline.touching = true; interacted(); }, {passive: true});
    box.addEventListener("touchend", () => { timeline.touching = false; timeline.interactedAt = performance.now(); }, {passive: true});
    box.addEventListener("keydown", (event) => { if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) interacted(); });
    box.addEventListener("pointerdown", (event) => { if (event.pointerType === "mouse") interacted(); });
    $("#open-checkin").addEventListener("click", openCheckin);
    $("#drivers-checkin").addEventListener("click", openCheckin);
    $("#checkin-submit").addEventListener("click", submitCheckin);
    $("#checkin-other").addEventListener("change", (event) => {
      const option = event.target.selectedOptions[0];
      if (option && option.value) pickStation(option.textContent, option.value, "");
    });
    for (const sheet of document.querySelectorAll(".sheet")) {
      for (const close of sheet.querySelectorAll("[data-close]")) close.addEventListener("click", () => { sheet.hidden = true; });
    }
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") for (const sheet of document.querySelectorAll(".sheet")) sheet.hidden = true;
    });
    $("#open-icon").addEventListener("click", openIcon);
    $("#icon-file").addEventListener("change", (event) => loadIconFile(event.target.files[0]));
    $("#icon-camera").addEventListener("change", (event) => loadIconFile(event.target.files[0]));
    $("#crop-zoom").addEventListener("input", (event) => { crop.zoom = Number(event.target.value); drawCrop(); });
    const canvas = $("#crop-canvas");
    canvas.addEventListener("pointerdown", (event) => {
      if (!crop.image) return;
      canvas.setPointerCapture(event.pointerId);
      crop.drag = {x: event.clientX, y: event.clientY, startX: crop.x, startY: crop.y};
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!crop.drag) return;
      const ratio = CROP_SIZE / canvas.clientWidth;
      crop.x = crop.drag.startX + (event.clientX - crop.drag.x) * ratio;
      crop.y = crop.drag.startY + (event.clientY - crop.drag.y) * ratio;
      drawCrop(false);
    });
    const endDrag = () => { if (crop.drag) { crop.drag = null; drawCrop(); } };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);
    $("#icon-save").addEventListener("click", saveIcon);
    $("#icon-remove").addEventListener("click", removeIcon);
    $("#change-duty").addEventListener("click", openChoose);
    $("#end-duty").addEventListener("click", endDuty);
    $("#me-visible").addEventListener("change", async (event) => {
      try {
        const payload = await api("/v1/driver/profile", {body: {visible: event.target.checked}});
        state.session.user = payload.user;
        saveOffline();
      } catch (error) {
        event.target.checked = !event.target.checked;
        if (error instanceof Unauthorized) showLogin(); else toast(error.message);
      }
    });
    $("#me-reminders").addEventListener("change", (event) => store.set("reminders", event.target.checked));
    $("#me-sound").addEventListener("change", (event) => store.set("sound", event.target.checked));
    $("#me-language").addEventListener("change", (event) => {
      language = event.target.value;
      store.set("language", language);
      translateStatic();
      lastNextKey = "";
      state.planFor = null;
      $("#first-moments").dataset.duty = "";
      route();
    });
    $("#me-theme").addEventListener("change", (event) => { store.set("theme", event.target.value); applyTheme(); });
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", applyTheme);
    $("#logout").addEventListener("click", async () => {
      try { await fetch("/v1/auth/logout", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, body: "{}"}); } catch { /* utloggad ändå */ }
      try { localStorage.removeItem("lf.cache"); } catch { /* privat läge */ }
      showLogin();
    });
    setInterval(() => { fadeDrivers(); if (app.dataset.screen === "me") renderMeClock(); }, 1000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && state.session) { pollClock(); refreshPresence(); }
    });
  }

  function start() {
    applyTheme();
    translateStatic();
    bind();
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/lokforaren/sw.js", {scope: "/lokforaren/"}).catch(() => { /* fungerar utan */ });
    }
    loadSession();
    requestAnimationFrame(frame);
  }

  start();
})();
