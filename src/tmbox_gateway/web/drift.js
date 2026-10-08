/* Drift · Kontrollrummet – ritar vyn.
 *
 * En skärm: klockan, banan, stationerna med sina boxar, nästa händelser och
 * diagrammet. Varje uppgift visas en gång; det som sällan behövs ligger bakom
 * knappar och dialoger. Siffrorna kommer från drift-model.js, hämtningen och
 * kommandona från app.js, som anropar TrainMeetDrift.update({...}).
 *
 * Servern har strikt CSP (inga inline-stilar, inget inline-skript): utseendet
 * ligger i kontrollrummet.css, och det som måste mätas sätts med CSSOM.
 */
(() => {
  "use strict";
  const model = globalThis.TrainMeetDriftModel;
  const doc = document;
  const root = doc.documentElement;
  const SVG = "http://www.w3.org/2000/svg";
  const $ = (selector, scope = doc) => scope.querySelector(selector);
  const $$ = (selector, scope = doc) => [...scope.querySelectorAll(selector)];
  const t = (source, values) => globalThis.TrainMeetI18n?.t
    ? globalThis.TrainMeetI18n.t(source, values || {})
    : String(source).replace(/\{(\w+)\}/g, (match, key) => values?.[key] ?? match);
  const hhmm = (value) => model.hhmm(value);

  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* privat läge: gäller bara den här sidan */ } },
  };

  // ── Tema ──────────────────────────────────────────────────────────────
  // Mörkt eller ljust, samma struktur. Valet gäller den här webbläsaren. Det
  // sätts i kr-theme.js innan sidan målas, så att den inte blinkar till.
  const THEME_KEY = "trainmeet.theme";
  const theme = {
    get() { return root.dataset.krTheme === "light" ? "light" : "dark"; },
    set(value, persist = true) {
      const next = value === "light" ? "light" : "dark";
      root.dataset.krTheme = next;
      if (persist) store.set(THEME_KEY, next);
      const button = $("#kr-theme-toggle");
      if (button) {
        const source = next === "dark" ? "Byt till ljust läge" : "Byt till mörkt läge";
        button.dataset.tmAriaLabel = source; button.dataset.tmTitle = source;
        button.setAttribute("aria-label", t(source)); button.title = t(source);
        button.setAttribute("aria-pressed", String(next === "light"));
      }
      $("meta[name=theme-color]")?.setAttribute("content", next === "dark" ? "#0d0f13" : "#ecebe6");
    },
    toggle() { theme.set(theme.get() === "dark" ? "light" : "dark"); },
  };

  // ── Små byggstenar ────────────────────────────────────────────────────
  function h(tag, props = {}, ...kids) {
    const el = doc.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") el.className = value;
      else if (key === "on") for (const [name, fn] of Object.entries(value)) el.addEventListener(name, fn);
      else if (key === "data") { for (const [name, v] of Object.entries(value)) if (v !== undefined && v !== null) el.dataset[name] = v; }
      else el.setAttribute(key, value === true ? "" : String(value));
    }
    for (const kid of kids.flat()) if (kid !== undefined && kid !== null && kid !== false) el.append(kid instanceof Node ? kid : doc.createTextNode(String(kid)));
    return el;
  }
  function svg(tag, attrs = {}, text = null) {
    const el = doc.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attrs)) if (value !== undefined && value !== null) el.setAttribute(key, String(value));
    if (text !== null) el.textContent = String(text);
    return el;
  }
  const dot = () => h("span", { class: "kr-dot", "aria-hidden": "true" });
  const pill = (tone, text) => h("span", { class: `kr-pill${tone ? " " + tone : ""}` }, dot(), text);
  const badge = (number, { hollow = false, selected = false, plain = false } = {}) =>
    h("span", { class: plain && !selected ? "kr-trainno" : `kr-badge${hollow ? " hollow" : ""}${selected ? " sel" : ""}` }, number);
  const button = (text, { cls = "kr-btn sm", on, ...rest } = {}) => h("button", { type: "button", class: cls, on, ...rest }, text);
  const plural = (n, one, many) => t(n === 1 ? one : many, { count: n, n });
  // Ett fönster kring midnatt börjar före 00:00, så minuten kan vara negativ.
  // Nedåt, som klockan: 09:17:54 är 09:17 (nu-linjen går med sekunderna).
  const clockLabel = (minute) => {
    const value = ((Math.floor(minute + 1e-9) % 1440) + 1440) % 1440;
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  };

  function formatTime(value, us) {
    const text = String(value || "").slice(0, 5);
    if (!us) return text || "--:--";
    const [hour, minute] = text.split(":");
    return Number.isFinite(Number(hour)) ? `${Number(hour) % 12 || 12}:${minute} ${Number(hour) >= 12 ? "PM" : "AM"}` : "--:--";
  }

  // ── Tillstånd ─────────────────────────────────────────────────────────
  const hooks = {};
  const ctx = {
    snapshot: null, devices: [], presentation: null, simulation: null, clock: null,
    selection: { train: null, station: null },
    trainDetail: { number: null, data: null, error: "" },
    us: false,
    graphWindow: Number(store.get("trainmeet.driftGraphWindow") ?? 180),
  };
  if (![0, 120, 180, 360].includes(ctx.graphWindow)) ctx.graphWindow = 180;
  let scheduled = false;
  const sigs = new Map();
  // Ritar om en del bara när det den visar har ändrats: annars tappar knappar fokus.
  const changed = (key, value) => { const sig = JSON.stringify(value); if (sigs.get(key) === sig) return false; sigs.set(key, sig); return true; };

  // Bredden en ritning får i pixlar, utan rutans marginaler: 1 enhet i SVG = 1 px.
  function contentWidth(box) {
    const style = getComputedStyle(box);
    return Math.round(box.clientWidth - parseFloat(style.paddingLeft || 0) - parseFloat(style.paddingRight || 0));
  }

  // ── Klockraden ────────────────────────────────────────────────────────
  // Klockan mellan uppdateringarna. app.js hämtar läget när något händer och
  // annars var 30:e sekund; klockan, nu-linjen och tågen på linjen går ändå
  // vidare i takt med träffklockan. Varje ny klocka får sin mottagningstid.
  let clockSeen = { clock: null, at: 0 };
  function liveSeconds() {
    const clock = ctx.clock || ctx.snapshot?.clock;
    const match = /^(\d{1,2}):(\d{2})(?::(\d{2}))?/.exec(String(clock?.time ?? ""));
    if (!match) return null;
    if (clockSeen.clock !== clock) clockSeen = { clock, at: performance.now() };
    const base = Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3] || 0);
    const run = clock.running ? (performance.now() - clockSeen.at) / 1000 * Number(clock.speed || 1) : 0;
    return ((base + run) % 86400 + 86400) % 86400;
  }
  const clockText = (seconds) => [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, Math.floor(seconds) % 60]
    .map((part) => String(part).padStart(2, "0")).join(":");

  function renderClock() {
    const clock = ctx.clock || ctx.snapshot?.clock;
    if (!clock) return;
    const live = liveSeconds();
    const time = formatTime(live === null ? clock.time : clockText(live), ctx.us);
    for (const id of ["#overview-clock", "#app-clock"]) {
      const el = $(id); if (!el) continue;
      el.textContent = time; el.classList.toggle("kr-stopped", !clock.running);
      if (id === "#app-clock") el.classList.toggle("tm-clock--stopped", !clock.running);
    }
    const state = $("#clock-state");
    if (state) {
      state.className = `kr-pill ${clock.running ? "ok" : "warn"}`;
      state.replaceChildren(dot(), clock.running ? t("Går · {speed}×", { speed: Number(clock.speed || 1) }) : t("Stoppad"));
    }
    const start = $("#overview-clock-start"), stop = $("#overview-clock-stop");
    if (start) start.hidden = Boolean(clock.running);
    if (stop) stop.hidden = !clock.running;
    const day = $("#overview-day"); if (day && ctx.snapshot?.active_day) day.textContent = ctx.snapshot.active_day;
  }

  // ── Nyckeltal ─────────────────────────────────────────────────────────
  function renderStats(rows) {
    const host = $("#drift-stats"); if (!host || !ctx.snapshot) return;
    const s = model.stats(ctx.snapshot, rows, ctx.simulation);
    if (!changed("stats", [s, root.lang])) return;
    const stat = (value, label, tone = "", sub = "") => h("div", { class: `kr-stat${tone ? " " + tone : ""}` },
      h("b", {}, String(value), sub ? h("small", {}, sub) : null), h("span", {}, label));
    host.replaceChildren(stat(s.onLine, t("tåg på linjen")), stat(s.atStations, t("inne på station")),
      stat(s.manned, t("bemannade"), "", ` / ${s.stations}`), stat(s.deviations, t("avvikelser"), s.deviations ? "warn" : "ok"));
  }

  // ── Banöversikten ─────────────────────────────────────────────────────
  function renderMap() {
    const target = $("#overview-topology"), snapshot = ctx.snapshot;
    if (!target || !snapshot || !hooks.renderTopology) return;
    const width = Math.max(280, contentWidth(target.parentElement) || 1200);
    const simulated = new Set((ctx.simulation?.active ? ctx.simulation.stations || [] : []).filter((station) => station.mode === "automatic").map((station) => station.id));
    hooks.renderTopology(snapshot, target, {
      kr: { width },
      selectedTrainNumber: ctx.selection.train, selectedStationID: ctx.selection.station, showBadge: false,
      stationClass: (station) => (simulated.has(station.id) ? "sim" : ""),
      onTrainSelect: (number) => hooks.selectTrain?.(number),
      onStationSelect: (id) => hooks.selectStation?.(id),
      onClear: () => hooks.clear?.(),
    });
    const stations = (snapshot.stations || []).length, lines = (snapshot.connections || []).length;
    const meta = $("#topology-head-meta");
    if (meta) meta.textContent = [plural(stations, "{count} station", "{count} stationer"), plural(lines, "{count} sträcka", "{count} sträckor"), t("tryck på en station för att filtrera")].join(" · ");
  }

  // ── Stationer och boxar ───────────────────────────────────────────────
  function deviceTime(connection) {
    if (!connection?.last_seen) return "";
    const date = new Date(connection.last_seen);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString(globalThis.TrainMeetI18n?.getLocale?.() || "sv-SE", { hour: "2-digit", minute: "2-digit" });
  }
  function statusTag(row) {
    const tag = (cls, text, dotted = true) => h("span", { class: `kr-tag ${cls}` }, dotted ? dot() : null, text);
    // En väntande box som tappat kontakten säger det i stället: den väntar inte längre på något.
    if (row.kind === "waiting" && row.device?.connection?.state !== "lost") return tag("warn", t("Väntar på station"));
    if (row.kind === "simulated") return tag("sim", t("Simuleras"));
    if (row.sim?.mode === "disconnected") return tag("warn", t("Kontakt saknas – väntar"));
    if (row.kind === "unmanned") return tag("off", t("Obemannad"), false);
    const connection = row.device.connection, time = deviceTime(connection), state = connection?.state || "offline";
    if (state === "online") return tag("ok", t("Online"));
    if (state === "lost") return tag("warn", t("Ingen kontakt · sist sedd {time}", { time }));
    return tag("off", time ? t("Offline sedan {time}", { time }) : t("Offline"));
  }
  function boxCell(row) {
    if (row.kind === "simulated") return h("td", { class: "m" }, t("Simulator"));
    if (!row.device) return h("td", { class: "m" }, "—");
    const side = { left: t("vänster"), right: t("höger") }[row.device.station_side];
    // Firmware för en fysisk box, appversion för iPhone. En webbläsarbox kör
    // serverns egen kod och har ingen egen version ("unknown").
    const version = row.device.firmware_version && row.device.firmware_version !== "unknown" ? row.device.firmware_version : "";
    return h("td", { class: "mono", title: [row.device.model, version && t("ver. {version}", { version }), row.device.device_id].filter(Boolean).join(" · ") },
      row.device.device_code, version ? h("span", { class: "kr-code" }, t("ver. {version}", { version })) : null,
      side ? h("span", { class: "kr-code" }, side) : null);
  }
  function placementCell(row) {
    if (!row.placement) return h("td", { class: "m kr-hide-sm" }, "—");
    const text = `${row.placement.left.join(", ") || "—"} · ${row.placement.right.join(", ") || "—"}`;
    if (!row.placement.available) return h("td", { class: "mono kr-hide-sm" }, text);
    return h("td", { class: "mono kr-hide-sm" }, h("button", { type: "button", class: "kr-textbtn mono", title: t("Ändra vänster och höger"),
      on: { click: (event) => hooks.editPlacement?.(row.station.id, event.currentTarget) } }, text));
  }
  function actionCell(row) {
    const cell = h("td", { class: "r" });
    if (row.kind === "waiting") cell.append(button(t("Välj station ▾"), { cls: "kr-btn sm primary", on: { click: (event) => hooks.editBox?.(row.device, null, event.currentTarget) } }));
    else if (row.kind === "simulated") cell.append(button(t("Ta över"), { cls: "kr-linkbtn", on: { click: () => hooks.simulationDetails?.() } }));
    else if (row.kind === "unmanned") cell.append(button(t("Tilldela"), { cls: "kr-linkbtn", on: { click: (event) => hooks.editBox?.(null, row.station, event.currentTarget) } }));
    else cell.append(button(t("Redigera"), { cls: "kr-linkbtn", on: { click: (event) => hooks.editBox?.(row.device, row.station, event.currentTarget) } }));
    return cell;
  }
  function renderStations(rows) {
    const body = $("#device-list"); if (!body) return;
    const selected = ctx.selection.station;
    const waiting = rows.filter((row) => row.kind === "waiting").length;
    const tag = $("#device-awaiting");
    if (tag) {
      tag.hidden = !waiting;
      tag.replaceChildren(...(waiting ? [dot(), plural(waiting, "{count} box väntar på station", "{count} boxar väntar på station")] : []));
    }
    const stations = ctx.snapshot?.stations?.length || 0;
    const manned = new Set(rows.filter((row) => row.kind === "box" && row.tone === "ok").map((row) => row.station.id)).size;
    const simulated = rows.filter((row) => row.kind === "simulated").length;
    const meta = $("#drift-stations-meta");
    if (meta) meta.textContent = [t("{manned} av {total} bemannade", { manned, total: stations }),
      simulated ? t(simulated === 1 ? "1 sköts av simulatorn" : "{n} sköts av simulatorn", { n: simulated }) : ""].filter(Boolean).join(" · ");
    const sig = [rows.map((row) => [row.key, row.kind, row.tone, row.trains, row.device?.connection?.state, row.device?.connection?.last_seen,
      row.device?.station_side, row.device?.device_code, row.device?.firmware_version, row.placement && [row.placement.left, row.placement.right], row.sim?.mode]), selected, root.lang];
    if (!changed("stations", sig)) return;
    if (!rows.length) { body.replaceChildren(h("tr", {}, h("td", { colspan: 6, class: "kr-empty" }, t("Inga stationer i träffen.")))); return; }
    body.replaceChildren(...rows.map((row) => {
      const tr = h("tr", { class: `${row.kind === "waiting" ? "wait" : ""}${row.station && row.station.id === selected ? " sel" : ""}`.trim(), data: { stationId: row.station?.id } });
      tr.append(
        row.station
          ? h("td", {}, h("button", { type: "button", class: "kr-rowlink", on: { click: () => hooks.selectStation?.(row.station.id) } }, h("b", {}, row.station.name), h("span", { class: "kr-code" }, row.station.code)))
          : h("td", { class: "m" }, "—"),
        boxCell(row), h("td", {}, statusTag(row)), placementCell(row),
        row.trains === null ? h("td", { class: "r m kr-hide-sm" }, "—") : h("td", { class: "r kr-num kr-hide-sm" }, String(row.trains)),
        actionCell(row));
      return tr;
    }));
  }

  // ── Nästa händelser ───────────────────────────────────────────────────
  function eventText(event, names) {
    if (event.kind === "arr") return t("ankommer {station}", { station: event.station });
    const next = event.nextStationId && names.get(event.nextStationId)?.name;
    return next ? t("avgår {station} → {next}", { station: event.station, next }) : t("avgår {station}", { station: event.station });
  }
  const eventIn = (event) => (event.delta <= 0 ? t("nu") : t("{n} min", { n: event.delta }));

  // Som i SJ:s app, i den mängd användaren valt (fem nivåer, förval 2): en
  // rad som just ändrats lyser upp kort och får "Nyss"; från nivå 3 syns
  // förseningen, från nivå 4 som röd bricka med den nya tiden.
  const FLASH_MS = 2000, RECENT_MS = 30000;
  let fresh = model.changeTracker(), trackedLevel = null;
  // Att byta nivå är ingen ändring i trafiken: minnet börjar om, inget lyser upp.
  const levelNow = () => {
    const value = model.deviationLevel(ctx.snapshot, globalThis.deviationOwnLevel?.() ?? "");
    if (trackedLevel !== null && value !== trackedLevel) fresh = model.changeTracker();
    trackedLevel = value;
    return value;
  };
  function markOf(view) {
    const mark = view.mark;
    if (!mark) return null;
    const label = globalThis.deviationLabel ? globalThis.deviationLabel(view) : mark.text;
    const cls = mark.tone === "early" ? "tm-early" : mark.style === "text" ? "tm-delay tm-delay--text" : "tm-delay";
    return h("span", { class: `${cls} tm-flip`, title: label, "aria-label": label }, mark.text);
  }
  const eventTime = (event, view) => (view.strike && event.expectedTime
    ? h("span", { class: "t" }, h("span", { class: "tm-was" }, event.time), h("span", { class: "tm-new tm-flip" }, event.expectedTime))
    : h("span", { class: "t" }, event.time));
  const eventWhen = (event, view) => h("span", { class: "in" }, view.mark?.style === "pill" ? markOf(view) : eventIn(event));
  /** Markera en rad som ändrats sedan förra gången den ritades. */
  function markFresh(row, key, signature, label = row, view = { flash: true }) {
    const changedAt = fresh.note(key, signature, Date.now());
    if (changedAt === null || !view.flash) return row;
    const age = Date.now() - changedAt;
    if (age < FLASH_MS) { row.classList.add("is-updated"); row.style.animationDelay = `-${age}ms`; }
    if (age < RECENT_MS) label.append(h("span", { class: "tm-recent" }, t("Nyss")));
    return row;
  }
  // Det som syns ingår i signaturen: på nivå 2 är det läget, inte minuterna.
  const eventSignature = (event, view) => [event.time, event.state, view.mark?.text || "", view.estimated];
  function renderEvents() {
    const host = $("#drift-upcoming"), snapshot = ctx.snapshot; if (!host || !snapshot) return;
    const { train, station } = ctx.selection;
    const names = model.stationMap(snapshot);
    const events = model.events(snapshot, { station: train ? null : station, train, limit: 8, nowSeconds: liveSeconds() });
    const scope = $("#drift-events-scope"), clear = $("#drift-events-clear");
    if (scope) scope.textContent = train ? t("tåg {number}", { number: train }) : station ? (names.get(station)?.name || "") : t("hela banan");
    if (clear) clear.hidden = !(train || station);
    const delayed = model.lateTrains(snapshot).length;
    const foot = $("#drift-events-foot");
    if (foot && changed("events-foot", [delayed, root.lang])) {
      foot.replaceChildren(
        delayed ? h("span", { class: "kr-tag warn" }, dot(), plural(delayed, "{count} avvikelse", "{count} avvikelser")) : h("span", { class: "kr-tag ok" }, dot(), t("Inga avvikelser")),
        h("span", {}, `· ${delayed ? t("något tåg är senare än tidtabellen") : t("trafiken följer tidtabellen")} · ${t("ofylld bricka = klart men inte avgått")}`));
    }
    const level = levelNow();
    if (!changed("events", [events, train, station, root.lang, level])) return;
    if (!events.length) { host.replaceChildren(h("p", { class: "kr-empty" }, t("Inga fler planerade händelser idag."))); return; }
    host.replaceChildren(...events.map((event) => {
      const selected = train === event.train;
      const view = model.deviationView(level, event);
      const mark = event.state === "on-line" ? badge(event.train, { selected }) : event.state === "cleared" ? badge(event.train, { hollow: true, selected }) : badge(event.train, { plain: true, selected });
      const what = h("span", { class: "w" }, eventText(event, names));
      if (view.mark?.style === "text") what.append(" ", markOf(view));
      const row = h("button", { type: "button", class: `kr-ev${selected ? " sel" : ""}${view.mark ? ` is-${view.mark.tone}` : ""}`, data: { trainNumber: event.train }, "aria-pressed": String(selected), on: { click: () => hooks.selectTrain?.(event.train) } },
        eventTime(event, view), mark, what, eventWhen(event, view));
      return markFresh(row, `ev|${event.train}|${event.kind}|${event.stationId}`, eventSignature(event, view), what, view);
    }));
  }

  // ── Tågdiagrammet ─────────────────────────────────────────────────────
  // Ritat för rutan det faktiskt har: texten blir lika stor oavsett fönster,
  // och hela diagrammet syns utan att man rullar.
  function renderGraph() {
    const target = $("#overview-graph"), snapshot = ctx.snapshot; if (!target || !snapshot) return;
    const live = liveSeconds();
    const g = model.graph(snapshot, { windowMinutes: ctx.graphWindow, train: ctx.selection.train, now: live === null ? null : live / 60 });
    const width = Math.max(280, contentWidth(target.parentElement) || 1200);
    // Namnen när det finns plats, annars koderna: aldrig båda, det blev plottrigt.
    const showNames = width >= 640;
    const longest = Math.max(0, ...g.stations.map((station) => String(station.name || "").length));
    const left = showNames ? Math.min(210, Math.max(96, Math.round(longest * 7.2) + 24)) : 64;
    const right = 18, top = 24, bottom = 22, step = 20;
    const height = top + (Math.max(g.stations.length, 1) - 1) * step + bottom + 6;
    const x = (minute) => left + (minute - g.start) / (g.end - g.start) * (width - left - right);
    const y = (row) => top + row * step;
    target.setAttribute("viewBox", `0 0 ${width} ${height}`);
    target.setAttribute("aria-label", t("Tågdiagram {from} till {to}", { from: clockLabel(g.start), to: clockLabel(g.end) }));
    target.replaceChildren();
    target.onclick = (event) => { if (event.target === target) hooks.clear?.(); };
    const parts = [];
    const lives = levelNow() >= 5 ? model.trainLive(ctx.snapshot, liveSeconds()) : null;
    // Tiden som redan har gått ligger i en svagt skuggad yta.
    if (g.now !== null) parts.push(svg("rect", { class: "sh", x: left, y: 4, width: Math.max(0, x(g.now) - left), height: height - 18 }));
    g.stations.forEach((station, row) => {
      parts.push(svg("line", { class: "gl", x1: left, y1: y(row), x2: width - right, y2: y(row) }));
      parts.push(showNames ? svg("text", { class: "lbl s", x: left - 10, y: y(row) + 4.5, "text-anchor": "end" }, station.name)
        : svg("text", { class: "cnt", x: left - 10, y: y(row) + 4, "text-anchor": "end" }, station.code || ""));
    });
    for (let minute = Math.ceil(g.start / 60) * 60; minute <= g.end; minute += 60) {
      const major = g.end - g.start <= 360 || (minute / 60) % 2 === 0;
      parts.push(svg("line", { class: `gl ${major ? "h" : "d"}`, x1: x(minute), y1: 4, x2: x(minute), y2: height - 18 }));
      if (major) parts.push(svg("text", { class: "ax", x: x(minute), y: height - 4, "text-anchor": "middle" }, clockLabel(minute)));
    }
    const gray = [], chosen = [], lit = [];
    const tagWidth = (number) => 14 + String(number).length * 8.4;
    for (const line of g.lines) {
      const points = line.points.map((point) => `${x(point.minute).toFixed(1)},${y(point.row)}`).join(" ");
      const open = (event) => { event.stopPropagation(); hooks.selectTrain?.(line.number); };
      const group = svg("g", { class: "tr-line", "data-train-number": line.number, role: "button", tabindex: "0", "aria-label": t("Tåg {number}", { number: line.number }) });
      group.addEventListener("click", open);
      group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") open(event); });
      group.append(svg("polyline", { class: `pl${line.selected ? " lit sel" : ""}`, points }), svg("polyline", { class: "hit", points }));
      const inside = line.points.find((point) => point.minute >= g.start && point.minute <= g.end);
      if (inside && !line.selected && !line.segment) group.append(svg("text", { class: "ptxt", x: x(inside.minute) + 7, y: y(inside.row) + 4 }, line.number));
      (line.selected ? chosen : gray).push(group);
      // Ett avgånget tåg på nu-linjen där det är nu; ett klart tåg vid avgången.
      const marker = line.at || (line.selected ? (line.segment ? line.segment[0] : line.points[0]) : line.segment ? line.segment[0] : null);
      if (line.segment && !line.selected) {
        const [from, to] = line.segment;
        const segment = svg("g", { class: "tr-lit", "data-train-number": line.number, role: "button", tabindex: "0", "aria-label": t("Tåg {number}", { number: line.number }) });
        segment.addEventListener("click", open);
        segment.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") open(event); });
        segment.append(svg("polyline", { class: "pl lit", points: `${x(from.minute).toFixed(1)},${y(from.row)} ${x(to.minute).toFixed(1)},${y(to.row)}` }));
        lit.push(segment);
      }
      if (marker) {
        const w = tagWidth(line.number), cleared = line.state === "cleared" && !line.selected;
        const mark = svg("g", { class: "tr-tag", "data-train-number": line.number, "pointer-events": "none" });
        mark.append(svg("rect", { class: `${cleared ? "tbh" : "tb"}${line.selected ? " sel" : ""}`, x: x(marker.minute) - w - 6, y: y(marker.row) - 11, width: w, height: 22, rx: 6 }),
          svg("text", { class: cleared ? "tbht" : "tbt", x: x(marker.minute) - w / 2 - 6, y: y(marker.row) + 5, "text-anchor": "middle" }, line.number));
        // Nivå 5 (Allt): förseningen ovanför taggen.
        const view = lives ? model.deviationView(5, lives.get(String(line.number))) : null;
        if (view?.mark) mark.append(svg("text", { class: `graph-train-mark is-${view.mark.tone}`, x: x(marker.minute) - w - 6, y: y(marker.row) - 14 }, view.mark.text));
        lit.push(mark);
      }
    }
    parts.push(...gray, ...chosen, ...lit);
    if (g.now !== null) {
      const at = x(g.now);
      parts.push(svg("line", { class: "now", x1: at, y1: 4, x2: at, y2: height - 18 }), svg("rect", { class: "nowb", x: at - 24, y: 0, width: 48, height: 18, rx: 5 }),
        svg("text", { class: "nowt", x: at, y: 13, "text-anchor": "middle" }, clockLabel(g.now)));
    }
    target.append(...parts);
    const meta = $("#drift-graph-meta");
    if (meta) meta.textContent = `${clockLabel(g.start)}–${clockLabel(g.end)} · ${t("tryck på ett tåg för att tända rutten")}`;
  }

  // ── Valt tåg och vald station ─────────────────────────────────────────
  const nowText = (now, stops) => {
    const code = (id) => stops.find((stop) => stop.station_id === id)?.station_code || id || "";
    const route = `${code(now.from_station_id)} → ${code(now.to_station_id)}`;
    const at = [code(now.station_id), now.track ? t("spår {track}", { track: now.track }) : ""].filter(Boolean).join(" ");
    switch (now.state) {
      case "waiting": return t("Väntar på klartecken {route}", { route });
      case "cleared": return t("Klart att avgå {route}", { route });
      case "on_line": return now.since ? t("På linjen {route} · avgick {time}", { route, time: now.since }) : t("På linjen {route}", { route });
      case "at_station": return now.time ? t("Vid {station} · avgår {time}", { station: at, time: now.time }) : t("Vid {station}", { station: at });
      case "arrived": return t("Ankommit {station}", { station: at });
      // The pill beside it already says "Inte avgått"; here only where and when.
      default: return !at ? "" : now.time ? t("Vid {station} · avgår {time}", { station: at, time: now.time }) : t("Vid {station}", { station: at });
    }
  };
  const NOW_LABEL = { on_line: ["sel", "På linjen"], waiting: ["warn", "Väntar"], cleared: ["sel", "Klart att avgå"], at_station: ["", "På station"], arrived: ["ok", "Ankommit"] };
  function closeButton(label, action) {
    const icon = svg("svg", { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 2.4, "stroke-linecap": "round", "aria-hidden": "true" });
    icon.append(svg("path", { d: "M6 6l12 12M18 6L6 18" }));
    return h("button", { type: "button", class: "kr-ibtn", "aria-label": label, on: { click: action } }, icon);
  }

  function trainAside() {
    const host = $("#drift-train-detail"), { number, data, error } = ctx.trainDetail;
    if (!host) return;
    host.hidden = !number;
    if (!number) { host.replaceChildren(); sigs.delete("train-aside"); return; }
    const listed = ctx.snapshot ? model.services(ctx.snapshot).find((service) => String(service.train_number) === String(number)) : null;
    const live = (data?.services || []).find((service) => (service.stops || []).length) || null;
    const service = live || listed;
    const names = ctx.snapshot ? model.stationMap(ctx.snapshot) : new Map();
    const stops = [...(service?.stops || [])].sort((a, b) => Number(a.stop_order ?? 0) - Number(b.stop_order ?? 0));
    if (!changed("train-aside", [number, live, listed?.stops, error, root.lang])) return;
    const stopName = (stop) => stop?.station_name || names.get(stop?.station_id)?.name || stop?.station_code || "";
    const first = stops[0], last = stops.at(-1);
    const times = [hhmm(first?.departure_time || first?.arrival_time), hhmm(last?.arrival_time || last?.departure_time)].filter(Boolean).join("–");
    const meta = [live?.train_type, times, stops.length ? plural(stops.length, "{count} uppehåll", "{count} uppehåll") : ""].filter(Boolean).join(" · ");
    const now = live?.now;
    const body = [h("div", { class: "kr-ph kr-ph--top" },
      h("div", { class: "kr-aside__title" },
        h("div", { class: "kr-aside__row" }, badge(number, { selected: true }), stops.length ? h("span", { class: "kr-aside__route" }, `${stopName(first)} → ${stopName(last)}`) : null),
        h("span", { class: "kr-c" }, meta)),
      h("span", { class: "kr-sp" }), closeButton(t("Stäng tågpanelen"), () => hooks.clear?.()))];
    if (error) body.push(h("p", { class: "kr-aside__note kr-error" }, error));
    else if (!live) body.push(h("p", { class: "kr-aside__note" }, t("Hämtar tåget …")));
    if (live && now) {
      const [tone, label] = NOW_LABEL[now.state] || ["", "Inte avgått"];
      body.push(h("div", { class: "kr-aside__status" }, pill(tone, t(label)), h("span", { class: "kr-aside__now" }, nowText(now, stops)),
        live.delay_minutes ? h("span", { class: "kr-tag warn" }, t("{minutes} min sen", { minutes: live.delay_minutes })) : null));
    }
    const list = h("div", { class: "kr-stops" });
    stops.forEach((stop, index) => {
      const reached = stop.departure === "departed" || stop.arrival === "arrived";
      const here = now && ["not_departed", "at_station", "arrived"].includes(now.state) && now.station_id === stop.station_id && (now.state !== "not_departed" || index === 0);
      const leaving = now && ["waiting", "cleared"].includes(now.state) && now.from_station_id === stop.station_id;
      const track = [stop.planned_track ? t("spår {track}", { track: stop.planned_track }) : "",
        stop.actual_track && stop.actual_track !== stop.planned_track ? t("inne på spår {track}", { track: stop.actual_track }) : ""].filter(Boolean).join(" · ");
      list.append(h("div", { class: `kr-stop${reached ? " done" : ""}${here || leaving ? " here" : ""}`, data: { stationId: stop.station_id } },
        h("i", { "aria-hidden": "true" }),
        h("button", { type: "button", class: "kr-stoplink", on: { click: () => hooks.selectStation?.(stop.station_id, true) } }, h("span", {}, stopName(stop)), track ? h("small", {}, track) : null),
        h("span", { class: "t" }, hhmm(stop.arrival_time) || "—"), h("span", { class: "t" }, hhmm(stop.departure_time) || "—")));
      if (now?.state === "on_line" && now.from_station_id === stop.station_id && stops[index + 1]?.station_id === now.to_station_id) {
        list.append(h("div", { class: "kr-stop between" }, h("i", { "aria-hidden": "true" }), h("span", { class: "kr-between__text" }, `${t("På linjen")} · ${number} →`), h("span", {}), h("span", {})));
      }
    });
    body.push(list, h("div", { class: "kr-pf" }, h("span", {}, t("Valt tåg lyser på kartan, i diagrammet och bland händelserna."))));
    host.replaceChildren(...body);
  }

  function stationAside() {
    const host = $("#overview-station-inspector"), id = ctx.selection.station, snapshot = ctx.snapshot;
    if (!host) return;
    const station = id && snapshot ? snapshot.stations?.find((item) => item.id === id) : null;
    host.hidden = !station;
    if (!station) { host.replaceChildren(); sigs.delete("station-aside"); return; }
    const here = model.stationCounts(snapshot).get(station.id) || [];
    const devices = ctx.devices.filter((device) => device.station_id === station.id);
    const place = model.placement(ctx.presentation, station.id);
    const events = model.events(snapshot, { station: station.id, limit: 5, nowSeconds: liveSeconds() });
    if (!changed("station-aside", [station.id, here, devices.map((device) => [device.device_code, device.connection?.state]), place, events, ctx.selection.train, root.lang])) return;
    const names = model.stationMap(snapshot);
    const links = (snapshot.connections || []).filter((c) => c.station_a_id === station.id || c.station_b_id === station.id).length;
    const section = (title, ...kids) => h("div", { class: "kr-aside__sec" }, h("h4", {}, title), ...kids);
    const body = [h("div", { class: "kr-ph kr-ph--top" },
      h("div", { class: "kr-aside__title" },
        h("div", { class: "kr-aside__row" }, h("b", { class: "kr-aside__name" }, station.name), h("span", { class: "kr-code" }, station.code || "")),
        h("span", { class: "kr-c" }, [plural(here.length, "{count} tåg inne", "{count} tåg inne"), plural(links, "{count} sträcka", "{count} sträckor")].join(" · "))),
      h("span", { class: "kr-sp" }), closeButton(t("Stäng stationspanelen"), () => hooks.selectStation?.(null, true)))];
    body.push(section(t("Tåg inne"), here.length
      ? h("div", { class: "kr-badges" }, here.map((number) => h("button", { type: "button", class: "kr-badgebtn", on: { click: () => hooks.selectTrain?.(number) } }, badge(number, { plain: true, selected: ctx.selection.train === number }))))
      : h("p", { class: "kr-aside__note" }, t("Inga tåg inne."))));
    body.push(section(t("Boxar"), devices.length
      ? h("div", { class: "kr-aside__lines" }, devices.map((device) => h("div", { class: "kr-aside__line" }, h("span", { class: "mono" }, device.device_code),
        pill(model.connectionTone(device.connection), device.connection?.state === "online" ? t("Online") : device.connection?.state === "lost" ? t("Ingen kontakt") : t("Offline")))))
      : h("p", { class: "kr-aside__note" }, t("Ingen box tilldelad."))));
    if (place.available) body.push(section(t("Vänster · höger"), h("p", { class: "mono kr-aside__plain" }, `${place.left.join(", ") || "—"} · ${place.right.join(", ") || "—"}`)));
    body.push(section(t("Nästa här"), events.length
      ? h("div", { class: "kr-events kr-events--tight" }, events.map((event) => {
        const view = model.deviationView(levelNow(), event);
        const what = h("span", { class: "w" }, eventText(event, names));
        if (view.mark?.style === "text") what.append(" ", markOf(view));
        return h("button", { type: "button", class: `kr-ev${view.mark ? ` is-${view.mark.tone}` : ""}`, on: { click: () => hooks.selectTrain?.(event.train) } },
          eventTime(event, view), badge(event.train, { plain: true }), what, eventWhen(event, view));
      }))
      : h("p", { class: "kr-aside__note" }, t("Inga fler planerade händelser idag."))));
    host.replaceChildren(...body);
  }

  // ── Sök ───────────────────────────────────────────────────────────────
  function wireSearch() {
    const input = $("#kr-search"), list = $("#kr-search-results"); if (!input || !list) return;
    const state = { results: [], active: -1 };
    const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); state.active = -1; input.removeAttribute("aria-activedescendant"); };
    const choose = (item) => {
      if (!item) return;
      close(); input.value = ""; input.blur();
      if (item.kind === "train") hooks.selectTrain?.(item.id); else hooks.selectStation?.(item.id);
    };
    const paint = () => {
      state.results = model.search(ctx.snapshot, input.value);
      const empty = h("li", { class: "kr-search-empty" }, input.value.trim() ? t("Inga träffar.") : "");
      list.replaceChildren(...(state.results.length ? state.results.map((item, index) => h("li", { role: "option", id: `kr-search-${index}`, class: `kr-search-item${index === state.active ? " on" : ""}`,
        "aria-selected": String(index === state.active), on: { mousedown: (event) => { event.preventDefault(); choose(item); } } },
      item.kind === "train" ? badge(item.label, { plain: true }) : h("b", {}, item.label), h("span", { class: "kr-c" }, item.detail), h("span", { class: "kr-sp" }),
      h("span", { class: "kr-kind" }, t(item.kind === "train" ? "tåg" : "station")))) : [empty]));
      const open = Boolean(input.value.trim());
      list.hidden = !open; input.setAttribute("aria-expanded", String(open));
    };
    input.addEventListener("input", () => { state.active = -1; paint(); });
    input.addEventListener("focus", () => { if (input.value.trim()) paint(); });
    input.addEventListener("blur", close);
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const count = state.results.length; if (!count) return;
        state.active = (state.active + (event.key === "ArrowDown" ? 1 : -1) + count) % count; paint();
        input.setAttribute("aria-activedescendant", `kr-search-${state.active}`);
      } else if (event.key === "Enter") { event.preventDefault(); choose(state.results[Math.max(state.active, 0)]); }
      else if (event.key === "Escape") { input.value = ""; close(); input.blur(); }
    });
    doc.addEventListener("keydown", (event) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(doc.activeElement?.tagName || "") || doc.activeElement?.isContentEditable || input.offsetParent === null) return;
      event.preventDefault(); input.focus();
    });
  }

  // ── Dialoger och popover ──────────────────────────────────────────────
  function openDialog(id, trigger) {
    const dialog = doc.getElementById(id); if (!dialog || dialog.open || doc.querySelector("dialog[open]")) return;
    dialog.__origin = trigger || doc.activeElement;
    doc.body.append(dialog);
    dialog.showModal();
    dialog.querySelector("[data-close-modal]")?.focus({ preventScroll: true });
    hooks.dialogOpened?.(id, dialog);
  }
  function wireDialogs() {
    for (const dialog of $$("dialog.kr-dialog")) {
      dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
      dialog.addEventListener("close", () => { const origin = dialog.__origin; if (origin?.isConnected) origin.focus({ preventScroll: true }); });
      $$("[data-close-modal]", dialog).forEach((el) => el.addEventListener("click", () => dialog.close()));
    }
    doc.addEventListener("click", (event) => {
      const trigger = event.target.closest?.("[data-kr-dialog]");
      if (trigger) openDialog(trigger.dataset.krDialog, trigger);
    });
    const pop = $("#connect-terminals"), opener = $("#drift-connect-open");
    if (pop && opener) {
      pop.addEventListener("toggle", (event) => {
        if (event.newState !== "open") return;
        const rect = opener.getBoundingClientRect(), width = Math.min(420, innerWidth - 24);
        pop.style.width = `${width}px`;
        pop.style.top = `${Math.round(rect.bottom + 8)}px`;
        pop.style.left = `${Math.round(Math.max(12, Math.min(innerWidth - width - 12, rect.right - width)))}px`;
      });
      pop.addEventListener("click", (event) => { if (event.target.closest("[data-open-modal]")) pop.hidePopover?.(); });
    }
    doc.addEventListener("click", async (event) => {
      const copy = event.target.closest?.("[data-copy]"); if (!copy) return;
      const source = doc.getElementById(copy.dataset.copy), original = copy.dataset.label || copy.textContent;
      copy.dataset.label = original;
      try { await navigator.clipboard.writeText(source.textContent); copy.textContent = t("Kopierat"); } catch { copy.textContent = t("Kopiera inte möjligt"); }
      setTimeout(() => { copy.textContent = original; }, 2000);
    });
  }

  function wireChrome() {
    $("#kr-theme-toggle")?.addEventListener("click", () => { theme.toggle(); scheduleRender(); });
    theme.set(theme.get(), false);
    $("#drift-events-clear")?.addEventListener("click", () => hooks.clear?.());
    const select = $("#drift-graph-window");
    if (select) {
      select.value = String(ctx.graphWindow);
      select.addEventListener("change", () => { ctx.graphWindow = Number(select.value); store.set("trainmeet.driftGraphWindow", String(ctx.graphWindow)); renderGraph(); });
    }
    doc.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || doc.querySelector("dialog[open]")) return;
      let popoverOpen = false;
      try { popoverOpen = Boolean(doc.querySelector(":popover-open")); } catch { /* äldre webbläsare */ }
      if (popoverOpen || doc.body.dataset.mode !== "kor" || !(ctx.selection.train || ctx.selection.station)) return;
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(doc.activeElement?.tagName || "")) return;
      hooks.clear?.();
    });
    // Kartan och diagrammet ritas om när rutan ändrar bredd.
    const map = $("#drift-map .kr-map");
    if (map && typeof ResizeObserver === "function") {
      let width = 0;
      new ResizeObserver(() => {
        const next = Math.round(map.clientWidth || 0);
        if (next && next !== width) { width = next; scheduleRender(); }
      }).observe(map);
    }
  }

  // ── Samlad uppritning ─────────────────────────────────────────────────
  function render() {
    renderClock();
    // US-träffar har ingen bana, inga stationer och inget diagram i Drift: bara klockan och trafiken i Dispatcher.
    for (const selector of ["#drift-simulation", "#drift-map", ".kr-split", "#drift-graph", "#drift-stats"]) { const el = $(selector); if (el) el.hidden = ctx.us; }
    $("#us-runtime-summary")?.classList.toggle("hidden", !ctx.us);
    if (ctx.us || !ctx.snapshot) return;
    const rows = model.stationRows({ snapshot: ctx.snapshot, devices: ctx.devices, presentation: ctx.presentation, simulation: ctx.simulation });
    renderStats(rows); renderStations(rows); renderEvents(); renderMap(); renderGraph();
    trainAside(); stationAside(); placeAsides();
  }
  // The panels float over the page. They keep to the side of the screen the
  // chosen station or train is not on, so that what was chosen stays in view.
  function placeAsides() {
    const host = $(".kr-asides"); if (!host) return;
    const mark = $("#overview-topology .topology-node.selected, #overview-topology .topology-train.selected");
    let left = false;
    if (mark && mark.getBoundingClientRect) { const box = mark.getBoundingClientRect(); left = box.width > 0 && box.left + box.width / 2 > innerWidth / 2; }
    host.classList.toggle("kr-asides--left", left);
  }
  function scheduleRender() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; render(); });
  }
  /** app.js lämnar nya data (delar räcker) och Drift ritar om det som berörs. */
  function update(part = {}) {
    for (const [key, value] of Object.entries(part)) {
      if (key === "selection") ctx.selection = { train: value.train ?? null, station: value.station ?? null };
      else if (key === "trainDetail") ctx.trainDetail = { number: value.number ?? null, data: value.data ?? null, error: value.error || "" };
      else ctx[key] = value;
    }
    scheduleRender();
  }

  // En gång i sekunden medan klockan går: klockan och diagrammet. Diagrammet
  // ritas inte om medan ett tåg i det har fokus från tangentbordet.
  function tick() {
    const clock = ctx.clock || ctx.snapshot?.clock;
    if (!clock?.running || doc.hidden) return;
    renderClock();
    const graph = $("#overview-graph");
    if (!ctx.us && graph && graph.getClientRects().length && !graph.contains(doc.activeElement)) renderGraph();
  }

  function init() { wireChrome(); wireSearch(); wireDialogs(); scheduleRender(); setInterval(tick, 1000); }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init); else init();

  globalThis.TrainMeetDrift = { hooks, update, render, theme, model, nowText, openDialog, formatTime, get context() { return ctx; } };
})();
