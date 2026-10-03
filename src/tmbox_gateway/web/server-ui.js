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
  function card(title, id) {
    const node = make("section", "kr-panel kr-body kr-help"); node.id = id;
    node.append(authored("h2", "", title)); return node;
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

  // Documentation is separate from the operator client. No legacy emulator is
  // started by merely visiting Help.
  const help = card("Hjälp", "help-view"); help.classList.add("view-panel", "hidden");
  const helpLinks = make("div", "kr-help__links");
  for (const [path, label] of [["/tmbox/", "Öppna TMBox"], ["/tmbox-lab/floden", "TMBox-flöden"], ["/tmbox-lab/", "TMBox-provbänk"], ["/drift", "Tillbaka till driften"]]) {
    const a = authored("a", "kr-btn", label); a.href = path; helpLinks.append(a);
  }
  help.append(helpLinks);
  // The flows, screens and reference that used to follow here were drawn by
  // the ESP32 and V1 engines, which the boxes no longer run. /tmbox-lab/floden
  // is drawn by the 16x2 engine they do run.
  help.append(authored("p", "kr-c", "TMBox-flöden visar varje bild en box med 16 × 2-display får, steg för steg, direkt ur servern."));
  $(".server-workspace").append(help);

  const api = { make, move, t, context: null, info: null, presentation: null };
  const territoryScreen = make("div", "sc-territories hidden"); territoryScreen.id = "territories-view"; $("#display-stage").append(territoryScreen);
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
    const newer = Boolean(update.pending_publication_id || update.available_publication_id);
    const status = $("#header-cloud-status");
    const offline = update.linked && update.state === "error" && (!update.last_checked_at || Date.now() - Date.parse(update.last_checked_at) > 600000);
    status.textContent = newer ? t("Ny version finns i Cloud")
      : offline ? (hasVersion ? `${t("Cloud inte nådd")} · ${t("kör")} ${version.toLowerCase()}` : t("Cloud inte nådd"))
      : (hasVersion ? version : t("Publicerad"));
    status.className = `tm-status tm-status--${newer ? "newer" : offline ? "offline" : "published"}`;
    $("#cloud-connection-meta").textContent = version;
    const region = $("#cloud-meet-region");
    if (region) { region.textContent = meet ? (us ? "US" : "EU") : ""; region.className = `tm-badge tm-badge--${us ? "us" : "eu"}`; region.hidden = !meet; }
    const runtime = api.info?.runtime || {};
    if ($("#cloud-meet-meta")) $("#cloud-meet-meta").textContent = runtime.station_count ? t("{stations} stationer · {day}", { stations: runtime.station_count, day: runtime.active_day || "" }) : "";
    const published = runtime.published_at ? new Date(runtime.published_at) : null;
    if ($("#cloud-published-at")) $("#cloud-published-at").textContent = published && !Number.isNaN(published.getTime())
      ? t("publicerad {date}", { date: `${published.toLocaleDateString("sv-SE")} ${published.toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit" })}` }) : "";
    $("#header-cloud-status").hidden = !meet;
  };
  api.refreshClock = clock => {
    // Tiden och start/stopp ritas av Drift; här finns bara dialogen Justera klockan.
    $("#stop-local-clock").hidden = !clock.running;
    $("#stop-local-clock").disabled = clock.source === "fastclock" && !clock.can_control;
    for (const id of ["#local-clock-time", "#local-clock-speed", '#clock-control-form button[type="submit"]']) $(id).disabled = clock.source === "fastclock";
  };
  api.network = connection => {
    // Boxes find the server themselves and never use the pairing code; that
    // code is for the apps and TKL and lives under Settings.
    if ($("#server-network-line")) $("#server-network-line").textContent = connection.host ? `${connection.host}:${connection.port}` : "—";
  };
  api.mode = mode => {
    $("#help-view").classList.toggle("hidden", mode !== "help");
    for (const [id, page] of [["#header-settings", "installningar"], ["#header-help", "help"]]) {
      if (mode === page) $(id)?.setAttribute("aria-current", "page"); else $(id)?.removeAttribute("aria-current");
    }
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
    // Skärmen är en 1920 × 1080-duk som skalas in. I fönsterläge står
    // verktygsraden kvar ovanför duken, och duken får det som blir över.
    const resize = () => {
      const chrome = $("#display-app")?.dataset.chrome === "window" ? ($("#display-toolbar")?.offsetHeight || 0) : 0;
      const scale = Math.min(innerWidth / 1920, (innerHeight - chrome) / 1080);
      stage.style.transform = `translate(-50%, -50%) scale(${scale})`;
      stage.style.marginTop = `${chrome / 2}px`;
    };
    api.resizeStage = resize;
    window.addEventListener("resize", resize); resize();
  };
  api.display = (snapshot, kind, time, extra = {}) => {
    const meet = $("#screen-meet"); if (!meet) return;
    const us = snapshot.meet?.operating_region === "us";
    // The meet's name only: EU or US is setup detail, not something the hall needs.
    meet.replaceChildren(make("span", "", snapshot.meet?.name || "TrainMeet"));
    const labels = {clock:"Träffklocka", topology:"Banöversikt", graph:"Tågdiagram", dashboard:"Översikt", territories:"Områdestavla"};
    // The name of the screen, and on the map and the diagram what the hall
    // looks for first: how many trains are out on the line right now.
    const out = (snapshot.train_positions || []).filter(p => p.connection_id).length;
    const live = ["topology", "graph"].includes(kind) ? ` · ${t(out === 1 ? "1 tåg på linjen" : "{n} tåg på linjen", { n: out })}` : "";
    const range = kind === "graph" && extra.range ? ` · ${extra.range}` : "";
    if (kind !== "clock") meet.append(make("span", "sc-subtitle", t(labels[kind]) + range + live));
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
    if (kind === "topology") host.replaceChildren(authored("span", "", "Fylld bricka = på linjen, pilen är riktningen · ofylld = klart, inte avgått · siffran = tåg inne"));
    else if (kind === "dashboard") host.replaceChildren(authored("span", "", "Siffran vid stationen = tåg inne"));
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
    // Som i designen: siffran (1 Wi-Fi, 2 Träffen) över namnet, bredvid koden.
    const item = (payload, number, name) => {
      const wrap = make("div", "sc-qr__item");
      const code = make("div", "sc-qr__code");
      code.innerHTML = api.qrSVG(payload);
      const caption = make("span", "sc-qr__text");
      if (number) caption.append(make("b", "", number));
      caption.append(authored("span", "", name));
      wrap.append(code, caption);
      return wrap;
    };
    host.replaceChildren(...(network ? [item(network, "1", "Wi-Fi"), item(link, "2", "Träffen")] : [item(link, "", "Skanna – allt om träffen")]));
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
