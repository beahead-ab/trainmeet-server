#!/usr/bin/env node

import { writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1]);
}

const slug = args.get("--slug");
const output = args.get("--output");
const baseURL = process.env.SUPABASE_URL || process.env.VITE_SUPABASE_URL;
const apiKey = process.env.SUPABASE_KEY || process.env.VITE_SUPABASE_PUBLISHABLE_KEY;

if (!slug || !output || !baseURL || !apiKey) {
  console.error(
    "Användning: SUPABASE_URL=… SUPABASE_KEY=… export-trainmeet-runtime.mjs " +
      "--slug <slug> --output <fil>",
  );
  process.exit(2);
}

const headers = {
  apikey: apiKey,
  Authorization: `Bearer ${apiKey}`,
};

async function select(table, columns, filters = [], { optional = false } = {}) {
  const url = new URL(`/rest/v1/${table}`, baseURL);
  url.searchParams.set("select", columns);
  for (const [key, value] of filters) url.searchParams.append(key, value);
  const response = await fetch(url, { headers });
  if (!response.ok) {
    if (optional && (response.status === 404 || response.status === 400)) return [];
    throw new Error(`${table}: ${response.status} ${await response.text()}`);
  }
  return response.json();
}

const [meet] = await select(
  "meets",
  "id,name,slug,active_day,dispatch_mode,internal_clock_time,internal_clock_speed,internal_clock_show_seconds,available_clock_styles",
  [["slug", `eq.${slug}`]],
);
if (!meet) throw new Error(`Träffen ${slug} finns inte`);

const meetFilter = [["meet_id", `eq.${meet.id}`]];
const [
  meetStations,
  trainRows,
  routeRows,
  segmentRows,
  preferenceRows,
  explicitPanels,
  autonomousLinks,
  stopReasonRows,
] =
  await Promise.all([
    select("meet_stations", "station_id,is_autonomous,is_topology_branch", meetFilter),
    select(
      "trains",
      "id,train_number,station_id,station,track,days,arrival_time,departure_time,arrival_from,departure_to,arrival_from_next,departure_to_next,sort_time,train_type,no_stop,note,manual_sort_order",
      meetFilter,
    ),
    select(
      "train_routes",
      "id,train_number,station_id,station_name,stop_order,arrival_time,departure_time",
      meetFilter,
    ),
    select(
      "track_segments",
      "id,from_station_id,to_station_id,track_type,tambox_key_from,tambox_key_to,display_side_from,display_side_to,display_order_from,display_order_to",
      meetFilter,
    ),
    select(
      "track_segment_preferences",
      "from_station_id,to_station_id,track_type,tambox_key_from,tambox_key_to,display_side_from,display_side_to,display_order_from,display_order_to",
      meetFilter,
      { optional: true },
    ),
    select(
      "runtime_panels",
      "id,station_id,name,sort_order",
      [...meetFilter, ["order", "sort_order.asc"]],
      { optional: true },
    ),
    select(
      "autonomous_station_links",
      "id,autonomous_station_id,related_station_id,direction",
      meetFilter,
      { optional: true },
    ),
    select(
      "clock_stop_reasons",
      "key,label,meet_id,sort_order",
      [
        ["clock_type", "eq.internal"],
        ["or", `(meet_id.is.null,meet_id.eq.${meet.id})`],
        ["order", "sort_order.asc"],
      ],
      { optional: true },
    ),
  ]);

const explicitSlots = explicitPanels.length
  ? await select(
      "runtime_panel_slots",
      "panel_id,slot_key,track_segment_id",
      [["panel_id", `in.(${explicitPanels.map((panel) => panel.id).join(",")})`]],
      { optional: true },
    )
  : [];

const stationIds = meetStations.map((row) => row.station_id);
const stationRows = await select(
  "stations",
  "id,code,name,aliases",
  [["id", `in.(${stationIds.join(",")})`], ["order", "code.asc"]],
);

