/* Lokföraren: körplanens tidsaxel (#226).
 *
 * En tjänst blir en lodrät tidsaxel där en träffminut är 7 px. Långa uppehåll
 * trycks ihop till 160 px, och rader som ligger tätt får den plats de behöver.
 * Axeln är styckvis linjär, så NU-linjen glider jämnt mellan tiderna och
 * hoppar aldrig från rad till rad. Inga beslut fattas här: det är ritning.
 */
(function(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LokforarenKorplan = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function() {
  "use strict";
  const PX_PER_MINUTE = 7;
  const COMPRESS_AFTER = 40;      // minuter
  const COMPRESSED_HEIGHT = 160;  // px
  const PRE_ROLL = 20;            // minuter före tjänsten
  const POST_ROLL = 30;           // minuter efter tjänsten
  const TOP = 30;
  const ROW_MIN = 44;
  const CHIPS_EXTRA = 30;
  const TRAIN_EXTRA = 42;
  const DAY = 1440;

  function minutes(value) {
    const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || ""));
    return match ? Number(match[1]) * 60 + Number(match[2]) : null;
  }

  function label(total) {
    const value = ((Math.floor(total) % DAY) + DAY) % DAY;
    return String(Math.floor(value / 60)).padStart(2, "0") + ":" + String(value % 60).padStart(2, "0");
  }

  // Händelserna i tidsordning på en löpande axel: en tid som ligger mer än
  // sex timmar före den förra har gått över midnatt.
  function rowsOf(duty) {
    const rows = [];
    let last = null;
    const onAxis = (value) => {
      let t = minutes(value);
      if (t === null) return null;
      while (last !== null && t < last - 360) t += DAY;
      last = t;
      return t;
    };
    for (const event of duty.events || []) {
      const row = {kind: event.kind, station: event.station || "", track: event.track || "", train: event.train || "",
        notes: event.notes || [], stationId: event.station_id || ""};
      if (event.kind === "stop") {
        row.t = onAxis(event.arrival);
        row.t2 = onAxis(event.departure);
        if (row.t === null || row.t2 === null) continue;
      } else {
        row.t = onAxis(event.time);
        if (row.t === null) continue;
      }
      rows.push(row);
    }
    return rows;
  }

  // Var tågbrickorna ska stå: före den första raden med ett nytt tåg.
  function trainStarts(rows) {
    const starts = new Set();
    let current = "";
    rows.forEach((row, index) => {
      if (row.train && row.train !== current) {
        starts.add(index);
        current = row.train;
      }
    });
    return starts;
  }

  function build(duty) {
    const rows = rowsOf(duty);
    if (!rows.length) return null;
    const starts = trainStarts(rows);
    // Ankare: varje tid som har en rad (ankomst och avgång för ett uppehåll).
    const points = [];
    rows.forEach((row, index) => {
      points.push({t: row.t, row: index, first: true});
      if (row.t2 !== undefined) points.push({t: row.t2, row: index, first: false});
    });
    const startT = rows[0].t;
    const endT = Math.max(...rows.map((row) => row.t2 ?? row.t));
    const anchors = [[startT - PRE_ROLL, TOP]];
    let y = TOP + PRE_ROLL * PX_PER_MINUTE;
    if (starts.has(0)) y = Math.max(y, TOP + TRAIN_EXTRA + 24);
    anchors.push([points[0].t, y]);
    const gaps = [];
    for (let i = 1; i < points.length; i++) {
      const previous = points[i - 1], point = points[i];
      const minutesBetween = Math.max(0, point.t - previous.t);
      let need = ROW_MIN;
      const previousRow = rows[previous.row];
      if (previous.first && previousRow.notes.length && point.row !== previous.row) need += CHIPS_EXTRA;
      if (point.row === previous.row) need = previousRow.notes.length ? ROW_MIN + CHIPS_EXTRA : ROW_MIN;
      if (point.first && starts.has(point.row)) need += TRAIN_EXTRA;
      let dy;
      if (minutesBetween > COMPRESS_AFTER && point.row !== previous.row) {
        dy = Math.max(COMPRESSED_HEIGHT, need + 40);
        gaps.push({from: previous.t, to: point.t, y: y + dy / 2, station: previousRow.station === rows[point.row].station ? previousRow.station : "", minutes: minutesBetween});
      } else {
        dy = Math.max(minutesBetween * PX_PER_MINUTE, need);
      }
      if (point.t === previous.t) {
        // Samma minut: raderna får sin plats, och axeln får en hårfin lutning
        // så att den fortfarande är strikt växande.
        y += dy;
        anchors.push([point.t + 0.001 * i, y]);
        continue;
      }
      y += dy;
      anchors.push([point.t, y]);
    }
    anchors.push([endT + POST_ROLL, y + POST_ROLL * PX_PER_MINUTE]);
    const height = y + POST_ROLL * PX_PER_MINUTE + 260;
    const at = (t) => yAt(anchors, t);
    const segments = [];
    for (let i = 0; i < rows.length; i++) {
      const row = rows[i];
      if (row.t2 !== undefined) segments.push({from: row.t, to: row.t2, kind: "dwell"});
      const next = rows[i + 1];
      if (!next) continue;
      const leave = row.t2 ?? row.t;
      const moving = next.train && (row.train === next.train) && ["departure", "stop", "pass"].includes(row.kind);
      segments.push({from: leave, to: next.t, kind: moving ? "run" : "idle"});
    }
    const trains = [...starts].map((index) => {
      const number = rows[index].train;
      const info = (duty.trains || []).find((train) => train.number === number) || {};
      return {row: index, number, from: info.from || "", to: info.to || "", vmax: info.vmax || null};
    });
    return {rows, anchors, height, segments, trains, gaps, start: startT, end: endT, y: at};
  }

  function yAt(anchors, t) {
    if (t <= anchors[0][0]) return anchors[0][1];
    for (let i = 1; i < anchors.length; i++) {
      const [t1, y1] = anchors[i];
      if (t <= t1) {
        const [t0, y0] = anchors[i - 1];
        return t1 === t0 ? y1 : y0 + (t - t0) / (t1 - t0) * (y1 - y0);
      }
    }
    return anchors[anchors.length - 1][1];
  }

  // Träffklockans sekund på dygnet, lagd på tjänstens löpande axel: det
  // dygnsvarv som ligger närmast tjänsten.
  function axisMinutes(clockSeconds, plan) {
    const base = clockSeconds / 60;
    const middle = (plan.start + plan.end) / 2;
    let best = base;
    for (const k of [-1, 1, 2]) {
      const candidate = base + k * DAY;
      if (Math.abs(candidate - middle) < Math.abs(best - middle)) best = candidate;
    }
    return best;
  }

  // Nästa moment efter nu: avgång, ankomst, uppehåll eller tjänstens slut.
  function moments(plan) {
    const list = [];
    for (const row of plan.rows) {
      if (row.kind === "stop") {
        list.push({t: row.t, kind: "arrival", row});
        list.push({t: row.t2, kind: "departure", row});
      } else {
        list.push({t: row.t, kind: row.kind, row});
      }
    }
    return list;
  }

  function next(plan, now) {
    return moments(plan).find((moment) => moment.t > now) || null;
  }

  // Stationen närmast NU, som förslag när föraren checkar in.
  function nearestStation(plan, now) {
    let best = null;
    for (const moment of moments(plan)) {
      if (!moment.row.station) continue;
      if (!best || Math.abs(moment.t - now) < Math.abs(best.t - now)) best = moment;
    }
    return best ? best.row : null;
  }

  function stations(plan) {
    const seen = [];
    for (const row of plan.rows) if (row.station && !seen.includes(row.station)) seen.push(row.station);
    return seen;
  }

  // Hur synlig en incheckning är: full i tio minuter, sedan mjukt ut på fem.
  function fade(ageSeconds, visible = 600, fadeSeconds = 300) {
    if (ageSeconds <= visible) return {opacity: 1, gray: 0};
    const k = Math.min(1, (ageSeconds - visible) / fadeSeconds);
    const eased = k * k * (3 - 2 * k);
    return {opacity: 1 - eased, gray: eased};
  }

  return {build, yAt, axisMinutes, next, nearestStation, stations, fade, minutes, label, PX_PER_MINUTE};
});
