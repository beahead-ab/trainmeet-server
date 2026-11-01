/* Tågdiagrammet – ritat som en grafisk tidtabell.
 *
 * Samma ritare för Drift och för skärmen, efter planeringsprogrammets grafiska
 * tidtabell: stationerna ligger på avstånd från varandra (planens km när
 * planens grafiska linjer finns, annars den typiska gångtiden), varje station kan
 * fällas ut till en rad per spår, uppehållet ritas som ett vågrätt streck på
 * spåret, minuterna står vid varje station, och tågnumret står längs sin
 * linje på varje delsträcka, i tågtypens färg. En förgrenad bana ritas som
 * ett diagram per linje, ovanpå varandra, med förgreningsstationen i båda.
 *
 * `lines`, `runTimes` och `layout` är rena funktioner över /v1/display-bilden
 * och körs också i tests/js. `render` ritar SVG:n.
 */
(function (root, factory) {
  // Modellen ligger på globalThis när drift-model.js har laddats före, som i
  // webbläsaren och där Cloud och TKL laddar filen som modul; i node (proven)
  // hämtas den med require. Ritaren läggs alltid på globalThis.
  const api = factory(root.TrainMeetDriftModel || require("./drift-model.js"));
  if (typeof module === "object" && module.exports) module.exports = api;
  root.TrainMeetTrainGraph = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (model) {
  "use strict";

  const SVG = "http://www.w3.org/2000/svg";
  const DAY = 1440;
  const TYPES = { person: "person", goods: "goods", work: "work" };

  const pair = (a, b) => (a < b ? `${a}|${b}` : `${b}|${a}`);
  const median = (values) => {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b), middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  };
  const clockLabel = (minute) => {
    const value = ((Math.floor(minute) % DAY) + DAY) % DAY;
    return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  };
  const minuteText = (minute) => String(((Math.round(minute) % 60) + 60) % 60).padStart(2, "0");

  // ── Banan som linjer ──────────────────────────────────────────────────

  /** Grannarna per station, ur sträckorna. */
  function adjacency(snapshot) {
    const ids = new Set((snapshot?.stations || []).map((station) => station.id));
    const next = new Map([...ids].map((id) => [id, []]));
    for (const connection of snapshot?.connections || []) {
      const a = connection.station_a_id, b = connection.station_b_id;
      if (!ids.has(a) || !ids.has(b) || a === b) continue;
      if (!next.get(a).includes(b)) next.get(a).push(b);
      if (!next.get(b).includes(a)) next.get(b).push(a);
    }
    return next;
  }

  const isPath = (ids, next) => ids.length >= 2 && ids.every((id, index) => index === 0 || (next.get(ids[index - 1]) || []).includes(id));

  /** Den längsta vägen i trädet från `from` över oanvända sträckor (djupet först). */
  function longestFrom(from, next, used, visited = new Set()) {
    visited.add(from);
    let best = [from];
    for (const neighbour of next.get(from) || []) {
      if (visited.has(neighbour) || used.has(pair(from, neighbour))) continue;
      const candidate = [from, ...longestFrom(neighbour, next, used, visited)];
      if (candidate.length > best.length) best = candidate;
    }
    visited.delete(from);
    return best;
  }

  /** Antalet tåg som går direkt mellan två grannstationer, per sträcka. */
  function traffic(snapshot, next) {
    const count = new Map();
    for (const service of snapshot?.services || []) {
      const stops = model.orderedStops(service);
      for (let index = 1; index < stops.length; index += 1) {
        const a = stops[index - 1].station_id, b = stops[index].station_id;
        if ((next.get(a) || []).includes(b)) count.set(pair(a, b), (count.get(pair(a, b)) || 0) + 1);
      }
    }
    return count;
  }

  /** Den tyngsta vägen i trädet från `from`: flest tåg längs sträckorna, vid lika den längsta. */
  function heaviestFrom(from, next, weight, visited = new Set()) {
    visited.add(from);
    let best = { ids: [from], total: 0 };
    for (const neighbour of next.get(from) || []) {
      if (visited.has(neighbour)) continue;
      const rest = heaviestFrom(neighbour, next, weight, visited);
      const total = rest.total + (weight.get(pair(from, neighbour)) || 0);
      if (total > best.total || (total === best.total && rest.ids.length + 1 > best.ids.length)) best = { ids: [from, ...rest.ids], total };
    }
    visited.delete(from);
    return best;
  }

  /** Ett stickspår i änden med en bråkdel av linjens trafik hör till en gren, inte huvudlinjen. */
  function trimmed(ids, weight) {
    let path = [...ids];
    for (let round = 0; round < ids.length && path.length > 2; round += 1) {
      const loads = path.slice(1).map((id, index) => weight.get(pair(path[index], id)) || 0);
      const mean = loads.reduce((sum, value) => sum + value, 0) / loads.length;
      if (loads[0] < mean / 2) path = path.slice(1);
      else if (loads.at(-1) < mean / 2) path = path.slice(0, -1);
      else break;
    }
    return path;
  }

  /**
   * Banans linjer: huvudlinjen först, sedan varje gren från sin
   * förgreningsstation utåt, den längsta resten först. Huvudlinjen är den väg
   * genom banan som flest tåg kör, som i planeringsprogrammets grafiska
   * tidtabell; utan tåg den Cloud har valt, annars den längsta vägen.
   * Förgreningsstationen står med i både linjen den lämnar och grenen. En
   * sträcka som sluter en ring blir en egen linje om två stationer.
   */
  function lines(snapshot) {
    const stations = model.stationMap(snapshot);
    if (!stations.size) return [];
    // Planens grafiska linjer (#100 C5): samma blad som arrangörens grafiska
    // tidtabell, med nummer, namn och km. En sträcka får ligga på flera blad.
    const planned = (snapshot?.display?.plan_lines || []).filter((line) =>
      Array.isArray(line?.station_ids) && line.station_ids.length > 1 && line.station_ids.every((id) => stations.has(id)));
    if (planned.length) {
      const result = planned.map((line) => ({
        id: `plan-${line.number || line.station_ids.join("-")}`,
        name: [line.number, line.name].filter(Boolean).join(": ") || `${stations.get(line.station_ids[0]).name}–${stations.get(line.station_ids.at(-1)).name}`,
        stations: line.station_ids.map((id) => stations.get(id)),
        km: Array.isArray(line.km) && line.km.length === line.station_ids.length ? line.km.map(Number) : null,
        color: /^#[0-9a-f]{6}$/i.test(line.color || "") ? line.color : null,
      }));
      const drawn = new Set(result.flatMap((line) => line.stations.map((station) => station.id)));
      for (const station of model.stationOrder(snapshot)) {
        if (!drawn.has(station.id)) { result.push({ id: station.id, name: station.name, stations: [station], km: null }); drawn.add(station.id); }
      }
      return result;
    }
    const next = adjacency(snapshot);
    const order = model.stationOrder(snapshot).map((station) => station.id);
    const branch = new Set(snapshot?.display?.topology_branch_station_ids || []);
    const used = new Set();
    const result = [];
    const add = (ids) => {
      for (let index = 1; index < ids.length; index += 1) used.add(pair(ids[index - 1], ids[index]));
      const first = stations.get(ids[0]), last = stations.get(ids.at(-1));
      result.push({ id: `${ids[0]}..${ids.at(-1)}`, name: ids.length > 1 ? `${first.name}–${last.name}` : first.name, stations: ids.map((id) => stations.get(id)) });
    };
    const weight = traffic(snapshot, next);
    let main = [];
    if (weight.size) {
      // Den tyngsta vägen: från varje ändstation i trädet, den bästa.
      let best = { ids: [], total: -1 };
      for (const id of order) {
        if ((next.get(id) || []).length !== 1 && order.length > 2) continue;
        const candidate = heaviestFrom(id, next, weight);
        if (candidate.total > best.total || (candidate.total === best.total && candidate.ids.length > best.ids.length)) best = candidate;
      }
      main = trimmed(best.ids, weight);
    }
    if (!isPath(main, next)) main = (snapshot?.display?.topology_mainline_station_ids || []).filter((id) => stations.has(id));
    if (!isPath(main, next)) main = order.filter((id) => !branch.has(id));
    if (!isPath(main, next)) {
      // Ingen angiven huvudlinje: den längsta vägen från den station som står först.
      const start = order[0] || [...stations.keys()][0];
      const far = longestFrom(start, next, used).at(-1);
      main = longestFrom(far, next, used);
    }
    // Vänd den så att den följer stationernas ordning.
    if (main.length > 1 && order.indexOf(main[0]) > order.indexOf(main.at(-1))) main.reverse();
    if (main.length < 2) main = [order[0] || [...stations.keys()][0]];
    add(main);
    // Grenar: från varje station på en ritad linje, i den ordning linjerna
    // har dem, tills alla sträckor är med.
    let index = 0;
    while (index < result.length) {
      const line = result[index];
      index += 1;
      for (const station of line.stations) {
        let guard = 0;
        while (guard < 64) {
          guard += 1;
          const open = (next.get(station.id) || []).filter((other) => !used.has(pair(station.id, other)));
          if (!open.length) break;
          const paths = open.map((other) => [station.id, ...longestFrom(other, next, new Set([...used, pair(station.id, other)]), new Set([station.id]))]);
          paths.sort((a, b) => b.length - a.length || order.indexOf(a[1]) - order.indexOf(b[1]));
          add(paths[0]);
        }
      }
    }
    // En station utan sträckor, eller utanför den nådda banan, blir en egen rad.
    const drawn = new Set(result.flatMap((line) => line.stations.map((station) => station.id)));
    for (const id of order) if (!drawn.has(id)) { add([id]); drawn.add(id); }
    for (const id of stations.keys()) if (!drawn.has(id)) { add([id]); drawn.add(id); }
    return result;
  }

  // ── Avstånd: den typiska gångtiden ────────────────────────────────────

  /** Tidpunkter för ett uppehåll i minuter, med dygnet inräknat. */
  function times(stop) {
    const offset = Number(stop.service_day_offset || 0) * DAY;
    let arrival = model.minutes(stop.arrival_time), departure = model.minutes(stop.departure_time);
    if (arrival !== null) arrival += offset;
    if (departure !== null) {
      departure += offset;
      if (arrival !== null && departure < arrival) departure += DAY;
    }
    if (arrival === null && departure === null && Number.isFinite(Number(stop.service_minute))) arrival = departure = Number(stop.service_minute);
    return { arrival, departure };
  }

  /**
   * Gångtiden mellan två grannstationer: medianen över tågen som går direkt
   * mellan dem. Utan tåg på sträckan: medianen av banans övriga sträckor,
   * och utan tåg alls tio minuter. Det är diagrammets avstånd.
   */
  function runTimes(snapshot) {
    const next = adjacency(snapshot);
    const samples = new Map();
    for (const service of snapshot?.services || []) {
      const stops = model.orderedStops(service);
      for (let index = 1; index < stops.length; index += 1) {
        const from = stops[index - 1], to = stops[index];
        if (!(next.get(from.station_id) || []).includes(to.station_id)) continue;
        const left = times(from), right = times(to);
        const start = left.departure ?? left.arrival, end = right.arrival ?? right.departure;
        if (start === null || end === null) continue;
        const run = ((end - start) % DAY + DAY) % DAY;
        if (run <= 0 || run > 720) continue;
        const key = pair(from.station_id, to.station_id);
        if (!samples.has(key)) samples.set(key, []);
        samples.get(key).push(run);
      }
    }
    const typical = new Map([...samples].map(([key, values]) => [key, median(values)]));
    const fallback = median([...typical.values()]) ?? 10;
    return { typical, fallback, of: (a, b) => typical.get(pair(a, b)) ?? fallback };
  }

  // ── Spåren ────────────────────────────────────────────────────────────

  /** stations-id → spårens etiketter i stationens ordning.
   *
   * Alla stationens aktiva spår, även de som står tomma: stationens egna
   * först och en driftplats spår (godsbangårdens ankomst- och avgångsspår)
   * efter, var för sig i katalogens ordning (4.9.0). Spår som bara står i
   * tidtabellen, utan katalog, kommer sist. */
  function stationTracks(snapshot) {
    const result = new Map();
    const catalogue = [...(snapshot?.tracks || [])].filter((track) => track.active !== false)
      .sort((a, b) => String(a.operating_point_id || "").localeCompare(String(b.operating_point_id || ""))
        || Number(a.sort_order || 0) - Number(b.sort_order || 0) || model.compare(a.display_label, b.display_label));
    for (const track of catalogue) {
      if (!result.has(track.station_id)) result.set(track.station_id, []);
      const label = String(track.display_label || "").trim();
      if (label && !result.get(track.station_id).includes(label)) result.get(track.station_id).push(label);
    }
    const extra = new Map();
    for (const row of snapshot?.trains || []) {
      const label = String(row.track || "").trim();
      if (!label || !row.station_id || result.get(row.station_id)?.includes(label)) continue;
      if (!extra.has(row.station_id)) extra.set(row.station_id, new Set());
      extra.get(row.station_id).add(label);
    }
    for (const [station, labels] of extra) result.set(station, [...(result.get(station) || []), ...[...labels].sort(model.compare)]);
    return result;
  }

  /** "tjänst|station" → spår: det spår tåget verkligen fick (TKL:s val eller
   * serverns standardspår, i `movement_live`), annars tidtabellens, annars
   * stationens första standardspår för raden (4.12.0). */
  function trackOf(snapshot) {
    const catalogue = snapshot?.tracks || [];
    const live = snapshot?.movement_live || {};
    // Det verkliga spåret är ett id; äldre lägen kan bära spårets namn.
    const actual = (row) => {
      const value = String(live[row.id]?.actual_track || "").trim();
      const track = value && (catalogue.find((entry) => String(entry.id) === value)
        || catalogue.find((entry) => entry.station_id === row.station_id && String(entry.display_label).trim() === value));
      return track ? String(track.display_label || "").trim() : "";
    };
    const result = new Map(), strength = new Map();
    for (const row of snapshot?.trains || []) {
      if (!row.service_id || !row.station_id) continue;
      const key = `${row.service_id}|${row.station_id}`;
      const found = [actual(row), String(row.track || "").trim(), String(row.default_tracks?.[0] || "").trim()];
      const index = found.findIndex(Boolean);
      if (index < 0) continue;
      // Det verkliga spåret går före tidtabellens, som går före standardspåret.
      const level = 3 - index;
      if (level >= (strength.get(key) || 0)) { result.set(key, found[index]); strength.set(key, level); }
    }
    return result;
  }

  // ── Geometrin ─────────────────────────────────────────────────────────

  const TYPE_OF = (service) => TYPES[String(service?.train_type || "").toLowerCase()] || "person";
  /** Tågslagets färg ur planen, om den syns på både ljus och mörk botten; annars tågtypens. */
  const readableColor = (value) => {
    if (!/^#[0-9a-f]{6}$/i.test(value || "")) return null;
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16) / 255);
    const light = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    return light < 0.12 || light > 0.92 ? null : value;
  };

  /**
   * Geometrin för ett diagram: en linje eller alla linjer ovanpå varandra.
   *
   * options: width, height (null = så högt som det blir), windowMinutes,
   * now (minuter), train (valt nummer), line (linjens id eller null = alla),
   * tracks ("none" | "all" | Set av stations-id som är utfällda),
   * minutes (true = minuter vid stationerna), gap (minsta radavstånd),
   * trackStep (höjd per spårrad), left/right/top/bottom (marginaler).
   */
  function layout(snapshot, options = {}) {
    const o = { width: 1200, height: null, windowMinutes: 180, now: null, train: null, line: null, tracks: "none", minutes: true,
      gap: 22, maxGap: 110, pxPerMinute: 4, trackStep: 14, left: 120, right: 18, top: 26, bottom: 24, title: 28, panelGap: 14, start: null, end: null, ...options };
    const all = lines(snapshot);
    const chosen = o.line ? all.filter((line) => line.id === o.line) : all;
    const shown = chosen.length ? chosen : all.slice(0, 1);
    const run = runTimes(snapshot);
    // Km och gångtid blandas inte: km bara när varje visat blad med flera
    // stationer har km. Skalan gör en km lika stor som en typisk gångminut.
    const multi = shown.filter((line) => line.stations.length > 1);
    const typical = (() => {
      const values = multi.flatMap((line) => line.stations.slice(1).map((station, index) => run.of(line.stations[index].id, station.id))).filter((v) => v > 0).sort((a, b) => a - b);
      const kms = multi.flatMap((line) => (line.km || []).slice(1).map((km, index) => Math.abs(km - line.km[index]))).filter((v) => v > 0).sort((a, b) => a - b);
      return values.length && kms.length ? values[Math.floor(values.length / 2)] / kms[Math.floor(kms.length / 2)] : 1;
    })();
    const kmScale = multi.length && multi.every((line) => line.km) ? typical : null;
    const tracks = stationTracks(snapshot), trackLookup = trackOf(snapshot);
    const expanded = (id) => o.tracks === "all" || (o.tracks instanceof Set && o.tracks.has(id));
    const rowsOf = (id) => (expanded(id) ? Math.max(1, (tracks.get(id) || []).length) : 1);
    // Tidsfönstret och tågens punkter: samma regler som modellens graph().
    const base = model.graph(snapshot, { windowMinutes: o.windowMinutes, train: o.train, now: o.now, all: true });
    // Skärmen anger sitt eget fönster (på jämn kvart); annars modellens på hela timmar.
    const own = o.start !== null && o.end !== null && o.end > o.start;
    const start = own ? o.start : base.start, end = own ? o.end : base.end;
    const now = own ? (o.now !== null && o.now >= start && o.now <= end ? o.now : null) : base.now;
    const x = (minute) => o.left + (minute - start) / Math.max(1, end - start) * (o.width - o.left - o.right);

    // Höjden: varje linje tar sina gångtider gånger skalan, plus spårraderna.
    const blocks = shown.map((line) => {
      const ids = line.stations.map((station) => station.id);
      // Med planens km står stationerna på sitt avstånd, som på arrangörens blad.
      const weights = ids.slice(1).map((id, index) => kmScale && line.km ? Math.max(0.5, Math.abs(line.km[index + 1] - line.km[index])) * kmScale : run.of(ids[index], id));
      const extra = ids.reduce((sum, id) => sum + (rowsOf(id) - 1) * o.trackStep, 0);
      return { line, ids, weights, extra, total: weights.reduce((sum, value) => sum + value, 0) };
    });
    // Flera linjer: ett rubrikband över var och en och luft efter den sista raden.
    const titles = shown.length > 1 ? o.title : 0, between = shown.length > 1 ? o.panelGap : 0;
    const fixed = blocks.reduce((sum, block) => sum + block.extra, 0) + (titles + between) * shown.length - between + o.top + o.bottom;
    const weight = blocks.reduce((sum, block) => sum + block.total, 0);
    let scale;
    if (o.height) {
      // Skärmen: allt ryms exakt. Sträckor som blir för korta får minsta
      // avståndet, och resten delar det som är kvar.
      scale = Math.max(0.01, (o.height - fixed) / Math.max(1, weight));
      for (let round = 0; round < 3; round += 1) {
        const short = blocks.flatMap((block) => block.weights).filter((value) => value * scale < o.gap);
        const long = blocks.flatMap((block) => block.weights).filter((value) => value * scale >= o.gap);
        const left = o.height - fixed - short.length * o.gap;
        const longWeight = long.reduce((sum, value) => sum + value, 0);
        scale = longWeight > 0 ? Math.max(0.01, left / longWeight) : scale;
      }
    } else {
      scale = o.pxPerMinute;
    }
    const gapOf = (value) => Math.max(o.gap, Math.min(o.height ? Infinity : o.maxGap, value * scale));
    const panels = [];
    let cursor = o.top;
    for (const block of blocks) {
      const panel = { line: block.line, title: titles ? block.line.name : "", y0: cursor, rows: [], rowOf: new Map(), stations: [] };
      cursor += titles;
      block.ids.forEach((id, index) => {
        const station = block.line.stations[index];
        const labels = expanded(id) ? (tracks.get(id) || []) : [];
        const entry = { station, y: cursor, tracks: labels.map((label, k) => ({ label, y: cursor + k * o.trackStep })), expanded: expanded(id) };
        panel.stations.push(entry);
        panel.rowOf.set(id, entry);
        cursor += (rowsOf(id) - 1) * o.trackStep;
        if (index < block.weights.length) cursor += gapOf(block.weights[index]);
      });
      panel.y1 = cursor;
      panels.push(panel);
      if (block !== blocks.at(-1)) cursor += between;
    }
    const height = o.height || Math.round(cursor + o.bottom);
    if (o.height && panels.length) {
      // Avrundningar: sista raden ska sluta vid nederkanten.
      const want = o.height - o.bottom, have = panels.at(-1).y1;
      if (Math.abs(want - have) > 0.5 && have > o.top) {
        const factor = (want - o.top) / (have - o.top);
        for (const panel of panels) {
          panel.y0 = o.top + (panel.y0 - o.top) * factor; panel.y1 = o.top + (panel.y1 - o.top) * factor;
          for (const entry of panel.stations) { entry.y = o.top + (entry.y - o.top) * factor; for (const track of entry.tracks) track.y = o.top + (track.y - o.top) * factor; }
        }
      }
    }

    // Tågen, per linje: en tur ritas där den följer linjen, och bryts där den
    // viker av. Spåret ger raden när stationen är utfälld.
    const states = model.trainStates(snapshot);
    const byNumber = new Map(base.lines.map((line) => [line.service.id || line.number, line]));
    const trains = [];
    for (const panel of panels) {
      for (const service of snapshot?.services || []) {
        const line = byNumber.get(service.id || String(service.train_number));
        if (!line) continue;
        const shift = line.shift || 0;
        const stops = model.orderedStops(service);
        const visits = [];
        let offLine = false, previous = null;
        for (const stop of stops) {
          const entry = panel.rowOf.get(stop.station_id);
          if (!entry) { offLine = true; continue; }
          const { arrival, departure } = times(stop);
          if (arrival === null && departure === null) { offLine = true; continue; }
          const track = trackLookup.get(`${service.id}|${stop.station_id}`) || "";
          const row = entry.tracks.find((item) => item.label === track);
          const visit = { station: entry.station, y: row ? row.y : entry.y, track, arrival: arrival === null ? null : arrival + shift,
            departure: departure === null ? null : departure + shift, breakBefore: offLine && previous !== null };
          visits.push(visit);
          offLine = false; previous = visit;
        }
        if (visits.length < 2) continue;
        const inWindow = visits.some((visit) => (visit.departure ?? visit.arrival) >= start && (visit.arrival ?? visit.departure) <= end);
        if (!inWindow) continue;
        const number = String(service.train_number);
        // Tågets nyckel: numret, eller serverns etikett där två tåg delar
        // nummer (Cloud #100). Valet och läget följer nyckeln.
        const key = model.trainKey ? model.trainKey(service) : number;
        const text = model.trainText ? model.trainText(service) : number;
        const state = states.get(key) || (key !== number ? null : states.get(number));
        const selected = o.train !== null && o.train !== undefined && key === String(o.train);
        // Tåg som delar nummer med ett annat står med bolag och tågslag: "DSB IC 116".
        const label = service.service_key ? `${service.service_key} ${number}` : number;
        const color = readableColor(service.color);
        const train = { number, key, text, label, color, type: TYPE_OF(service), service, selected, state: state?.state || null,
          lit: state?.state === "on-line" || state?.state === "cleared" || selected, runs: [], legs: [], dwells: [], at: null, tag: null };
        let run = [];
        const flush = () => { if (run.length >= 2) train.runs.push(run); run = []; };
        visits.forEach((visit, index) => {
          if (visit.breakBefore) flush();
          const from = visit.departure ?? visit.arrival, to = visit.arrival ?? visit.departure;
          if (!run.length || run.at(-1).minute !== to || run.at(-1).y !== visit.y) run.push({ minute: to, x: x(to), y: visit.y, station: visit.station.id, kind: "arr" });
          if (visit.departure !== null && visit.arrival !== null && visit.departure !== visit.arrival) {
            train.dwells.push({ x1: x(visit.arrival), x2: x(visit.departure), y: visit.y, station: visit.station.id });
            run.push({ minute: from, x: x(from), y: visit.y, station: visit.station.id, kind: "dep" });
          }
          const following = visits[index + 1];
          if (following && !following.breakBefore) {
            const depart = visit.departure ?? visit.arrival, arrive = following.arrival ?? following.departure;
            if (arrive !== null && depart !== null && arrive >= depart) {
              train.legs.push({ x1: x(depart), y1: visit.y, x2: x(arrive), y2: following.y, from: visit.station.id, to: following.station.id,
                depart, arrive, dep: minuteText(depart), arr: minuteText(arrive), number,
                lit: Boolean(state && state.state !== "at-station" && state.from === visit.station.id && state.to === following.station.id) });
            }
          }
        });
        flush();
        // Tåget på linjen: på nu-linjen så långt det kommit på sin sträcka.
        const current = train.legs.find((leg) => leg.lit);
        if (current && state?.state === "on-line" && now !== null) {
          const part = model.legProgress({ minute: current.depart }, { minute: current.arrive }, state.departedSeconds, now);
          train.at = { x: x(now), y: current.y1 + (current.y2 - current.y1) * part, progress: part };
        }
        const marker = train.at || (selected ? (current ? { x: current.x1, y: current.y1 } : { x: train.runs[0]?.[0]?.x, y: train.runs[0]?.[0]?.y }) : current ? { x: current.x1, y: current.y1 } : null);
        if (marker && Number.isFinite(marker.x)) train.tag = { x: marker.x, y: marker.y, cleared: state?.state === "cleared" && !selected };
        train.panel = panel;
        trains.push(train);
      }
    }
    const hours = [];
    for (let minute = Math.ceil(start / 60) * 60; minute <= end; minute += 60) hours.push({ minute, x: x(minute), label: clockLabel(minute), major: end - start <= 360 || (minute / 60) % 2 === 0 });
    const halves = [];
    if (end - start <= 360) for (let minute = Math.ceil(start / 30) * 30; minute <= end; minute += 30) if (minute % 60) halves.push({ minute, x: x(minute) });
    // Det minsta avståndet mellan två stationers rader. På skärmen ryms allt
    // alltid, och med många linjer kan raderna bli tätare än namnens höjd.
    const spacing = panels.flatMap((panel) => panel.stations.slice(1).map((entry, index) => entry.y - (panel.stations[index].tracks.at(-1)?.y ?? panel.stations[index].y)));
    const rowSpacing = spacing.length ? Math.min(...spacing) : Infinity;
    return { width: o.width, height, left: o.left, right: o.right, top: o.top, bottom: o.bottom, start, end, now, nowX: now === null ? null : x(now), x, panels, trains, hours, halves, rowSpacing,
      lines: all, options: o };
  }

  // ── Ritningen ─────────────────────────────────────────────────────────

  function el(tag, attrs = {}, text) {
    const node = document.createElementNS(SVG, tag);
    for (const [key, value] of Object.entries(attrs)) if (value !== undefined && value !== null) node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = text;
    return node;
  }

  /** Tågslagets färg på linjen eller numret. Aldrig som style-attribut: serverns
   *  CSP (style-src 'self') stoppar det, och Drift och storskärmen fick då bara
   *  två färger. Elementets egen stil (CSSOM) släpps igenom och väger lika
   *  tungt, så Cloud ser ut som förut. */
  function paint(node, property, color) {
    if (color) node.style.setProperty(property, color);
    return node;
  }

  /** Textens vinkel längs en sträcka, aldrig upp och ner. */
  function angle(leg) {
    let degrees = Math.atan2(leg.y2 - leg.y1, leg.x2 - leg.x1) * 180 / Math.PI;
    if (degrees > 90) degrees -= 180;
    if (degrees < -90) degrees += 180;
    return degrees;
  }

  const DRIFT = { past: "sh", grid: "gl", gridMajor: "gl h", gridMinor: "gl d", track: "gl trk", axis: "ax", label: "lbl s", code: "cnt", trackLabel: "tlbl", title: "gtitle",
    train: "tr-line", line: "pl", hit: "hit", lit: "tr-lit", litLine: "pl lit", dwell: "dw", name: "tr-name", minute: "tr-min",
    tag: "tr-tag", tagBox: "tb", tagText: "tbt", tagBoxCleared: "tbh", tagTextCleared: "tbht", now: "now", nowBox: "nowb", nowText: "nowt", tagHeight: 22, labelDy: 4.5, colors: true };
  const SCREEN = { past: "sc-graph-past", grid: "graph-grid graph-row", gridMajor: "graph-grid graph-col", gridMinor: "graph-grid graph-col is-half", track: "graph-grid graph-track", axis: "sc-graph-time",
    label: "sc-graph-label", code: "sc-graph-code", trackLabel: "sc-graph-track", title: "sc-graph-title", train: "graph-train-group", line: "sc-graph-line", hit: "sc-graph-hit", lit: "graph-train-group is-lit", litLine: "sc-graph-line is-out",
    dwell: "sc-graph-dwell", name: "sc-graph-train", minute: "sc-graph-minute", tag: "graph-train-group", tagBox: "sc-graph-tag", tagText: "sc-graph-tag-text",
    tagBoxCleared: "sc-graph-tag is-cleared", tagTextCleared: "sc-graph-tag-text is-cleared", now: "sc-graph-now", nowBox: "sc-graph-now-tag", nowText: "sc-graph-now-text", tagHeight: 18, labelDy: 5.5,
    // Storskärmens tågnummer, minuter och klocka är 15 % större än Drifts (4.12.0); skarmar.css har storlekarna.
    // Spårens namn står i en egen kolumn, så att stationens namn inte skrivs över.
    // labelSize: stationsnamnens höjd (skarmar.css); tätare rader krymper namnen och rubrikerna.
    text: 1.15, trackColumn: 24, colors: true, labelSize: 16 };

  /**
   * Ritar geometrin i en SVG. `cls` är klassnamnen (DRIFT eller SCREEN),
   * `onSelect(key)` och `onStation(stationId)` svarar på klick, `label`
   * översätter "Tåg {number}", och `marks` ger nivå 5:s förseningstext per
   * tåg. Nyckeln är tågnumret, eller etiketten där två tåg delar nummer
   * ("DSB IC 116", drift-model trainKey).
   */
  function render(svg, g, { cls = DRIFT, codes = false, onSelect = null, onStation = null, label = (number) => String(number), marks = null, clip = null } = {}) {
    const { left, width, height } = g;
    const right = width - g.right;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.replaceChildren();
    const parts = [];
    const areaTop = g.top - 8, areaBottom = height - g.bottom + 6;
    if (clip) {
      const defs = el("defs"), clipPath = el("clipPath", { id: clip });
      clipPath.append(el("rect", { x: left, y: areaTop - 2, width: width - left, height: areaBottom - areaTop + 4 }));
      defs.append(clipPath); parts.push(defs);
    }
    if (g.nowX !== null) parts.push(el("rect", { class: cls.past, x: left, y: areaTop, width: Math.max(0, Math.min(g.nowX, right) - left), height: areaBottom - areaTop }));
    // Alla nio linjer på en skärm gav rader tätare än namnen, som skrevs över
    // varandra. Då krymper namnen och rubrikerna lika mycket som raderna (dock
    // högst till hälften); skarmar.css läser skalan.
    // Linjernas rubriker ritas sist, ovanpå tågen, så att ett tågnummer vid
    // fönstrets början inte skriver över linjens namn.
    const titles = [];
    const squeeze = cls.labelSize && Number.isFinite(g.rowSpacing) ? Math.max(0.5, Math.min(1, g.rowSpacing / (cls.labelSize * 1.1))) : 1;
    if (squeeze < 1) svg.style.setProperty("--graph-label-scale", squeeze.toFixed(3)); else svg.style.removeProperty("--graph-label-scale");
    for (const panel of g.panels) {
      // Rubriken står i sitt eget band ovanför linjens första station, också när bandet är hoptryckt.
      const band = panel.stations.length ? panel.stations[0].y - panel.y0 : 26;
      if (panel.title) titles.push(el("text", { class: cls.title, x: left, y: panel.y0 + Math.min(13, band * 0.62) }, panel.title));
      for (const entry of panel.stations) {
        parts.push(el("line", { class: cls.grid, x1: left, y1: entry.y, x2: right, y2: entry.y }));
        const name = el("text", { class: codes ? cls.code : cls.label, x: left - 10 - (entry.tracks.length ? cls.trackColumn || 0 : 0), y: entry.y + cls.labelDy, "text-anchor": "end", "data-station-id": entry.station.id },
          codes ? entry.station.code || "" : entry.station.name);
        if (onStation) { name.setAttribute("role", "button"); name.setAttribute("tabindex", "0"); name.addEventListener("click", () => onStation(entry.station.id)); name.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onStation(entry.station.id); } }); }
        parts.push(name);
        entry.tracks.forEach((track, index) => {
          if (index > 0) parts.push(el("line", { class: cls.track, x1: left, y1: track.y, x2: right, y2: track.y }));
          parts.push(el("text", { class: cls.trackLabel, x: left - 4, y: track.y + 3.5, "text-anchor": "end" }, track.label));
        });
      }
    }
    for (const half of g.halves) parts.push(el("line", { class: cls.gridMinor, x1: half.x, y1: areaTop, x2: half.x, y2: areaBottom }));
    for (const hour of g.hours) {
      parts.push(el("line", { class: hour.major ? cls.gridMajor : cls.gridMinor, x1: hour.x, y1: areaTop, x2: hour.x, y2: areaBottom }));
      if (hour.major) parts.push(el("text", { class: cls.axis, x: hour.x, y: height - 6, "text-anchor": hour.minute <= g.start ? "start" : hour.minute >= g.end ? "end" : "middle" }, hour.label));
    }
    const gray = [], chosen = [], lit = [];
    const group = (train, klass) => {
      const node = el("g", { class: klass, "data-train-number": train.key ?? train.number, role: onSelect ? "button" : null, tabindex: onSelect ? "0" : null, "aria-label": label(train.text ?? train.number) });
      if (onSelect) {
        const open = (event) => { event.stopPropagation(); onSelect(train.key ?? train.number); };
        node.addEventListener("click", open);
        node.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(event); } });
      }
      return node;
    };
    // Texternas mått följer storleken: storskärmen har större tågnummer och minuter.
    const k = cls.text || 1;
    const tagWidth = (number) => 14 + String(number).length * 8.4 * k;
    for (const train of g.trains) {
      const node = group(train, `${cls.train} is-${train.type}`);
      for (const run of train.runs) {
        const points = run.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
        // Ett valt tåg behåller valets färg (.pl.lit.sel), inte tågslagets.
        node.append(paint(el("polyline", { class: `${cls.line} is-${train.type}${train.selected ? " lit sel" : ""}`, points }), "stroke", cls.colors && !train.selected ? train.color : null),
          el("polyline", { class: cls.hit, points }));
      }
      for (const dwell of train.dwells) node.append(el("line", { class: `${cls.dwell} is-${train.type}`, x1: dwell.x1.toFixed(1), y1: dwell.y.toFixed(1), x2: dwell.x2.toFixed(1), y2: dwell.y.toFixed(1) }));
      for (const leg of train.legs) {
        const length = Math.hypot(leg.x2 - leg.x1, leg.y2 - leg.y1), textWidth = String(train.label || train.number).length * 6.6 * k + 6;
        if (length >= textWidth + 10) {
          const mx = (leg.x1 + leg.x2) / 2, my = (leg.y1 + leg.y2) / 2, degrees = angle(leg);
          // Ovanför linjen, vinkelrätt mot den.
          const radians = degrees * Math.PI / 180, ox = Math.sin(radians) * 7 * k, oy = -Math.cos(radians) * 7 * k;
          node.append(paint(el("text", { class: `${cls.name} is-${train.type}`, x: 0, y: 0, "text-anchor": "middle", transform: `translate(${(mx + ox).toFixed(1)} ${(my + oy).toFixed(1)}) rotate(${degrees.toFixed(1)})` }, train.label || train.number),
            "fill", cls.colors ? train.color : null));
        }
        if (g.options.minutes && Math.abs(leg.y2 - leg.y1) >= 14) {
          const down = leg.y2 > leg.y1;
          node.append(el("text", { class: cls.minute, x: (leg.x1 + 3).toFixed(1), y: (down ? leg.y1 - 3 : leg.y1 + 10 * k).toFixed(1), "text-anchor": "start" }, leg.dep),
            el("text", { class: cls.minute, x: (leg.x2 - 3).toFixed(1), y: (down ? leg.y2 + 10 * k : leg.y2 - 3).toFixed(1), "text-anchor": "end" }, leg.arr));
        }
      }
      (train.selected ? chosen : gray).push(node);
      const current = train.legs.find((leg) => leg.lit);
      if (current && !train.selected) {
        const segment = group(train, cls.lit);
        segment.append(el("polyline", { class: `${cls.litLine} is-${train.type}`, points: `${current.x1.toFixed(1)},${current.y1.toFixed(1)} ${current.x2.toFixed(1)},${current.y2.toFixed(1)}` }));
        lit.push(segment);
      }
      if (train.tag) {
        const w = tagWidth(train.text ?? train.number), h = cls.tagHeight * k, cleared = train.tag.cleared;
        const mark = el("g", { class: cls.tag, "data-train-number": train.key ?? train.number, "pointer-events": "none" });
        mark.append(el("rect", { class: `${cleared ? cls.tagBoxCleared : cls.tagBox}${train.selected ? " sel" : ""}`, x: (train.tag.x - w - 6).toFixed(1), y: (train.tag.y - h / 2).toFixed(1), width: w, height: h, rx: h > 20 ? 6 : 4 }),
          el("text", { class: cleared ? cls.tagTextCleared : cls.tagText, x: (train.tag.x - w / 2 - 6).toFixed(1), y: (train.tag.y + 5 * k).toFixed(1), "text-anchor": "middle" }, train.text ?? train.number));
        const text = marks ? marks(train.key ?? train.number) : null;
        if (text) mark.append(el("text", { class: `graph-train-mark is-${text.tone}`, x: (train.tag.x - w - 6).toFixed(1), y: (train.tag.y - h / 2 - 3).toFixed(1) }, text.text));
        lit.push(mark);
      }
    }
    const trainsLayer = el("g", clip ? { "clip-path": `url(#${clip})` } : {});
    trainsLayer.append(...gray, ...chosen, ...lit);
    parts.push(trainsLayer, ...titles);
    if (g.nowX !== null) {
      parts.push(el("line", { class: cls.now, x1: g.nowX, y1: areaTop, x2: g.nowX, y2: areaBottom }), el("rect", { class: cls.nowBox, x: g.nowX - 24 * k, y: 0, width: 48 * k, height: 18 * k, rx: 5 }),
        el("text", { class: cls.nowText, x: g.nowX, y: 13 * k, "text-anchor": "middle" }, clockLabel(g.now)));
    }
    svg.append(...parts);
    return svg;
  }

  return { lines, runTimes, stationTracks, trackOf, layout, render, angle, clockLabel, minuteText, DRIFT, SCREEN, adjacency };
});