const preferences = new Map(
  preferenceRows.map((row) => [pairKey(row.from_station_id, row.to_station_id), row]),
);
const segments = segmentRows.map((segment) => ({
  ...segment,
  ...(preferences.get(pairKey(segment.from_station_id, segment.to_station_id)) || {}),
  id: segment.id,
}));

const stationMeta = new Map(meetStations.map((row) => [row.station_id, row]));
const timetableRows = mergeDuplicateTrainRows(trainRows);
const operatingPointsByStation = buildOperatingPoints(stationRows, timetableRows);
const diagramOrder = buildDiagramOrder(stationRows, segments);
const stations = stationRows.map((station) => ({
  ...station,
  operating_points: operatingPointsByStation.get(station.id) || [],
  diagram_order: diagramOrder.indexOf(station.id),
  is_autonomous: Boolean(stationMeta.get(station.id)?.is_autonomous),
  is_topology_branch: Boolean(stationMeta.get(station.id)?.is_topology_branch),
}));
const stationNames = new Map(stations.map((station) => [station.id, station.name]));
const services = buildServices(timetableRows, routeRows, stationNames);
const routes = services.flatMap((service) =>
  service.stops.map((stop) => ({
    id: `${service.id}-${stop.stop_order}`,
    service_id: service.id,
    train_number: service.train_number,
    days: service.days,
    ...stop,
  })),
);

const allClockStyles = [
  "swiss", "swedish", "norwegian", "danish", "german", "finnish",
  "polish", "dutch", "french", "italian", "american", "digital",
];
const meetStopReasons = stopReasonRows.filter((row) => row.meet_id === meet.id);
const stopReasons = (meetStopReasons.length
  ? meetStopReasons
  : stopReasonRows.filter((row) => !row.meet_id)
).map((row) => ({ key: row.key, label: row.label }));
const publishedAt = new Date().toISOString();
const runtimePackage = {
  schema_version: 2,
  publication_id: `publication-${meet.id}-${randomUUID()}`,
  published_at: publishedAt,
  meet: {
    id: meet.id,
    name: meet.name,
    slug: meet.slug,
    active_day: meet.active_day || "Dagl",
    timezone: "Europe/Stockholm",
    default_dispatch_mode: meet.dispatch_mode === "anmalan" ? "direct" : "clearance",
    clock_time: String(meet.internal_clock_time || "12:00").slice(0, 5),
  },
  clock: {
    source: "local",
    start_time: String(meet.internal_clock_time || "12:00").slice(0, 5),
    speed: Number(meet.internal_clock_speed || 1),
    show_seconds: meet.internal_clock_show_seconds !== false,
    available_styles: meet.available_clock_styles?.length
      ? meet.available_clock_styles
      : allClockStyles,
    stop_reasons: stopReasons.length
      ? stopReasons
      : [
          { key: "trafikstopp", label: "Trafikstopp" },
          { key: "rast", label: "Rast" },
          { key: "tekniskt", label: "Tekniskt stopp" },
        ],
  },
  stations,
  connections: segments.map((segment) => ({
    id: segment.id,
    station_a_id: segment.from_station_id,
    station_b_id: segment.to_station_id,
    track_type: segment.track_type === "double" ? "double" : "single",
    display_side_a: segment.display_side_from || "right",
    display_side_b: segment.display_side_to || "left",
    display_order_a: Number(segment.display_order_from || 0),
    display_order_b: Number(segment.display_order_to || 0),
    tambox_key_a: segment.tambox_key_from || null,
    tambox_key_b: segment.tambox_key_to || null,
  })),
  autonomous_links: autonomousLinks,
  panels: buildPanels(stations, segments, explicitPanels, explicitSlots),
  trains: timetableRows.map((train) => {
    const operatingPoint = (operatingPointsByStation.get(train.station_id) || []).find(
      (point) => normalizeStationLabel(point.source_name) === normalizeStationLabel(train.station),
    );
    return {
      ...train,
      ...(operatingPoint ? { operating_point_id: operatingPoint.id } : {}),
      service_id: serviceId(train),
      days: normalizeDays(train.days),
    };
  }),
  routes,
  services,
  display: {
    graph_station_order: diagramOrder,
    topology_branch_station_ids: stations
      .filter((station) => station.is_topology_branch)
      .map((station) => station.id),
    default_theme: "dark",
  },
};

