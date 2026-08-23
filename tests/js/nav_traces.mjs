// Replays the key sequences dump_traces.cpp runs, through the web navigation
// state machine, in the format golden_traces.txt holds.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { LocalNavigationState, INPUT_LOCK_MS } = require("../../src/tmbox_gateway/web/tmbox-nav.js");

// Referensstationen och de tolv spåren bor i en delad modul, så att
// flödeskartan i webbadmin driver exakt samma sekvenser som guldfilen är
// avtryckt ur. Två listor av samma tolv scenarier är två som glider isär.
const { config, twoMovements, withCases, TRACES } = require(
  "../../src/tmbox_gateway/web/tmbox-fixtures.js"
);

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

for (const trace of TRACES) {
  const snapshot = trace.snapshot === "cases" ? withCases() : twoMovements();
  run(trace.name, trace.keys, snapshot, trace.allowed, trace.pace);
}

process.stdout.write(out.join("\n") + "\n");
