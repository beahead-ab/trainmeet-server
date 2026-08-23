// Kontrollerar de två påståenden dokumentationsvyn vilar på: att spårens
// lugna tempo är samma konstant som inmatningslåset, och att skärmkatalogen
// ritar exakt de rutor guldfilen håller.
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { INPUT_LOCK_MS } = require("../../src/tmbox_gateway/web/tmbox-nav.js");
const { render } = require("../../src/tmbox_gateway/web/tmbox-render.js");
const fx = require("../../src/tmbox_gateway/web/tmbox-fixtures.js");

const frames = {};
for (const [geometryName, geometry] of fx.GEOMETRIES) {
  for (const [caseName, screen, movement] of fx.CASES) {
    frames[`${geometryName} ${caseName}`] =
      render(geometry, fx.viewFor(screen, movement), fx.config, fx.snapshot);
  }
}

process.stdout.write(JSON.stringify({
  inputLock: INPUT_LOCK_MS,
  unhurried: fx.UNHURRIED,
  hurried: fx.HURRIED,
  cases: fx.CASES.map(([name]) => name),
  traces: fx.TRACES.map((t) => t.name),
  frames,
}) + "\n");
