// Rebuilds the fixtures dump_golden.cpp uses, renders them with the web
// renderer, and prints the same format golden_frames.txt holds. The Python
// test diffs the two.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { render } = require("../../src/tmbox_gateway/web/tmbox-render.js");

// Referensstationen, fallen och geometrierna bor i en delad modul, så att
// skärmkatalogen i webbadmin ritar ur exakt samma fixturer som guldfilen är
// avtryckt ur. Två kopior av en referensstation är två som glider isär.
const { config, snapshot, CASES, GEOMETRIES, viewFor } = require(
  "../../src/tmbox_gateway/web/tmbox-fixtures.js"
);

const out = [
  "# Genererad av dump_golden.cpp - redigera inte for hand.",
  "# Varje rad ar exakt sa bred som geometrin sager.",
];
for (const [geometryName, geometry] of GEOMETRIES) {
  for (const [caseName, screen, movement] of CASES) {
    const view = viewFor(screen, movement);
    out.push("", `[${geometryName} ${caseName}]`);
    for (const line of render(geometry, view, config, snapshot)) out.push(`|${line}|`);
  }
}
process.stdout.write(out.join("\n") + "\n");