await writeFile(output, `${JSON.stringify(runtimePackage, null, 2)}\n`, "utf8");
console.log(
  JSON.stringify({
    output,
    meet: meet.name,
    stations: stations.length,
    connections: segments.length,
    services: services.length,
    source_timetable_rows: trainRows.length,
    timetable_rows: timetableRows.length,
    merged_duplicate_rows: trainRows.length - timetableRows.length,
    routes: routes.length,
    panels: runtimePackage.panels.length,
  }),
);

function normalizeDays(value) {
  const aliases = { M: "Mån", Ti: "Tis", On: "Ons", O: "Ons", To: "Tor", Fr: "Fre", Lö: "Lör", Sö: "Sön" };
  const text = String(value || "Dagl").trim();
  if (/^dagl(igen)?$/i.test(text)) return "Dagl";
  return text
    .split(",")
    .map((part) => part.trim().split("-").map((day) => aliases[day.trim()] || day.trim()).join("-"))
    .join(",");
}

function normalizeStationLabel(value) {
  return String(value || "")
    .trim()
    .toLocaleLowerCase("sv-SE")
    .replace(/\s+/g, " ");
}

function normalizedValue(value) {
  return String(value ?? "").trim().replace(/\s+/g, " ");
}

function movementFingerprint(train) {
  return JSON.stringify([
    train.station_id,
    normalizedValue(train.station).toLocaleLowerCase("sv-SE"),
    normalizedValue(train.train_number),
    normalizeDays(train.days),
    normalizedValue(train.track),
    normalizedValue(train.arrival_time),
    normalizedValue(train.departure_time),
    normalizedValue(train.arrival_from).toLocaleLowerCase("sv-SE"),
    normalizedValue(train.departure_to).toLocaleLowerCase("sv-SE"),
    normalizedValue(train.arrival_from_next).toLocaleLowerCase("sv-SE"),
    normalizedValue(train.departure_to_next).toLocaleLowerCase("sv-SE"),
    normalizedValue(train.sort_time),
    normalizedValue(train.train_type).toLocaleLowerCase("sv-SE"),
    Boolean(train.no_stop),
  ]);
}

function mergeDuplicateTrainRows(trains) {
  const groups = new Map();
  for (const train of trains) {
    const key = movementFingerprint(train);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(train);
  }
  return [...groups.values()].map((rows) => {
    const ordered = [...rows].sort((a, b) => String(a.id).localeCompare(String(b.id)));
    const notes = [...new Set(
      ordered
        .map((row) => String(row.note || "").trim())
        .filter(Boolean),
    )];
    const manualSortOrders = ordered
      .map((row) => Number(row.manual_sort_order))
      .filter(Number.isFinite);
    return {
      ...ordered[0],
      note: notes.join("\n"),
      manual_sort_order: manualSortOrders.length ? Math.min(...manualSortOrders) : null,
      source_row_ids: ordered.map((row) => row.id),
    };
  });
}

function operatingPointCode(stationName, sourceName) {
  const parent = normalizeStationLabel(stationName);
  const source = String(sourceName || "").trim();
  const normalizedSource = normalizeStationLabel(source);
  if (normalizedSource.startsWith(`${parent} `)) {
    return source.slice(String(stationName).trim().length).trim().toLocaleUpperCase("sv-SE");
  }
  return source.split(/\s+/).at(-1).toLocaleUpperCase("sv-SE");
}

function operatingPointId(stationId, sourceName) {
  const label = normalizeStationLabel(sourceName)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return `operating-point-${stationId}-${label}`;
}

