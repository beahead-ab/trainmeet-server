/* Server presentation adapter. Existing forms, API handlers and command fences
 * remain the single source of behaviour; this file owns layout, not traffic. */
(() => {
  const $ = s => document.querySelector(s);
  const make = (tag, className = "", text = "") => {
    const n = document.createElement(tag); n.className = className; n.textContent = text; return n;
  };
  const move = (selector, parent) => { const node = $(selector); if (node) parent.append(node); return node; };
  const mark = selector => $(selector)?.classList.add("legacy-internal");
  const t = text => globalThis.TrainMeetI18n.t(text);
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
    node.append(make("h2", "", t(title))); return node;
  }
  function details(title, content) {
    const d = make("details", "server-details"); d.append(make("summary", "", t(title)), content); return d;
  }

  // Persistent header, with no second navigation system hidden behind a burger.
  const logout = move("#logout", $(".topbar-right"));
  logout.className = "tm-icon-btn"; logout.title = t("Logga ut"); logout.setAttribute("aria-label", t("Logga ut")); logout.textContent = "⇥";
  mark("#application-menu"); mark("#app-devices"); mark("#connection");
  $("#app-clock").className = "tm-clock";

  const overview = $("#overview-view");
  const row = make("section", "card tm-clockrow server-clockrow"); row.id = "drift-clock";
  overview.prepend(row);
  const time = make("div", "tm-clockrow__time");
  time.append(make("span", "tm-eyebrow", t("Träffklocka")));
  move("#overview-clock", time).className = "tm-clockrow__big";
  move("#clock-state", time); move("#clock-source-status", time);
  row.append(time);
  const clockForm = inlineForm("#clock-control-form", row);
  clockForm.classList.add("tm-clockrow__form");
  const source = make("div", "server-source");
  source.append(make("span", "tm-eyebrow", t("Klockkälla")));
  move('[data-open-modal="clock-source-modal"]', source);
  clockForm.insertBefore(source, $("#local-clock-reason").closest("label"));
  move("#overview-clock-start", clockForm.querySelector(".clock-control-actions"));
  mark("#overview-clock-stop"); mark("#clock-adjust");
  const simulation = make("aside", "tm-clockrow__aside"); simulation.id = "drift-simulation";
  simulation.append(make("span", "tm-eyebrow", t("Simulering")));
  move("#simulation-summary", simulation);
  const actions = make("div", "server-actions");
  for (const id of ["simulation-start-open", "simulation-pause", "simulation-reset-open", "simulation-finish-open"]) move(`#${id}`, actions);
  simulation.append(actions); move("#simulation-error", simulation); row.append(simulation);
  overview.prepend(row);
  mark(".meet-summary-card"); mark(".overview-actions");
  const simDetails = details("Simulering · stationer och tåg", make("div")); simDetails.id = "drift-simulation-details"; simDetails.hidden = true;
  move("#simulation-stations-card", simDetails.lastChild); move("#simulation-trains-card", simDetails.lastChild);
  row.after(simDetails);
  move("#device-management", overview).classList.remove("admin-section-panel", "hidden");
  simDetails.after($("#device-management"));
  $("#device-management h2").textContent = t("Stationer och klienter");
  $("#device-management").querySelectorAll(".eyebrow, .compact-heading p").forEach(n => n.classList.add("legacy-internal"));
  const network = make("p", "server-network"); network.id = "client-network";
  $("#device-management .section-heading").append(network);
  const stationRows = make("div", "server-station-rows"); stationRows.id = "server-station-rows";
  $("#device-management").append(stationRows);
  mark("#display-placement-section");
  const two = make("div", "server-grid-two"); two.id = "drift-traffic-grid"; overview.append(two);
  move(".topology-overview-card", two); move("#overview-traffic", two);
  $("#traffic-only-deviations").checked = true;
  const trafficStats = make("div", "server-stats"); trafficStats.id = "drift-traffic-stats";
  $(".traffic-filters").after(trafficStats);
  const upcoming = make("div", "server-events"); upcoming.id = "drift-upcoming"; trafficStats.after(upcoming);
  const stationSection = $("#traffic-stations").closest("section");
  const stationParent = stationSection.parentElement;
  stationParent.insertBefore(details("Inne på stationerna", stationSection), stationParent.querySelector(".traffic-history"));
  move(".overview-graph-card", overview); move("#overview-timetable", overview);
  move("#published-findings", $("#overview-timetable"));

  // Settings are seven navigable sections in two stable columns.
  const settings = $("#admin-view");
  $("#settings-heading p").textContent = "";
  const nav = make("nav", "server-settings-nav"); nav.setAttribute("aria-label", t("Inställningar"));
  const targets = [["traff", "Träff och Cloud"], ["server", "Den här servern"], ["anvandare", "Användare"], ["skarmar", "Skärmar och klocka"], ["sprak", "Språk"], ["uppdatering", "Programuppdatering"], ["farozon", "Farozon"]];
  for (const [id, label] of targets) { const a = make("a", "tm-seg", t(label)); a.href = `/installningar#${id}`; nav.append(a); }
  const back = make("a", "tm-btn", t("← Tillbaka till driften")); back.href = "/drift"; nav.append(back);
  $("#settings-heading").append(nav);
  const columns = make("div", "server-settings-columns"); const left = make("div"); const right = make("div"); columns.append(left, right); settings.append(columns);
  const sections = [["#sync-and-devices", left, "traff"], ["#server-identity-settings", left, "server"], ["#server-system-settings", left, "farozon"], ["#admin-users-settings", right, "anvandare"], [".clock-control-card", right, "skarmar"], ["#language-settings", right, "sprak"], ["#software-update-settings", right, "uppdatering"]];
  for (const [selector, column, anchor] of sections) {
    const section = move(selector, column); section.classList.add("server-card"); section.dataset.anchor = anchor;
    const link = make("span", "server-anchor"); link.id = anchor; section.prepend(link);
  }
  mark("#admin-access-settings");
  $("#sync-and-devices h2").textContent = t("Träff och Cloud");
  inlineForm("#cloud-auto-form", $("#sync-and-devices")); mark("#cloud-auto-edit");
  $("#cloud-auto-form > p").classList.add("legacy-internal");
  const identity = $("#server-identity-settings");
  inlineForm("#server-identity-form", identity);
  mark('[data-open-modal="server-identity-form-modal"]');
  const networkSettings = make("p", "server-network"); networkSettings.id = "server-network"; identity.append(networkSettings);
  const danger = $("#server-system-settings"); danger.prepend(make("h2", "", t("Farozon")));
  const appearance = $(".clock-control-card");
  appearance.querySelector("h2").textContent = t("Skärmar och klocka");
  appearance.querySelectorAll(".eyebrow, .compact-heading p, .modal-launch").forEach(n => n.classList.add("legacy-internal"));
  inlineForm("#clock-appearance-form", appearance);
  inlineForm("#connection-badge-form", appearance);
  const code = move("#connection-badge-code", appearance); code.classList.add("server-network");
  const workspace = make("a", "tm-btn", t("Byt arbetsyta")); workspace.href = "/#workspaces"; $("#language-settings").append(workspace);
  $("#users-invite-open").textContent = t("+ Bjud in");

  // Documentation is separate from the operator client. No legacy emulator is
  // started by merely visiting Help.
  const help = card("Hjälp", "help-view"); help.classList.add("view-panel", "hidden");
  const helpLinks = make("div", "server-actions");
  for (const [path, label] of [["/tmbox/", "Öppna TMBox"], ["/tmbox-lab/", "TMBox-provbänk"], ["/drift", "Tillbaka till driften"]]) {
    const a = make("a", "tm-btn", t(label)); a.href = path; helpLinks.append(a);
  }
  help.append(helpLinks);
  for (const [id, label] of [["#tmbox-pane-floden", "Flöden"], ["#tmbox-pane-skarmar", "Skärmkatalog"], ["#tmbox-pane-referens", "Referens"]]) {
    const content = $(id); content.classList.remove("hidden"); help.append(details(label, content));
  }
  $(".server-workspace").append(help);

  const api = { make, move, t, context: null, info: null, presentation: null };
  const territoryScreen = make("div", "sc-territories hidden"); territoryScreen.id = "territories-view"; $("#display-stage").append(territoryScreen);
  const territoryCheck = make("label"); const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.value = "territories";
  territoryCheck.append(checkbox, document.createTextNode(t("Områdestavla"))); $("#connection-badge-screens").append(territoryCheck);
  api.refreshHeader = () => {
    const context = api.context || {}, meet = context.selected_meet, update = context.cloud_update || {};
    const us = context.operating_region === "us"; document.body.dataset.region = us ? "us" : "eu";
    document.querySelectorAll("[data-eu-only]").forEach(n => { n.hidden = us; });
    document.querySelectorAll("[data-us-only]").forEach(n => { n.hidden = !us; });
    $("#server-region").textContent = meet ? (us ? "US" : "EU") : "";
    $("#server-region").className = `tm-badge tm-badge--${us ? "us" : "eu"}`;
    // A publication UUID is not a human version number.
    const ordinal = meet?.version_number ?? meet?.publication_version;
    const version = Number.isInteger(ordinal) ? `${t("Version")} ${ordinal}` : (meet ? t("Publicerad träff") : t("Ingen träff vald"));
    $("#header-server-meta").textContent = [version, api.info?.runtime?.server_name].filter(Boolean).join(" · ");
    const conflicts = !us && api.presentation?.findings?.filter(f => f.level === "conflict").length;
    const newer = Boolean(update.pending_publication_id || update.available_publication_id);
    const status = $("#header-cloud-status");
    const offline = update.linked && update.state === "error" && (!update.last_checked_at || Date.now() - Date.parse(update.last_checked_at) > 600000);
    status.textContent = newer ? t("Ny version finns i Cloud") : offline ? t("Cloud inte nådd") : version + (conflicts ? ` · ${conflicts} ${t("konflikter")}` : "");
    status.className = `tm-status tm-status--${newer ? "newer" : offline ? "offline" : "published"}`;
    $("#cloud-connection-meta").textContent = version;
    $("#drift-simulation").hidden = us;
    $("#device-management").hidden = us;
    $("#drift-traffic-grid").hidden = us;
    $(".overview-graph-card").hidden = us;
    $("#header-cloud-status").hidden = !meet;
  };
  api.refreshClock = clock => {
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
    const label = connection.host ? `${connection.host}:${connection.port}${connection.code ? ` · ${t("Kod")} ${connection.code}` : ""}` : t("Anslut klienten till denna server");
    $("#client-network").textContent = label; $("#server-network").textContent = label;
  };
  api.mode = mode => {
    $("#help-view").classList.toggle("hidden", mode !== "help");
    if (mode === "installningar" && location.hash) requestAnimationFrame(() => document.getElementById(location.hash.slice(1))?.scrollIntoView({block: "start"}));
  };
  api.stationRows = rows => {
    const host = $("#server-station-rows");
    const expanded = host.querySelector("details")?.open;
    host.replaceChildren();
    const table = make("table", "tm-table");
    const head = make("thead"), heading = make("tr");
    for (const label of ["Station", "Vänster", "Höger", "TMBox-visning"]) heading.append(make("th", "", t(label)));
    head.append(heading); table.append(head);
    const body = make("tbody"); table.append(body);
    const all = [...rows.children]; all.slice(0, 5).forEach(row => body.append(row)); host.append(table);
    if (all.length > 5) {
      const rest = make("table", "tm-table"), restBody = make("tbody"); rest.append(restBody);
      all.slice(5).forEach(row => restBody.append(row));
      const more = details(`${t("Visa alla stationer")} (${all.length})`, rest); more.open = expanded; host.append(more);
    }
  };
  api.traffic = (snapshot, station = "") => {
    const positions = (snapshot.train_positions || []).filter(p => !station || [p.station_id, p.from_station_id, p.to_station_id].includes(station));
    const moving = positions.filter(p => p.connection_id);
    const now = String(snapshot.clock?.time || "00:00").slice(0, 5);
    const upcoming = (snapshot.routes || []).filter(r => (!station || r.station_id === station) && (r.departure_time || r.arrival_time || "") >= now)
      .sort((a, b) => (a.departure_time || a.arrival_time).localeCompare(b.departure_time || b.arrival_time)).slice(0, 4);
    const delayed = moving.filter(p => (snapshot.routes || []).some(r => r.train_number === p.train_number && r.station_id === p.to_station_id && r.arrival_time && r.arrival_time < now));
    const stats = $("#drift-traffic-stats"); stats.replaceChildren();
    for (const [count, label] of [[moving.length, "På linjen"], [positions.filter(p => !p.connection_id && p.station_id).length, "På station"], [delayed.length, "Sena ankomster"]]) {
      const stat = make("div"); stat.append(make("strong", "", String(count)), make("span", "", t(label))); stats.append(stat);
    }
    const events = $("#drift-upcoming"); events.replaceChildren(make("strong", "", t("Kommande enligt tidtabell")));
    for (const row of upcoming) {
      const name = snapshot.stations?.find(s => s.id === row.station_id)?.name || row.station_id;
      const entry = make("div", "server-event"); entry.append(make("span", "", row.departure_time || row.arrival_time), make("b", "", row.train_number), make("span", "", `${name} · ${t(row.departure_time ? "Avgång" : "Ankomst")}`)); events.append(entry);
    }
    if (!upcoming.length) events.append(make("p", "", t("Inga fler planerade händelser idag.")));
  };
  api.initDisplay = () => {
    document.body.classList.add("server-display");
    const stage = $("#display-stage");
    const content = make("div", "sc-content");
    while (stage.firstChild) content.append(stage.firstChild);
    stage.append(content);
    const header = make("header", "sc-top"); header.id = "screen-header";
    const meet = make("div", "sc-top__meet"); meet.id = "screen-meet";
    const clock = make("div", "sc-top__clock"); clock.id = "screen-time";
    header.append(meet, clock);
    const footer = make("footer", "sc-foot"); footer.id = "screen-footer";
    move("#display-connection", footer);
    const status = make("span", ""); status.id = "screen-status"; footer.append(status);
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
    meet.replaceChildren(make("span", "", snapshot.meet?.name || "TrainMeet"), make("span", `sc-badge sc-badge--${us ? "us" : "eu"}`, us ? "US" : "EU"));
    const labels = {clock:"Träffklocka", topology:"Banöversikt", graph:"Tågdiagram", dashboard:"Översikt", territories:"Områdestavla"};
    if (kind !== "clock") meet.append(make("span", "sc-subtitle", t(labels[kind])));
    const hour = Number(time.slice(0,2));
    $("#screen-time").textContent = kind === "clock" ? "" : us ? `${hour%12||12}${time.slice(2,5)} ${hour>=12?"PM":"AM"}` : time.slice(0, 5);
    $("#screen-status").textContent = snapshot.server_name || "TrainMeet Server";
    $("#display-stage").dataset.kind = kind;
    api.lastDisplayContact = Date.now();
  };
  api.us = (data, screen = false) => {
    const host = screen ? $("#territories-view") : $("#us-runtime-summary");
    const signature = JSON.stringify(data || {});
    if (host.dataset.signature === signature) return;
    host.dataset.signature = signature; host.replaceChildren();
    if (!data) { host.append(make("p", "", t("Områdestavlan gäller en US-träff."))); return; }
    if (!screen) {
      const intro = make("div", "server-actions"); intro.append(make("h2", "", t("US · Trafikledning")));
      const link = make("a", "tm-btn tm-btn--primary", t("Öppna Dispatcher")); link.href = "/us/dispatcher"; intro.append(link); host.append(intro);
      host.append(make("p", "", t("Körtillstånd utfärdas och bekräftas av Dispatcher. Inga tillstånd skapas automatiskt.")));
    }
    const infra = make("section", "server-us-infra"); infra.append(make("h3", "", t("Territorier")));
    if (!data.active) infra.append(make("p", "", t("Ingen US-körning är aktiv. Starta körningen i Dispatcher.")));
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
      const box = make("section", "server-us-card"); box.append(make("h3", "", t(title)));
      const tbl = make("table", "tm-table"), head = make("tr"); headings.forEach(h=>head.append(make("th","",t(h))));
      const thead = make("thead"); thead.append(head); tbl.append(thead); const body = make("tbody");
      rows.forEach(values=>{const tr=make("tr"); values.forEach(v=>tr.append(make("td","",String(v ?? "—")))); body.append(tr);}); tbl.append(body); box.append(tbl);
      if (!rows.length) box.append(make("p", "", t("Inga aktuella poster."))); sections.append(box);
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
