/* Drift · Kontrollrummet – siffrorna bakom vyn.
 *
 * Rena funktioner över serverns egen /v1/display-bild, boxlistan och Clouds
 * presentation. Ingenting här rör DOM:en, så samma regler körs i webbläsaren
 * och i tests/js. drift.js ritar; app.js hämtar och skickar kommandon.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  // Alltid på globalThis också: Cloud och TKL laddar filen som modul men
  // läser den därifrån, och train-graph.js hittar den där.
  root.TrainMeetDriftModel = api;
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

  /** Tågets nyckel i Drift: numret, och där två tåg delar nummer serverns
   *  etikett ("DSB IC 116"), så att DSB IC 116 och SJ X 116 hålls isär
   *  (Cloud #100). Turer av samma tåg har samma nyckel, och ett nummer som
   *  bara ett tåg har får numret som förut. */
  function trainKey(service) {
    const number = String(service?.train_number ?? "").trim();
    return service?.train_label ? String(service.train_label) : number;
  }

  /** Det som står på tåget där det är trångt (kartan, listorna): "IC 116" där
   *  numret delas, annars numret. */
  function trainText(service) {
    const number = String(service?.train_number ?? "").trim();
    return service?.short_label ? String(service.short_label) : number;
  }

  /** Ett tåg per nyckel (tågnummer, eller etikett där två tåg delar nummer):
   *  turen med flest stopp, sorterade som tal. */
  function services(snapshot) {
    const byKey = new Map();
    for (const service of snapshot?.services || []) {
      if (!String(service.train_number || "").trim()) continue;
      const key = trainKey(service);
      const existing = byKey.get(key);
      if (!existing || (service.stops?.length || 0) > (existing.stops?.length || 0)) byKey.set(key, service);
    }
    return [...byKey.values()].sort((a, b) => compare(a.train_number, b.train_number) || compare(trainKey(a), trainKey(b)));
  }

  /** Tjänsten en rörelse (snapshot.trains) hör till, och tåget den är:
   *  { serviceId, key, text, service }. Utan rörelse, eller i en äldre bild
   *  utan tjänster, gäller numret. */
  const identityIndexes = new WeakMap();
  function trainIdentity(snapshot, trainNumber, movementId = null) {
    const number = String(trainNumber ?? "").trim();
    let index = identityIndexes.get(snapshot);
    if (!index) {
      const byService = new Map((snapshot?.services || []).map((service) => [String(service.id), service]));
      index = new Map();
      for (const row of snapshot?.trains || []) {
        const service = byService.get(String(row.service_id));
        if (service) index.set(String(row.id), service);
      }
      if (snapshot && typeof snapshot === "object") identityIndexes.set(snapshot, index);
    }
    const service = movementId === null || movementId === undefined ? null : index.get(String(movementId)) || null;
    if (!service || String(service.train_number ?? "").trim() !== number) return { serviceId: null, key: number, text: number, service: null };
    return { serviceId: service.id ?? null, key: trainKey(service), text: trainText(service), service };
  }

  function orderedStops(service) {
    return [...(service?.stops || [])].sort((a, b) => Number(a.stop_order) - Number(b.stop_order));
  }

  function stationMap(snapshot) { return new Map((snapshot?.stations || []).map((station) => [station.id, station])); }

  /** Var tågen är, ur det /v1/display redan har: varje riktad kanal på en sträcka
   *  (reserverad = klartecken givet, upptagen = avgått), sedan de noterade
   *  lägena för tåg som inte står på en kanal. Äldre vägar noterar bara en
   *  sträckposition, vilket också räknas som på linjen. */
  // Varje tåg bär också sin nyckel (key), texten på taggen (text) och sin
  // tjänst (serviceId), ur rörelsen: två tåg med samma nummer hålls isär.
  function trains(snapshot) {
    const onLine = [], atStation = [], seen = new Set();
    for (const connection of snapshot?.connection_states || []) {
      for (const channel of connection.channels || []) {
        if (!channel.train_number || !["reserved", "occupied"].includes(channel.state)) continue;
        const who = trainIdentity(snapshot, channel.train_number, channel.movement_id);
        seen.add(who.key);
        onLine.push({ trainNumber: String(channel.train_number), key: who.key, text: who.text, serviceId: who.serviceId,
          from: channel.from_station_id, to: channel.to_station_id, departed: channel.state === "occupied",
          departedSeconds: channel.departed_seconds ?? null, movementId: channel.movement_id ?? null });
      }
    }
    for (const position of snapshot?.train_positions || []) {
      const trainNumber = String(position.train_number);
      const who = trainIdentity(snapshot, trainNumber, position.movement_id);
      if (seen.has(who.key)) continue;
      const train = { trainNumber, key: who.key, text: who.text, serviceId: who.serviceId };
      if (position.status === "connection") onLine.push({ ...train, from: position.from_station_id, to: position.to_station_id, departed: true,
        departedSeconds: position.departed_seconds ?? null, movementId: position.movement_id ?? null });
      else if (position.station_id) atStation.push({ ...train, station: position.station_id });
    }
    return { onLine, atStation };
  }

  /** tågets nyckel (numret, eller etiketten där två tåg delar nummer) →
   *  { state: "on-line" | "cleared" | "at-station", from, to, station } */
  function trainStates(snapshot) {
    const result = new Map();
    const { onLine, atStation } = trains(snapshot);
    for (const train of onLine) result.set(train.key, { state: train.departed ? "on-line" : "cleared", from: train.from, to: train.to, departedSeconds: train.departedSeconds });
    for (const train of atStation) if (!result.has(train.key)) result.set(train.key, { state: "at-station", station: train.station });
    return result;
  }

  /** stations-id → nycklarna för tågen som står inne på stationen */
  function stationCounts(snapshot) {
    const result = new Map((snapshot?.stations || []).map((station) => [station.id, []]));
    for (const train of trains(snapshot).atStation) {
      if (!result.has(train.station)) result.set(train.station, []);
      result.get(train.station).push(train.key);
    }
    for (const list of result.values()) list.sort(compare);
    return result;
  }

  /** Vilken tur ett tåg på linjen kör: { service, from, to, departure_time,
   *  arrival_time, departure, arrival } – stoppet det avgick från, det nästa på
   *  sträckan, turen de hör till och de planerade tiderna ("HH:MM" och minuter).
   *  Rörelsen som avgick (movementId, ur /v1/display) pekar ut turen exakt. Utan
   *  den – ett äldre läge, det gamla trafikläget – gäller den tur vars planerade
   *  avgång ligger närmast den faktiska avgången, annars närmast klockan. Aldrig
   *  bara den första med numret: samma nummer kan köras flera gånger om dagen,
   *  och då fick morgontåget kvällsturens ankomst. Finns ingen tur med sträckan
   *  men en ankomst till stationen (bara ruttstopp, ett extratåg) gäller den,
   *  utan avgång (from och departure null). null när tidtabellen inte har något. */
  function onLineLeg(snapshot, train, nowMinutes = null) {
    const number = String(train?.trainNumber);
    const movement = train?.movementId === null || train?.movementId === undefined ? null
      : (snapshot?.trains || []).find((row) => String(row.id) === String(train.movementId)) || null;
    // Turerna med numret; en äldre bild utan turer har bara ruttstoppen, som grupperas.
    let runs = (snapshot?.services || []).filter((service) => String(service.train_number) === number);
    // Två tåg med samma nummer: bara det här tågets turer (Cloud #100).
    const own = train?.key && train.key !== number ? runs.filter((service) => trainKey(service) === train.key) : [];
    if (own.length) runs = own;
    if (!runs.length) {
      const groups = new Map();
      for (const route of snapshot?.routes || []) {
        if (String(route.train_number) !== number) continue;
        const id = route.service_id || route.train_number;
        if (!groups.has(id)) groups.set(id, { id, train_number: route.train_number, stops: [] });
        groups.get(id).stops.push(route);
      }
      runs = [...groups.values()];
    }
    const collect = (exact) => {
      const legs = [];
      for (const service of runs) {
        if (exact && movement.service_id && service.id !== movement.service_id) continue;
        const stops = orderedStops(service);
        stops.forEach((stop, index) => {
          if (stop.station_id !== train.from) return;
          if (exact && (hhmm(stop.departure_time) !== hhmm(movement.departure_time) || hhmm(stop.arrival_time) !== hhmm(movement.arrival_time))) return;
          const next = stops.slice(index + 1).find((later) => later.station_id === train.to);
          const departure = minutes(stop.departure_time || stop.arrival_time);
          const arrival = next ? minutes(next.arrival_time || next.departure_time) : null;
          if (departure === null || arrival === null) return;
          legs.push({ service, from: stop, to: next, departure, arrival,
            departure_time: hhmm(stop.departure_time || stop.arrival_time), arrival_time: hhmm(next.arrival_time || next.departure_time) });
        });
      }
      return legs;
    };
    const arrivals = () => runs.flatMap((service) => orderedStops(service)
      .filter((stop) => stop.station_id === train.to && minutes(stop.arrival_time) !== null)
      .map((stop) => ({ service, from: null, to: stop, departure: null, arrival: minutes(stop.arrival_time), departure_time: "", arrival_time: hhmm(stop.arrival_time) })));
    const exact = movement ? collect(true) : [];
    const broad = exact.length ? exact : collect(false);
    const legs = broad.length ? broad : arrivals();
    if (legs.length > 1) {
      const actual = train.departedSeconds === null || train.departedSeconds === undefined ? NaN : Number(train.departedSeconds);
      const reference = Number.isFinite(actual) ? ((actual % 86400) + 86400) % 86400 / 60 : nowMinutes ?? minutes(snapshot?.clock?.time);
      if (reference !== null) {
        const gap = (leg) => { const d = (((leg.departure ?? leg.arrival) - reference) % 1440 + 1440) % 1440; return Math.min(d, 1440 - d); };
        legs.sort((a, b) => gap(a) - gap(b));
      }
    }
    return legs[0] || null;
  }

  /** Tåg på linjen som är senare än tidtabellen (ankomsten på turen det kör har redan passerat). */
  function lateTrains(snapshot) {
    const now = minutes(snapshot?.clock?.time);
    if (now === null) return [];
    return trains(snapshot).onLine.filter((train) => {
      const leg = train.departed ? onLineLeg(snapshot, train, now) : null;
      return leg !== null && leg.arrival < now;
    });
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
   * kind: waiting | box | operator | automatic | unmanned. `automatic` är
   * /v1/automatic-stations: en station utan box som automatiken sköter blir
   * `automatic`; en box på en sådan station står kvar, med `auto` satt. En
   * station som sköts av något annat än en box i listan, en TKL, blir
   * `operator` (förut "Obemannad"). Första raden per station har `first`.
   */
  function stationRows({ snapshot, devices = [], presentation = null, automatic = null }) {
    const counts = stationCounts(snapshot);
    const automated = new Map((automatic?.enabled ? automatic.stations || [] : []).map((station) => [station.id, station]));
    const rows = [];
    for (const device of devices.filter((item) => !item.station_id)) {
      rows.push({ key: `device:${device.device_id}`, kind: "waiting", station: null, device, tone: "warn", placement: null, trains: null });
    }
    for (const station of snapshot?.stations || []) {
      const here = devices.filter((device) => device.station_id === station.id);
      const base = { station, placement: placement(presentation, station.id), trains: (counts.get(station.id) || []).length };
      const auto = automated.get(station.id);
      if (!here.length) {
        const kind = auto?.mode === "automatic" ? "automatic" : auto?.operator ? "operator" : "unmanned";
        rows.push({ ...base, key: `station:${station.id}`, kind, device: null, first: true,
          tone: kind === "automatic" ? "auto" : auto?.mode === "disconnected" ? "warn" : kind === "operator" ? "ok" : "off", auto });
        continue;
      }
      here.forEach((device, index) => {
        rows.push({ ...base, key: `station:${station.id}:${device.device_id}`, kind: "box", device, first: index === 0,
          tone: auto?.mode === "disconnected" ? "warn" : connectionTone(device.connection), auto });
      });
    }
    return rows;
  }

  /** Nyckeltalen över sidan: tåg på linjen, inne på station, bemannade, avvikelser. */
  // En station som tappat kontakten, med automatik efter N minuter: hur många
  // minuter kvar, räknat från när läget hämtades (fetchedAt, ms). Aldrig
  // (inställningen 0) ger null: stationen väntar på sin operatör.
  function takesOverMinutes(auto, fetchedAt, now = Date.now()) {
    if (auto?.mode !== "disconnected" || typeof auto.takes_over_in !== "number") return null;
    const left = auto.takes_over_in - Math.max(0, now - (fetchedAt ?? now)) / 1000;
    return Math.max(1, Math.ceil(left / 60));
  }

  function stats(snapshot, rows = []) {
    const { onLine, atStation } = trains(snapshot);
    const stations = (snapshot?.stations || []).length;
    // En box som är ansluten till en station som automatiken sköter bemannar den inte.
    const manned = new Set(rows.filter((row) => row.station && ["box", "operator"].includes(row.kind) && row.tone === "ok"
      && row.auto?.mode !== "automatic").map((row) => row.station.id));
    const automatic = new Set(rows.filter((row) => row.station && row.auto?.mode === "automatic").map((row) => row.station.id));
    return {
      onLine: onLine.filter((train) => train.departed).length,
      cleared: onLine.filter((train) => !train.departed).length,
      atStations: atStation.length,
      manned: manned.size,
      stations,
      automatic: automatic.size,
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
    // Stoppen räknas på tjänstens dygn: ett stopp efter midnatt (service_day_offset 1)
    // ligger efter kvällens. Går klockan förbi midnatt på gårdagens dag (tåg som
    // kör klart innan dagen byts) ligger nu också på andra dygnet.
    const base = Number.isFinite(nowSeconds) ? Math.floor(nowSeconds / 60) : minutes(snapshot?.clock?.time) ?? 0;
    const now = base + (Number(snapshot?.clock?.elapsed_seconds) >= 86400 && base < 720 ? 1440 : 0);
    const stopAt = (stop, key) => { const value = minutes(stop?.[key]); return value === null ? null : value + Number(stop.service_day_offset || 0) * 1440; };
    const names = stationMap(snapshot);
    const states = trainStates(snapshot);
    const listed = services(snapshot);
    // Tågets nyckel: numret, eller etiketten där två tåg delar nummer.
    const byKey = new Map(listed.map((service) => [trainKey(service), service]));
    const nextAfter = (stationId, step, service) => {
      const stops = orderedStops(service);
      const index = stops.findIndex((stop) => stop.station_id === stationId);
      return index >= 0 ? stops[index + step]?.station_id || null : null;
    };
    // Tåg som delar nummer samma dag hålls isär: händelsen bär sitt tåg och
    // sin nyckel, och står med bolag och tågslag, "DSB IC 116" (Cloud #100).
    // Vid en station står tåget med sin nyckel (service_key), som förut.
    const build = (stop, kind, at, service, { keyed = false } = {}) => {
      const number = String(service.train_number), key = trainKey(service);
      return {
        time: hhmm(at), minute: stopAt(stop, kind === "dep" ? "departure_time" : "arrival_time"), delta: stopAt(stop, kind === "dep" ? "departure_time" : "arrival_time") - now, train: number, key, stationId: stop.station_id,
        serviceId: keyed || key !== number ? service.id ?? null : null,
        // Vid en station med bolag och tågslag ("DSB IC 116", #230); annars
        // taggens kortform där numret delas ("IC 116"), som på kartan.
        label: keyed ? (service.train_label ? String(service.train_label) : service.service_key ? `${service.service_key} ${number}` : number) : trainText(service),
        station: names.get(stop.station_id)?.name || stop.station_id,
        kind, nextStationId: kind === "dep" ? nextAfter(stop.station_id, 1, service) : null,
        previousStationId: kind === "arr" ? nextAfter(stop.station_id, -1, service) : null,
        state: (states.get(key) || (key !== number ? null : states.get(number)))?.state || null,
      };
    };
    const result = [];

    if (train) {
      const service = byKey.get(String(train));
      for (const stop of service ? orderedStops(service) : []) {
        const arrival = stopAt(stop, "arrival_time"), departure = stopAt(stop, "departure_time");
        if (departure !== null && departure >= now) result.push(build(stop, "dep", stop.departure_time, service));
        else if (arrival !== null && arrival >= now) result.push(build(stop, "arr", stop.arrival_time, service));
      }
    } else if (station) {
      // Vid en station syns varje tåg, också två med samma nummer.
      for (const service of (snapshot?.services || []).filter((item) => String(item.train_number || "").trim())) {
        for (const stop of orderedStops(service)) {
          if (stop.station_id !== station) continue;
          const arrival = stopAt(stop, "arrival_time"), departure = stopAt(stop, "departure_time");
          if (arrival !== null && arrival >= now) result.push(build(stop, "arr", stop.arrival_time, service, { keyed: true }));
          if (departure !== null && departure >= now && departure !== arrival) result.push(build(stop, "dep", stop.departure_time, service, { keyed: true }));
        }
      }
    } else {
      for (const service of listed) {
        const number = String(service.train_number), key = trainKey(service), stops = orderedStops(service);
        const live = states.get(key) || (key !== number ? null : states.get(number));
        let event = null;
        if (live && live.state === "on-line") {
          const stop = stops.find((item) => item.station_id === live.to);
          const at = stop?.arrival_time || stop?.departure_time;
          if (stop && minutes(at) !== null) event = build(stop, stop.arrival_time ? "arr" : "dep", at, service);
        } else if (live) {
          const here = live.state === "cleared" ? live.from : live.station;
          const stop = stops.find((item) => item.station_id === here);
          const departs = stop?.departure_time;
          if (stop && minutes(departs) !== null) event = build(stop, "dep", departs, service);
        }
        if (!event && !live) {
          for (const stop of stops) {
            const at = stop.departure_time || stop.arrival_time;
            const minute = stopAt(stop, stop.departure_time ? "departure_time" : "arrival_time");
            if (minute !== null && minute >= now) { event = build(stop, stop.departure_time ? "dep" : "arr", at, service); break; }
          }
        }
        if (event) result.push(event);
      }
    }
    // Förseningen ur trainLive: en händelse vid en senare station får samma
    // försening, den man står vid får den beräknade.
    const lives = trainLive(snapshot, Number.isFinite(nowSeconds) ? nowSeconds : now * 60);
    for (const event of result) {
      const live = (event.serviceId !== null && lives.get(String(event.serviceId))) || lives.get(event.key) || lives.get(event.train);
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
    // Varje tåg för sig, under sitt tjänst-id, också när två delar nummer
    // (DSB IC 116 och SJ X 116, Cloud #100). Numret ger det tåg det gäller
    // nu, som train_live.py.
    const all = (snapshot?.services || []).filter((service) => String(service.train_number || "").trim());
    const shared = new Map();
    for (const service of all) { const number = String(service.train_number).trim(); shared.set(number, (shared.get(number) || 0) + 1); }
    const byNumber = new Map(), byKey = new Map();
    for (const service of all) {
      const number = String(service.train_number).trim();
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
      const last = stops.at(-1);
      // En kanal säger bara numret: delar två tåg det gäller den det tåg som
      // har avgått men inte kommit fram.
      // Med rörelsen vet kanalen vilket tåg det är: då gäller tågets nyckel.
      const key = trainKey(service);
      const channel = key !== number && states.has(key) ? states.get(key)
        : shared.get(number) === 1 || (lastIndex >= 0 && !last.arrived) ? states.get(number) : null;
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
      const item = { number, key, serviceId: service.id ?? null, state, station, from, to, delayMinutes: delay, late: delay >= LATE_MINUTES, estimated,
        earlyMinutes, earlyKind, trainType: String(service.train_type || "person").toLowerCase(),
        arrivedAt: last.actualArrival, stops: times };
      if (service.id !== undefined && service.id !== null) result.set(String(service.id), item);
      const start = times.map((stop) => stop.plannedDeparture ?? stop.plannedArrival).find((value) => value !== null) ?? 0;
      if (!byNumber.has(number)) byNumber.set(number, []);
      byNumber.get(number).push({ item, start });
      if (key !== number) {
        if (!byKey.has(key)) byKey.set(key, []);
        byKey.get(key).push({ item, start });
      }
    }
    // Det tåg ett delat nummer gäller nu: på väg, annars nästa, annars det
    // senaste. Detsamma för varje tåg (nyckel) bland turerna med dess nummer.
    const current = (candidates) => {
      const moving = candidates.find(({ item }) => ["on_line", "at_station", "waiting"].includes(item.state));
      const coming = candidates.filter(({ item }) => item.state === "not_departed").sort((a, b) => a.start - b.start)[0];
      const latest = [...candidates].sort((a, b) => b.start - a.start)[0];
      return (moving || coming || latest).item;
    };
    for (const [number, candidates] of byNumber) result.set(number, current(candidates));
    for (const [key, candidates] of byKey) result.set(key, current(candidates));
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

  function graph(snapshot, { windowMinutes = 180, train = null, now: liveNow = null, all: everyTurn = false } = {}) {
    const stations = stationOrder(snapshot);
    const rowOf = new Map(stations.map((station, index) => [station.id, index]));
    // Drift visar ett tåg per nyckel (numret, eller etiketten där två tåg delar
    // nummer); den grafiska tidtabellen varje tur (`all`).
    const chosen = everyTurn ? (snapshot?.services || []).filter((service) => String(service.train_number || "").trim()) : services(snapshot);
    const lines = chosen.map((service) => ({ service, number: String(service.train_number), key: trainKey(service), points: routePoints(service, rowOf), shift: 0 }))
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
        line.shift = shift;
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
      const state = live.get(line.key) || (line.key !== line.number ? null : live.get(line.number));
      line.state = state?.state || null;
      line.selected = train !== null && line.key === String(train);
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
      const number = String(service.train_number), key = trainKey(service);
      // Numret, eller etiketten där två tåg delar det ("IC 116" hittas på "ic").
      const haystack = [number, key, trainText(service)].map((value) => value.toLocaleLowerCase("sv"));
      if (!haystack.some((value) => value.includes(needle))) continue;
      const stops = orderedStops(service), names = stationMap(snapshot);
      const first = names.get(stops[0]?.station_id)?.name, last = names.get(stops.at(-1)?.station_id)?.name;
      result.push({ kind: "train", id: key, label: trainText(service), detail: first && last ? `${first} → ${last}` : "", rank: haystack.some((value) => value.startsWith(needle)) ? 0 : 1 });
    }
    for (const station of snapshot?.stations || []) {
      const name = String(station.name || "").toLocaleLowerCase("sv"), code = String(station.code || "").toLocaleLowerCase("sv");
      if (!name.includes(needle) && !code.includes(needle)) continue;
      result.push({ kind: "station", id: station.id, label: station.name, detail: station.code || "", rank: name.startsWith(needle) || code === needle ? 0 : 1 });
    }
    return result.sort((a, b) => a.rank - b.rank || (a.kind === b.kind ? 0 : a.kind === "train" ? -1 : 1) || compare(a.label, b.label)).slice(0, limit);
  }

  return { compare, minutes, hhmm, trainKey, trainText, trainIdentity, services, orderedStops, stationMap, trains, trainStates, stationCounts, onLineLeg, lateTrains,
    placement, connectionTone, stationRows, stats, events, stationOrder, routePoints, graph, legProgress, search, LATE_MINUTES, movementOf, trainLive, changeTracker,
    DEVIATION_LEVELS, DEFAULT_DEVIATION_LEVEL, deviationLevel, deviationView, takesOverMinutes };
});
