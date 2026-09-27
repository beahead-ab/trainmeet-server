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
    $("#pv-region").textContent = us ? "US · TWC" : "EU";
    $("#pv-region").className = `tm-badge tm-badge--${us ? "us" : "eu"}`;
    $("#pv-day").textContent = snapshot.active_day || "";
    const authenticated = typeof state !== "undefined" && Boolean(state?.authStatus?.authenticated);
    for (const link of [$("#pv-login"), $("#pv-foot-login")]) {
      link.href = authenticated ? "/drift" : "/login";
      label(link, authenticated ? "Till driften →" : "Logga in");
    }
    $("#pv-foot-meta").textContent = [snapshot.meet?.version_number ? `${t("Version")} ${snapshot.meet.version_number}` : "", snapshot.server_name, "TrainMeet Server"].filter(Boolean).join(" · ");
    document.querySelectorAll("[data-pv-eu]").forEach((node) => { node.hidden = us; });
    document.querySelectorAll("[data-pv-us]").forEach((node) => { node.hidden = !us; });
  }

  // One clock, in the style chosen under ⚙ – as on the screens. Faces are
  // app.js's own (the Swiss one is the existing code, untouched).
  function renderClockCard() {
    const target = $("#pv-clock");
    if (!target || !snapshot) return;
    const clock = snapshot.clock || {};
    const style = clock.style || "digital";
    const digital = style === "digital";
    const showSeconds = clock.show_seconds !== false;
    const stopped = !clock.running;
    const time = currentClockTime(snapshot);
    const us = snapshot.meet?.operating_region === "us";
    let displayTime = showSeconds ? time : time.slice(0, 5);
    if (us && /^\d\d:/.test(time)) { const hour = Number(time.slice(0, 2)); displayTime = `${hour % 12 || 12}${displayTime.slice(2)} ${hour >= 12 ? "PM" : "AM"}`; }
    const meta = `${snapshot.meet?.name || ""} · ${snapshot.active_day || ""} · ${clock.source === "fastclock" ? "FastClock" : t("intern klocka")}`;
    const status = stopped
      ? html`<div class="pv-clock__status is-stopped">${t("Klockan stoppad")}${clock.stopped_reason ? ` · ${escapeHTML(clock.stopped_reason)}` : ""}</div>`
      : html`<div class="pv-clock__status">${t("Klockan går")} · ${Number(clock.speed || 1)}×</div>`;
    const signature = [style, showSeconds, stopped, clock.stopped_reason, clock.speed, meta, TrainMeetI18n.getLanguage()].join("|");
    if (target.dataset.signature !== signature) {
      target.dataset.signature = signature;
      const face = digital
        ? html`<div class="pv-clock__digits${stopped ? " is-stopped" : ""}"></div>`
        : `<div class="pv-clock__face${stopped ? " is-stopped" : ""}">${clockSVG(style, style !== "stationsur", showSeconds, stopped)}</div>`;
      target.innerHTML = `${face}${status}<div class="pv-clock__meta">${escapeHTML(meta)}</div>`;
    }
    const digits = target.querySelector(".pv-clock__digits");
    if (digits && digits.textContent !== displayTime) digits.textContent = displayTime;
    if (!digital) updateAnalogClockHands(target, currentClockSeconds(snapshot), style, !stopped);
  }

  function renderTrack() {
    const positions = snapshot.train_positions || [];
    const moving = positions.filter((position) => position.connection_id);
    const visibleMoving = moving.filter(p => !selectedStation || [p.from_station_id, p.to_station_id].includes(selectedStation));
    const now = nowMinutes();
    const staffed = snapshot.staffed_station_count;
    const stations = snapshot.stations?.length || 0;
    $("#pv-stat-line").textContent = String(moving.length);
    $("#pv-stat-station").textContent = String(positions.filter((position) => position.station_id && !position.connection_id).length);
    $("#pv-stat-staffed").textContent = staffed == null ? String(stations) : `${staffed} / ${stations}`;
    label($("#pv-stat-staffed-label"), staffed == null ? "stationer" : "bemannade");
    const svg = $("#pv-topology");
    renderTopology(snapshot, svg, { showBadge: false, selectedStationID: selectedStation,
      onStationSelect: id => { selectedStation = selectedStation === id ? null : id; renderTrack(); renderTimetable(); },
      onClear: () => { selectedStation = null; renderTrack(); renderTimetable(); } });
    $("#pv-clear-station").hidden = !selectedStation;
    // Fit the drawing to its stations, not to the editor's padded canvas, so a
    // small layout is not a speck in the corner of the phone.
    try {
      const box = svg.getBBox();
      if (box.width > 0 && box.height > 0) {
        svg.setAttribute("viewBox", `${box.x - 24} ${box.y - 24} ${box.width + 48} ${box.height + 48}`);
        // Height is fixed; the width follows the layout's shape. Wider than the
        // phone means it scrolls sideways, as the hint under it says.
        const host = svg.closest(".pv-map");
        const width = Math.max(640, host?.clientWidth || 0, Math.round(((box.width + 48) / (box.height + 48)) * 220));
        svg.style.width = `${Math.min(width, 1400)}px`;
        svg.classList.toggle("is-wide", width > (host?.clientWidth || 0));
      }
    } catch {}
    const arrival = (position) => (snapshot.routes || []).find((route) => route.train_number === position.train_number && route.station_id === position.to_station_id)?.arrival_time;
    const line = $("#pv-on-line");
    line.replaceChildren();
    if (!visibleMoving.length) line.append(Object.assign(document.createElement("p"), { className: "pv-empty", textContent: t("Inget tåg är ute på linjen just nu.") }));
    for (const position of visibleMoving.slice(0, 6)) {
      line.insertAdjacentHTML("beforeend", html`<div class="pv-row-item pv-row-item--line"><b>${escapeHTML(position.train_number)}</b><span>${escapeHTML(stationNameOf(position.from_station_id))} → ${escapeHTML(stationNameOf(position.to_station_id))}</span><span class="m">${arrival(position) ? `${t("ank")} ${escapeHTML(arrival(position))}` : ""}</span></div>`);
    }
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
      events.insertAdjacentHTML("beforeend", html`<div class="pv-row-item pv-row-item--event"><span class="m mono">${escapeHTML(route.eventTime)}</span><b>${escapeHTML(route.train_number)}</b><span>${escapeHTML(route.station_name || stationNameOf(route.station_id))} · ${route.departure ? t("avgång") : t("ankomst")}</span></div>`);
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
    const rows = fullTimetable || needle ? matches : (upcoming.length ? upcoming.slice(0, 5) : matches.slice(-5));
    $("#pv-timetable-count").textContent = `${services.length} ${t("tåg")} · ${snapshot.active_day || ""}`;
    const list = $("#pv-timetable");
    list.replaceChildren();
    if (!rows.length) list.append(Object.assign(document.createElement("p"), { className: "pv-empty", textContent: needle ? t("Inget tåg matchar sökningen.") : t("Ingen tidtabell för dagen.") }));
    for (const row of rows) {
      const tag = out.has(row.number) ? `<span class="tm-tag tm-tag--info">${t("ute")}</span>` : `<span class="m">${escapeHTML(relative(row.departure))}</span>`;
      list.insertAdjacentHTML("beforeend", html`<div class="pv-row-item pv-row-item--line"><b>${escapeHTML(row.number)}</b><span>${escapeHTML(row.from)} ${escapeHTML(row.departure)} → ${escapeHTML(row.to)} ${escapeHTML(row.arrival)}</span>${tag}</div>`);
    }
    const toggle = $("#pv-timetable-toggle");
    toggle.hidden = Boolean(needle) || matches.length <= rows.length && !fullTimetable;
    label(toggle, fullTimetable ? "Visa bara de närmaste ↑" : "Visa hela tidtabellen ↓");
  }

  function renderConnect() {
    const connection = snapshot.connection || {};
    const wifi = connection.wifi || {};
    const name = wifi.name || "";
    const wifiHost = $("#pv-wifi");
    wifiHost.replaceChildren();
    if (name) {
      wifiHost.insertAdjacentHTML("beforeend", html`<span class="pv-value">${escapeHTML(name)}</span>${wifi.password ? html`<span class="pv-value">${escapeHTML(wifi.password)}</span>` : ""}`);
      label($("#pv-wifi-note"), wifi.password ? "Skanna Wi-Fi-koden för att ansluta till träffens nätverk." : wifi.has_password ? "Fråga trafikledningen om lösenordet till träffens Wi-Fi." : "Öppet nätverk – inget lösenord behövs.");
    } else {
      wifiHost.insertAdjacentHTML("beforeend", html`<span class="pv-value pv-value--muted">${t("Fråga trafikledningen om träffens Wi-Fi")}</span>`);
      label($("#pv-wifi-note"), "Nätverket är inte inskrivet på servern ännu.");
    }
    const qrHost = $("#pv-wifi-qr");
    const qrPayload = serverUI.wifiQR(wifi);
    qrHost.hidden = !qrPayload;
    if (qrPayload && qrHost.dataset.payload !== qrPayload) {
      qrHost.dataset.payload = qrPayload;
      qrHost.innerHTML = serverUI.qrSVG(qrPayload);
    }
    const address = connection.host ? `${connection.host}` : "";
    $("#pv-address").replaceChildren();
    $("#pv-address").insertAdjacentHTML("beforeend", address
      ? html`<span class="pv-value">${escapeHTML(address)}</span><span class="pv-value">${t("port")} ${escapeHTML(String(connection.port || ""))}</span>`
      : html`<span class="pv-value pv-value--muted">${t("Adressen visas när servern är på nätverket")}</span>`);
    const code = $("#pv-code");
    code.textContent = connection.code || "—";
    label($("#pv-code-note"), connection.code
      ? (connection.validity_hours ? "Koden gäller {n} timmar från att servern startades. Boxen dyker upp hos trafikledningen som ”väntar på station” och får sin station därifrån." : "Koden gäller tills vidare. Boxen dyker upp hos trafikledningen som ”väntar på station” och får sin station därifrån.")
      : "Ingen parningskod är utfärdad just nu.");
    if (connection.code && connection.validity_hours) $("#pv-code-note").textContent = t($("#pv-code-note").dataset.tmText, { n: connection.validity_hours });
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
      if (request === current) { request = null; if (active) pollTimer = setTimeout(poll, 5000); }
    }
  }

  function tick() {
    if (!active) return;
    renderClockCard();
    frameTimer = requestAnimationFrame(tick);
  }

  function start() {
    if (active) return;
    active = true;
    document.body.classList.add("participant-mode");
    $("#participant-view").classList.remove("hidden");
    if (!bound) {
      bound = true;
      // Annotate only authored, empty markup once, before any meet data arrives.
      TrainMeetI18n.annotate($("#participant-view"));
      $("#pv-timetable-search")?.addEventListener("input", (event) => { query = event.target.value; if (snapshot) renderTimetable(); });
      $("#pv-timetable-toggle")?.addEventListener("click", () => { fullTimetable = !fullTimetable; if (snapshot) renderTimetable(); });
      $("#pv-clear-station")?.addEventListener("click", () => { selectedStation = null; if (snapshot) { renderTrack(); renderTimetable(); } });
      document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && active) poll(); });
    }
    poll();
    frameTimer = requestAnimationFrame(tick);
  }

  function stop() {
    if (!active) return;
    active = false;
    clearTimeout(pollTimer); cancelAnimationFrame(frameTimer); request?.abort(); request = null;
    document.body.classList.remove("participant-mode");
    $("#participant-view").classList.add("hidden");
  }

  globalThis.TrainMeetParticipant = { start, stop, refresh: () => { if (active) poll(); } };
})();
