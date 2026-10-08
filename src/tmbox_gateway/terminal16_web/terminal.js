/* Generic terminal adapter. No train states, routes, destinations or action
   names live here. All ordinary display text/key labels arrive from Server. */
(function (root) {
  "use strict";
  class EntryBuffer {
    constructor() { this.digits = ""; this.context = null; }
    clear() { this.digits = ""; this.context = null; }
    sync(frame) { if (this.context && this.context !== frame.entry?.context) this.clear(); }
    press(key, frame) {
      this.sync(frame);
      const entry = frame.entry;
      if (!entry) return {local: false};
      if (/^[0-9]$/.test(key)) {
        if (this.digits.length < entry.max_length) this.digits += key;
        this.context = entry.context;
        return {local: true};
      }
      if (this.digits) {
        if (key === entry.cancel) { this.clear(); return {local: true}; }
        if (key === entry.erase) { this.digits = this.digits.slice(0, -1); return {local: true}; }
        if (key === entry.shortcut) { this.clear(); return {local: false}; }
        if (key === entry.commit) return {local: false, train_number: this.digits, entry_context: entry.context};
        return {local: true};
      }
      return {local: false};
    }
    lines(frame) {
      this.sync(frame);
      if (!this.digits) return frame.lines;
      const lines = [...frame.entry.lines];
      const {row, column, max_length} = frame.entry;
      const original = [...lines[row]];
      original.splice(column, max_length, ...this.digits.padEnd(max_length, "_"));
      lines[row] = original.join("");
      return lines;
    }
  }
  // Same rule and the same times as the physical box (server_terminal.h), so
  // the browser keypad behaves like the real one. After a screen change only
  // keys that act on traffic wait input_guard_ms, so a press meant for the
  // previous screen cannot act on the new one. Browsing, digits and train
  // search answer at once; a key without the flag counts as acting.
  const WAITING_SHOWN_MS = 1500, COMMAND_GIVE_UP_MS = 30000, UNANSWERED_SHOWN_MS = 3000, SILENCE_MS = 15000, POLL_MS = 500;
  const WAITING_TEXT = "VANTAR PA SVAR", UNANSWERED_TEXT = "INGET SVAR";
  // The same two lines in the box's own language, as device_ui.py sends them
  // to a physical box (tests/test_tmbox_language.py keeps the two equal).
  const BOX_TEXT = {
    sv: {"VANTAR PA SVAR": "VANTAR PA SVAR", "INGET SVAR": "INGET SVAR"},
    en: {"VANTAR PA SVAR": "AWAITING REPLY", "INGET SVAR": "NO REPLY"},
    da: {"VANTAR PA SVAR": "AFVENTER SVAR", "INGET SVAR": "INTET SVAR"},
    nb: {"VANTAR PA SVAR": "VENTER PA SVAR", "INGET SVAR": "INGEN SVAR"},
    de: {"VANTAR PA SVAR": "WARTE AUF ANTW.", "INGET SVAR": "KEINE ANTWORT"},
  };
  const boxText = (text, language) => BOX_TEXT[language]?.[text] ?? text;
  function screenChanged(before, after) {
    return !before || before.view_token !== after.view_token || JSON.stringify(before.keys) !== JSON.stringify(after.keys);
  }
  function guarded(frame, key, digits, now, until) {
    if (now >= until || digits || /^[0-9]$/.test(key)) return false;
    return frame.keys?.[key]?.acts !== false;
  }
  function overlay(model, now) {
    if (model.busy && now - model.sentAt >= WAITING_SHOWN_MS) return WAITING_TEXT;
    if (model.unansweredAt && now - model.unansweredAt < UNANSWERED_SHOWN_MS) return UNANSWERED_TEXT;
    return "";
  }
  // randomUUID exists only on HTTPS and localhost. A meet's box is opened at
  // http://trainmeet.local, where it is missing: every key that goes to the
  // server then threw before it was sent, while digits and B, which stay
  // here, still worked. getRandomValues is there everywhere.
  function commandId(source = root.crypto) {
    if (typeof source?.randomUUID === "function") return source.randomUUID();
    const bytes = source.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40; bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  // The station's timetable beside the live box shows delays as the meet's
  // other views do (Casper 2026-10-08): the server sends each row's delay,
  // and drift-model.js deviationView decides what the chosen level shows.
  const clockShift = (time, minutes) => {
    const match = /^(\d{2}):(\d{2})$/.exec(String(time || ""));
    if (!match) return time;
    const value = ((Number(match[1]) * 60 + Number(match[2]) + minutes) % 1440 + 1440) % 1440;
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  };
  function timetableRow(row, level, drift) {
    const view = drift.deviationView(level, {delayMinutes: row.delay_minutes || 0, estimated: Boolean(row.estimated),
      earlyMinutes: row.early_minutes || 0, earlyKind: row.early_kind || null, trainType: row.train_type || "person"});
    const early = view.mark?.tone === "early";
    const shown = !view.strike ? null : early ? clockShift(row.time, -view.mark.minutes) : row.expected_time || clockShift(row.time, view.mark.minutes);
    const done = row.state === "arrived" || (row.kind === "departure" && row.state === "departed");
    return {view, early, time: row.time, shown, done, signature: [row.state, view.mark?.text || "", view.estimated]};
  }
  if (typeof module !== "undefined") module.exports = {EntryBuffer, screenChanged, guarded, overlay, commandId, boxText, BOX_TEXT, timetableRow, clockShift,
    times: {WAITING_SHOWN_MS, COMMAND_GIVE_UP_MS, UNANSWERED_SHOWN_MS, SILENCE_MS, POLL_MS}};
  if (typeof document === "undefined") return;

  // The page around the box follows the reader's language (i18n.js, loaded
  // first); what the box itself shows - frames, key labels, status - arrives
  // from Server in the language set for that box and is never translated here.
  const i18n = root.TrainMeetI18n;
  const t = (source, values = {}) => i18n ? i18n.t(source, values)
    : String(source).replace(/\{(\w+)\}/g, (match, name) => name in values ? String(values[name]) : match);
  i18n?.annotate(document.body);
  const helpWords = () => { for (const summary of document.querySelectorAll(".tm-help > summary")) Object.assign(summary.dataset, {show: t("Visa"), hide: t("Dölj")}); };
  helpWords();

  const boxes = new Map();
  const live = document.body.dataset.terminal === "live";
  let identity = null, pollVersion = 0;
  let connected = false, resetting = false, text = {}, lastContact = 0;
  let placement = null, placementDraft = null;
  const placementDialog = document.querySelector("#placement-dialog");
  const keyOrder = "123A456B789C*0#D";
  const {drawLCD} = root.TMBoxLCD;  // lcd.js, loaded first
  function makeBox(frame) {
    const card = document.createElement("article"); card.className = "box"; card.tabIndex = 0; card.dataset.device = frame.device_id;
    card.innerHTML = '<div class="box-heading"><h2></h2><span class="box-code"></span></div><div class="box-status"></div><div class="box-queue" role="status"></div><div class="tmbox-case"><div class="lcd-frame"><div class="lcd" role="img"></div></div><div class="keypad"></div></div><div class="key-hints"></div><p class="box-message" role="status"></p><div class="box-timetable"></div>';
    const model = {card, frame, entry: new EntryBuffer(), busy: false, sentAt: 0, unansweredAt: 0,
      until: performance.now() + (frame.input_guard_ms ?? 0), uiMessage: "", uiError: false};
    card.querySelector("h2").textContent = frame.station;
    card.querySelector(".box-code").textContent = frame.device_id;
    for (const key of keyOrder) {
      const button = document.createElement("button"); button.type = "button";
      button.className = "key" + (/[A-D]/.test(key) ? " function" : "");
      button.textContent = key; button.dataset.key = key;
      button.addEventListener("click", () => press(model, key));
      card.querySelector(".keypad").append(button);
    }
    card.addEventListener("keydown", event => {
      if (event.key.length === 1 && keyOrder.includes(event.key.toUpperCase())) {
        event.preventDefault(); if (!event.repeat) press(model, event.key.toUpperCase());
      }
    });
    document.querySelector("#boxes").append(card); boxes.set(frame.device_id, model); return model;
  }
  function renderTimetable(model, timetable) {
    if (!timetable) return;
    const signature = JSON.stringify(timetable);
    if (signature === model.timetableSignature) return;
    model.timetableSignature = signature;
    const table = document.createElement("table");
    const caption = document.createElement("caption"); caption.textContent = t(timetable.title);
    const head = document.createElement("thead"), heading = document.createElement("tr");
    for (const label of timetable.columns) {
      const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = t(label); heading.append(cell);
    }
    // Rows carry the kind, the time and the other station, so the words around them follow the page's language.
    const words = {departure: ["Avg {time}", "Till {station}"], arrival: ["Ank {time}", "Från {station}"]};
    head.append(heading);
    const body = document.createElement("tbody");
    for (const row of timetable.rows) {
      const line = document.createElement("tr");
      for (const key of ["train_number", "time", "route"]) {
        const cell = document.createElement(key === "train_number" ? "th" : "td");
        if (key === "train_number") cell.scope = "row";
        const [time, route] = words[row.kind] || [];
        cell.textContent = key === "time" && time ? t(time, {time: row.clock}) : key === "route" && route ? t(route, {station: row.station}) : row[key];
        line.append(cell);
      }
      body.append(line);
    }
    if (!timetable.rows.length) {
      const line = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = timetable.columns.length; cell.textContent = t(timetable.empty); line.append(cell); body.append(line);
    }
    table.append(caption, head, body);
    model.card.querySelector(".box-timetable").replaceChildren(table);
  }
  function render(model) {
    const {card, frame, entry} = model;
    const lines = [...entry.lines(frame)];
    const shown = overlay(model, performance.now());
    if (shown) lines[1] = boxText(shown, frame.language).padEnd(16);
    drawLCD(card.querySelector(".lcd"), lines);
    card.querySelector("h2").textContent = frame.station || t("Väntar på station");
    card.querySelector(".box-code").textContent = live ? identity?.device_code || frame.device_id : frame.device_id;
    card.querySelector(".box-status").textContent = frame.status;
    const queue = card.querySelector(".box-queue");
    queue.textContent = frame.requests?.label || "";
    queue.classList.toggle("pending", !!frame.requests?.count);
    const hints = card.querySelector(".key-hints"); hints.replaceChildren();
    const labels = entry.digits ? frame.entry.labels : Object.fromEntries(Object.entries(frame.keys).map(([key, info]) => [key, info.label]));
    for (const [key, label] of Object.entries(labels)) { const hint = document.createElement("span"); const strong = document.createElement("b"); strong.textContent = key; hint.append(strong, label); hints.append(hint); }
    // Every key can always be pressed, as on the box. One that cannot do
    // anything right now simply does nothing; nothing lights up or dims.
    for (const button of card.querySelectorAll(".key")) {
      const key = button.dataset.key;
      button.setAttribute("aria-label", labels[key] ? `${key} · ${labels[key]}` : key);
    }
    const status = card.querySelector(".box-message");
    status.textContent = model.uiMessage || (entry.digits ? t(text.entry) : "");
    status.classList.toggle("error", model.uiError);
  }
  function apply(frame) {
    const model = boxes.get(frame.device_id) || makeBox(frame);
    if (model.frame.entry?.context === frame.entry?.context &&
        (frame.revision < model.frame.revision || (frame.revision === model.frame.revision && frame.view_revision < model.frame.view_revision))) return;
    if (model.frame.entry?.context !== frame.entry?.context) message(model, "");
    if (screenChanged(model.frame, frame)) model.until = Math.max(model.until, performance.now() + (frame.input_guard_ms ?? 0));
    model.frame = frame; render(model);
  }
  function message(model, value, error=false) {
    model.uiMessage = value || ""; model.uiError = error;
    const element = model.card.querySelector(".box-message"); element.textContent = value || ""; element.classList.toggle("error", error);
  }
  async function press(model, key) {
    if (!connected || resetting || model.busy) return;
    if (guarded(model.frame, key, model.entry.digits, performance.now(), model.until)) return;
    const entry = model.entry.press(key, model.frame);
    if (entry.local) { message(model, ""); render(model); return; }
    if (!entry.train_number && !(key in model.frame.keys)) return;
    const body = {device_id: model.frame.device_id, command_id: commandId(), view_token: model.frame.view_token, key};
    if (entry.train_number) Object.assign(body, {train_number: entry.train_number, entry_context: entry.entry_context});
    const context = model.frame.entry?.context;
    ++pollVersion;
    // As on the box: nothing is shown for a quick answer, the second row says
    // so after 1.5 s, and the command is given up only after 30 s. Digits stay.
    model.busy = true; model.sentAt = performance.now(); model.unansweredAt = 0; message(model, ""); render(model);
    const waiting = setTimeout(() => render(model), WAITING_SHOWN_MS);
    try {
      if (live) delete body.device_id;
      const response = await fetch(live ? "/v1/tmbox/terminal" : "./api/key", {method:"POST", credentials: live ? "omit" : "same-origin", headers:{"Content-Type":"application/json", ...(live ? {Authorization: `Bearer ${identity.access_token}`} : {})}, body:JSON.stringify(body), signal:AbortSignal.timeout(COMMAND_GIVE_UP_MS)});
      const result = await response.json();
      lastContact = performance.now();
      if (model.frame.entry?.context !== context) return;
      if (result.status === "accepted" || result.status === "duplicate") model.entry.clear();
      if (result.frame) apply(result.frame);
      // Server's answers are a fixed Swedish set (terminal16.py, terminal16_runtime.py); tmbox.txt translates them.
      message(model, result.message ? t(result.message) : "", !response.ok);
    } catch {
      model.unansweredAt = performance.now();
      setTimeout(() => render(model), UNANSWERED_SHOWN_MS + 10);
    }
    finally { clearTimeout(waiting); model.busy = false; render(model); }
  }
  // Page chrome only: the dot in the header and the "Din TMBox" card. Nothing here
  // reads or changes traffic; it mirrors the same frame the box itself renders.
  function showConnection(online) {
    const dot = document.querySelector("#connection-dot");
    if (!dot) return;
    dot.classList.toggle("is-offline", !online);
    dot.title = document.querySelector("#connection").textContent;
  }
  function renderIdentity(frame) {
    const state = document.querySelector("#box-state");
    if (!state || !identity) return;
    const assigned = Boolean(frame.station);
    state.classList.toggle("tm-state--ok", assigned);
    document.querySelector("#box-state-text").textContent = assigned ? t("Tilldelad station: {station}", {station: frame.station}) : t("Väntar på station");
    const code = document.querySelector("#box-identity");
    code.hidden = false; code.classList.toggle("is-big", !assigned);
    document.querySelector("#box-identity-code").textContent = identity.device_code || "";
    document.querySelector("#box-note").textContent = assigned
      ? t("Boxen arbetar mot träffens riktiga trafik. Trafikledningen kan flytta den till en annan station eller ta bort den under Inställningar → TMBoxar. Språket väljer trafikledningen i TrainMeet Server.")
      : t("Visa koden för trafikledningen, som tilldelar din station under Inställningar → TMBoxar. Boxen börjar arbeta direkt när den är tilldelad – du behöver inte ladda om sidan.");
  }
  // Page chrome for both pages: key help, how often the browser asks and which
  // server version answers. The test bench asks its own health check, so the
  // footer always names the code its boxes actually run.
  async function loadChrome() {
    const help = document.querySelector("#key-help");
    if (help && matchMedia("(min-width: 901px)").matches) help.open = true;
    const rate = document.querySelector("#connection-rate");
    if (rate) rate.textContent = ` · ${t("uppdateras {n} gånger i sekunden", {n: 1000 / POLL_MS})}`;
    try {
      const response = await fetch(live ? "/healthz" : "./healthz", {credentials: "omit", cache: "no-store", signal: AbortSignal.timeout(5000)});
      const health = response.ok ? await response.json() : null;
      if (health?.version) document.querySelector("#server-version").textContent = `TrainMeet Server ${health.version}`;
    } catch {}
    if (!live) return;
    try {
      const response = await fetch("/v1/workspaces", {credentials: "omit", cache: "no-store", signal: AbortSignal.timeout(5000)});
      const meet = response.ok ? (await response.json()).selected_meet : null;
      if (meet?.name) document.querySelector("#meet-name").textContent = meet.name;
      const region = document.querySelector("#meet-region");
      if (meet?.operating_region) {
        region.textContent = (meet.country || (meet.operating_region === "us" ? "us" : "se")).toUpperCase();
        region.className = `tm-badge tm-badge--${meet.operating_region === "us" ? "us" : "eu"}`;
        region.hidden = false;
      }
    } catch {}
  }
  let lastState = null;
  function update(state) {
    connected = true; text = state.text; lastState = state;
    if (state.placement) placement = state.placement;
    resetButtons();
    // The test bench's server sends these in Swedish; they are fixed page copy.
    document.querySelector("h1").textContent = t(text.title);
    document.querySelector("#subtitle").textContent = live ? text.subtitle : t(text.subtitle);
    document.querySelector("#connection").textContent = t(text.ready);
    document.querySelector("#session-info").textContent = text.session ? t(text.session, text.values) : "";
    showConnection(true);
    for (const frame of state.frames) apply(frame);
    if (live && state.frames.length) renderIdentity(state.frames[0]);
    for (const [device, timetable] of Object.entries(state.timetables || {})) {
      const model = boxes.get(device); if (model) renderTimetable(model, timetable);
    }
    const samples = document.querySelector("#language-samples"); samples.replaceChildren();
    for (const sample of state.language_samples || []) {
      const card = document.createElement("div"); card.className = "language-sample";
      const title = document.createElement("h3"); title.textContent = sample.label;
      const bezel = document.createElement("div"); bezel.className = "lcd-frame";
      const lcd = document.createElement("div"); lcd.className = "lcd"; lcd.lang = sample.language; lcd.setAttribute("role", "img"); drawLCD(lcd, sample.lines);
      const note = document.createElement("p"); note.textContent = t("{count}/8 egna LCD-tecken i denna bild", {count: sample.lcd.glyphs.length});
      bezel.append(lcd); card.append(title, bezel, note); samples.append(card);
    }
    document.querySelector("#mode").textContent = t(state.mode === "direct" ? "Testläge: direkttrafik, fortfarande med reservation och faktisk avgång." : "Testläge: mottagarens klartecken krävs före avgång.");
    document.querySelector("#event-count").textContent = t(state.audit.length === 1 ? "{count} trafikåtgärd" : "{count} trafikåtgärder", {count: state.audit.length});
    const events = document.querySelector("#events"); events.replaceChildren();
    for (const event of state.audit) { const li = document.createElement("li"); li.textContent = `${event.revision}. ${event.station_id.toUpperCase()} · ${event.action} · ${event.connection_id}`; events.append(li); }
  }
  function lost() {
    // As when the box's session ends: typed digits go with it.
    connected = false; resetButtons(); document.querySelector("#connection").textContent = t(text.offline || (live ? "Servern är inte ansluten." : "Testservern är inte ansluten.")); showConnection(false);
    for (const model of boxes.values()) { model.entry.clear(); render(model); }
  }
  // No answer is not a lost server: like the box, give up only after 15 s
  // without one. A refusal is an answer and counts at once.
  function silent() { if (performance.now() - lastContact >= SILENCE_MS) lost(); }
  // The test bench is pushed every change, like the box. A dropped stream
  // reconnects by itself; only 15 s without it - or a stream the server has
  // closed for good, such as an expired test - counts as a lost server.
  const events = live ? {} : new EventSource("./events");
  let silence = null;
  const heard = () => { clearTimeout(silence); silence = null; };
  events.onopen = heard;
  events.onmessage = event => { heard(); update(JSON.parse(event.data)); };
  events.onerror = () => {
    if (events.readyState === EventSource.CLOSED) { heard(); lost(); }
    else silence ??= setTimeout(() => { silence = null; lost(); }, SILENCE_MS);
  };
  // A phone that wakes up often reloads the page before its Wi-Fi is back.
  // The saved box is still this box: use it at once and keep asking until
  // the server answers. Only the server saying it no longer knows the box
  // (401) means a new one is needed, and that is made when someone asks.
  const SAVED = "trainmeet.browser-tmbox";
  function forgotten() {
    identity = null; ++pollVersion; lost();
    try { localStorage.removeItem(SAVED); } catch {}
    document.querySelector("#connection").textContent = t("Den här TMBoxen finns inte längre på servern.");
    document.querySelector("#start-client").hidden = false;
  }
  async function startLive() {
    document.querySelector("#start-client").hidden = true;
    let saved; try { saved = JSON.parse(localStorage.getItem(SAVED)); } catch {}
    if (saved?.access_token) {
      identity = saved;
      confirmLive(saved.access_token);
      return;
    }
    try {
      const response = await fetch("/v1/browser-clients", {method:"POST", credentials:"omit", cache:"no-store",
        headers:{"Content-Type":"application/json"}, body:JSON.stringify({workspace:"tmbox"}), signal:AbortSignal.timeout(5000)});
      const result = await response.json();
      if (!response.ok || result.workspace !== "tmbox") throw Error(result.message || t("Klienten kan inte anslutas."));
      identity = result;
      localStorage.setItem(SAVED, JSON.stringify(identity));
    } catch (error) {
      document.querySelector("#connection").textContent = error.message;
      document.querySelector("#start-client").hidden = false;
    }
  }
  // The saved box asks who it is (station, code) until the server answers.
  async function confirmLive(token) {
    let response;
    try {
      response = await fetch("/v1/browser-clients/self", {credentials:"omit", cache:"no-store",
        headers:{Authorization:`Bearer ${token}`}, signal:AbortSignal.timeout(5000)});
      if (response.status === 401) return identity?.access_token === token && forgotten();
      if (!response.ok) throw Error();
      const result = await response.json();
      if (result.workspace !== "tmbox" || identity?.access_token !== token) return;
      identity = {...result, access_token: token};
      try { localStorage.setItem(SAVED, JSON.stringify(identity)); } catch {}
    } catch {
      if (identity?.access_token === token) setTimeout(() => confirmLive(token), 2000);
    }
  }
  // The box is sent every change; the browser fetches as often as the server
  // checks for them, and keeps doing so while a command waits. Coming back to
  // the page, or the network coming back, asks at once instead of waiting.
  let pollTimer = null, polling = false;
  async function pollLive() {
    clearTimeout(pollTimer); pollTimer = null;
    if (polling) return;
    polling = true;
    try {
      if (identity && !document.hidden) {
        const version = pollVersion, token = identity.access_token;
        let response;
        try {
          response = await fetch("/v1/tmbox/terminal", {credentials:"omit",cache:"no-store",headers:{Authorization:`Bearer ${token}`},signal:AbortSignal.timeout(5000)});
          if (response.status >= 500) throw Error();
          const frame = await response.json();
          lastContact = performance.now();
          if (response.status === 401 && identity?.access_token === token) forgotten();
          else if (!response.ok) lost();
          else if (version === pollVersion) update({frames:[frame],audit:[],text:{title:"TMBox",subtitle:location.host,
            session:"Enhetskod: {code} · Station tilldelas av administratören",values:{code:identity.device_code},ready:"Ansluten till servern",offline:"Servern är inte ansluten.",entry:"Siffrorna stannar här tills du trycker #."}});
        } catch { if (response && response.status < 500) lost(); else silent(); }
      }
    } finally {
      polling = false;
      pollTimer = setTimeout(pollLive, POLL_MS);
    }
  }
  // The station's timetable beside the live box, with delays. How much is
  // shown is chosen in the "Your TMBox" card and kept in this browser; empty
  // follows the meet's default. Asked for every few seconds while the page shows.
  const DEVIATION_KEY = "trainmeet.tmbox.deviationLevel", TIMETABLE_MS = 5000, RECENT_MS = 30000;
  const LEVEL_NAMES = {1: "Ingen markering", 2: "När det inträffar", 3: "Diskret", 4: "Fler", 5: "Allt"};
  const LEVEL_HINTS = {
    1: "Bara tidtabellens tider, inget rött och ingen markering",
    2: "Raden lyser kort och får Nyss när något händer",
    3: "Dessutom förseningen i liten röd text från 5 min",
    4: "Röd bricka från 3 min, den nya tiden och för tidig avgång för persontåg",
    5: "Allt från 1 min, även för tidig ankomst och vid tågen på kartan",
  };
  const drift = root.TrainMeetDriftModel;
  let recent = drift?.changeTracker(), shownLevel = null;
  let liveTable = null, tableTimer = null, tableVersion = 0;
  function ownLevel() { try { return localStorage.getItem(DEVIATION_KEY) || ""; } catch { return ""; } }
  function meetLevel() { return drift?.deviationLevel({display: {deviation_level: liveTable?.deviation_level}}) ?? 2; }
  function renderLevelChoice() {
    const select = document.querySelector("#box-deviation-level");
    if (!select || !drift) return;
    // Not while the list is open or being chosen in; the note still follows.
    if (select !== document.activeElement) {
      const named = (level) => `${level} · ${t(LEVEL_NAMES[level])}`;
      select.replaceChildren(new Option(t("Som träffen: {level}", {level: named(meetLevel())}), ""),
        ...drift.DEVIATION_LEVELS.map((level) => new Option(named(level), String(level))));
      select.value = ownLevel();
    }
    document.querySelector("#box-deviation-note").textContent = t(LEVEL_HINTS[Number(select.value) || meetLevel()]);
  }
  function renderLiveTimetable() {
    const box = boxes.values().next().value;
    renderLevelChoice();
    if (!box || !drift) return;
    const container = box.card.querySelector(".box-timetable");
    if (!liveTable?.station) { container.replaceChildren(); return; }
    const level = drift.deviationLevel({display: {deviation_level: liveTable.deviation_level}}, ownLevel());
    // A new level is not a change on the rows: nothing lights up for it.
    if (level !== shownLevel) { recent = drift.changeTracker(); shownLevel = level; }
    const now = performance.now();
    const table = document.createElement("table");
    const caption = document.createElement("caption"); caption.textContent = t("Tidtabell · {station}", {station: liveTable.station.name});
    const head = document.createElement("thead"), heading = document.createElement("tr");
    for (const label of ["Tåg", "Tid", "Från / till"]) { const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = t(label); heading.append(cell); }
    head.append(heading);
    const body = document.createElement("tbody");
    const words = {departure: ["Avg {time}", "Till {station}"], arrival: ["Ank {time}", "Från {station}"]};
    for (const row of liveTable.rows) {
      const shown = timetableRow(row, level, drift);
      const changedAt = recent.note(`${row.movement_id}:${row.kind}`, shown.signature, now);
      const fresh = shown.view.flash && changedAt !== null && now - changedAt < RECENT_MS;
      const line = document.createElement("tr");
      line.classList.toggle("is-done", shown.done);
      line.classList.toggle("is-early", shown.early);
      if (fresh) { line.classList.add("is-updated"); line.style.animationDelay = `${-(now - changedAt)}ms`; }
      const number = document.createElement("th"); number.scope = "row"; number.textContent = row.train_number;
      const [timeWord, routeWord] = words[row.kind] || ["{time}", "{station}"];
      const time = document.createElement("td");
      if (shown.shown) {
        const was = document.createElement("s"); was.className = "tm-was"; was.textContent = row.time;
        const next = document.createElement("b"); next.className = "tm-new"; next.textContent = t(timeWord, {time: shown.shown});
        time.append(was, next);
      } else time.textContent = t(timeWord, {time: row.time});
      const mark = shown.view.mark;
      if (mark) {
        const badge = document.createElement("span");
        badge.className = mark.tone === "early" ? "tm-early" : mark.style === "text" ? "tm-delay tm-delay--text" : "tm-delay";
        badge.textContent = mark.text;
        const label = mark.tone === "early" ? t("{minutes} min för tidigt", {minutes: mark.minutes})
          : t("{minutes} min sen", {minutes: mark.minutes}) + (shown.view.estimated ? ` · ${t("beräknad")}` : "");
        badge.title = label; badge.setAttribute("aria-label", label);
        time.append(badge);
      }
      if (fresh) { const note = document.createElement("span"); note.className = "tm-recent"; note.textContent = t("Nyss"); time.append(note); }
      const route = document.createElement("td"); route.textContent = t(routeWord, {station: row.station?.name || ""});
      line.append(number, time, route);
      body.append(line);
    }
    if (!liveTable.rows.length) {
      const line = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = 3; cell.textContent = t("Inga tåg vid stationen i dag."); line.append(cell); body.append(line);
    }
    table.append(caption, head, body);
    container.replaceChildren(table);
  }
  async function pollTimetable() {
    clearTimeout(tableTimer); tableTimer = null;
    const version = ++tableVersion;
    try {
      if (identity && !document.hidden) {
        const response = await fetch("/v1/tmbox/terminal/timetable", {credentials:"omit", cache:"no-store",
          headers:{Authorization:`Bearer ${identity.access_token}`}, signal:AbortSignal.timeout(5000)});
        if (response.ok && version === tableVersion) { liveTable = await response.json(); renderLiveTimetable(); }
      }
    } catch {}
    finally { if (version === tableVersion) tableTimer = setTimeout(pollTimetable, TIMETABLE_MS); }
  }
  if (live) {
    document.querySelector("#start-client").addEventListener("click",()=>{try { localStorage.removeItem(SAVED); } catch {} startLive();});
    document.addEventListener("visibilitychange",()=>{if(document.hidden){++pollVersion;lost();} else pollLive();});
    addEventListener("pageshow", event => { if (event.persisted) pollLive(); });
    addEventListener("online", () => pollLive());
    startLive().then(() => { pollLive(); pollTimetable(); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) pollTimetable(); });
    document.querySelector("#box-deviation-level")?.addEventListener("change", (event) => {
      try { if (event.target.value) localStorage.setItem(DEVIATION_KEY, event.target.value); else localStorage.removeItem(DEVIATION_KEY); } catch {}
      renderLiveTimetable();
    });
  }
  loadChrome();
  // A language chosen in another tab: draw the page's own words again.
  i18n?.subscribe(() => {
    helpWords();
    for (const model of boxes.values()) model.timetableSignature = null;
    if (lastState) update(lastState);
    if (live) renderLiveTimetable();
    const rate = document.querySelector("#connection-rate");
    if (rate) rate.textContent = ` · ${t("uppdateras {n} gånger i sekunden", {n: 1000 / POLL_MS})}`;
  });
  function resetButtons() {
    for (const id of ["reset-all", "reset-clearance", "reset-direct"]) document.getElementById(id).disabled = !connected || resetting;
    const open = document.querySelector("#placement-open");
    if (open) open.disabled = !connected || resetting || !placement;
  }
  if (placementDialog && !live) {
    const form = document.querySelector("#placement-form");
    const close = () => { if (!resetting) placementDialog.close(); };
    document.querySelector("#placement-open").addEventListener("click", () => {
      if (!placement || resetting) return;
      placementDraft = structuredClone(placement);
      const fields = document.querySelector("#placement-fields"); fields.replaceChildren();
      document.querySelector("#placement-error").textContent = "";
      for (const station of placementDraft.stations) {
        const group = document.createElement("fieldset"), legend = document.createElement("legend");
        legend.textContent = station.name; group.append(legend);
        for (const connection of station.connections) {
          const label = document.createElement("label"), title = document.createElement("span"), select = document.createElement("select");
          title.textContent = connection.other_station_name;
          select.dataset.station = station.station_id; select.dataset.connection = connection.connection_id;
          for (const [value, name] of [["default", t(connection.default_side === "left" ? "Standard (vänster)" : "Standard (höger)")], ["left", t("Vänster")], ["right", t("Höger")]]) {
            const option = document.createElement("option"); option.value = value; option.textContent = name; select.append(option);
          }
          select.value = connection.overridden ? connection.side : "default";
          label.append(title, select); group.append(label);
        }
        fields.append(group);
      }
      placementDialog.showModal();
    });
    document.querySelector("#placement-close").addEventListener("click", close);
    document.querySelector("#placement-cancel").addEventListener("click", close);
    placementDialog.addEventListener("cancel", event => { if (resetting) event.preventDefault(); });
    placementDialog.addEventListener("close", () => document.querySelector("#placement-open").focus());
    document.querySelector("#placement-default").addEventListener("click", () => { for (const select of form.querySelectorAll("select")) select.value = "default"; });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (!connected || resetting || !placementDraft) return;
      const body = {epoch: placementDraft.epoch, revision: placementDraft.revision,
        stations: Object.fromEntries(placementDraft.stations.map(station => [station.station_id, {}]))};
      for (const select of form.querySelectorAll("select")) if (select.value !== "default") body.stations[select.dataset.station][select.dataset.connection] = select.value;
      resetting = true; resetButtons();
      for (const control of form.querySelectorAll("button, select")) control.disabled = true;
      const error = document.querySelector("#placement-error"); error.textContent = "";
      try {
        const response = await fetch("./api/display-placement", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body), signal: AbortSignal.timeout(5000)});
        const result = await response.json();
        if (!response.ok) throw new Error(result.message || t("Placeringen kunde inte sparas."));
        update(result); placementDialog.close();
        document.querySelector("#placement-status").textContent = t("Testplaceringen är sparad. Träffens inställningar är oförändrade.");
      } catch (failure) { error.textContent = t(failure.message); }
      finally {
        resetting = false; resetButtons();
        for (const control of form.querySelectorAll("button, select")) control.disabled = false;
        for (const model of boxes.values()) render(model);
        if (!placementDialog.open) document.querySelector("#placement-open").focus();
      }
    });
  }
  async function resetLab(path, body, success) {
    if (!connected || resetting) return;
    const status = document.querySelector("#reset-status");
    resetting = true; resetButtons();
    for (const model of boxes.values()) render(model);
    status.classList.remove("error"); status.textContent = t("Nollställer provbänken…");
    try {
      const response = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body), signal:AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error();
      update(await response.json());
      status.textContent = t(success);
    } catch {
      status.classList.add("error"); status.textContent = t("Nollställningen kunde inte bekräftas. Kontrollera displayerna innan du försöker igen.");
    } finally {
      resetting = false; resetButtons();
      for (const model of boxes.values()) render(model);
    }
  }
  document.querySelector("#reset-all").addEventListener("click", () => resetLab("./api/reset-devices", {}, "Alla enheter är nollställda. Inga pågående tågrörelser."));
  for (const mode of ["clearance", "direct"]) document.querySelector(`#reset-${mode}`).addEventListener("click", () => resetLab("./api/reset", {mode}, "Nytt test startat. Alla enheter är nollställda."));
})(typeof globalThis !== "undefined" ? globalThis : this);