function buildOperatingPoints(stations, trains) {
  const labelsByStation = new Map();
  for (const train of trains) {
    if (!train.station_id || !String(train.station || "").trim()) continue;
    if (!labelsByStation.has(train.station_id)) labelsByStation.set(train.station_id, new Map());
    const normalized = normalizeStationLabel(train.station);
    if (!labelsByStation.get(train.station_id).has(normalized)) {
      labelsByStation.get(train.station_id).set(normalized, String(train.station).trim());
    }
  }

  return new Map(stations.map((station) => {
    const sourceNames = [...(labelsByStation.get(station.id)?.values() || [])];
    if (sourceNames.length < 2) return [station.id, []];
    const points = sourceNames
      .map((sourceName) => ({
        id: operatingPointId(station.id, sourceName),
        code: operatingPointCode(station.name, sourceName),
        name: operatingPointCode(station.name, sourceName),
        kind: /(^|\s)(rbg|ranger)(\s|$)/i.test(sourceName) ? "yard" : "station",
        aliases: [sourceName],
        source_name: sourceName,
        tracks: [...new Set(
          trains
            .filter((train) => train.station_id === station.id && normalizeStationLabel(train.station) === normalizeStationLabel(sourceName))
            .map((train) => String(train.track || "").trim())
            .filter(Boolean),
        )].sort(),
      }))
      .sort((a, b) => a.name.localeCompare(b.name, "sv-SE"));
    return [station.id, points];
  }));
}

function serviceId(train) {
  const number = String(train.train_number || "okant").replace(/[^a-zA-Z0-9_-]/g, "-");
  const days = normalizeDays(train.days).replace(/[^a-zA-Z0-9ÅÄÖåäö_-]/g, "-");
  return `service-${number}-${days}`;
}

function timeMinutes(value) {
  const match = String(value || "").match(/^(\d{1,2}):(\d{2})/);
  return match ? Number(match[1]) * 60 + Number(match[2]) : 24 * 60;
}

function orderAcrossMidnight(rows) {
  const sorted = [...rows].sort((a, b) => timeMinutes(a.sort_time) - timeMinutes(b.sort_time));
  if (sorted.length < 2) return sorted;
  let largestGap = -1;
  let startIndex = 0;
  for (let index = 0; index < sorted.length; index += 1) {
    const current = timeMinutes(sorted[index].sort_time);
    const next = timeMinutes(sorted[(index + 1) % sorted.length].sort_time) +
      (index + 1 === sorted.length ? 24 * 60 : 0);
    if (next - current > largestGap) {
      largestGap = next - current;
      startIndex = (index + 1) % sorted.length;
    }
  }
  return [...sorted.slice(startIndex), ...sorted.slice(0, startIndex)];
}

function buildServices(trains, routes, names) {
  const groups = new Map();
  for (const train of trains) {
    const id = serviceId(train);
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(train);
  }
  return [...groups.entries()].map(([id, rows]) => {
    const routeOrder = routes
      .filter((route) => String(route.train_number) === String(rows[0]?.train_number))
      .sort((a, b) => Number(a.stop_order) - Number(b.stop_order));
    const movements = new Map(rows.map((row) => [row.station_id, row]));
    const ordered = routeOrder.length
      ? routeOrder.map((route) => ({
          ...movements.get(route.station_id),
          ...route,
          sort_time: route.arrival_time || route.departure_time || movements.get(route.station_id)?.sort_time,
        }))
      : orderAcrossMidnight(rows);
    const stops = [];
    let dayOffset = 0;
    let previousMinute = -1;
    for (const row of ordered) {
      const minute = timeMinutes(row.sort_time);
      if (previousMinute >= 0 && minute < previousMinute) dayOffset += 1;
      previousMinute = minute;
      stops.push({
        station_id: row.station_id,
        station_name: row.station || row.station_name || names.get(row.station_id) || "Okänd station",
        stop_order: stops.length,
        arrival_time: row.arrival_time || null,
        departure_time: row.departure_time || null,
        service_day_offset: dayOffset,
        service_minute: minute + dayOffset * 24 * 60,
      });
    }
    return {
      id,
      train_number: rows[0]?.train_number,
      days: normalizeDays(rows[0]?.days),
      train_type: rows[0]?.train_type || "person",
      stops,
    };
  });
}

