/* Inställningar · Kontrollrummet.
 *
 * Ett avsnitt i taget med en sidomeny, och varje panel som går att ändra är ett
 * eget formulär med Avbryt och Spara som är släckta tills något är ändrat.
 * Fälten, rutterna och sparandet ligger kvar i app.js; den här filen äger bara
 * det som rör hur sidan beter sig: menyn, "är något ändrat?", stilvalen och
 * QR-förhandsvisningen.
 *
 * Ett formulär är "ändrat" när något fält skiljer sig från sina värden vid
 * senaste läsning från servern (rebase) eller senaste lyckade sparande. Ändrar
 * man tillbaka till de ursprungliga värdena är inget ändrat och knapparna
 * släcks igen.
 */
(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const i18n = () => globalThis.TrainMeetI18n;
  const t = (source, values) => i18n().t(source, values);
  const SVGNS = "http://www.w3.org/2000/svg";

  const SECTIONS = ["traff", "skarmar", "wifi", "obemannade", "server", "kod", "anvandare", "uppdatering", "sprak", "farozon"];
  const ALIASES = { anslutning: "kod", fynd: "traff", klocka: "skarmar", cloud: "traff", meet: "traff" };

  // ── Avsnitt och meny ───────────────────────────────────────────────────
  function currentSection() {
    const raw = decodeURIComponent(location.hash.slice(1));
    const id = ALIASES[raw] || raw;
    return SECTIONS.includes(id) ? id : "traff";
  }

  function show(id = currentSection()) {
    for (const section of $$(".kr-setsec")) section.hidden = section.dataset.section !== id;
    for (const link of $$(".kr-nav")) {
      if (link.dataset.section === id) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    }
    if (location.hash === "#fynd") $("#published-findings")?.scrollIntoView({ block: "start" });
    else window.scrollTo({ top: 0, behavior: "auto" });
  }

  function bindNav() {
    const nav = $("#settings-nav");
    if (!nav) return;
    addEventListener("hashchange", () => { if (document.body.dataset.mode === "installningar") show(); });
    const search = $("#settings-search");
    const filter = () => {
      const needle = search.value.trim().toLocaleLowerCase("sv");
      const visible = new Set();
      for (const link of $$(".kr-nav", nav)) {
        const match = !needle || link.textContent.toLocaleLowerCase("sv").includes(needle);
        link.hidden = !match;
        if (match && link.dataset.group) visible.add(link.dataset.group);
      }
      for (const group of $$(".kr-grp", nav)) group.hidden = Boolean(needle) && !visible.has(group.dataset.group);
    };
    search.addEventListener("input", filter);
    search.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      const first = $$(".kr-nav", nav).find((link) => !link.hidden);
      if (first) { event.preventDefault(); location.hash = first.hash; search.value = ""; filter(); }
    });
  }

  // ── Ändrat eller inte ──────────────────────────────────────────────────
  const baselines = new WeakMap();
  const controlsOf = (form) => $$("input, select, textarea", form).filter((control) => control.type !== "hidden");
  const snapshot = (form) => controlsOf(form).map((control) => [control, control.value, control.checked]);

  function changedControls(form) {
    return (baselines.get(form) || []).filter(([control, value, checked]) => control.value !== value || control.checked !== checked).map(([control]) => control);
  }

  function labelOf(control) {
    const own = control.dataset.changeLabel
      || control.getAttribute("aria-label")
      || control.labels?.[0]?.textContent
      || "";
    return t(own.trim());
  }

  function refresh(form) {
    if (!form || !form.isConnected) return;
    const changed = changedControls(form);
    const dirty = changed.length > 0;
    if (dirty) form.dataset.dirty = "true"; else delete form.dataset.dirty;
    const bar = $("[data-savebar]", form);
    if (!bar) return;
    const busy = form.dataset.busy === "true";
    const submit = $("[data-save-submit]", bar), cancel = $("[data-save-cancel]", bar), state = $("[data-save-state]", bar);
    if (submit) submit.disabled = !dirty || busy;
    if (cancel) cancel.disabled = !dirty || busy;
    if (!state) return;
    const justSaved = Number(form.dataset.savedUntil || 0) > Date.now();
    if (dirty) {
      const labels = [...new Set(changed.map(labelOf).filter(Boolean))];
      state.textContent = labels.length ? t("Ändrat: {fields}", { fields: labels.join(", ") }) : t("Osparade ändringar");
      state.dataset.dirty = "true"; delete state.dataset.saved;
    } else if (justSaved) {
      state.textContent = t("Sparat"); state.dataset.saved = "true"; delete state.dataset.dirty;
    } else {
      state.textContent = t("Inget ändrat"); delete state.dataset.dirty; delete state.dataset.saved;
    }
  }

  /** Fältens nuvarande värden är de sparade. Anropas när sidan har fyllt i fälten från servern. */
  function rebase(form) {
    if (!form) return;
    baselines.set(form, snapshot(form));
    form.dispatchEvent(new CustomEvent("kr:rebased"));
    refresh(form);
  }

  function saved(form) {
    if (!form) return;
    form.dataset.savedUntil = String(Date.now() + 3500);
    rebase(form);
    clearTimeout(form.__savedTimer);
    form.__savedTimer = setTimeout(() => refresh(form), 3600);
  }

  function cancel(form) {
    for (const [control, value, checked] of baselines.get(form) || []) {
      control.value = value; control.checked = checked;
      control.dispatchEvent(new Event("change", { bubbles: true }));
    }
    for (const message of $$(".form-message", form)) { message.textContent = ""; message.className = "form-message"; delete message.dataset.tmText; }
    form.dispatchEvent(new CustomEvent("kr:rebased"));
    refresh(form);
  }

  function bindForm(form) {
    if (form.dataset.krBound) return;
    form.dataset.krBound = "true";
    baselines.set(form, snapshot(form));
    const update = () => refresh(form);
    form.addEventListener("input", update);
    form.addEventListener("change", update);
    $("[data-save-cancel]", form)?.addEventListener("click", () => cancel(form));
    refresh(form);
  }

  // ── Tillfälliga val: klockstil och språk ───────────────────────────────
  function svgEl(tag, attrs = {}) {
    const node = document.createElementNS(SVGNS, tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, String(value));
    return node;
  }

  /** En liten förhandsbild av klockstilen. Ritas lokalt, ingen bild följer med. */
  function clockPreview(style) {
    const box = document.createElement("span");
    box.className = "kr-prev";
    if (style === "digital") {
      const time = document.createElement("span");
      time.className = "kr-prev__time"; time.textContent = "05:12";
      box.append(time);
      return box;
    }
    const light = style === "swiss" || style === "stationsur";
    const ink = light ? "#0b0b0b" : "#f1f3f6";
    const svg = svgEl("svg", { width: 52, height: 52, viewBox: "0 0 100 100", "aria-hidden": "true" });
    svg.append(svgEl("circle", { cx: 50, cy: 50, r: 46, fill: light ? "#f1f3f6" : "#1b1f27", stroke: light ? "none" : "#5c6370", "stroke-width": 3 }));
    svg.append(svgEl("circle", { cx: 50, cy: 50, r: 40, fill: "none", stroke: ink, "stroke-width": light ? 8 : 7, "stroke-dasharray": light ? "4 16.9" : "3 16.9", "stroke-dashoffset": light ? 2 : 1.5 }));
    svg.append(svgEl("rect", { x: 47, y: 24, width: 6, height: 30, rx: 1, fill: ink, transform: "rotate(156 50 50)" }));
    svg.append(svgEl("rect", { x: 48, y: 14, width: 4, height: 40, rx: 1, fill: ink, transform: "rotate(-30 50 50)" }));
    if (style === "swiss") {
      svg.append(svgEl("rect", { x: 49, y: 12, width: 2, height: 46, fill: "#d8261b", transform: "rotate(72 50 50)" }));
      svg.append(svgEl("circle", { cx: 50, cy: 18, r: 5, fill: "#d8261b", transform: "rotate(72 50 50)" }));
    }
    box.append(svg);
    return box;
  }

  /** Radiogrupp av knappar som styr ett dolt <select>. Select är källan; knapparna visar den. */
  function bindChoice({ select, host, build, after }) {
    const sync = () => {
      for (const button of $$("[role=radio]", host)) {
        const on = button.dataset.value === select.value;
        button.setAttribute("aria-checked", String(on));
        button.tabIndex = on ? 0 : -1;
      }
      after?.();
    };
    const rebuild = () => {
      host.replaceChildren();
      for (const option of [...select.options]) {
        const button = build(option);
        button.type = "button";
        button.setAttribute("role", "radio");
        button.dataset.value = option.value;
        button.addEventListener("click", () => {
          if (select.value === option.value) return;
          select.value = option.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        });
        host.append(button);
      }
      sync();
    };
    host.addEventListener("keydown", (event) => {
      const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
      if (!(event.key in keys)) return;
      const buttons = $$("[role=radio]", host);
      const index = buttons.findIndex((button) => button.dataset.value === select.value);
      const next = buttons[(index + keys[event.key] + buttons.length) % buttons.length];
      if (!next) return;
      event.preventDefault();
      select.value = next.dataset.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      next.focus();
    });
    select.addEventListener("change", sync);
    select.closest("form")?.addEventListener("kr:rebased", sync);
    new MutationObserver(rebuild).observe(select, { childList: true });
    rebuild();
    return { sync, rebuild };
  }

  function bindClockStyle() {
    const select = $("#meet-clock-style"), host = $("#clock-style-tiles");
    if (!select || !host) return;
    bindChoice({
      select, host,
      build: (option) => {
        const tile = document.createElement("button");
        tile.className = "kr-tile";
        const name = document.createElement("b");
        name.textContent = option.textContent;
        tile.append(clockPreview(option.value), name);
        return tile;
      },
    });
  }

  function bindLanguage() {
    const form = $("#language-form"), select = $("#language-choice"), host = $("#language-tiles");
    if (!form || !select || !host || !i18n()) return;
    const fill = () => {
      select.replaceChildren(...i18n().languages.map(([id, label]) => new Option(label, id)));
      select.value = i18n().getLanguage();
    };
    fill();
    rebase(form);
    const hint = (id) => (id === i18n().getLanguage() ? t("vald") : id === "en" ? t("US-träffar startar här") : "");
    const choice = bindChoice({
      select, host,
      build: (option) => {
        const tile = document.createElement("button");
        tile.className = "kr-lang";
        const code = document.createElement("span");
        code.className = "kr-lang__code"; code.textContent = option.value;
        const name = document.createElement("span");
        name.className = "kr-lang__name";
        const label = document.createElement("b"); label.textContent = option.textContent;
        const note = document.createElement("span"); note.textContent = hint(option.value) || " ";
        name.append(label, note);
        tile.append(code, name);
        return tile;
      },
    });
    bindForm(form);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (select.value === i18n().getLanguage()) return;
      i18n().setLanguage(select.value);
    });
    // Språket byts här eller på annat håll: valet följer det som gäller nu.
    i18n().subscribe(() => { fill(); choice.rebuild(); saved(form); });
  }

  // ── QR-koderna som skärmarna visar ─────────────────────────────────────
  function qrItem(index, caption, payload) {
    const item = document.createElement("div");
    item.className = "kr-qr";
    const cap = document.createElement("div");
    cap.className = "kr-qr__cap";
    const number = document.createElement("b"); number.textContent = index;
    const text = document.createElement("span"); text.textContent = caption;
    cap.append(number, text);
    const code = document.createElement("div");
    code.className = "kr-qr__code";
    code.innerHTML = globalThis.TrainMeetServerUI.qrSVG(payload);
    item.append(cap, code);
    return item;
  }

  function renderQr(connection) {
    const host = $("#settings-qrs");
    if (!host || typeof globalThis.qrcode !== "function" || !globalThis.TrainMeetServerUI?.qrSVG) return;
    const link = new URL("/", location.href).href;
    const wifi = globalThis.TrainMeetServerUI.wifiQR(connection?.wifi);
    const signature = `${wifi}|${link}|${i18n().getLanguage()}`;
    if (host.dataset.signature === signature) return;
    host.dataset.signature = signature;
    host.replaceChildren(...(wifi ? [qrItem("1", t("Wi-Fi"), wifi)] : []), qrItem(wifi ? "2" : "1", t("Träffen"), link));
  }

  // ── Resten ──────────────────────────────────────────────────────────────
  function bindWifiReveal() {
    const button = $("#connection-wifi-show"), input = $("#connection-wifi-password");
    if (!button || !input) return;
    button.addEventListener("click", () => {
      const shown = input.type === "password";
      input.type = shown ? "text" : "password";
      button.setAttribute("aria-pressed", String(shown));
      button.textContent = t(shown ? "Dölj" : "Visa");
    });
    i18n()?.subscribe(() => { button.textContent = t(input.type === "password" ? "Visa" : "Dölj"); });
  }

  function mirrorResetText() {
    const source = $("#reset-modal #reset-mode-description"), target = $("#reset-what");
    if (!source || !target) return;
    const copy = () => { if (source.textContent.trim()) target.textContent = source.textContent; };
    new MutationObserver(copy).observe(source, { childList: true, characterData: true, subtree: true });
    copy();
  }

  function init() {
    bindNav();
    $$("form.kr-setform").forEach(bindForm);
    bindClockStyle();
    bindLanguage();
    bindWifiReveal();
    mirrorResetText();
    show();
  }

  globalThis.TrainMeetSettings = {
    show, rebase, saved, refresh, renderQr, currentSection, sections: SECTIONS,
    setVersion: (text) => { const node = $("#settings-version"); if (node) node.textContent = text || ""; },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
