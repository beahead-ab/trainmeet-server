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
  function events(snapshot, { station = null, train = null, limit = 8 } = {}) {
    const now = minutes(snapshot?.clock?.time) ?? 0;
    const names = stationMap(snapshot);
    const states = trainStates(snapshot);
    const listed = services(snapshot);
    const stopsOf = new Map(listed.map((service) => [String(service.train_number), orderedStops(service)]));
    const nextAfter = (number, stationId) => {
      const stops = stopsOf.get(String(number)) || [];
      const index = stops.findIndex((stop) => stop.station_id === stationId);
      return index >= 0 ? stops[index + 1]?.station_id || null : null;
    };
    const build = (number, stop, kind, at) => ({
      time: hhmm(at), minute: minutes(at), delta: minutes(at) - now, train: String(number), stationId: stop.station_id,
      station: names.get(stop.station_id)?.name || stop.station_id,
      kind, nextStationId: kind === "dep" ? nextAfter(number, stop.station_id) : null,
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
    return result.sort((a, b) => a.minute - b.minute || compare(a.train, b.train)).slice(0, limit);
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
    placement, connectionTone, stationRows, stats, events, stationOrder, routePoints, graph, legProgress, search };
});