function buildDiagramOrder(stations, segments) {
  // Match the order used by TrainMeet's existing TrainGraph: segment insertion
  // order is significant, because it preserves the organizer's established
  // top-to-bottom diagram instead of re-sorting the same topology by code.
  const adjacency = new Map();
  const connectedIDs = new Set();
  for (const segment of segments) {
    connectedIDs.add(segment.from_station_id);
    connectedIDs.add(segment.to_station_id);
    if (!adjacency.has(segment.from_station_id)) adjacency.set(segment.from_station_id, new Set());
    if (!adjacency.has(segment.to_station_id)) adjacency.set(segment.to_station_id, new Set());
    adjacency.get(segment.from_station_id).add(segment.to_station_id);
    adjacency.get(segment.to_station_id).add(segment.from_station_id);
  }
  if (!connectedIDs.size) return stations.map((station) => station.id);

  let start = "";
  let maximumDegree = -1;
  for (const [stationId, neighbors] of adjacency) {
    if (neighbors.size > maximumDegree) {
      maximumDegree = neighbors.size;
      start = stationId;
    }
  }
  for (const [stationId, neighbors] of adjacency) {
    if (neighbors.size === 1) {
      start = stationId;
      break;
    }
  }

  const visited = new Set([start]);
  const result = [];
  const queue = [start];
  while (queue.length) {
    const stationId = queue.shift();
    result.push(stationId);
    for (const neighbor of adjacency.get(stationId) || []) {
      if (!visited.has(neighbor)) {
        visited.add(neighbor);
        queue.push(neighbor);
      }
    }
  }
  for (const station of stations) {
    if (!visited.has(station.id)) result.push(station.id);
  }
  return result;
}

function buildPanels(stations, segments, panels, slots) {
  const keys = ["A", "B", "C", "D"];
  const result = [];
  for (const station of stations) {
    const stationPanels = panels
      .filter((panel) => panel.station_id === station.id)
      .sort((a, b) => Number(a.sort_order) - Number(b.sort_order));
    if (stationPanels.length) {
      for (const panel of stationPanels) {
        const panelSlots = { A: null, B: null, C: null, D: null };
        for (const slot of slots.filter((value) => value.panel_id === panel.id)) {
          panelSlots[slot.slot_key] = slot.track_segment_id;
        }
        result.push({
          id: panel.id,
          station_id: station.id,
          name: panel.name,
          slots: panelSlots,
        });
      }
      continue;
    }

    const adjacent = segments
      .filter((segment) => segment.from_station_id === station.id || segment.to_station_id === station.id)
      .sort((a, b) => endpointOrder(a, station.id) - endpointOrder(b, station.id));
    const slots = { A: null, B: null, C: null, D: null };
    for (const segment of adjacent) {
      const preferred = endpointValue(segment, station.id, "tambox_key");
      const slot = keys.includes(preferred) && !slots[preferred]
        ? preferred
        : keys.find((key) => slots[key] === null);
      if (!slot) throw new Error(`${station.code} har fler än fyra anslutningar`);
      slots[slot] = segment.id;
    }
    result.push({
      id: `station-${station.id}-panel-1`,
      station_id: station.id,
      name: `${station.code} TMBox`,
      slots,
    });
  }
  return result;
}

function endpointValue(segment, stationId, field) {
  return segment[stationId === segment.from_station_id ? `${field}_from` : `${field}_to`];
}

function endpointOrder(segment, stationId) {
  return Number(endpointValue(segment, stationId, "display_order") || 0);
}

function pairKey(a, b) {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}
