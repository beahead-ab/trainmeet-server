// Rebuilds the fixtures dump_golden.cpp uses, renders them with the web
// renderer, and prints the same format golden_frames.txt holds. The Python
// test diffs the two.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { render } = require("../../src/tmbox_gateway/web/tmbox-render.js");

const config = {
  station_id: "st-cda", code: "CDA", name: "Charlottendal",
  tracks: [
    { id: "track-cda-1a", display_label: "1A" },
    { id: "track-cda-1b", display_label: "1B" },
    { id: "track-cda-2a", display_label: "2A" },
    { id: "track-cda-2b", display_label: "2B" },
  ],
  connections: [
    { connection_id: "connection-cda-vst", other_station_code: "VST", track_type: "single" },
    { connection_id: "connection-cda-kun", other_station_code: "KUN", track_type: "double" },
  ],
};

const snapshot = {
  station_id: "st-cda",
  clock: { time: "09:00", running: true },
  movements: [
    { id: "movement-421-cda", train_number: "421", arrival_time: "", departure_time: "09:20",
      departure: "none", arrival: "none", assignedTrackId: "track-cda-1b", crewReady: false,
      allowed_actions: ["train.position.set"] },
    { id: "movement-428-cda", train_number: "428", arrival_time: "09:41", departure_time: "",
      departure: "none", arrival: "none", assignedTrackId: "track-cda-2a", crewReady: false,
      allowed_actions: ["train.approaching", "train.arrived"] },
  ],
  active_clearances: [
    { clearance_id: "clr-1", movement_id: "movement-421-cda",
      connection_id: "connection-cda-vst", status: "waiting",
      from_station_id: "st-vst", to_station_id: "st-cda" },
  ],
  line_messages: [
    { message_id: "msg-1", connection_id: "connection-cda-vst",
      status: "delivered_to_device", from_station_id: "st-vst" },
  ],
};

const CASES = [
  ["identity", "Identity", -1],
  ["awaiting-assignment", "AwaitingAssignment", -1],
  ["station-overview", "StationOverview", -1],
  ["movement-departure", "MovementDetail", 0],
  ["movement-arrival", "MovementDetail", 1],
  ["track-picker", "TrackPicker", 0],
  ["clearance-inbox", "ClearanceInbox", 0],
  ["line-inbox", "LineInbox", 0],
  ["command-accepted", "CommandAccepted", -1],
  ["command-rejected", "CommandRejected", -1],
];

const GEOMETRIES = [
  ["16x2", { rows: 2, cols: 16, supportsSwedish: false }],
  ["20x2", { rows: 2, cols: 20, supportsSwedish: false }],
  ["16x4", { rows: 4, cols: 16, supportsSwedish: false }],
  ["20x4", { rows: 4, cols: 20, supportsSwedish: false }],
];

const out = [
  "# Genererad av dump_golden.cpp - redigera inte for hand.",
  "# Varje rad ar exakt sa bred som geometrin sager.",
];
for (const [geometryName, geometry] of GEOMETRIES) {
  for (const [caseName, screen, movement] of CASES) {
    const view = {
      screen, device_code: "TMBOX-A7K2C3", selected_movement: movement,
      selected_track: 0, selected_case: 0, reason: "spar_upptaget",
    };
    out.push("", `[${geometryName} ${caseName}]`);
    for (const line of render(geometry, view, config, snapshot)) out.push(`|${line}|`);
  }
}
process.stdout.write(out.join("\n") + "\n");
