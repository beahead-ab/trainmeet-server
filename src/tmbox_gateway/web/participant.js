/* Deltagarvyn (SPEC A4): the server's start page, what the QR code on the
 * screens leads to. No login, nothing to change. Reads the same public
 * snapshot as the screens (/v1/display) and reuses app.js's clock model,
 * clock faces and track map. Loaded after app.js; app.js opens it for "/". */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const label = (node, source) => { node.dataset.tmText = source; node.textContent = t(source); };
  let snapshot = null;
  let pollTimer = null;
  let frameTimer = null;
  let request = null;
  let active = false;
  let fullTimetable = false;
  let query = "";
  let selectedStation = null;
  let bound = false;

  const minutes = (time) => {
    if (!time) return null;
    const [hour, minute] = String(time).split(":").map(Number);
    return Number.isFinite(hour) && Number.isFinite(minute) ? hour * 60 + minute : null;
  };
  const stationNameOf = (id) => snapshot?.stations?.find((station) => station.id === id)?.name || id || "—";
  const nowMinutes = () => Math.floor(currentClockSeconds(snapshot) / 60);
  const relative = (time) => {
    if (minutes(time) === null) return "";
    const delta = minutes(time) - nowMinutes();
    if (delta < 0) return t("gick {n} min sedan", { n: -delta });
    if (delta === 0) return t("nu");
    return t("om {n} min", { n: delta });
  };

  function renderTop() {
    const meet = snapshot.meet || {};
    const us = meet.operating_region === "us";
    $("#pv-meet-name").textContent = meet.name || "TrainMeet Server";
    $("#pv-region").textContent = us ? "US · TWC" : String(meet.country || "se").toUpperCase();
    const authenticated = typeof state !== "undefined" && Boolean(state?.authStatus?.authenticated);
    const link = $("#pv-login");
    link.href = authenticated ? "/drift" : "/login";
    label(link, authenticated ? "Till driften →" : "Logga in");
    $("#pv-foot-meta").textContent = [snapshot.meet?.version_number ? `${t("Version")} ${snapshot.meet.version_number}` : "", snapshot.server_name, "TrainMeet Server"].filter(Boolean).join(" · ");
    document.querySelectorAll("[data-pv-eu]").forEach((node) => { node.hidden = us; });
    document.querySelectorAll("[data-pv-us]").forEach((node) => { node.hidden = !us; });
  }

  // One clock, in the style the meet has chosen – as on the screens: hours and
  // minutes large, the seconds (when they are on) small beside them. The faces
  // are app.js's own.
  function renderClockCard() {
    const target = $("#pv-clock");
    if (!target || !snapshot) return;
    const clock = snapshot.clock || {};
    const style = clock.style || "digital";
    const digital = style === "digital";
    const showSeconds = clock.show_seconds !== false;
    const stopped = !clock.running;
    const us = snapshot.meet?.operating_region === "us";
    const meta = [snapshot.active_day, stopped && clock.stopped_reason ? clock.stopped_reason : clock.source === "fastclock" ? "FastClock" : t("intern klocka")].filter(Boolean).join(" · ");
    const status = stopped ? t("Klockan stoppad") : `${t("Klockan går")} · ${Number(clock.speed || 1)}×`;
    const signature = [style, showSeconds, stopped, status, meta, TrainMeetI18n.getLanguage()].join("|");
    if (target.dataset.signature !== signature) {
      target.dataset.signature = signature;
      const face = digital
        ? `<div class="pv-clock__time${stopped ? " is-stopped" : ""}"><span class="hm"></span><small></small></div>`
        : `<div class="pv-clock__face${stopped ? " is-stopped" : ""}">${clockSVG(style, style !== "stationsur", showSeconds, stopped)}</div>`;
      target.innerHTML = `${face}<div class="pv-clock__side"><div class="pv-clock__status${stopped ? " is-stopped" : ""}">${escapeHTML(status)}</div><div class="pv-clock__meta">${escapeHTML(meta)}</div></div>`;
    }
    if (digital) {
      const parts = clockDigitParts(currentClockTime(snapshot), us, showSeconds);
      const small = [parts.ss ? `:${parts.ss}` : "", parts.ap].filter(Boolean).join(" ");
      const hm = target.querySelector(".hm"), tail = target.querySelector("small");
      if (hm && hm.textContent !== parts.hm) hm.textContent = parts.hm;
      if (tail && tail.textContent !== small) tail.textContent = small;
    } else {
      updateAnalogClockHands(target, currentClockSeconds(snapshot), style, !stopped);
    }
  }

  // The map is drawn by app.js with the same options as Drift, in real pixels
  // on the width of its panel: on a phone held upright the line stands upright
  // with each station's name and the number of trains inside, and on a computer
  // it runs across its column. Nothing has to be dragged; the hint under the map only
  // shows if a drawing is ever wider than its panel.
  function drawMap(svg) {
    const host = svg.closest(".pv-map");
    const available = Math.max(280, (host?.clientWidth || 0) - 20);
    renderTopology(snapshot, svg, { kr: { width: available }, showBadge: false, selectedStationID: selectedStation,
      onStationSelect: id => { selectedStation = selectedStation === id ? null : id; renderTrack(); renderTimetable(); },
      onClear: () => { selectedStation = null; renderTrack(); renderTimetable(); } });
    const [, , width, height] = svg.getAttribute("viewBox").split(" ").map(Number);
    svg.style.width = `${width}px`;
    svg.style.height = `${height}px`;
    $("#pv-map-hint").hidden = Boolean(selectedStation) || width <= available + 1;
  }

  // A new window size redraws the map for its new column.
  let resizeTimer = null;
  addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (snapshot) drawMap($("#pv-topology")); }, 150);
  });

  function renderTrack() {
    const positions = snapshot.train_positions || [];
    const moving = positions.filter((position) => position.connection_id);
    const now = nowMinutes();
    const late = globalThis.TrainMeetDriftModel?.lateTrains?.(snapshot).length || 0;
    $("#pv-stat-line").textContent = String(moving.length);
    $("#pv-stat-station").textContent = String(positions.filter((position) => position.station_id && !position.connection_id).length);
    $("#pv-stat-dev").textContent = String(late);
    $("#pv-stat-dev-chip").classList.toggle("is-late", late > 0);
    drawMap($("#pv-topology"));
    $("#pv-clear-station").hidden = !selectedStation;
    const scope = $("#pv-events-scope");
    scope.dataset.tmText = selectedStation ? "" : "hela banan";
    scope.textContent = selectedStation ? stationNameOf(selectedStation) : t("hela banan");
    const upcoming = (snapshot.routes || [])
      .filter(route => !selectedStation || route.station_id === selectedStation)
      .flatMap(route => ["arrival_time", "departure_time"].filter(key => route[key]).map(key => ({...route, eventTime: route[key], departure: key === "departure_time"})))
      .filter((route) => minutes(route.eventTime) >= now)
      .sort((a, b) => minutes(a.eventTime) - minutes(b.eventTime))
      .slice(0, 4);
    const events = $("#pv-events");
    events.replaceChildren();
    if (!upcoming.length) events.append(Object.assign(document.createElement("p"), { className: "pv-empty", textContent: t("Inga fler planerade händelser idag.") }));
    for (const route of upcoming) {
      const station = route.station_name || stationNameOf(route.station_id);
      const what = route.departure ? t("avgår {station}", { station }) : t("ankommer {station}", { station });
      const delta = minutes(route.eventTime) - now;
      // Hollow: the train stands at the station and is about to leave; filled: on its way in.
      events.insertAdjacentHTML("beforeend", html`<div class="pv-item pv-item--event"><span class="t">${escapeHTML(route.eventTime)}</span><span class="pv-badge-train${route.departure ? " is-hollow" : ""}">${escapeHTML(route.train_number)}</span><span>${escapeHTML(what)}</span><span class="in">${escapeHTML(delta <= 0 ? t("nu") : t("{n} min", { n: delta }))}</span></div>`);
    }
  }

  function renderTimetable() {
    const now = nowMinutes();
    const out = new Set((snapshot.train_positions || []).filter((position) => position.connection_id).map((position) => String(position.train_number)));
    const needle = query.trim().toLowerCase();
    const services = (snapshot.services || []).filter(service => !selectedStation || (service.stops || []).some(stop => stop.station_id === selectedStation)).map((service) => {
      const stops = service.stops || [];
      const first = stops[0], last = stops[stops.length - 1];
      return { number: String(service.train_number), search: stops.map(stop => stop.station_name || stationNameOf(stop.station_id)).join(" ").toLowerCase(), from: first?.station_name || stationNameOf(first?.station_id), to: last?.station_name || stationNameOf(last?.station_id),
               departure: first?.departure_time || first?.arrival_time || "", arrival: last?.arrival_time || last?.departure_time || "" };
    }).filter((row) => row.departure)
      .sort((a, b) => minutes(a.departure) - minutes(b.departure));
    const matches = needle
      ? services.filter((row) => row.number.toLowerCase().includes(needle) || row.search.includes(needle))
      : services;
    const upcoming = matches.filter((row) => minutes(row.arrival || row.departure) >= now);
    const rows = fullTimetable || needle ? matches : (upcoming.length ? upcoming.slice(0, 4) : matches.slice(-4));
    $("#pv-timetable-count").textContent = `${t(services.length === 1 ? "1 tåg" : "{count} tåg", { count: services.length })} · ${snapshot.active_day || ""}`;
    const list = $("#pv-timetable");
    list.replaceChildren();
    if (!rows.length) list.append(Object.assign(document.createElement("p"), { className: "pv-empty", textContent: needle ? t("Inget tåg matchar sökningen.") : t("Ingen tidtabell för dagen.") }));
    for (const row of rows) {
      const tag = out.has(row.number) ? `<span class="pv-tag-out">${t("ute")}</span>` : `<span class="in">${escapeHTML(relative(row.departure))}</span>`;
      list.insertAdjacentHTML("beforeend", html`<div class="pv-item pv-item--line"><span class="no">${escapeHTML(row.number)}</span><span>${escapeHTML(row.from)} <span class="sub">${escapeHTML(row.departure)}</span> → ${escapeHTML(row.to)} <span class="sub">${escapeHTML(row.arrival)}</span></span>${tag}</div>`);
    }
    const toggle = $("#pv-timetable-toggle");
    $("#pv-timetable-toggle").closest(".pv-foot-row").hidden = toggle.hidden = Boolean(needle) || matches.length <= rows.length && !fullTimetable;
    label(toggle, fullTimetable ? "Visa bara de närmaste ↑" : "Hela tidtabellen ↓");
  }

  function renderConnect() {
    const connection = snapshot.connection || {};
    const wifi = connection.wifi || {};
    const name = wifi.name || "";
    const wifiHost = $("#pv-wifi");
    wifiHost.replaceChildren();
    if (name) {
      wifiHost.insertAdjacentHTML("beforeend", html`<span class="pv-value">${escapeHTML(name)}</span>${wifi.password ? html`<span class="pv-value">${escapeHTML(wifi.password)}</span>` : ""}`);
      // No QR here: whoever reads this page is already on the network, and a
      // TMBox cannot scan. The Wi-Fi QR belongs on the screens.
      label($("#pv-wifi-note"), wifi.password ? "Nätverk och lösenord, som boxen frågar efter." : wifi.has_password ? "Fråga trafikledningen om lösenordet till träffens Wi-Fi." : "Öppet nätverk – inget lösenord behövs.");
    } else {
      wifiHost.insertAdjacentHTML("beforeend", html`<span class="pv-value pv-value--muted">${t("Fråga trafikledningen om träffens Wi-Fi")}</span>`);
      label($("#pv-wifi-note"), "Nätverket är inte inskrivet på servern ännu.");
    }
    const ttl = Number(connection.web_client_ttl_minutes || 30);
    label($("#pv-virtual-ttl"), "En inaktiv virtuell TMBox utan station tas bort efter {n} minuter.");
    $("#pv-virtual-ttl").textContent = t("En inaktiv virtuell TMBox utan station tas bort efter {n} minuter.", { n: ttl });
  }

  function render() {
    if (!snapshot) return;
    renderTop();
    renderClockCard();
    if (snapshot.meet?.operating_region !== "us") {
      renderTrack();
      renderTimetable();
    }
    renderConnect();
    $("#pv-live").classList.remove("is-offline");
  }

  async function poll() {
    clearTimeout(pollTimer);
    request?.abort();
    const current = new AbortController();
    request = current;
    const deadline = setTimeout(() => current.abort(), 5000);
    try {
      const response = await fetch("/v1/display", { cache: "no-store", signal: current.signal });
      if (!response.ok) throw new Error("no snapshot");
      const payload = await response.json();
      if (request !== current) return;
      syncDisplayClock(payload, performance.now());
      snapshot = payload;
      render();
    } catch {
      if (request === current) $("#pv-live").classList.add("is-offline");
    } finally {
      clearTimeout(deadline);
      if (request === current) { request = null; schedulePoll(); }
    }
  }

  // With the stream up a change arrives at once; the timer is a fallback.
  function schedulePoll() {
    clearTimeout(pollTimer);
    if (active) pollTimer = setTimeout(poll, globalThis.TrainMeetLive?.connected ? 30000 : 5000);
  }

  function tick() {
    if (!active) return;
    renderClockCard();
    frameTimer = requestAnimationFrame(tick);
  }

  // Mörkt är grundläget i Kontrollrummet; deltagarvyn har ingen väljare och
  // följer därför enhetens ljus eller mörker så länge ingen har valt själv.
  // Lämnar man vyn (till Drift eller inloggningen) gäller det sparade valet igen.
  function applyTheme(own) {
    let stored = null;
    try { stored = localStorage.getItem("trainmeet.theme"); } catch { /* privat läge */ }
    const light = stored === "light" || (own && stored === null && matchMedia("(prefers-color-scheme: light)").matches);
    document.documentElement.dataset.krTheme = light ? "light" : "dark";
  }

  function start() {
    if (active) return;
    active = true;
    applyTheme(true);
    document.body.classList.add("participant-mode");
    $("#participant-view").classList.remove("hidden");
    if (!bound) {
      bound = true;
      // Annotate only authored, empty markup once, before any meet data arrives.
      TrainMeetI18n.annotate($("#participant-view"));
      $("#pv-timetable-search")?.addEventListener("input", (event) => { query = event.target.value; if (snapshot) renderTimetable(); });
      $("#pv-timetable-toggle")?.addEventListener("click", () => { fullTimetable = !fullTimetable; if (snapshot) renderTimetable(); });
      const sheet = $("#pv-connect-card");
      $("#pv-connect-open")?.addEventListener("click", () => { if (typeof sheet.showModal === "function") sheet.showModal(); else sheet.setAttribute("open", ""); });
      $("#pv-connect-close")?.addEventListener("click", () => sheet.close());
      sheet?.addEventListener("click", (event) => { if (event.target === sheet) sheet.close(); });
      $("#pv-clear-station")?.addEventListener("click", () => { selectedStation = null; if (snapshot) { renderTrack(); renderTimetable(); } });
      document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && active) poll(); });
      globalThis.TrainMeetLive?.subscribe((topics) => {
        if (active && ["traffic", "clock", "runtime", "simulation"].some((name) => topics.has(name))) poll();
      });
      // The stream came up or went down: the waiting timer takes the new pace.
      globalThis.TrainMeetLive?.onStatus(() => { if (!request) schedulePoll(); });
    }
    poll();
    frameTimer = requestAnimationFrame(tick);
  }

  function stop() {
    applyTheme(false);
    if (!active) return;
    active = false;
    clearTimeout(pollTimer); cancelAnimationFrame(frameTimer); request?.abort(); request = null;
    document.body.classList.remove("participant-mode");
    $("#participant-view").classList.add("hidden");
  }

  globalThis.TrainMeetParticipant = { start, stop, refresh: () => { if (active) poll(); } };
})();
