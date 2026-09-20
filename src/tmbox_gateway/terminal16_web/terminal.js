/* Generic terminal adapter. No train states, routes, destinations or action
   names live here. All ordinary display text/key labels arrive from Server. */
(function (root) {
  "use strict";
  class EntryBuffer {
    constructor() { this.digits = ""; this.context = null; }
    clear() { this.digits = ""; this.context = null; }
    sync(frame) { if (this.context && this.context !== frame.entry.context) this.clear(); }
    press(key, frame) {
      this.sync(frame);
      const entry = frame.entry;
      if (/^[0-9]$/.test(key)) {
        if (this.digits.length < entry.max_length) this.digits += key;
        this.context = entry.context;
        return {local: true};
      }
      if (this.digits) {
        if (key === entry.cancel) { this.clear(); return {local: true}; }
        if (key === entry.erase) { this.digits = this.digits.slice(0, -1); return {local: true}; }
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
  let connected = false, resetting = false, text = {};
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
    card.innerHTML = '<div class="box-heading"><h2></h2><span class="box-code"></span></div><div class="box-status"></div><div class="tmbox-case"><div class="lcd-frame"><div class="lcd" role="img"></div></div><div class="keypad"></div></div><div class="key-hints"></div><p class="box-message" role="status"></p>';
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
  function render(model) {
    const {card, frame, entry} = model;
    const lines = entry.lines(frame);
    drawLCD(card.querySelector(".lcd"), lines);
    card.querySelector(".box-status").textContent = frame.status;
    const hints = card.querySelector(".key-hints"); hints.replaceChildren();
    const labels = entry.digits ? frame.entry.labels : Object.fromEntries(Object.entries(frame.keys).map(([key, info]) => [key, info.label]));
    for (const [key, label] of Object.entries(labels)) { const hint = document.createElement("span"); const strong = document.createElement("b"); strong.textContent = key; hint.append(strong, label); hints.append(hint); }
    for (const button of card.querySelectorAll(".key")) {
      const key = button.dataset.key;
      button.disabled = !connected || resetting || model.busy || performance.now() < model.until || (!/^[0-9]$/.test(key) && !(key in labels));
      button.setAttribute("aria-label", labels[key] ? `${key} · ${labels[key]}` : key);
    }
    const status = card.querySelector(".box-message");
    status.textContent = model.uiMessage || (entry.digits ? text.entry : "");
    status.classList.toggle("error", model.uiError);
  }
  function apply(frame) {
    const model = boxes.get(frame.device_id) || makeBox(frame);
    if (model.frame.entry.context === frame.entry.context &&
        (frame.revision < model.frame.revision || (frame.revision === model.frame.revision && frame.view_revision < model.frame.view_revision))) return;
    if (model.frame.entry.context !== frame.entry.context) message(model, "");
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
    const context = model.frame.entry.context;
    model.busy = true; message(model, text.sending); render(model);
    try {
      const response = await fetch("./api/key", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body), signal:AbortSignal.timeout(5000)});
      const result = await response.json();
      if (model.frame.entry.context !== context) return;
      if (result.status === "accepted") model.entry.clear();
      if (result.frame) apply(result.frame);
      message(model, result.message, !response.ok);
    } catch { message(model, "Serversvar saknas. Kontrollera det aktuella läget innan nytt försök.", true); }
    finally {
      model.busy = false; model.until = performance.now() + model.frame.input_guard_ms;
      render(model); setTimeout(() => render(model), model.frame.input_guard_ms + 10);
    }
  }
  function update(state) {
    connected = true; text = state.text;
    resetButtons();
    document.querySelector("h1").textContent = text.title;
    document.querySelector("#subtitle").textContent = text.subtitle;
    document.querySelector("#connection").textContent = text.ready;
    document.querySelector("#session-info").textContent = text.session || "";
    for (const frame of state.frames) apply(frame);
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
  const events = new EventSource("./events");
  events.onmessage = event => update(JSON.parse(event.data));
  events.onerror = () => { connected = false; resetButtons(); document.querySelector("#connection").textContent = text.offline || "Testservern är inte ansluten."; for (const model of boxes.values()) render(model); };
  function resetButtons() {
    for (const id of ["reset-all", "reset-clearance", "reset-direct"]) document.getElementById(id).disabled = !connected || resetting;
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
