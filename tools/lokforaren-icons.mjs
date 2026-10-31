// Bygger Lokförarens app-ikoner: stationsskylten med "LF" i samma ram och
// vikt som "TM" i TrainMeets ikon (familjevarianten, som TrainCatalogs "TC").
// Kör igen när ikonen ändras: node tools/lokforaren-icons.mjs
// Kräver Playwright med Chromium (finns i CI och utvecklingsmiljön).
import { createRequire } from "node:module";
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = createRequire(import.meta.url)("playwright");
const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "tmbox_gateway", "web", "lokforaren");

// Bokstäverna är raka staplar med samma mått som T:et i TrainMeets ikon:
// stam 66,4 enheter, versalhöjd 329,7, tvärslå 57, hörnradie 5,65.
const R = 5.65, STEM = 66.42, CAP = 329.728, BAR = 57;
const bar = (x, y, w, h) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${R}"/>`;
const L = (x) => bar(x, -CAP, STEM, CAP) + bar(x, -BAR, 165, BAR);
const F = (x) => bar(x, -CAP, STEM, CAP) + bar(x, -CAP, 172, BAR) + bar(x, -199, 150, 54);
const letters = `<g transform="translate(330.5 676.86)" fill="#FFFFFF">${L(0)}${F(191)}</g>`;

export const svg = (square = false) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="64" height="64" role="img" aria-label="Lokföraren">
<rect width="1024" height="1024"${square ? "" : ' rx="225.28"'} fill="#1D4ED8"/>
<rect x="102.4" y="102.4" width="819.2" height="819.2" rx="133.12" fill="none" stroke="#FFFFFF" stroke-width="30.72"/>
${letters}
</svg>
`;

const browser = await chromium.launch();
const page = await browser.newPage();
async function png(name, size, square) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(`<html><body style="margin:0;background:transparent">${svg(square).replace('width="64" height="64"', `width="${size}" height="${size}"`)}</body></html>`);
  writeFileSync(join(OUT, name), await page.locator("svg").screenshot({ omitBackground: true }));
}
writeFileSync(join(OUT, "ikon.svg"), svg(false));
await png("ikon-180.png", 180, true);       // iPhone: hemskärmen rundar hörnen själv
await png("ikon-192.png", 192, false);
await png("ikon-512.png", 512, false);
await png("ikon-maskable-512.png", 512, true); // Android: ramen ligger inom säkra zonen
await png("ikon-32.png", 32, false);
await browser.close();
