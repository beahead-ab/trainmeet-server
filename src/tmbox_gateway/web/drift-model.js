/* Drift · Kontrollrummet – siffrorna bakom vyn.
 *
 * Rena funktioner över serverns egen /v1/display-bild, boxlistan och Clouds
 * presentation. Ingenting här rör DOM:en, så samma regler körs i webbläsaren
 * och i tests/js. drift.js ritar; app.js hämtar och skickar kommandon.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.TrainMeetDriftModel = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const compare = (a, b) => String(a).localeCompare(String(b), "sv", { numeric: true });

  /** "05:12" eller "05:12:40" → minuter efter midnatt; null när det inte är en tid. */
  function minutes(value) {
    const match = /^(\d{1,2}):(\d{2})/.exec(String(value ?? ""));
    return match ? Number(match[1]) * 60 + Number(match[2]) : null;
  }

  /** "05:12:40" → "05:12"; tomt när det inte är en tid. */
  function hhmm(value) { return minutes(value) === null ? "" : String(value).slice(0, 5); }

  /** Ett tåg per tågnummer: det med flest stopp, sorterade som tal. */
  function services(snapshot) {
    const byNumber = new Map();
    for (const service of snapshot?.services || []) {
      const number = String(service.train_number || "").trim();
      if (!number) continue;
      const existing = byNumber.get(number);
      if (!existing || (service.stops?.length || 0) > (existing.stops?.length || 0)) byNumber.set(number, service);
    }
    return [...byNumber.values()].sort((a, b) => compare(a.train_number, b.train_number));
  }

  function orderedStops(service) {
    return [...(service?.stops || [])].sort((a, b) => Number(a.stop_order) - Number(b.stop_order));
  }

  function stationMap(snapshot) { return new Map((snapshot?.stations || []).map((station) => [station.id, station])); }

  /** Var tågen är, ur det /v1/display redan har: varje riktad kanal på en sträcka
   *  (reserverad = klartecken givet, upptagen = avgått), sedan de noterade
   *  lägena för tåg som inte står på en kanal. Äldre vägar noterar bara en
   *  sträckposition, vilket också räknas som på linjen. */
  function trains(snapshot) {
    const onLine = [], atStation = [], seen = new Set();
    for (const connection of snapshot?.connection_states || []) {
      for (const channel of connection.channels || []) {
        if (!channel.train_number || !["reserved", "occupied"].includes(channel.state)) continue;
        seen.add(String(channel.train_number));
        onLine.push({ trainNumber: String(channel.train_number), from: channel.from_station_id, to: channel.to_station_id, departed: channel.state === "occupied",
          departedSeconds: channel.departed_seconds ?? null });
      }
    }
    for (const position of snapshot?.train_positions || []) {
      const trainNumber = String(position.train_number);
      if (seen.has(trainNumber)) continue;
      if (position.status === "connection") onLine.push({ trainNumber, from: position.from_station_id, to: position.to_station_id, departed: true,
        departedSeconds: position.departed_seconds ?? null });
      else if (position.station_id) atStation.push({ trainNumber, station: position.station_id });
    }
    return { onLine, atStation };
  }

  /** tågnummer → { state: "on-line" | "cleared" | "at-station", from, to, station } */
  function trainStates(snapshot) {
    const result = new Map();
    const { onLine, atStation } = trains(snapshot);
    for (const train of onLine) result.set(train.trainNumber, { state: train.departed ? "on-line" : "cleared", from: train.from, to: train.to, departedSeconds: train.departedSeconds });
    for (const train of atStation) if (!result.has(train.trainNumber)) result.set(train.trainNumber, { state: "at-station", station: train.station });
    return result;
  }

  /** stations-id → tågnummer som står inne på stationen */
  function stationCounts(snapshot) {
    const result = new Map((snapshot?.stations || []).map((station) => [station.id, []]));
    for (const train of trains(snapshot).atStation) {
      if (!result.has(train.station)) result.set(train.station, []);
      result.get(train.station).push(train.trainNumber);
    }
    for (const list of result.values()) list.sort(compare);
    return result;
  }

  /** Tåg på linjen som är senare än tidtabellen (ankomsten har redan passerat). */
  function lateTrains(snapshot) {
    const now = hhmm(snapshot?.clock?.time) || "00:00";
    return trains(snapshot).onLine.filter((train) => train.departed && (snapshot?.routes || []).some((route) =>
      String(route.train_number) === train.trainNumber && route.station_id === train.to && route.arrival_time && hhmm(route.arrival_time) < now));
  }

  // ── Stationer och boxar ───────────────────────────────────────────────

  /** Vänster och höger på stationens TMBox: koderna på grannstationerna, som Cloud placerar dem. */
  function placement(presentation, stationId) {
    const entry = (presentation?.stations || []).find((station) => station.station_id === stationId);
    const side = (name) => (entry?.connections || []).filter((connection) => connection.side === name).map((connection) => connection.other_station_code);
    return { available: Boolean(presentation?.supported && entry?.connections?.length), left: side("left"), right: side("right") };
  }

  /** Anslutningens läge i klartext: online · ingen kontakt · offline. */
  function connectionTone(connection) {
    const state = connection?.state || "offline";
    return { online: "ok", lost: "warn" }[state] || "off";
  }

  /**
   * En rad per box och station, för tabellen Stationer och boxar.
   *   väntande boxar först (de har ingen station än),
   *   sedan varje station med sin box, eller en rad om stationen saknar box.
   * kind: waiting | box | simulated | unmanned
   */
  function stationRows({ snapshot, devices = [], presentation = null, simulation = null }) {
    const counts = stationCounts(snapshot);
    const simulated = new Map((simulation?.active ? simulation.stations || [] : []).map((station) => [station.id, station]));
    const rows = [];
    for (const device of devices.filter((item) => !item.station_id)) {
      rows.push({ key: `device:${device.device_id}`, kind: "waiting", station: null, device, tone: "warn", placement: null, trains: null });
    }
    for (const station of snapshot?.stations || []) {
      const here = devices.filter((device) => device.station_id === station.id);
      const base = { station, placement: placement(presentation, station.id), trains: (counts.get(station.id) || []).length };
      const sim = simulated.get(station.id);
      if (sim && sim.mode === "automatic") {
        rows.push({ ...base, key: `station:${station.id}`, kind: "simulated", device: null, tone: "sim", sim });
        continue;
      }
      if (!here.length) {
        rows.push({ ...base, key: `station:${station.id}`, kind: "unmanned", device: null, tone: sim?.mode === "disconnected" ? "warn" : "off", sim });
        continue;
      }
      for (const device of here) {
        rows.push({ ...base, key: `station:${station.id}:${device.device_id}`, kind: "box", device, tone: sim?.mode === "disconnected" ? "warn" : connectionTone(device.connection), sim });
      }
    }
    return rows;
  }

  /** Nyckeltalen över sidan: tåg på linjen, inne på station, bemannade, avvikelser. */
  function stats(snapshot, rows = [], simulation = null) {
    const { onLine, atStation } = trains(snapshot);
    const stations = (snapshot?.stations || []).length;
    const manned = new Set(rows.filter((row) => row.station && row.kind === "box" && row.tone === "ok").map((row) => row.station.id));
    const simulatedCount = rows.filter((row) => row.kind === "simulated").length;
    return {
      onLine: onLine.filter((train) => train.departed).length,
      cleared: onLine.filter((train) => !train.departed).length,
      atStations: atStation.length,
      manned: manned.size,
      stations,
      simulated: simulation?.active ? simulatedCount : 0,
      deviations: lateTrains(snapshot).length,
    };
  }

  // ── Nästa händelser ───────────────────────────────────────────────────

  /**
   * Händelserna framåt, med tre omfång:
   *   hela banan   – varje tågs nästa händelse (på linjen: ankomsten, annars avgången),
   *   en station   – det som sker på stationen, i tidsordning,
   *   ett tåg      – tågets återstående stopp.
   * delta = minuter från träffklockan; 0 eller mindre visas som "nu".
   */
  function events(snapshot, { station = null, train = null, limit = 8, nowSeconds = null } = {}) {
    const now = Number.isFinite(nowSeconds) ? Math.floor(nowSeconds / 60) : minutes(snapshot?.clock?.time) ?? 0;
    const names = stationMap(snapshot);
    const states = trainStates(snapshot);
    const listed = services(snapshot);
    const stopsOf = new Map(listed.map((service) => [String(service.train_number), orderedStops(service)]));
    const nextAfter = (number, stationId, step = 1) => {
      const stops = stopsOf.get(String(number)) || [];
      const index = stops.findIndex((stop) => stop.station_id === stationId);
      return index >= 0 ? stops[index + step]?.station_id || null : null;
    };
    const build = (number, stop, kind, at) => ({
      time: hhmm(at), minute: minutes(at), delta: minutes(at) - now, train: String(number), stationId: stop.station_id,
      station: names.get(stop.station_id)?.name || stop.station_id,
      kind, nextStationId: kind === "dep" ? nextAfter(number, stop.station_id) : null,
      previousStationId: kind === "arr" ? nextAfter(number, stop.station_id, -1) : null,
      state: states.get(String(number))?.state || null,
    });
    const rowsOf = (number) => stopsOf.get(String(number)) || [];
    const result = [];

    if (train) {
      for (const stop of rowsOf(train)) {
        const arrival = minutes(stop.arrival_time), departure = minutes(stop.departure_time);
        if (departure !== null && departure >= now) result.push(build(train, stop, "dep", stop.departure_time));
        else if (arrival !== null && arrival >= now) result.push(build(train, stop, "arr", stop.arrival_time));
      }
    } else if (station) {
      for (const service of listed) {
        for (const stop of orderedStops(service)) {
          if (stop.station_id !== station) continue;
          const arrival = minutes(stop.arrival_time), departure = minutes(stop.departure_time);
          if (arrival !== null && arrival >= now) result.push(build(service.train_number, stop, "arr", stop.arrival_time));
          if (departure !== null && departure >= now && departure !== arrival) result.push(build(service.train_number, stop, "dep", stop.departure_time));
        }
      }
    } else {
      for (const service of listed) {
        const number = String(service.train_number), stops = orderedStops(service), live = states.get(number);
        let event = null;
        if (live && live.state === "on-line") {
          const stop = stops.find((item) => item.station_id === live.to);
          const at = stop?.arrival_time || stop?.departure_time;
          if (stop && minutes(at) !== null) event = build(number, stop, stop.arrival_time ? "arr" : "dep", at);
        } else if (live) {
          const here = live.state === "cleared" ? live.from : live.station;
          const stop = stops.find((item) => item.station_id === here);
          const departs = stop?.departure_time;
          if (stop && minutes(departs) !== null) event = build(number, stop, "dep", departs);
        }
        if (!event && !live) {
          for (const stop of stops) {
            const at = stop.departure_time || stop.arrival_time;
            if (minutes(at) !== null && minutes(at) >= now) { event = build(number, stop, stop.departure_time ? "dep" : "arr", at); break; }
          }
        }
        if (event) result.push(event);
      }
    }
    // Förseningen ur trainLive: en händelse vid en senare station får samma
    // försening, den man står vid får den beräknade.
    const lives = trainLive(snapshot, Number.isFinite(nowSeconds) ? nowSeconds : now * 60);
    for (const event of result) {
      const live = lives.get(event.train);
      const delay = live && live.state !== "arrived" ? live.delayMinutes : 0;
      event.delayMinutes = delay;
      event.late = delay >= LATE_MINUTES;
      event.estimated = Boolean(live?.estimated);
      event.earlyMinutes = live?.earlyMinutes || 0;
      event.earlyKind = live?.earlyKind || null;
      event.trainType = live?.trainType || "person";
      event.expectedTime = event.late && event.minute !== null ? clock(event.minute + delay) : null;
      event.expectedDelta = event.late ? event.delta + delay : event.delta;
    }
    return result.sort((a, b) => a.minute - b.minute || compare(a.train, b.train)).slice(0, limit);
  }

  // ── Verkliga tider och förseningar ────────────────────────────────────

  /** Från hur många minuter en försening visas som en röd bricka (Casper 2026-10-08). */
  const LATE_MINUTES = 3;

  /** Minuter från a till b på ett dygn, alltid mellan −12 och +12 timmar. */
  const minutesBetween = (a, b) => ((b - a + 720) % 1440 + 1440) % 1440 - 720;
  const clock = (minute) => { const value = ((Math.round(minute) % 1440) + 1440) % 1440; return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`; };

  /** Rörelsen bakom ett stopp: raden i snapshot.trains med samma tjänst, station
   *  och tider, som serverns train_routes._visits. Tvetydigt ger ingen. */
  const movementIndexes = new WeakMap();
  function movementOf(snapshot, service, stop) {
    let index = movementIndexes.get(snapshot);
    if (!index) {
      index = new Map();
      for (const row of snapshot?.trains || []) {
        const key = `${row.service_id}|${row.station_id}|${hhmm(row.arrival_time)}|${hhmm(row.departure_time)}`;
        index.set(key, index.has(key) ? null : String(row.id));
      }
      if (snapshot && typeof snapshot === "object") movementIndexes.set(snapshot, index);
    }
    return index.get(`${service?.id}|${stop?.station_id}|${hhmm(stop?.arrival_time)}|${hhmm(stop?.departure_time)}`) || null;
  }

  /**
   * Var varje tåg är och hur sent, ur serverns movement_live och klockan.
   * tågnummer → { state, station, from, to, delayMinutes, late, estimated, stops }
   *
   *  - not_departed: inget har hänt och avgången är inte passerad
   *  - waiting: står kvar efter sin avgångstid (förseningen räknas upp, "beräknad")
   *  - on_line: avgått, inte framme
   *  - at_station: inne på en station längs vägen
   *  - arrived: framme vid sista stationen
   *
   * Försening = verklig tid − planerad, från den senaste händelsen med tid.
   * Lägen som bara tidtabellen gav har ingen tid: utan någon tid är tåget i tid.
   * Tiderna framåt får förseningen: "ny tid".
   */
  function trainLive(snapshot, nowSeconds) {
    const live = snapshot?.movement_live || {};
    const states = trainStates(snapshot);
    const now = Number.isFinite(nowSeconds) ? nowSeconds / 60 : minutes(snapshot?.clock?.time) ?? 0;
    const result = new Map();
    for (const service of services(snapshot)) {
      const number = String(service.train_number);
      const stops = orderedStops(service).map((stop) => {
        const offset = Number(stop.service_day_offset || 0) * 1440;
        const state = live[movementOf(snapshot, service, stop)] || null;
        const arrival = minutes(stop.arrival_time), departure = minutes(stop.departure_time);
        return { stationId: stop.station_id, stop, state,
          plannedArrival: arrival === null ? null : arrival + offset, plannedDeparture: departure === null ? null : departure + offset,
          arrived: state?.arrival === "arrived", departed: state?.departure === "departed",
          actualArrival: Number.isFinite(state?.arrived_seconds) ? state.arrived_seconds / 60 : null,
          actualDeparture: Number.isFinite(state?.departed_seconds) ? state.departed_seconds / 60 : null };
      });
      if (!stops.length) continue;
      // Den senaste händelsen med en verklig tid ger förseningen. Ett läge utan
      // tid (tidtabellens, eller "tåget hoppar fram") säger inget om den.
      let delay = 0, estimated = false, lastIndex = -1, measuredAt = null;
      stops.forEach((stop, index) => { if (stop.arrived || stop.departed) lastIndex = index; });
      for (let index = stops.length - 1; index >= 0; index -= 1) {
        const stop = stops[index];
        if (stop.departed && stop.actualDeparture !== null && stop.plannedDeparture !== null) { delay = minutesBetween(stop.plannedDeparture, stop.actualDeparture); measuredAt = "dep"; break; }
        if (stop.arrived && stop.actualArrival !== null && stop.plannedArrival !== null) { delay = minutesBetween(stop.plannedArrival, stop.actualArrival); measuredAt = "arr"; break; }
      }
      const channel = states.get(number);
      const last = stops.at(-1);
      let state, station = null, from = null, to = null;
      if (channel && channel.state === "on-line") { state = "on_line"; from = channel.from; to = channel.to; }
      else if (lastIndex >= 0 && stops[lastIndex].departed && lastIndex < stops.length - 1) {
        state = "on_line"; from = stops[lastIndex].stationId; to = stops[lastIndex + 1].stationId;
      } else if (last.arrived) { state = "arrived"; station = last.stationId; }
      else {
        // Står på en station: den senast nådda, annars den första.
        const here = stops[Math.max(0, lastIndex)];
        station = here.stationId;
        const leaves = here.plannedDeparture;
        const overdue = leaves !== null ? minutesBetween(leaves, now) : -1;
        state = lastIndex < 0 && overdue < 0 ? "not_departed" : lastIndex >= 0 && overdue < 0 ? "at_station" : "waiting";
        if (state === "waiting" && Math.floor(overdue) > delay) { delay = Math.floor(overdue); estimated = true; measuredAt = null; }
        if (channel?.state === "cleared") { from = channel.from; to = channel.to; }
      }
      delay = Math.round(delay);
      // För tidigt: en verklig avgång eller ankomst före den planerade. En
      // beräknad tid är aldrig tidig.
      const earlyMinutes = delay < 0 ? -delay : 0;
      const earlyKind = earlyMinutes ? measuredAt : null;
      if (delay < 0) delay = 0;
      const shift = delay > 0 ? delay : 0;
      const times = stops.map((stop, index) => {
        const done = index <= lastIndex;
        const expected = (planned) => planned === null ? null : planned + shift;
        return { stationId: stop.stationId, plannedArrival: stop.plannedArrival, plannedDeparture: stop.plannedDeparture,
          arrived: stop.arrived, departed: stop.departed, actualArrival: stop.actualArrival, actualDeparture: stop.actualDeparture,
          expectedArrival: done ? stop.actualArrival : expected(stop.plannedArrival),
          expectedDeparture: stop.departed ? stop.actualDeparture : expected(stop.plannedDeparture) };
      });
      result.set(number, { number, state, station, from, to, delayMinutes: delay, late: delay >= LATE_MINUTES, estimated,
        earlyMinutes, earlyKind, trainType: String(service.train_type || "person").toLowerCase(),
        arrivedAt: last.actualArrival, stops: times });
    }
    return result;
  }

  // ── Hur mycket avvikelser som visas (Casper 2026-10-08) ───────────────
  //
  // Det blir lätt plottrigt, eftersom tåg nästan alltid är lite sena. Fem
  // nivåer; var och en väljer på sin enhet, admin sätter träffens förval.
  //   1 Ingen markering
  //   2 När det inträffar (förval): raden lyser kort och får "Nyss"
  //   3 Diskret + när det händer: dessutom liten röd text "+7" från +5 min
  //   4 Fler: röd bricka från +3, överstruken tid med den nya, "beräknad",
  //     och för tidig avgång för persontåg som grön "−2"
  //   5 Allt: allt från +1, för tidig ankomst för alla tåg, och förseningen
  //     även vid tågnumret på kartan och i tågdiagrammet
  // En för tidig avgång är tillåten för godståg och arbetståg och markeras aldrig.

  const DEVIATION_LEVELS = [1, 2, 3, 4, 5];
  const DEFAULT_DEVIATION_LEVEL = 2;
  const validLevel = (value) => { const level = Number(value); return DEVIATION_LEVELS.includes(level) ? level : null; };

  /** Enhetens eget val går före träffens förval; annars nivå 2. */
  function deviationLevel(snapshot, own = null) {
    return validLevel(own) ?? validLevel(snapshot?.display?.deviation_level) ?? DEFAULT_DEVIATION_LEVEL;
  }

  /**
   * Vad en rad visar för ett tåg (trainLive eller en händelse) på en nivå:
   *   flash   – raden lyser kort och får "Nyss" när något ändras
   *   mark    – { tone: late|early, style: text|pill, minutes, text } eller null
   *   strike  – den planerade tiden överstruken med den nya bredvid
   *   estimated – "beräknad" står med
   */
  function deviationView(level, train) {
    const view = { flash: level >= 2, mark: null, strike: false, estimated: false };
    if (!train) return view;
    const delay = Math.max(0, Math.round(train.delayMinutes || 0));
    if (level === 3 && delay >= 5) view.mark = { tone: "late", style: "text", minutes: delay, text: `+${delay}` };
    if (level >= 4 && delay >= (level >= 5 ? 1 : LATE_MINUTES)) {
      view.mark = { tone: "late", style: "pill", minutes: delay, text: `+${delay}` };
      view.strike = true;
      view.estimated = Boolean(train.estimated);
    }
    const early = Math.max(0, Math.round(train.earlyMinutes || 0));
    if (!view.mark && level >= 4 && early >= 1) {
      const passenger = String(train.trainType || "person").toLowerCase() === "person";
      if ((train.earlyKind === "dep" && passenger) || (train.earlyKind === "arr" && level >= 5)) {
        view.mark = { tone: "early", style: "pill", minutes: early, text: `\u2212${early}` };
        view.strike = true;
      }
    }
    return view;
  }

  /**
   * Vad som ändrats sedan förra ritningen. note(key, signatur, nu) ger tiden
   * för den senaste ändringen av nyckeln, eller null. Första gången en nyckel
   * ses är ingen ändring: en lista som ritas för första gången blinkar inte.
   */
  function changeTracker() {
    const seen = new Map();
    return {
      note(key, signature, now) {
        const sig = JSON.stringify(signature);
        const previous = seen.get(key);
        if (!previous) { seen.set(key, { sig, changedAt: null }); return null; }
        if (previous.sig !== sig) { previous.sig = sig; previous.changedAt = now; }
        return previous.changedAt;
      },
    };
  }

  // ── Tågdiagram ────────────────────────────────────────────────────────

  function stationOrder(snapshot) {
    const byId = stationMap(snapshot);
    const order = (snapshot?.display?.graph_station_order || []).map((id) => byId.get(id)).filter(Boolean);
    for (const station of snapshot?.stations || []) if (!order.some((item) => item.id === station.id)) order.push(station);
    return order;
  }

  /** Tågets punkter (minut, stationsrad) längs hela rutten. */
  function routePoints(service, rowOf) {
    const points = [];
    for (const stop of orderedStops(service)) {
      const row = rowOf.get(stop.station_id);
      if (row === undefined) continue;
      const offset = Number(stop.service_day_offset || 0) * 1440;
      let arrival = minutes(stop.arrival_time), departure = minutes(stop.departure_time);
      if (arrival !== null) arrival += offset;
      if (departure !== null) {
        departure += offset;
        if (arrival !== null && departure < arrival) departure += 1440;
      }
      if (arrival !== null) points.push({ minute: arrival, row, station: stop.station_id });
      if (departure !== null && departure !== arrival) points.push({ minute: departure, row, station: stop.station_id });
      if (arrival === null && departure === null && Number.isFinite(Number(stop.service_minute))) points.push({ minute: Number(stop.service_minute), row, station: stop.station_id });
    }
    return points;
  }

  /**
   * Diagrammets innehåll för ett tidsfönster (minuter; 0 = hela dagen).
   * Fönstret ligger på hela timmar och har nu-linjen en bit in, så att det
   * som just hänt och det som kommer syns. Tåget på linjen får sin nuvarande
   * sträcka tänd; det valda tåget hela sin rutt.
   */
  /**
   * Hur långt ett avgånget tåg har kommit på sin sträcka, 0–1, i takt med
   * klockan: från den faktiska avgången (träffklockans sekunder) eller den
   * planerade, fram till den planerade ankomsten. Samma regel som kartan.
   */
  function legProgress(from, to, departedSeconds, now) {
    let departed = from.minute;
    if (departedSeconds !== null && departedSeconds !== undefined && Number.isFinite(Number(departedSeconds))) {
      const actual = Number(departedSeconds) / 60;
      departed = actual + Math.round((from.minute - actual) / 1440) * 1440;
    }
    return Math.min(1, Math.max(0, (now - departed) / Math.max(1, to.minute - from.minute)));
  }

  function graph(snapshot, { windowMinutes = 180, train = null, now: liveNow = null } = {}) {
    const stations = stationOrder(snapshot);
    const rowOf = new Map(stations.map((station, index) => [station.id, index]));
    const lines = services(snapshot).map((service) => ({ service, number: String(service.train_number), points: routePoints(service, rowOf) }))
      .filter((line) => line.points.length >= 2);
    // Drift skickar klockan som den går (minuter med decimaler); annars bildens.
    const now = liveNow ?? minutes(snapshot?.clock?.time);
    // Klockan visar bara tid på dygnet. I ett fönster flyttas därför varje tåg
    // till den förekomst som ligger närmast klockan, som skärmens tågdiagram
    // gör. Förut flyttades klockan i stället till nästa dygn för hela
    // tidtabellen så fort den stod före dagens första tåg och ett nattåg var
    // ute vid samma klockslag nästa morgon - efter en nollställning visade
    // fönstret då nästa dygn (#137).
    if (windowMinutes && now !== null) {
      for (const line of lines) {
        const centre = (line.points[0].minute + line.points.at(-1).minute) / 2;
        const shift = Math.round((now - centre) / 1440) * 1440;
        if (shift) line.points = line.points.map((point) => ({ ...point, minute: point.minute + shift }));
      }
    }
    const all = lines.flatMap((line) => line.points.map((point) => point.minute));
    let start, end;
    if (!windowMinutes) {
      start = all.length ? Math.floor(Math.min(...all) / 60) * 60 : 0;
      end = all.length ? Math.max(start + 60, Math.ceil(Math.max(...all) / 60) * 60) : 24 * 60;
    } else {
      const anchor = now === null ? (all.length ? Math.min(...all) : 0) : now;
      start = Math.floor((anchor - windowMinutes * 0.3) / 60) * 60;
      end = start + windowMinutes;
    }
    const live = trainStates(snapshot);
    for (const line of lines) {
      const state = live.get(line.number);
      line.state = state?.state || null;
      line.selected = train !== null && line.number === String(train);
      line.lit = state?.state === "on-line" || state?.state === "cleared" || line.selected;
      // Tåg på linjen: just den sträckan som är under väg.
      line.segment = null;
      if (state && state.state !== "at-station") {
        // Från avgången där till ankomsten vid nästa station, inte från
        // ankomsten där till avgången därifrån.
        const end = line.points.findIndex((point, index) => point.station === state.to
          && line.points.slice(0, index).some((earlier) => earlier.station === state.from));
        const start = end < 0 ? -1 : line.points.slice(0, end).map((point) => point.station).lastIndexOf(state.from);
        const from = line.points[start], to = line.points[end];
        if (start >= 0 && to.minute >= from.minute) line.segment = [from, to];
      }
      // Ett avgånget tåg står på nu-linjen, så högt upp på sträckan som det kommit.
      line.at = null;
      if (state?.state === "on-line" && line.segment && now !== null) {
        const [from, to] = line.segment, part = legProgress(from, to, state.departedSeconds, now);
        line.at = { minute: now, row: from.row + (to.row - from.row) * part, progress: part };
      }
    }
    // Hela dagen visar tidtabellen som den är. Står klockan före den och ryms
    // den på nästa dygn hör den till nattågen efter midnatt.
    let clockMinute = now;
    if (clockMinute !== null && clockMinute < start && clockMinute + 1440 <= end) clockMinute += 1440;
    return { stations, lines, start, end, now: clockMinute !== null && clockMinute >= start && clockMinute <= end ? clockMinute : null };
  }

  // ── Sökning, kontrolluppgifter ────────────────────────────────────────

  /** Träffar på tågnummer och på stationens namn eller kod. Tåg först. */
  function search(snapshot, query, { limit = 8 } = {}) {
    const needle = String(query || "").trim().toLocaleLowerCase("sv");
    if (!needle) return [];
    const result = [];
    for (const service of services(snapshot)) {
      const number = String(service.train_number);
      if (!number.toLocaleLowerCase("sv").includes(needle)) continue;
      const stops = orderedStops(service), names = stationMap(snapshot);
      const first = names.get(stops[0]?.station_id)?.name, last = names.get(stops.at(-1)?.station_id)?.name;
      result.push({ kind: "train", id: number, label: number, detail: first && last ? `${first} → ${last}` : "", rank: number.toLocaleLowerCase("sv").startsWith(needle) ? 0 : 1 });
    }
    for (const station of snapshot?.stations || []) {
      const name = String(station.name || "").toLocaleLowerCase("sv"), code = String(station.code || "").toLocaleLowerCase("sv");
      if (!name.includes(needle) && !code.includes(needle)) continue;
      result.push({ kind: "station", id: station.id, label: station.name, detail: station.code || "", rank: name.startsWith(needle) || code === needle ? 0 : 1 });
    }
    return result.sort((a, b) => a.rank - b.rank || (a.kind === b.kind ? 0 : a.kind === "train" ? -1 : 1) || compare(a.label, b.label)).slice(0, limit);
  }

  return { compare, minutes, hhmm, services, orderedStops, stationMap, trains, trainStates, stationCounts, lateTrains,
    placement, connectionTone, stationRows, stats, events, stationOrder, routePoints, graph, legProgress, search, LATE_MINUTES, movementOf, trainLive, changeTracker,
    DEVIATION_LEVELS, DEFAULT_DEVIATION_LEVEL, deviationLevel, deviationView };
});
