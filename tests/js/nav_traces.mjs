// Replays the key sequences dump_traces.cpp runs, through the web navigation
// state machine, in the format golden_traces.txt holds.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { LocalNavigationState, INPUT_LOCK_MS } = require("../../src/tmbox_gateway/web/tmbox-nav.js");

const config = {
  station_id: "st-cda", code: "CDA", name: "Charlottendal",
  tracks: [
    { id: "track-cda-1a", display_label: "1A" }, { id: "track-cda-1b", display_label: "1B" },
    { id: "track-cda-2a", display_label: "2A" }, { id: "track-cda-2b", display_label: "2B" },
  ],
  connections: [
    { connection_id: "connection-cda-vst", other_station_code: "VST", track_type: "single" },
    { connection_id: "connection-cda-kun", other_station_code: "KUN", track_type: "double" },
  ],
};

function twoMovements() {
  return {
    station_id: "st-cda", clock: { time: "09:00", running: true },
    movements: [
      { id: "movement-421-cda", train_number: "421", arrival_time: "", departure_time: "09:20",
        departure: "none", arrival: "none", assignedTrackId: "track-cda-1b", crewReady: false,
        allowed_actions: ["train.position.set"] },
      { id: "movement-428-cda", train_number: "428", arrival_time: "09:41", departure_time: "",
        departure: "none", arrival: "none", assignedTrackId: "track-cda-2a", crewReady: false,
        allowed_actions: ["train.approaching", "train.arrived"] },
    ],
    active_clearances: [], line_messages: [],
  };
}

function withCases() {
  const snapshot = twoMovements();
  snapshot.active_clearances = [{ clearance_id: "clr-1", movement_id: "movement-421-cda",
    connection_id: "connection-cda-vst", status: "waiting",
    from_station_id: "st-vst", to_station_id: "st-cda" }];
  snapshot.line_messages = [{ message_id: "msg-1", connection_id: "connection-cda-vst",
    status: "delivered_to_device", from_station_id: "st-vst" }];
  return snapshot;
}

const out = ["# Genererad av dump_traces.cpp - redigera inte for hand."];

function run(name, keys, snapshot, allowed, step) {
  if (allowed.length && snapshot.movements.length) snapshot.movements[0].allowed_actions = allowed;
  const nav = new LocalNavigationState();
  nav.show("StationOverview", 0);
  let now = 10000;
  out.push("", `[${name}]`);
  for (const key of keys) {
    const result = nav.press(key, now, config, snapshot);
    now += step;
    let line = `${key} -> ${result.outcome} screen=${nav.view.screen}`
      + ` move=${nav.view.selected_movement} track=${nav.view.selected_track}`
      + ` conn=${nav.view.selected_connection}` + ` digits=${nav.view.lookup_digits || "-"}` + ` case=${nav.view.selected_case}`;
    if (result.outcome === "Send") {
      const command = result.command;
      line += ` action=${command.action}`;
      if (command.movement_id) line += ` movement=${command.movement_id}`;
      if (command.track_id) line += ` track_id=${command.track_id}`;
      if (command.connection_id) line += ` connection=${command.connection_id}`;
      if (command.train_number) line += ` train=${command.train_number}`;
      if (command.clearance_id) line += ` clearance=${command.clearance_id}`;
      if (command.message_id) line += ` message=${command.message_id}`;
      if (command.has_approved) line += ` approved=${command.approved ? "1" : "0"}`;
    }
    out.push(line);
  }
}

// A press per half second: the lock has always lapsed, so these show what
// each key means. Then a press every 100 ms - faster than an operator, but
// exactly what a stuck key or a bounced contact produces.
const UNHURRIED = INPUT_LOCK_MS;
const HURRIED = 100;

run("browse-and-position", "CCA*", twoMovements(), ["train.position.set"], UNHURRIED);
run("track-change-refused-then-allowed", "CBA", twoMovements(), ["train.position.set"], UNHURRIED);
run("track-picker-picks-second", "CBCA", twoMovements(), ["train.track.change"], UNHURRIED);
run("hash-opens-clearance-and-a-settles", "#A", withCases(), [], UNHURRIED);
run("hash-then-b-refuses", "#B", withCases(), [], UNHURRIED);
run("line-message-only-acknowledges", "##AB", withCases(), [], UNHURRIED);
run("star-always-returns", "C#*", withCases(), [], UNHURRIED);
run("nothing-allowed-stays-silent", "CA", twoMovements(), ["train.track.change"], UNHURRIED);

run("clearance-request-picks-the-neighbour", "CACA", twoMovements(), ["clearance.request"], UNHURRIED);
run("digits-look-up-a-train", "421BA", twoMovements(), [], UNHURRIED);
run("hurried-presses-are-swallowed", "CCA", twoMovements(), ["train.position.set"], HURRIED);
run("hurried-clearance-answer-is-swallowed", "#A", withCases(), [], HURRIED);

process.stdout.write(out.join("\n") + "\n");
