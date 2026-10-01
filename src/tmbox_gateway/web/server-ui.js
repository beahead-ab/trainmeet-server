/* Server presentation adapter. Existing forms, API handlers and command fences
 * remain the single source of behaviour; this file owns layout, not traffic. */
(() => {
  const $ = s => document.querySelector(s);
  const make = (tag, className = "", text = "") => {
    const n = document.createElement(tag); n.className = className; n.textContent = text; return n;
  };
  const move = (selector, parent) => { const node = $(selector); if (node) parent.append(node); return node; };
  const mark = selector => $(selector)?.classList.add("legacy-internal");
  const t = (text, values) => globalThis.TrainMeetI18n.t(text, values);
  const authored = (tag, className, source) => {
    const node = make(tag, className, t(source)); node.dataset.tmText = source; return node;
  };
  const label = (node, source) => { node.dataset.tmText = source; node.textContent = t(source); };
  function inlineForm(id, parent) {
    const form = move(id, parent);
    form.classList.add("server-inline-form");
    form.querySelectorAll("[data-close-modal]").forEach(n => n.remove());
    const dirty = () => {
      if (!form.dataset.dirty) form.dataset.meetGeneration = String(globalThis.TrainMeetServerUI?.context?.selected_meet?.generation ?? "");
      form.dataset.dirty = "true";
    };
    form.addEventListener("input", dirty);
    form.addEventListener("change", dirty);
    return form;
  }
  function card(title, id) {
    const node = make("section", "card section-card server-card"); node.id = id;
    node.append(authored("h2", "", title)); return node;
  }
  function details(title, content) {
    const d = make("details", "server-details"); d.append(authored("summary", "", title), content); return d;
  }
  // Drift folds nothing away (Casper, 2026-10-01): a module is a heading and
  // what is in it, always shown.
  function block(title, content) {
    const section = make("section", "server-block"); section.append(authored("h3", "server-block__title", title), content); return section;
  }

  // Persistent header, with no second navigation system hidden behind a burger.
  const logout = move("#logout", $(".topbar-right"));
  logout.className = "tm-icon-btn"; logout.title = t("Logga ut"); logout.setAttribute("aria-label", t("Logga ut"));
  logout.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"></path></svg>';
  mark("#application-menu"); mark("#app-devices"); mark("#connection");
  $("#app-clock").className = "tm-clock";

  // Drop-down menus (details.tm-dropdown) close the way people expect: a click
  // or tap anywhere outside, Escape, choosing an item, or opening another menu.
  const openMenus = () => document.querySelectorAll("details.tm-dropdown[open]");
  const closeMenus = (except) => openMenus().forEach(menu => { if (menu !== except) menu.open = false; });
  document.addEventListener("pointerdown", event => {
    closeMenus(event.target.closest?.("details.tm-dropdown"));
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    const menu = event.target.closest?.("details.tm-dropdown[open]") || openMenus()[0];
    closeMenus(); menu?.querySelector("summary")?.focus();
  });
  document.addEventListener("click", event => {
    const item = event.target.closest?.("details.tm-dropdown nav a, details.tm-dropdown nav button");
    if (item) item.closest("details").open = false;
  });
  document.addEventListener("toggle", event => {
    if (event.target.matches?.("details.tm-dropdown") && event.target.open) closeMenus(event.target);
  }, true);

  const overview = $("#overview-view");
  const row = make("section", "card tm-clockrow server-clockrow"); row.id = "drift-clock";
  overview.prepend(row);
  const time = make("div", "tm-clockrow__time");
  time.append(authored("span", "tm-eyebrow", "Träffklocka"));
  move("#overview-clock", time).className = "tm-clockrow__big";
  const clockState = make("div", "tm-clockrow__state");
  clockState.append($("#clock-state"), $("#clock-source-status")); time.append(clockState);
  row.append(time);
  const clockForm = inlineForm("#clock-control-form", row);
  clockForm.classList.add("tm-clockrow__form");
  const source = make("div", "server-source");
  source.append(authored("span", "server-field-label", "Klockkälla"));
  // Intern | FastClock…: the source in use is marked; the other opens the
  // FastClock dialog with that choice made (app.js keeps the dialog).
  const sourceLauncher = move('[data-open-modal="clock-source-modal"]', source); sourceLauncher.classList.add("legacy-internal");
  const toggle = make("div", "tm-toggle"); toggle.id = "clock-source-toggle"; toggle.setAttribute("role", "group");
  for (const [value, text] of [["internal", "Intern"], ["fastclock", "FastClock…"]]) {
    const button = authored("button", "", text); button.type = "button"; button.dataset.source = value;
    button.addEventListener("click", () => {
      if (button.classList.contains("is-active") && value === "internal") return;
      const select = $("#clock-source"); if (select) { select.value = value; globalThis.renderClockSourceFields?.(); }
      globalThis.openModal?.("clock-source-modal", button);
    });
    toggle.append(button);
  }
  source.append(toggle);
  label($("#local-clock-speed").closest("label").querySelector("tm-text"), "Hastighet");
  label($("#local-clock-reason").closest("label").querySelector("tm-text"), "Orsak vid stopp (valfritt)");
  label($("#stop-local-clock"), "Stoppa klockan");
  const reasonField = $("#local-clock-reason").closest("label");
  clockForm.insertBefore(source, reasonField);
  // Kit order (SPEC A5): time · speed · source · Spara │ reason · Stoppa/Starta.
  // Saving an edited time is the quiet action; stopping and starting is the one
  // primary button, so the two never compete in blue.
  const save = clockForm.querySelector('button[type="submit"]');
  save.className = "secondary"; source.after(save);
  save.after(make("div", "server-divider"));
  $("#stop-local-clock").className = "primary";
  move("#overview-clock-start", clockForm.querySelector(".clock-control-actions"));
  mark("#overview-clock-stop"); mark("#clock-adjust");
  const simulation = make("aside", "tm-clockrow__aside"); simulation.id = "drift-simulation";
  simulation.append(authored("span", "tm-eyebrow", "Simulering"));
  move("#simulation-summary", simulation);
  const actions = make("div", "server-actions");
  for (const id of ["simulation-start-open", "simulation-pause", "simulation-reset-open", "simulation-finish-open"]) move(`#${id}`, actions);
  actions.querySelector("#simulation-start-open").className = "secondary";
  label(actions.querySelector("#simulation-start-open"), "Starta simulering…");
  // A virtual TMBox is under Öppna in the header; the side panel is the simulation's.
  simulation.append(actions); move("#simulation-error", simulation); row.append(simulation);
  overview.prepend(row);
  mark(".meet-summary-card"); mark(".overview-actions");
  const simDetails = block("Simulering · stationer och tåg", make("div")); simDetails.id = "drift-simulation-details"; simDetails.hidden = true;
  move("#simulation-stations-card", simDetails.lastChild); move("#simulation-trains-card", simDetails.lastChild);
  row.after(simDetails);
  move("#device-management", overview).classList.remove("admin-section-panel", "hidden");
  simDetails.after($("#device-management"));
  // Two modules, two cards. Clients are operations, followed all meet long;
  // left/right per station is a setting changed now and then. Sharing one card
  // made the station table read as part of the client list.
  label($("#device-management h2"), "Klienter");
  $("#device-management").querySelectorAll(".eyebrow, .compact-heading p").forEach(n => n.classList.add("legacy-internal"));
  const network = make("span", "server-network server-chip"); network.id = "client-network";
  const awaiting = $("#device-awaiting"); awaiting.classList.add("tm-tag", "tm-tag--warn");
  $("#device-management .section-heading h2").after(awaiting);
  $("#device-management .section-heading").append(network);
  // Reconnecting a removed client is rare: after the list, not above it.
  $("#device-removed-trying").after($("#device-management .device-reconnect"));
  // Signal boxes (TKL) and apps pair with one code. It belongs where the
  // clients are, not in the display settings, with what to type where.
  const connect = card("Anslut ställverk och appar", "connect-terminals");
  const connectFacts = make("div", "connect-facts");
  for (const [label, id] of [["Serverns adress", "connect-address"], ["Anslutningskod", "connect-code"]]) {
    const fact = make("div", "connect-fact");
    const value = make("b", "connect-value"); value.id = id;
    const copy = authored("button", "secondary connect-copy", "Kopiera"); copy.type = "button"; copy.dataset.copy = id;
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(value.textContent); copy.textContent = t("Kopierat"); }
      catch { copy.textContent = t("Kopiera inte möjligt"); }
      setTimeout(() => { copy.textContent = t(copy.dataset.tmText); }, 2000);
    });
    fact.append(authored("span", "server-field-label", label), value, copy);
    connectFacts.append(fact);
  }
  const connectNote = make("p", "connect-note"); connectNote.id = "connect-code-note"; connectNote.setAttribute("role", "status");
  const steps = make("ol", "connect-steps");
  for (const step of ["Starta TKL och skriv adressen under TrainMeet Server. Tryck Anslut.",
    "Skriv koden i rutorna under Anslutningskod. Tryck Fortsätt.", "Välj station och bekräfta. Ställverket syns sedan under Klienter."]) {
    steps.append(authored("li", "", step));
  }
  const renew = authored("button", "secondary", "Ny kod"); renew.type = "button"; renew.id = "connect-new-code";
  const renewNote = authored("p", "connect-renew-note", "En ny kod gäller för nya ställverk och appar. De som redan är anslutna fortsätter.");
  connect.append(connectFacts, connectNote, steps, renew, renewNote);
  $("#device-management").after(connect);
  const placement = card("TMBox-placering", "station-placement");
  placement.append(authored("p", "server-placement-note", "Vänster och höger på stationens boxar. Lokala val behålls när Cloud uppdaterar träffen; tågens destinationer ändras inte."));
  const stationRows = make("div", "server-station-rows"); stationRows.id = "server-station-rows";
  placement.append(stationRows);
  connect.after(placement);
  mark("#display-placement-section");
  const two = make("div", "server-grid-two"); two.id = "drift-traffic-grid"; overview.append(two);
  move(".topology-overview-card", two); move("#overview-traffic", two);
  $("#traffic-only-deviations").checked = true;
  const trafficStats = make("div", "server-stats"); trafficStats.id = "drift-traffic-stats";
  $(".traffic-filters").after(trafficStats);
  const upcoming = make("div", "server-events"); upcoming.id = "drift-upcoming"; trafficStats.after(upcoming);
  // A train picked in the list: its route and where it is now (app.js).
  const trainDetail = make("section", "drift-train-detail"); trainDetail.id = "drift-train-detail"; trainDetail.hidden = true;
  upcoming.after(trainDetail);
  move(".overview-graph-card", overview);
  // "Öppna på skärm ↗" in every module head that has a TV screen.
  const screenLink = (selector, href) => {
    let link = selector && $(selector);
    if (!link) { link = make("a"); link.href = href; link.target = "_blank"; link.rel = "noopener"; }
    link.classList.add("server-screen-link"); label(link, "Öppna på skärm ↗"); return link;
  };
  screenLink('.topology-overview-card .overview-section-heading a[href="/display/topology"]');
  screenLink('.overview-graph-card .overview-section-heading a[href="/display/graph"]');
  const filters = $(".traffic-filters"); $('label[for="traffic-station"]').classList.add("tm-visually-hidden");
  filters.append($("#traffic-station"));
  $("#overview-traffic .overview-section-heading").append(filters, screenLink(null, "/display/dashboard"));
  // Module heads as in the design (DriftEU): the name, then a short grey line.
  const headMeta = (selector, id) => {
    const meta = make("span", "server-head-meta"); if (id) meta.id = id;
    $(selector).closest(".overview-section").querySelector(".overview-section-heading h3").after(meta); return meta;
  };
  headMeta("#overview-topology", "topology-head-meta");
  headMeta("#overview-graph").append(authored("span", "", "klicka på ett tåg för att tända rutten"));
  // One card for the timetable, with Cloud's check findings for the active
  // version as its last part: the count in the heading, the list below it.
  const timetable = make("section", "card overview-section timetable-card"); timetable.id = "drift-timetable";
  overview.append(timetable);
  move("#overview-timetable", timetable);
  const timetableMeta = make("span", "server-head-meta"); timetableMeta.id = "timetable-summary-meta";
  $("#overview-timetable > .overview-timetable-head").append(timetableMeta);
  const findings = move("#published-findings", timetable);
  findings.classList.add("timetable-findings");
  const findingsTag = make("span", "tm-tag tm-tag--neutral"); findingsTag.id = "published-findings-tag";
  findings.querySelector(".timetable-findings-head").append(findingsTag);

  // Settings are seven navigable sections in two stable columns.
  const settings = $("#admin-view");
  $("#settings-heading p").textContent = "";
  const nav = make("nav", "server-settings-nav"); nav.setAttribute("aria-label", t("Inställningar"));
  const targets = [["traff", "Träff och Cloud"], ["server", "Den här servern"], ["anslutning", "Anslutning"], ["anvandare", "Användare"], ["skarmar", "Skärmar och klocka"], ["sprak", "Språk"], ["uppdatering", "Programuppdatering"], ["farozon", "Farozon"]];
  // The code for apps and TKL and how long it holds: its own section.
  const connection = card("Anslutning", "connection-settings"); settings.append(connection);
  for (const [id, label] of targets) { const a = authored("a", "tm-seg", label); a.href = `/installningar#${id}`; nav.append(a); }
  const back = authored("a", "tm-btn", "← Tillbaka till driften"); back.href = "/drift"; nav.append(back);
  $("#settings-heading").append(nav);
  // The button of the section in view is marked: the section whose top has
  // most recently passed under the header (two columns: the left one wins a
  // tie). A button just pressed stays marked while the page scrolls to it.
  let pickedUntil = 0;
  const setActive = id => nav.querySelectorAll("a.tm-seg").forEach(a => a.classList.toggle("is-active", a.hash === `#${id}`));
  const markSection = () => {
    if (document.body.dataset.mode !== "installningar" || Date.now() < pickedUntil) return;
    let current = targets[0][0], best = -Infinity;
    for (const [id] of targets) {
      const top = document.getElementById(id)?.getBoundingClientRect().top;
      if (top !== undefined && top < 140 && top > best + 1) { best = top; current = id; }
    }
    if (scrollY > 0 && innerHeight + scrollY >= document.documentElement.scrollHeight - 4) current = targets[targets.length - 1][0];
    setActive(current);
  };
  nav.addEventListener("click", event => { const a = event.target.closest?.("a.tm-seg"); if (a) { pickedUntil = Date.now() + 800; setActive(a.hash.slice(1)); } });
  addEventListener("scroll", markSection, { passive: true });
  const columns = make("div", "server-settings-columns"); const left = make("div"); const right = make("div"); columns.append(left, right); settings.append(columns);
  // Farozon comes last of all, under both columns, on a phone as on a computer.
  const sections = [["#sync-and-devices", left, "traff"], ["#server-identity-settings", left, "server"], ["#connection-settings", left, "anslutning"], ["#admin-users-settings", right, "anvandare"], [".clock-control-card", right, "skarmar"], ["#language-settings", right, "sprak"], ["#software-update-settings", right, "uppdatering"], ["#server-system-settings", settings, "farozon"]];
  for (const [selector, column, anchor] of sections) {
    const section = move(selector, column); section.classList.add("server-card"); section.dataset.anchor = anchor;
    const link = make("span", "server-anchor"); link.id = anchor; section.prepend(link);
  }
  mark("#admin-access-settings");
  label($("#sync-and-devices h2"), "Träff och Cloud");
  inlineForm("#cloud-auto-form", $("#sync-and-devices")); mark("#cloud-auto-edit");
  $("#cloud-auto-form > p").classList.add("legacy-internal");
  const identity = $("#server-identity-settings");
  identity.querySelector(".server-anchor").after(authored("h2", "", "Den här servern"));
  mark("#server-identity-settings .identity-status-grid");
  inlineForm("#server-identity-form", identity);
  mark('[data-open-modal="server-identity-form-modal"]');
  const danger = $("#server-system-settings"); danger.prepend(authored("h2", "", "Farozon"));
  danger.querySelectorAll("button").forEach(button => button.classList.add("danger-action"));
  const appearance = $(".clock-control-card");
  label(appearance.querySelector("h2"), "Skärmar och klocka");
  appearance.querySelectorAll(".eyebrow, .compact-heading p, .modal-launch").forEach(n => n.classList.add("legacy-internal"));
  // Four parts, each with its own heading and its own Spara.
  const part = (title) => {
    const node = make("div", `server-part${appearance.querySelector(".server-part") ? "" : " server-part--first"}`);
    node.append(authored("h3", "server-part__title", title)); appearance.append(node); return node;
  };
  inlineForm("#clock-appearance-form", part("Klocka"));
  const styleField = make("div", "server-field");
  $('label[for="meet-clock-style"]').before(styleField);
  styleField.append($('label[for="meet-clock-style"]'), $("#meet-clock-style"));
  inlineForm("#connection-badge-form", part("QR-koder på skärmarna"));
  connection.append(authored("p", "server-placement-note", "Ställverk (TKL) och appar ansluter med serverns adress och den här koden. Samma uppgifter finns i Drift under Klienter."));
  const code = move("#connection-badge-code", connection); code.classList.add("server-network");
  inlineForm("#connection-code-form", connection);
  inlineForm("#connection-wifi-form", part("Träffens Wi-Fi"));
  label($("#users-invite-open"), "+ Bjud in");
  $("#admin-users-settings .section-heading").append($("#users-invite-open"));

  // ---- Inställningar in the design's form (ServerSida): every card is a head
  // (name and a short grey line), short rows of key and value with the action
  // at the end of its row, and a foot for the small print. The controls are
  // the same elements as before, moved; only their places change.
  const spacer = () => make("span", "tm-spacer");
  const muted = (source) => authored("span", "tm-line__muted", source);
  // The value side is its own box, so a row that wraps stays in its column.
  const settingRow = (key, ...nodes) => {
    const line = make("div", "tm-line");
    if (key) line.append(typeof key === "string" ? authored("span", "tm-line__key", key) : key);
    const value = make("div", "tm-line__value"); value.append(...nodes.filter(Boolean)); line.append(value); return line;
  };
  const settingRows = (...lines) => { const box = make("div", "tm-lines"); box.append(...lines); return box; };
  const head = (card, note) => {
    let bar = card.querySelector(":scope > .section-heading");
    if (!bar) {
      const h2 = card.querySelector(":scope > h2");
      bar = make("div", "section-heading server-card__head"); h2.before(bar); bar.append(h2);
    }
    if (note) bar.querySelector("h2").after(authored("span", "server-card__note-inline", note));
    return bar;
  };
  const foot = (card, ...nodes) => { const node = make("div", "tm-card__foot"); node.append(...nodes); card.append(node); return node; };

  // Träff och Cloud: Träff · Kör version · Cloud, the automatic fetch, and
  // Byt träff in the foot.
  const cloudCard = $("#sync-and-devices");
  head(cloudCard, "träffens innehåll ändras i Cloud och hämtas hit");
  cloudCard.querySelector(":scope > p:not([id])")?.classList.add("legacy-internal");
  const meetRegion = make("span", "tm-badge"); meetRegion.id = "cloud-meet-region";
  const meetMeta = make("span", "tm-line__muted"); meetMeta.id = "cloud-meet-meta";
  const runningVersion = $("#cloud-connection-meta"); runningVersion.classList.add("tm-badge", "tm-badge--version");
  const publishedAt = make("span", "tm-line__muted"); publishedAt.id = "cloud-published-at";
  const searchUpdate = $("#runtime-check-update"); searchUpdate.classList.add("tm-linkbtn");
  cloudCard.querySelector(".cloud-connection-summary").replaceWith(settingRows(
    settingRow("Träff", $("#cloud-connection-meet"), meetRegion, meetMeta),
    settingRow("Kör version", runningVersion, publishedAt, $("#cloud-version-state")),
    settingRow("Cloud", $("#cloud-connection-state"), $("#cloud-auto-status"), spacer(), searchUpdate),
    settingRow(null, $("#cloud-auto-form"))));
  const changeMeet = cloudCard.querySelector('[data-open-modal="runtime-sync-form-modal"]');
  label(changeMeet, "Byt träff…"); changeMeet.classList.add("tm-btn--sm");
  foot(cloudCard, changeMeet, muted("Träffkod från Cloud · användare och nätverk behålls, stationstilldelningarna följer inte med"));
  cloudCard.querySelector(".cloud-actions").classList.add("legacy-internal");

  // Den här servern: Namn [fält] [Spara] and the address on the meet's network.
  head(identity, "namnet syns i Cloud och längst ner på skärmarna");
  const identityForm = $("#server-identity-form"); identityForm.classList.add("tm-line");
  identityForm.querySelector('label[for="admin-server-name"]').classList.add("tm-line__key");
  label(identityForm.querySelector('[type="submit"]'), "Spara");
  const serverAddress = make("span", "tm-line__mono"); serverAddress.id = "server-network-line";
  identityForm.after(settingRows(settingRow("Nätverk", serverAddress, muted("adressen på träffens nätverk"))));

  // Användare: "+ Bjud in" in the head, the rule about owners in the foot.
  label($("#users-invite-open"), "+ Bjud in");
  const usersHead = $("#admin-users-settings .section-heading");
  usersHead.append(spacer(), $("#users-invite-open"));
  const usersNote = $("#admin-users-settings .access-explainer"); if (usersNote) foot($("#admin-users-settings"), usersNote);

  // Skärmar och klocka: the four parts stay, each one row with its Spara last.
  appearance.querySelector(".section-heading h2").after(authored("span", "server-card__note-inline", "gäller träffens skärmar på alla datorer"));
  $('label[for="meet-clock-style"]').classList.add("tm-visually-hidden");

  // Språk: one row.
  const language = $("#language-settings"); head(language, "gäller den här webbläsaren");
  language.querySelector(":scope > p")?.classList.add("legacy-internal");
  const languageChoice = language.querySelector(".settings-language-choice");
  languageChoice.classList.add("tm-line"); languageChoice.querySelector("span").classList.add("tm-line__key");
  languageChoice.append(muted("TMBoxarnas språk sätts per box på Drift"));

  // Programuppdatering: version, state and the button on one row; what the
  // update does in the foot.
  const update = $("#software-update-settings");
  const updateNote = update.querySelector(".update-explainer");
  const updateRow = update.querySelector(".update-actions"); updateRow.classList.add("tm-line");
  updateRow.prepend($("#software-version"));
  if (updateNote) foot(update, updateNote);

  // Farozon: each action on its row with what it does beside it.
  head(danger, "varje åtgärd bekräftas i ett eget fönster");
  const restoreButton = danger.querySelector('[data-open-modal="restore-modal"]');
  const resetButton = $("#reset-mode-summary");
  const resetWhat = make("span", "tm-line__muted");
  const mirror = () => { resetWhat.textContent = $("#reset-mode-description")?.textContent || ""; };
  new MutationObserver(mirror).observe($("#reset-mode-description"), { childList: true, characterData: true, subtree: true }); mirror();
  const dangerRows = settingRows(settingRow(null, restoreButton, muted("En kopia tas automatiskt före varje programuppdatering.")), settingRow(null, resetButton, resetWhat));
  danger.querySelector(":scope > .section-heading").after(dangerRows);

  // Documentation is separate from the operator client. No legacy emulator is
  // started by merely visiting Help.
  const help = card("Hjälp", "help-view"); help.classList.add("view-panel", "hidden");
  const helpLinks = make("div", "server-actions");
  for (const [path, label] of [["/tmbox/", "Öppna TMBox"], ["/tmbox-lab/", "TMBox-provbänk"], ["/drift", "Tillbaka till driften"]]) {
    const a = authored("a", "tm-btn", label); a.href = path; helpLinks.append(a);
  }
  help.append(helpLinks);
  for (const [id, label] of [["#tmbox-pane-floden", "Flöden"], ["#tmbox-pane-skarmar", "Skärmkatalog"], ["#tmbox-pane-referens", "Referens"]]) {
    const content = $(id); content.classList.remove("hidden"); help.append(details(label, content));
  }
  $(".server-workspace").append(help);

  const api = { make, move, t, context: null, info: null, presentation: null };
  const territoryScreen = make("div", "sc-territories hidden"); territoryScreen.id = "territories-view"; $("#display-stage").append(territoryScreen);
  const territoryCheck = make("label"); const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.value = "territories";
  territoryCheck.append(checkbox, authored("span", "", "Områdestavla")); $("#connection-badge-screens").append(territoryCheck);
  api.refreshHeader = () => {
    const context = api.context || {}, meet = context.selected_meet, update = context.cloud_update || {};
    const us = context.operating_region === "us"; document.body.dataset.region = us ? "us" : "eu";
    document.querySelectorAll("[data-eu-only]").forEach(n => { n.hidden = us; });
    document.querySelectorAll("[data-us-only]").forEach(n => { n.hidden = !us; });
    $("#server-region").textContent = meet ? (us ? "US" : "EU") : "";
    $("#server-region").className = `tm-badge tm-badge--${us ? "us" : "eu"}`;
    // A publication UUID is not a human version number.
    const ordinal = meet?.version_number ?? meet?.publication_version;
    const hasVersion = Number.isInteger(ordinal);
    const version = hasVersion ? `${t("Version")} ${ordinal}` : (meet ? t("Publicerad träff") : t("Ingen träff vald"));
    // The meet block says version · server; the status pill says the state.
    // Without a version number from Cloud the block shows only the server name,
    // so "Publicerad träff" is never written twice side by side.
    $("#header-server-meta").textContent = [hasVersion || !meet ? version : "", api.info?.runtime?.server_name].filter(Boolean).join(" · ");
    const conflicts = !us && api.presentation?.findings?.filter(f => f.level === "conflict").length;
    api.findingsTag(hasVersion ? ordinal : null);
    const newer = Boolean(update.pending_publication_id || update.available_publication_id);
    const status = $("#header-cloud-status");
    const offline = update.linked && update.state === "error" && (!update.last_checked_at || Date.now() - Date.parse(update.last_checked_at) > 600000);
    status.textContent = newer ? t("Ny version finns i Cloud")
      : offline ? (hasVersion ? `${t("Cloud inte nådd")} · ${t("kör")} ${version.toLowerCase()}` : t("Cloud inte nådd"))
      : (hasVersion ? version : t("Publicerad")) + (conflicts ? ` · ${conflicts} ${t("konflikter")}` : "");
    status.className = `tm-status tm-status--${newer ? "newer" : offline ? "offline" : "published"}`;
    $("#cloud-connection-meta").textContent = version;
    const region = $("#cloud-meet-region");
    if (region) { region.textContent = meet ? (us ? "US" : "EU") : ""; region.className = `tm-badge tm-badge--${us ? "us" : "eu"}`; region.hidden = !meet; }
    const runtime = api.info?.runtime || {};
    if ($("#cloud-meet-meta")) $("#cloud-meet-meta").textContent = runtime.station_count ? t("{stations} stationer · {day}", { stations: runtime.station_count, day: runtime.active_day || "" }) : "";
    const published = runtime.published_at ? new Date(runtime.published_at) : null;
    if ($("#cloud-published-at")) $("#cloud-published-at").textContent = published && !Number.isNaN(published.getTime())
      ? t("publicerad {date}", { date: `${published.toLocaleDateString("sv-SE")} ${published.toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit" })}` }) : "";
    $("#drift-simulation").hidden = us;
    $("#device-management").hidden = us;
    $("#station-placement").hidden = us;
    $("#connect-terminals").hidden = us;
    $("#drift-traffic-grid").hidden = us;
    $(".overview-graph-card").hidden = us;
    $("#header-cloud-status").hidden = !meet;
  };
  // "3 konflikter i version 8" on the timetable card's findings row.
  api.findingsTag = ordinal => {
    const findings = api.presentation?.findings, tag = $("#published-findings-tag");
    const count = level => findings.filter(f => f.level === level).length;
    const parts = [];
    if (Array.isArray(findings)) {
      const conflicts = count("conflict"), observations = count("observation");
      if (conflicts) parts.push(t(conflicts === 1 ? "{count} konflikt" : "{count} konflikter", {count: conflicts}));
      if (observations) parts.push(t(observations === 1 ? "{count} observation" : "{count} observationer", {count: observations}));
      if (!parts.length) parts.push(t("Inga konflikter"));
      tag.className = `tm-tag ${conflicts ? "tm-tag--warn" : observations ? "tm-tag--neutral" : "tm-tag--ok"}`;
    } else {
      parts.push(t("Inga kontrolluppgifter"));
      tag.className = "tm-tag tm-tag--neutral";
    }
    tag.textContent = parts.join(" · ") + (ordinal === null ? "" : ` ${t("i version {version}", {version: ordinal})}`);
  };
  api.refreshClock = clock => {
    $("#clock-source-toggle")?.querySelectorAll("button").forEach(button =>
      button.classList.toggle("is-active", button.dataset.source === (clock.source === "fastclock" ? "fastclock" : "internal")));
    const us = api.context?.operating_region === "us";
    const value = String(clock.time || "--:--").slice(0, 5);
    const [h, m] = value.split(":");
    const formatted = us && Number.isFinite(Number(h)) ? `${Number(h) % 12 || 12}:${m} ${Number(h) >= 12 ? "PM" : "AM"}` : value;
    for (const id of ["#overview-clock", "#app-clock"]) { $(id).textContent = formatted; $(id).classList.toggle("tm-clock--stopped", !clock.running); }
    $("#overview-clock-start").hidden = Boolean(clock.running);
    $("#stop-local-clock").hidden = !clock.running;
    $("#stop-local-clock").disabled = clock.source === "fastclock" && !clock.can_control;
    for (const id of ["#local-clock-time", "#local-clock-speed", '#clock-control-form button[type="submit"]']) $(id).disabled = clock.source === "fastclock";
  };
  api.network = connection => {
    // Boxes find the server themselves and never use the pairing code; that
    // code is for the apps and TKL and lives under Settings.
    const label = connection.host ? `${connection.host}:${connection.port}` : t("Anslut klienten till denna server");
    $("#client-network").textContent = label;
    if ($("#server-network-line")) $("#server-network-line").textContent = connection.host ? `${connection.host}:${connection.port}` : "—";
  };
  api.mode = mode => {
    $("#help-view").classList.toggle("hidden", mode !== "help");
    for (const [id, page] of [["#header-settings", "installningar"], ["#header-help", "help"]]) {
      if (mode === page) $(id)?.setAttribute("aria-current", "page"); else $(id)?.removeAttribute("aria-current");
    }
    requestAnimationFrame(markSection);
    if (mode === "installningar" && location.hash) requestAnimationFrame(() => document.getElementById(location.hash.slice(1))?.scrollIntoView({block: "start"}));
  };
  api.stationRows = rows => {
    const host = $("#server-station-rows");
    host.replaceChildren();
    const table = make("table", "tm-table");
    const head = make("thead"), heading = make("tr");
    for (const label of ["Station", "Vänster", "Höger"]) heading.append(authored("th", "", label));
    heading.append(make("th"));
    head.append(heading); table.append(head);
    const body = make("tbody"); table.append(body);
    // Every station, as on the rest of Drift: nothing to unfold.
    body.append(...rows.children); host.append(table);
  };
  api.traffic = (snapshot, station = "", selectedTrain = null) => {
    const positions = (snapshot.train_positions || []).filter(p => !station || [p.station_id, p.from_station_id, p.to_station_id].includes(station));
    const moving = positions.filter(p => p.connection_id);
    const now = String(snapshot.clock?.time || "00:00").slice(0, 5);
    const upcoming = (snapshot.routes || []).filter(r => (!station || r.station_id === station) && (r.departure_time || r.arrival_time || "") >= now)
      .sort((a, b) => (a.departure_time || a.arrival_time).localeCompare(b.departure_time || b.arrival_time)).slice(0, 4);
    const delayed = moving.filter(p => (snapshot.routes || []).some(r => r.train_number === p.train_number && r.station_id === p.to_station_id && r.arrival_time && r.arrival_time < now));
    const stats = $("#drift-traffic-stats"); stats.replaceChildren();
    for (const [count, label, tone] of [[moving.length, "tåg på linjen", ""], [positions.filter(p => !p.connection_id && p.station_id).length, "inne på stationerna", ""], [delayed.length, "avvikelser", delayed.length ? "warn" : "ok"]]) {
      const stat = make("div", tone ? `server-stat--${tone}` : ""); stat.append(make("strong", "", String(count)), authored("span", "", label)); stats.append(stat);
    }
    const online = $("#traffic-online")?.closest("section"); if (online) online.hidden = !moving.length;
    const atStations = $("#traffic-stations-heading");
    if (atStations) atStations.textContent = `${t("Inne på stationerna")} (${(snapshot.stations || []).length})`;
    // Same small capitals as "På linjen just nu" under it; then one row per
    // event in columns: time, train, where and what, and how long until.
    const events = $("#drift-upcoming"); events.replaceChildren(authored("h3", "section-eyebrow", "Nästa händelser"));
    const minutesOf = value => { const [h, m] = String(value || "").split(":").map(Number); return h * 60 + m; };
    for (const row of upcoming) {
      const name = snapshot.stations?.find(s => s.id === row.station_id)?.name || row.station_id;
      const when = row.departure_time || row.arrival_time, delta = minutesOf(when) - minutesOf(now);
      // Each train opens its route and position (api.onTrainSelect, app.js).
      const entry = make("button", "server-event drift-event"); entry.type = "button"; entry.dataset.trainNumber = row.train_number;
      entry.setAttribute("aria-pressed", String(String(row.train_number) === String(selectedTrain)));
      entry.append(make("span", "drift-event__time", when), make("b", "drift-event__train", row.train_number),
        make("span", "drift-event__what", t(row.departure_time ? "avgår {station}" : "ankommer {station}", { station: name })),
        make("span", "drift-event__in", Number.isFinite(delta) ? (delta <= 0 ? t("nu") : t("{n} min", { n: delta })) : ""));
      entry.addEventListener("click", () => api.onTrainSelect?.(row.train_number));
      events.append(entry);
    }
    if (!upcoming.length) events.append(authored("p", "drift-event__empty", "Inga fler planerade händelser idag."));
  };
  api.initDisplay = () => {
    document.body.classList.add("server-display");
    const stage = $("#display-stage");
    const content = make("div", "sc-content");
    while (stage.firstChild) content.append(stage.firstChild);
    stage.append(content);
    const header = make("header", "sc-top"); header.id = "screen-header";
    const meet = make("div", "sc-top__meet"); meet.id = "screen-meet";
    // Right in the top row: the QR codes when this screen shows them, then
    // the meet clock and whether it runs (● 4×) or stands still.
    const right = make("div", "sc-top__right"); right.id = "screen-top-right";
    const clock = make("div", "sc-top__clock"); clock.id = "screen-time";
    const run = make("span", "sc-top__run"); run.id = "screen-run";
    right.append(clock, run);
    header.append(meet, right);
    const footer = make("footer", "sc-foot"); footer.id = "screen-footer";
    move("#display-connection", footer);
    const status = make("span", ""); status.id = "screen-status"; footer.append(status);
    const legend = make("span", "sc-legend"); legend.id = "screen-legend"; footer.append(legend);
    // QR codes for anyone in the hall: first the Wi-Fi, then the link to the
    // participant view (a local address, so the Wi-Fi has to come first).
    // They never take room from the map, the diagram or the lists: they sit
    // in the top row beside the clock, in the tile row on Översikt and in
    // the corner of the clock screen. api.display puts them there.
    const qr = make("div", "sc-qr"); qr.id = "screen-qr"; qr.hidden = true;
    right.prepend(qr);
    stage.append(header, content, footer);
    const resize = () => {
      const scale = Math.min(innerWidth / 1920, innerHeight / 1080);
      stage.style.transform = `translate(-50%, -50%) scale(${scale})`;
    };
    window.addEventListener("resize", resize); resize();
  };
  api.display = (snapshot, kind, time) => {
    const meet = $("#screen-meet"); if (!meet) return;
    const us = snapshot.meet?.operating_region === "us";
    // The meet's name only: EU or US is setup detail, not something the hall needs.
    meet.replaceChildren(make("span", "", snapshot.meet?.name || "TrainMeet"));
    const labels = {clock:"Träffklocka", topology:"Banöversikt", graph:"Tågdiagram", dashboard:"Översikt", territories:"Områdestavla"};
    // The name of the screen, and on the map and the diagram what the hall
    // looks for first: how many trains are out on the line right now.
    const out = (snapshot.train_positions || []).filter(p => p.connection_id).length;
    const live = ["topology", "graph"].includes(kind) ? ` · ${t(out === 1 ? "1 tåg på linjen" : "{n} tåg på linjen", { n: out })}` : "";
    if (kind !== "clock") meet.append(make("span", "sc-subtitle", t(labels[kind]) + live));
    const hour = Number(time.slice(0,2));
    $("#screen-time").textContent = kind === "clock" ? "" : us ? `${hour%12||12}${time.slice(2,5)} ${hour>=12?"PM":"AM"}` : time.slice(0, 5);
    const run = $("#screen-run"), running = Boolean(snapshot.clock?.running);
    run.textContent = running ? `${Number(snapshot.clock?.speed || 1)}×` : t("Stoppad");
    run.classList.toggle("is-stopped", !running);
    $("#screen-status").textContent = snapshot.server_name || "TrainMeet Server";
    const stage = $("#display-stage");
    stage.dataset.kind = kind;
    // The clock screen has no top row: there, and on Översikt where the
    // codes are the last tile of the tile row, they stay in the footer.
    const qr = $("#screen-qr"), home = ["clock", "dashboard"].includes(kind) ? $("#screen-footer") : $("#screen-top-right");
    if (qr && qr.parentElement !== home) { if (home.id === "screen-footer") home.append(qr); else home.prepend(qr); }
    api.legend(kind);
    api.lastDisplayContact = Date.now();
  };
  // What the colours mean, in the footer where the address line used to be.
  api.legend = (kind) => {
    const host = $("#screen-legend"); if (!host) return;
    const signature = `${kind}|${globalThis.TrainMeetI18n?.getLanguage?.() || ""}`;
    if (host.dataset.signature === signature) return;
    host.dataset.signature = signature;
    const item = (swatch, text) => { const span = make("span", "sc-legend__item"); span.append(make("i", `sc-legend__swatch sc-legend__swatch--${swatch}`), authored("span", "", text)); return span; };
    if (kind === "topology") host.replaceChildren(authored("span", "", "Fylld tågbricka = på linjen · pilen visar riktningen"));
    else if (kind === "graph") host.replaceChildren(item("line", "på linjen nu"), item("plan", "planerat"), item("now", "nu"));
    else host.replaceChildren();
  };
  api.wifiQR = (wifi) => {
    if (!wifi?.name) return "";
    const esc = (value) => String(value).replace(/([\\;,":])/g, "\\$1");
    if (wifi.password) return `WIFI:T:WPA;S:${esc(wifi.name)};P:${esc(wifi.password)};;`;
    return wifi.has_password ? `WIFI:T:WPA;S:${esc(wifi.name)};;` : `WIFI:T:nopass;S:${esc(wifi.name)};;`;
  };
  api.qrSVG = (payload) => {
    globalThis.qrcode.stringToBytes = globalThis.qrcode.stringToBytesFuncs["UTF-8"];
    const qr = globalThis.qrcode(0, "M"); qr.addData(payload); qr.make();
    return qr.createSvgTag({cellSize: 4, margin: 0, scalable: true});
  };
  api.qr = ({ link = "", wifi = null } = {}, visible) => {
    const host = $("#screen-qr"); if (!host) return;
    host.hidden = !visible || !link || typeof globalThis.qrcode !== "function";
    if (host.hidden) return;
    const network = api.wifiQR(wifi);
    const signature = `${network}|${link}`;
    if (host.dataset.signature === signature) return;
    host.dataset.signature = signature;
    const item = (payload, caption) => {
      const wrap = make("div", "sc-qr__item");
      const code = make("div", "sc-qr__code");
      code.innerHTML = api.qrSVG(payload);
      wrap.append(code, authored("span", "sc-qr__text", caption));
      return wrap;
    };
    host.replaceChildren(...(network ? [item(network, "1 · Wi-Fi"), item(link, "2 · Träffen")] : [item(link, "Skanna – allt om träffen")]));
  };
  api.us = (data, screen = false) => {
    const host = screen ? $("#territories-view") : $("#us-runtime-summary");
    const signature = globalThis.TrainMeetI18n.getLanguage() + JSON.stringify(data || {});
    if (host.dataset.signature === signature) return;
    host.dataset.signature = signature; host.replaceChildren();
    if (!data) { host.append(authored("p", "", "Områdestavlan gäller en US-träff.")); return; }
    if (!screen) {
      const intro = make("div", "server-actions"); intro.append(authored("h2", "", "US · Trafikledning"));
      const link = authored("a", "tm-btn tm-btn--primary", "Öppna Dispatcher"); link.href = "/us/dispatcher"; intro.append(link); host.append(intro);
      host.append(authored("p", "", "Körtillstånd utfärdas och bekräftas av Dispatcher. Inga tillstånd skapas automatiskt."));
    }
    const infra = make("section", "server-us-infra"); infra.append(authored("h3", "", "Territorier"));
    if (!data.active) infra.append(authored("p", "", "Ingen US-körning är aktiv. Starta körningen i Dispatcher."));
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 1500 ${Math.max(160, (data.territories || []).length * 170)}`); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", t("Territoriediagram"));
    const draw = (tag, attrs, text) => { const n = document.createElementNS(svg.namespaceURI, tag); Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v)); if (text != null) n.textContent = text; svg.append(n); return n; };
    (data.territories || []).forEach((territory, i) => {
      const nodes = (data.nodes || []).filter(n=>n.territory_id === territory.id);
      const mp = nodes.length > 1 && nodes.every(n=>Number.isFinite(n.mp));
      const numbers = nodes.map(n=>mp ? n.mp : n.x);
      const min = Math.min(...numbers), max = Math.max(...numbers);
      const x = n => 330 + ((mp ? n.mp : n.x) - min) / Math.max(1, max-min) * 1060;
      const y = 80 + i*170;
      draw("text", {x:10, y:y-10, class:"us-diagram-label"}, territory.name);
      draw("text", {x:10, y:y+25, class:"us-diagram-meta"}, mp ? "Mileposts" : t("Diagramläge · inte MP"));
      const byId = new Map(nodes.map(n=>[n.id,n]));
      for (const segment of data.segments || []) {
        const a = byId.get(segment.from_node), b = byId.get(segment.to_node); if (!a || !b) continue;
        draw("line", {x1:x(a),x2:x(b),y1:y,y2:y,class:"us-diagram-track"});
      }
      nodes.forEach(n=>{ draw("circle",{cx:x(n),cy:y,r:8,class:"us-diagram-node"}); draw("text",{x:x(n),y:y-22,"text-anchor":"middle",class:"us-diagram-label"},n.name); if(mp)draw("text",{x:x(n),y:y+36,"text-anchor":"middle",class:"us-diagram-meta"},`MP ${n.mp}`); });
    });
    infra.append(svg); host.append(infra);
    const sections = make("div", "server-grid-two"); host.append(sections);
    const table = (title, headings, rows) => {
      const box = make("section", "server-us-card"); box.append(authored("h3", "", title));
      const tbl = make("table", "tm-table"), head = make("tr"); headings.forEach(h=>head.append(make("th","",t(h))));
      const thead = make("thead"); thead.append(head); tbl.append(thead); const body = make("tbody");
      rows.forEach(values=>{const tr=make("tr"); values.forEach(v=>tr.append(make("td","",String(v ?? "—")))); body.append(tr);}); tbl.append(body); box.append(tbl);
      if (!rows.length) box.append(authored("p", "", "Inga aktuella poster.")); sections.append(box);
    };
    const symbol = id => (data.runs || []).find(r=>r.id===id)?.symbol || id;
    const warrants = (data.warrants || []).filter(w=>["active","release_requested"].includes(w.status));
    table("Gällande körtillstånd", ["Tåg","Nummer","Typ","Status"], warrants.map(w=>[symbol(w.run_id), w.number,w.kind,w.status === "active" ? t("I kraft") : t("Frigivning rapporterad")]));
    table("Rapporter och förfrågningar", ["Tid","Tåg","Typ","Meddelande"], (data.requests || []).slice(-4).reverse().map(r=>[r.meet_time,symbol(r.run_id),r.kind,r.message]));
    if (!screen) table("Tåguppdrag och förare", ["Tåg / symbol","Conductor","Redo","Rapporterat läge"], (data.runs || []).map(r=>[r.symbol,r.conductor_name || t("Ej tilldelad"),r.ready?t("Ja"):t("Nej"),r.position?.mp!=null?`MP ${r.position.mp}`:r.position?.node_id || r.position?.milepost_id || "—"]));
  };
  setInterval(() => {
    if (api.lastDisplayContact && Date.now() - api.lastDisplayContact > 30000) $("#screen-status").textContent = t("Kontakt saknas · senast mottagna läge visas");
  }, 5000);
  globalThis.TrainMeetServerUI = api;
})();
