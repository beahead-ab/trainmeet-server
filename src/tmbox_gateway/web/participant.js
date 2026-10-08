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

  // ── Verkliga tider och förseningar, som i SJ:s app ────────────────────
  const model = globalThis.TrainMeetDriftModel;
  const FLASH_MS = 2000, RECENT_MS = 30000;
  let eventFresh = model.changeTracker(), timetableFresh = model.changeTracker();
  const clockOf = (minute) => {
    if (minute === null || minute === undefined) return null;
    const value = ((Math.round(minute) % 1440) + 1440) % 1440;
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  };
  /** Planerad tid, eller den överstruken med den nya bredvid. */
  const timeCell = (planned, changed, kind = "t") => changed && changed !== planned
    ? `<span class="${kind}"><span class="tm-was">${escapeHTML(planned)}</span><span class="tm-new tm-flip">${escapeHTML(changed)}</span></span>`
    : `<span class="${kind}">${escapeHTML(planned)}</span>`;
  // Hur mycket som visas: deltagarens eget val (Visning), annars träffens förval.
  // Att byta nivå är ingen ändring i trafiken: minnet börjar om, inget lyser upp.
  let trackedLevel = null;
  const level = () => {
    const value = deviationLevelOf(snapshot);
    if (trackedLevel !== null && value !== trackedLevel) { eventFresh = model.changeTracker(); timetableFresh = model.changeTracker(); }
    trackedLevel = value;
    return value;
  };
  /** Var tåget är nu, i stället för bara tid kvar enligt tidtabellen. */
  function statusCell(row, live, view) {
    if (!live || live.state === "not_departed") return `<span class="in">${escapeHTML(relative(row.departure))}</span>`;
    const mark = deviationMarkHTML(view);
    const text = live.state === "on_line" ? t("På väg mot {station}", { station: stationNameOf(live.to) })
      : live.state === "arrived" ? t("Ankom {time}", { time: clockOf(live.arrivedAt) || row.arrival })
      : live.state === "waiting" ? t("Väntar i {station}", { station: stationNameOf(live.station) })
      : t("Vid {station}", { station: stationNameOf(live.station) });
    return `<span class="in pv-status">${escapeHTML(text)}${mark ? ` ${mark}` : ""}</span>`;
  }
  /** En rad som ändrats sedan förra uppdateringen lyser upp kort och får "Nyss". */
  function markFresh(row, tracker, key, signature, view) {
    if (!row || signature === null) return;
    const changedAt = tracker.note(key, signature, Date.now());
    if (changedAt === null || !view.flash) return;
    const age = Date.now() - changedAt;
    if (age < FLASH_MS) { row.classList.add("is-updated"); row.style.animationDelay = `-${age}ms`; }
    if (age < RECENT_MS) row.querySelector(".w")?.insertAdjacentHTML("beforeend", `<span class="tm-recent">${escapeHTML(t("Nyss"))}</span>`);
  }

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
    // Uppladdade klockor (klockpaket) ritas som bilder av clock-face.js.
    globalThis.TrainMeetClockFace?.remember(clock.faces);
    const style = clock.style || "digital";
    const digital = style === "digital";
    const showSeconds = clock.show_seconds !== false;
    const stopped = !clock.running;
    const us = snapshot.meet?.operating_region === "us";
    const meta = [meetDayLabel(snapshot), stopped && clock.stopped_reason ? clock.stopped_reason : clock.source === "fastclock" ? "FastClock" : t("intern klocka")].filter(Boolean).join(" · ");
    const status = stopped ? t("Klockan stoppad") : `${t("Klockan går")} · ${Number(clock.speed || 1)}×`;
    const signature = [style, globalThis.TrainMeetClockFace?.find(style)?.sha256, document.documentElement.dataset.krTheme, showSeconds, stopped, status, meta, TrainMeetI18n.getLanguage()].join("|");
    if (target.dataset.signature !== signature) {
      target.dataset.signature = signature;
      const face = digital
        ? `<div class="pv-clock__time${stopped ? " is-stopped" : ""}"><span class="hm"></span><small></small></div>`
        : `<div class="pv-clock__face${stopped ? " is-stopped" : ""}">${clockSVG(style, true, showSeconds, stopped)}</div>`;
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
    // Varje ankomst och avgång som är kvar, i tidtabellens ordning. Det tåget
    // redan gjort är borta; ett sent tåg står kvar med sin nya tid.
    const lives = model.trainLive(snapshot, currentClockSeconds(snapshot));
    const shown = level();
    const upcoming = [];
    for (const service of model.services(snapshot)) {
      const number = String(service.train_number), live = lives.get(number);
      model.orderedStops(service).forEach((stop, index) => {
        if (selectedStation && stop.station_id !== selectedStation) return;
        const done = live?.stops[index];
        for (const [kind, planned] of [["arr", stop.arrival_time], ["dep", stop.departure_time]]) {
          if (minutes(planned) === null || (done && (kind === "arr" ? done.arrived : done.departed))) continue;
          // Ett sent tåg står kvar i listan med sin nya tid, vad nivån än visar.
          const delay = live?.late ? live.delayMinutes : 0;
          if (minutes(planned) + delay < now) continue;
          upcoming.push({ number, kind, planned, delay, live, stationId: stop.station_id,
            station: stop.station_name || stationNameOf(stop.station_id), state: live?.state || null });
        }
      });
    }
    upcoming.sort((a, b) => minutes(a.planned) - minutes(b.planned) || model.compare(a.number, b.number));
    const events = $("#pv-events");
    events.replaceChildren();
    if (!upcoming.length) events.append(Object.assign(document.createElement("p"), { className: "pv-empty", textContent: t("Inga fler planerade händelser idag.") }));
    for (const event of upcoming.slice(0, 4)) {
      const what = event.kind === "dep" ? t("avgår {station}", { station: event.station }) : t("ankommer {station}", { station: event.station });
      const view = model.deviationView(shown, event.live ? { ...event.live, delayMinutes: event.delay } : null);
      const delta = minutes(event.planned) + event.delay - now;
      const changed = view.strike && event.delay ? clockOf(minutes(event.planned) + event.delay) : null;
      const quiet = view.mark?.style === "text" ? ` ${deviationMarkHTML(view)}` : "";
      // Hollow: the train stands at the station and is about to leave; filled: on its way in.
      events.insertAdjacentHTML("beforeend", html`<div class="pv-item pv-item--event${view.mark ? ` is-${view.mark.tone}` : ""}">${timeCell(event.planned, changed)}<span class="pv-badge-train${event.kind === "dep" ? " is-hollow" : ""}">${escapeHTML(event.number)}</span><span class="w">${escapeHTML(what)}${quiet}</span><span class="in">${view.mark?.style === "pill" ? deviationMarkHTML(view) : escapeHTML(delta <= 0 ? t("nu") : t("{n} min", { n: delta }))}</span></div>`);
      markFresh(events.lastElementChild, eventFresh, `${event.number}|${event.kind}|${event.stationId}|${event.planned}`,
        [event.state, view.mark?.text || "", view.estimated], view);
    }
  }

  function renderTimetable() {
    const now = nowMinutes();
    const lives = model.trainLive(snapshot, currentClockSeconds(snapshot));
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
    // Kvar att se: tåg som inte är framme, och de som ska komma senare.
    const upcoming = matches.filter((row) => { const live = lives.get(row.number); return live ? live.state !== "arrived" && (live.state !== "not_departed" || minutes(row.arrival || row.departure) >= now) : minutes(row.arrival || row.departure) >= now; });
    const rows = fullTimetable || needle ? matches : (upcoming.length ? upcoming.slice(0, 4) : matches.slice(-4));
    $("#pv-timetable-count").textContent = `${t(services.length === 1 ? "1 tåg" : "{count} tåg", { count: services.length })} · ${meetDayLabel(snapshot)}`;
    const list = $("#pv-timetable");
    list.replaceChildren();
    if (!rows.length) list.append(Object.assign(document.createElement("p"), { className: "pv-empty", textContent: needle ? t("Inget tåg matchar sökningen.") : t("Ingen tidtabell för dagen.") }));
    const shown = level();
    for (const row of rows) {
      const live = lives.get(row.number);
      const view = model.deviationView(shown, live);
      const first = live?.stops[0], last = live?.stops.at(-1);
      // Avgången: den verkliga om den var sen eller för tidig; ankomsten: den verkliga eller den nya.
      const actualFirst = first?.actualDeparture !== null && first?.actualDeparture !== undefined;
      const departure = view.strike && actualFirst && !live.estimated ? clockOf(first.actualDeparture) : null;
      const arrival = view.strike && view.mark?.tone === "late" && last ? clockOf(last.expectedArrival ?? last.actualArrival)
        : view.strike && last?.actualArrival !== null && last?.actualArrival !== undefined ? clockOf(last.actualArrival) : null;
      list.insertAdjacentHTML("beforeend", html`<div class="pv-item pv-item--line${view.mark ? ` is-${view.mark.tone}` : ""}"><span class="no">${escapeHTML(row.number)}</span><span class="w">${escapeHTML(row.from)} ${timeCell(row.departure, departure, "sub")} → ${escapeHTML(row.to)} ${timeCell(row.arrival, arrival, "sub")}</span>${statusCell(row, live, view)}</div>`);
      markFresh(list.lastElementChild, timetableFresh, row.number,
        live ? [live.state, live.station, live.to, view.mark?.text || "", view.estimated, live.arrivedAt] : null, view);
    }
    const toggle = $("#pv-timetable-toggle");
    $("#pv-timetable-toggle").closest(".pv-foot-row").hidden = toggle.hidden = Boolean(needle) || matches.length <= rows.length && !fullTimetable;
    label(toggle, fullTimetable ? "Visa bara de närmaste ↑" : "Hela tidtabellen ↓");
  }

  // Visning: deltagarens eget val av hur mycket förseningar som syns. Tomt
  // betyder träffens förval; valet sparas bara i den här webbläsaren.
  function renderLevels() {
    const host = $("#pv-view-levels");
    if (!host) return;
    const own = deviationOwnLevel(DEVIATION_LEVEL_KEY);
    const meet = model.deviationLevel(snapshot);
    const choice = (value, title, hint) => {
      const row = document.createElement("label");
      row.className = "pv-level";
      const input = Object.assign(document.createElement("input"), { type: "radio", name: "pv-level", value, checked: own === value });
      input.addEventListener("change", () => {
        try { if (value) localStorage.setItem(DEVIATION_LEVEL_KEY, value); else localStorage.removeItem(DEVIATION_LEVEL_KEY); } catch { /* privat läge */ }
        if (snapshot) { renderTrack(); renderTimetable(); }
      });
      const name = Object.assign(document.createElement("b"), { textContent: title });
      const note = Object.assign(document.createElement("span"), { textContent: hint });
      row.append(input, name, note);
      return row;
    };
    host.replaceChildren(choice("", t("Som träffen: {level}", { level: `${meet} · ${t(DEVIATION_LEVEL_NAMES[meet])}` }), t("Trafikledningens förval")),
      ...model.DEVIATION_LEVELS.map((level) => choice(String(level), `${level} · ${t(DEVIATION_LEVEL_NAMES[level])}`, t(DEVIATION_LEVEL_HINTS[level]))));
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

  // Listorna räknas om var tionde sekund medan klockan går: "om N min" och
  // ett tåg som står kvar och blir allt senare, även utan ny hämtning.
  let lastLists = 0;
  function tick() {
    if (!active) return;
    renderClockCard();
    const now = performance.now();
    if (snapshot?.clock?.running && snapshot.meet?.operating_region !== "us" && now - lastLists > 10000) {
      lastLists = now;
      renderTrack();
      renderTimetable();
    }
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
      const viewSheet = $("#pv-view-card");
      $("#pv-view-open")?.addEventListener("click", () => { renderLevels(); if (typeof viewSheet.showModal === "function") viewSheet.showModal(); else viewSheet.setAttribute("open", ""); });
      $("#pv-view-close")?.addEventListener("click", () => viewSheet.close());
      viewSheet?.addEventListener("click", (event) => { if (event.target === viewSheet) viewSheet.close(); });
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
