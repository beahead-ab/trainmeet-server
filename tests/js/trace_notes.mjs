// Skriver ut, per spår, vilka tangenter som faktiskt ignorerades och vilka
// som skickade. Det Python-testet bredvid jämför mot notisernas påståenden.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { LocalNavigationState } = require("../../src/tmbox_gateway/web/tmbox-nav.js");
const { config, twoMovements, withCases, TRACES } = require(
  "../../src/tmbox_gateway/web/tmbox-fixtures.js"
);

const out = [];
for (const trace of TRACES) {
  const snapshot = trace.snapshot === "cases" ? withCases() : twoMovements();
  if (trace.allowed.length && snapshot.movements.length) {
    snapshot.movements[0].allowed_actions = trace.allowed;
  }
  const nav = new LocalNavigationState();
  nav.show("StationOverview", 0);
  let now = 10000;
  const ignored = new Set(), sent = new Set();
  for (const key of trace.keys) {
    const result = nav.press(key, now, config, snapshot);
    now += trace.pace;
    if (result.outcome === "Ignored") ignored.add(key);
    if (result.outcome === "Send") sent.add(key);
  }
  out.push(JSON.stringify({
    name: trace.name, note: trace.note,
    ignored: [...ignored], sent: [...sent],
  }));
}
process.stdout.write(out.join("\n") + "\n");
