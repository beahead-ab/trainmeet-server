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
  if (typeof module !== "undefined") module.exports = {EntryBuffer};
  if (typeof document === "undefined") return;

  const boxes = new Map();
  const live = document.body.dataset.terminal === "live";
  let identity = null, pollVersion = 0;
  let connected = false, resetting = false, text = {};
  let placement = null, placementDraft = null;
  const placementDialog = document.querySelector("#placement-dialog");
  const keyOrder = "123A456B789C*0#D";
  function drawLCD(lcd, lines) {
    lcd.replaceChildren();
    lcd.setAttribute("aria-label", lines.join(". "));
    for (const line of lines) {
      const row = document.createElement("div"); row.className = "lcd-row";
      for (const character of line.normalize("NFC")) { const cell = document.createElement("span"); cell.className = "lcd-cell"; cell.textContent = character; row.append(cell); }
      lcd.append(row);
    }
  }
  function makeBox(frame) {
    const card = document.createElement("article"); card.className = "box"; card.tabIndex = 0; card.dataset.device = frame.device_id;
    card.innerHTML = '<div class="box-heading"><h2></h2><span class="box-code"></span></div><div class="box-status"></div><div class="box-queue" role="status"></div><div class="tmbox-case"><div class="lcd-frame"><div class="lcd" role="img"></div></div><div class="keypad"></div></div><div class="key-hints"></div><p class="box-message" role="status"></p><div class="box-timetable"></div>';
    const model = {card, frame, entry: new EntryBuffer(), busy: false, until: 0, uiMessage: "", uiError: false};
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
    const caption = document.createElement("caption"); caption.textContent = timetable.title;
    const head = document.createElement("thead"), heading = document.createElement("tr");
    for (const label of timetable.columns) {
      const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = label; heading.append(cell);
    }
    head.append(heading);
    const body = document.createElement("tbody");
    for (const row of timetable.rows) {
      const line = document.createElement("tr");
      for (const key of ["train_number", "time", "route"]) {
        const cell = document.createElement(key === "train_number" ? "th" : "td");
        if (key === "train_number") cell.scope = "row";
        cell.textContent = row[key]; line.append(cell);
      }
      body.append(line);
    }
    if (!timetable.rows.length) {
      const line = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = timetable.columns.length; cell.textContent = timetable.empty; line.append(cell); body.append(line);
    }
    table.append(caption, head, body);
    model.card.querySelector(".box-timetable").replaceChildren(table);
  }
  function render(model) {
    const {card, frame, entry} = model;
    const lines = entry.lines(frame);
    drawLCD(card.querySelector(".lcd"), lines);
    card.querySelector("h2").textContent = frame.station || "Väntar på station";
    card.querySelector(".box-code").textContent = live ? identity?.device_code || frame.device_id : frame.device_id;
    card.querySelector(".box-status").textContent = frame.status;
    const queue = card.querySelector(".box-queue");
    queue.textContent = frame.requests?.label || "";
    queue.classList.toggle("pending", !!frame.requests?.count);
    const hints = card.querySelector(".key-hints"); hints.replaceChildren();
    const labels = entry.digits ? frame.entry.labels : Object.fromEntries(Object.entries(frame.keys).map(([key, info]) => [key, info.label]));
    for (const [key, label] of Object.entries(labels)) { const hint = document.createElement("span"); const strong = document.createElement("b"); strong.textContent = key; hint.append(strong, label); hints.append(hint); }
    for (const button of card.querySelectorAll(".key")) {
      const key = button.dataset.key;
      button.disabled = !connected || resetting || model.busy || performance.now() < model.until || (/^[0-9]$/.test(key) ? !frame.entry : !(key in labels));
      button.setAttribute("aria-label", labels[key] ? `${key} · ${labels[key]}` : key);
    }
    const status = card.querySelector(".box-message");
    status.textContent = model.uiMessage || (entry.digits ? text.entry : "");
    status.classList.toggle("error", model.uiError);
  }
  function apply(frame) {
    const model = boxes.get(frame.device_id) || makeBox(frame);
    if (model.frame.entry?.context === frame.entry?.context &&
        (frame.revision < model.frame.revision || (frame.revision === model.frame.revision && frame.view_revision < model.frame.view_revision))) return;
    if (model.frame.entry?.context !== frame.entry?.context) message(model, "");
    // A newly offered primary action must be readable before accepting a key.
    // This is a generic display/input guard, not client-side traffic logic.
    if (!model.entry.digits && model.frame.keys['#']?.label !== frame.keys['#']?.label) {
      model.until = Math.max(model.until, performance.now() + frame.input_guard_ms);
      setTimeout(() => render(model), frame.input_guard_ms + 10);
    }
    model.frame = frame; render(model);
  }
  function message(model, value, error=false) {
    model.uiMessage = value || ""; model.uiError = error;
    const element = model.card.querySelector(".box-message"); element.textContent = value || ""; element.classList.toggle("error", error);
  }
  async function press(model, key) {
    if (!connected || resetting || model.busy || performance.now() < model.until) return;
    const entry = model.entry.press(key, model.frame);
    if (entry.local) { message(model, ""); render(model); return; }
    if (!entry.train_number && !(key in model.frame.keys)) return;
    const body = {device_id: model.frame.device_id, command_id: crypto.randomUUID(), view_token: model.frame.view_token, key};
    if (entry.train_number) Object.assign(body, {train_number: entry.train_number, entry_context: entry.entry_context});
    const context = model.frame.entry?.context;
    ++pollVersion;
    model.busy = true; message(model, text.sending); render(model);
    try {
      if (live) delete body.device_id;
      const response = await fetch(live ? "/v1/tmbox/terminal" : "./api/key", {method:"POST", credentials: live ? "omit" : "same-origin", headers:{"Content-Type":"application/json", ...(live ? {Authorization: `Bearer ${identity.access_token}`} : {})}, body:JSON.stringify(body), signal:AbortSignal.timeout(5000)});
      const result = await response.json();
      if (model.frame.entry?.context !== context) return;
      if (result.status === "accepted" || result.status === "duplicate") model.entry.clear();
      if (result.frame) apply(result.frame);
      message(model, result.message, !response.ok);
    } catch { message(model, "Serversvar saknas. Kontrollera det aktuella läget innan nytt försök.", true); }
    finally {
      model.busy = false; model.until = performance.now() + model.frame.input_guard_ms;
      render(model); setTimeout(() => render(model), model.frame.input_guard_ms + 10);
    }
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
    document.querySelector("#box-state-text").textContent = assigned ? `Tilldelad station: ${frame.station}` : "Väntar på station";
    const code = document.querySelector("#box-identity");
    code.hidden = false; code.classList.toggle("is-big", !assigned);
    document.querySelector("#box-identity-code").textContent = identity.device_code || "";
    document.querySelector("#box-note").textContent = assigned
      ? "Boxen arbetar mot träffens riktiga trafik. Trafikledningen kan flytta den till en annan station eller ta bort den under Inställningar → TMBoxar. Språket byter du med * på boxen."
      : "Visa koden för trafikledningen, som tilldelar din station under Inställningar → TMBoxar. Boxen börjar arbeta direkt när den är tilldelad – du behöver inte ladda om sidan.";
  }
  async function loadLiveChrome() {
    const help = document.querySelector("#key-help");
    if (help && matchMedia("(min-width: 901px)").matches) help.open = true;
    try {
      const response = await fetch("/v1/workspaces", {credentials: "omit", cache: "no-store", signal: AbortSignal.timeout(5000)});
      const meet = response.ok ? (await response.json()).selected_meet : null;
      if (meet?.name) document.querySelector("#meet-name").textContent = meet.name;
      const region = document.querySelector("#meet-region");
      if (meet?.operating_region) {
        region.textContent = meet.operating_region.toUpperCase();
        region.className = `tm-badge tm-badge--${meet.operating_region === "us" ? "us" : "eu"}`;
        region.hidden = false;
      }
    } catch {}
    try {
      const response = await fetch("/healthz", {credentials: "omit", cache: "no-store", signal: AbortSignal.timeout(5000)});
      const health = response.ok ? await response.json() : null;
      if (health?.version) document.querySelector("#server-version").textContent = `TrainMeet Server ${health.version}`;
    } catch {}
  }
  function update(state) {
    connected = true; text = state.text;
    if (state.placement) placement = state.placement;
    resetButtons();
    document.querySelector("h1").textContent = text.title;
    document.querySelector("#subtitle").textContent = text.subtitle;
    document.querySelector("#connection").textContent = text.ready;
    document.querySelector("#session-info").textContent = text.session || "";
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
      const note = document.createElement("p"); note.textContent = `${sample.lcd.glyphs.length}/8 egna LCD-tecken i denna bild`;
      bezel.append(lcd); card.append(title, bezel, note); samples.append(card);
    }
    document.querySelector("#mode").textContent = state.mode === "direct" ? "Testläge: direkttrafik, fortfarande med reservation och faktisk avgång." : "Testläge: mottagarens klartecken krävs före avgång.";
    document.querySelector("#event-count").textContent = `${state.audit.length} trafikåtgärder`;
    const events = document.querySelector("#events"); events.replaceChildren();
    for (const event of state.audit) { const li = document.createElement("li"); li.textContent = `${event.revision}. ${event.station_id.toUpperCase()} · ${event.action} · ${event.connection_id}`; events.append(li); }
  }
  const events = live ? {} : new EventSource("./events");
  events.onmessage = event => update(JSON.parse(event.data));
  events.onerror = () => { connected = false; resetButtons(); document.querySelector("#connection").textContent = text.offline || (live ? "Servern är inte ansluten." : "Testservern är inte ansluten."); showConnection(false); for (const model of boxes.values()) render(model); };
  async function startLive() {
    document.querySelector("#start-client").hidden = true;
    try {
      let saved; try { saved = JSON.parse(localStorage.getItem("trainmeet.browser-tmbox")); } catch {}
      const response = await fetch(saved?.access_token ? "/v1/browser-clients/self" : "/v1/browser-clients", {
        method: saved?.access_token ? "GET" : "POST", credentials: "omit", cache: "no-store",
        headers: saved?.access_token ? {Authorization: `Bearer ${saved.access_token}`} : {"Content-Type":"application/json"},
        body: saved?.access_token ? undefined : JSON.stringify({workspace:"tmbox"}), signal:AbortSignal.timeout(5000)});
      const result = await response.json();
      if (!response.ok || result.workspace !== "tmbox") throw Error(result.message || "Klienten kan inte anslutas.");
      identity = {...result,access_token: saved?.access_token || result.access_token};
      localStorage.setItem("trainmeet.browser-tmbox", JSON.stringify(identity));
    } catch (error) {
      document.querySelector("#connection").textContent = error.message;
      document.querySelector("#start-client").hidden = false;
    }
  }
  async function pollLive() {
    if (identity && !document.hidden && ![...boxes.values()].some(m=>m.busy)) {
      const version = pollVersion;
      try {
        const response = await fetch("/v1/tmbox/terminal", {credentials:"omit",cache:"no-store",headers:{Authorization:`Bearer ${identity.access_token}`},signal:AbortSignal.timeout(5000)});
        const frame = await response.json();
        if (!response.ok) throw Error(frame.message || "Anslutningen bröts.");
        if (version === pollVersion) update({frames:[frame],audit:[],text:{title:"TMBox",subtitle:location.host,
          session:`Enhetskod: ${identity.device_code} · Station tilldelas av administratören`,ready:"Ansluten till servern",offline:"Servern är inte ansluten.",entry:"Siffrorna stannar här tills du trycker #.",sending:"Inväntar servern…"}});
      } catch { events.onerror(); }
    }
    setTimeout(pollLive,1000);
  }
  if (live) {
    document.querySelector("#start-client").addEventListener("click",()=>{localStorage.removeItem("trainmeet.browser-tmbox");startLive();});
    document.addEventListener("visibilitychange",()=>{if(document.hidden){++pollVersion;for(const model of boxes.values())model.entry.clear();events.onerror();}});
    loadLiveChrome();
    startLive().then(pollLive);
  }
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
          for (const [value, name] of [["default", `Standard (${connection.default_side === "left" ? "vänster" : "höger"})`], ["left", "Vänster"], ["right", "Höger"]]) {
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
        if (!response.ok) throw new Error(result.message || "Placeringen kunde inte sparas.");
        update(result); placementDialog.close();
        document.querySelector("#placement-status").textContent = "Testplaceringen är sparad. Träffens inställningar är oförändrade.";
      } catch (failure) { error.textContent = failure.message; }
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
    status.classList.remove("error"); status.textContent = "Nollställer provbänken…";
    try {
      const response = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body), signal:AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error();
      update(await response.json());
      status.textContent = success;
    } catch {
      status.classList.add("error"); status.textContent = "Nollställningen kunde inte bekräftas. Kontrollera displayerna innan du försöker igen.";
    } finally {
      resetting = false; resetButtons();
      for (const model of boxes.values()) render(model);
    }
  }
  document.querySelector("#reset-all").addEventListener("click", () => resetLab("./api/reset-devices", {}, "Alla enheter är nollställda. Inga pågående tågrörelser."));
  for (const mode of ["clearance", "direct"]) document.querySelector(`#reset-${mode}`).addEventListener("click", () => resetLab("./api/reset", {mode}, "Nytt test startat. Alla enheter är nollställda."));
})(typeof globalThis !== "undefined" ? globalThis : this);
