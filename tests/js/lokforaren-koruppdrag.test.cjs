// Köruppdragen till Lokföraren läses i administratörens webbläsare (#225):
// tjänsterna ur planeringsfilen (JSON) och sidorna ur köruppdragens PDF.
// Här: planeringsprogrammets $id/$ref-form, förberedelse och avslutning,
// växling på en station, midnatt, sidmatchningen och att den medföljande
// pdf.js läser en riktig PDF, också å, ä, ö, æ och ø.
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const reader = require("../../src/tmbox_gateway/web/lokforaren-koruppdrag.js");
const { tinyPdf, tinyPlan } = require("./lokforaren-fixtures.cjs");

const PDFJS = path.join(__dirname, "../../src/tmbox_gateway/web/pdfjs");
const PDFLib = require("../../src/tmbox_gateway/web/pdflib/pdf-lib.min.js");

test("tjänsterna i planeringsfilen blir Lokförarens tjänster, med anvisningar", () => {
  const result = reader.fromPlan(JSON.parse(JSON.stringify(tinyPlan())));
  assert.equal(result.name, "Grimslöv 2027");
  assert.deepEqual(result.duties.map((duty) => duty.number), ["211", "212", "213"]);
  assert.deepEqual(result.warnings, ["Tjänst 214 har inga tåg med tider och togs inte med"]);
  const [d211, d212, d213] = result.duties;
  // Tjänsten börjar när föraren infinner sig (första tågets ankomsttid) och
  // slutar vid sista tågets avgångstid.
  assert.deepEqual(
    { operator: d211.operator, vehicle: d211.vehicle, start: d211.start, end: d211.end, trains: d211.trains.map((train) => train.number) },
    { operator: "CargoNet AS", vehicle: "Di4", start: "17:10", end: "21:02", trains: ["G 5453", "G 5456"] });
  const days = { mo_we_fr: [1, 3, 5], tu_th_sa: [2, 4, 6] };
  assert.deepEqual(d211.events.map(({ kind, station, time, arrival, track, instructions }) => [kind, station, time || arrival, track || "", instructions || []]), [
    ["duty_start", "Spiten", "17:10", "", []],
    ["departure", "Spiten", "17:30", "1", [
      { kind: "from_parking", vehicles: ["CN 02 Di4"], days: days.mo_we_fr },
      { kind: "from_parking", vehicles: ["CN 03 Di4"], days: days.tu_th_sa }]],
    // Möte med persontåget i motsatt riktning, rundgång (planen säger alltid),
    // nyckeln till Kjøllstad och skjut vagnarna.
    ["stop", "Tveiten", "17:58", "2", [
      { kind: "crosses", meets: [{ train: "VY RE 2511", from: "18:00", to: "18:05" }] },
      { kind: "circulate" },
      { kind: "pick_up_key", key: "K16", place: "Kjøllstad" },
      { kind: "push_wagons" }]],
    // Tåget vänder i Kjøllstad: koppla loss och gör rundgång.
    ["arrival", "Kjøllstad", "18:25", "1", [
      { kind: "uncouple", vehicles: ["CN 02 Di4"], days: days.mo_we_fr },
      { kind: "uncouple", vehicles: ["CN 03 Di4"], days: days.tu_th_sa },
      { kind: "circulate" }]],
    ["departure", "Kjøllstad", "19:35", "1", [
      { kind: "couple", vehicles: ["CN 02 Di4"], days: days.mo_we_fr },
      { kind: "couple", vehicles: ["CN 03 Di4"], days: days.tu_th_sa }]],
    ["pass", "Tveiten", "20:02", "2", []],
    ["arrival", "Spiten", "20:42", "1", [
      { kind: "to_parking", vehicles: ["CN 02 Di4"], days: days.mo_we_fr },
      { kind: "to_parking", vehicles: ["CN 03 Di4"], days: days.tu_th_sa }]],
    ["duty_end", "Spiten", "21:02", "", []],
  ]);
  assert.deepEqual([d211.events[0].notes, d211.events.at(-1).notes], [["Förberedelse"], ["Avslutning"]]);
  // Växling på en station: hela tiden där och tågslagets namn, inget tågnummer.
  assert.deepEqual([d212.start, d212.end, d212.trains, d212.events.map((event) => event.kind)],
    ["08:30", "11:30", [{ number: "Växling", from: "Tveiten", to: "Tveiten", vmax: 80 }], ["stop"]]);
  // Tjänstens egen starttid, och tider efter midnatt ("1.00:25:00").
  assert.deepEqual([d213.start, d213.end, d213.events.map((event) => event.time)], ["23:00", "00:45", ["23:00", "23:40", "00:25", "00:45"]]);
  assert.deepEqual(d213.events[2].notes, ["Ring tågklareraren"]);
});

test("samma anvisning för fordon som tillsammans går alla dagar sägs en gång", () => {
  const plan = tinyPlan();
  // Lok 03 går nu måndag, onsdag och fredag också: tillsammans alla dagar.
  const loco = plan.ScheduledObjects.$values[1];
  loco.ScheduleAssignments.$values[0].Sessions = 0x8000 | 0x3fff;
  const [d211] = reader.fromPlan(JSON.parse(JSON.stringify(plan))).duties;
  assert.deepEqual(d211.events[1].instructions, [
    { kind: "from_parking", vehicles: ["CN 02 Di4"], days: [1, 3, 5] },
    { kind: "from_parking", vehicles: ["CN 03 Di4"] },
  ]);
});

test("en fil som inte är en planeringsfil säger det", () => {
  assert.throws(() => reader.fromPlan([]), /ingen planeringsfil/);
  assert.throws(() => reader.fromPlan({ Name: "Stationstidtabell" }), /DriverDuties/);
});

test("kontrollsumman känner igen samma fil", () => {
  assert.equal(reader.fingerprint("{\"a\":1}"), reader.fingerprint("{\"a\":1}"));
  assert.notEqual(reader.fingerprint("{\"a\":1}"), reader.fingerprint("{\"a\":2}"));
  assert.match(reader.fingerprint("x".repeat(5000)), /^[0-9a-f]{16,}$/);
});

const duties = [
  { number: "211", trains: [{ number: "G 5453" }, { number: "G 5456" }] },
  { number: "212", trains: ["21"] },
  { number: "213", trains: [{ number: "G 5481" }] },
];

test("sidorna för varje tjänst hittas på tjänstens rubrik", () => {
  const pages = [
    ["Köruppdrag Grimslöv 2027", "Innehåll"],
    ["Innehåll", "Tjeneste 211 ... 3", "Tjeneste 212 ... 5", "Tjeneste 213 ... 6", "Tjenste 9 ... 7"],
    ["Tjeneste 211", "Spiten 17:30 G 5453"],
    ["Kjøllstad 19:35 G 5456"],
    ["Tjänst nr. 212", "Tveiten 08:30 Växling 21"],
    ["TJENESTE 213:", "Spiten 23:40 G 5481"],
    ["Tjeneste 9", "en tjänst som inte finns i planen"],
  ];
  const result = reader.matchPages(pages, duties);
  // Sidan med tjänst 9 hör inte till planen och läggs inte till 213.
  assert.deepEqual(result.pages, { 211: [3, 4], 212: [5, 5], 213: [6, 6] });
  assert.equal(result.total, 7);
  assert.deepEqual(result.warnings, []);
});

test("planeringsprogrammets häften: omslag, sidfötter och tjänster på åtta sidor", () => {
  // Så skriver planeringsprogrammet ut köruppdragen (Värnamo, 612 sidor):
  // omslaget har ordet och numret på olika rader, sidfötterna "Tjänst 1 –
  // Dagl – Sida 4", och mittsidan med tidtabellen har inget nummer alls.
  const cover = (word, number) => ["Topologi", "Värnamo Moduldagar", "Idp Ryt Ebh Hgt Gbg " + word, "Tri", number, "Dagligen"];
  const pages = [
    cover("Tjänst", "211"), ["Tjänst 211 – Dagl – Sida 4"], ["Tjänst i CN tåg G 5453", "Avg Spiten 1 17:30"], ["Inga fler tåg i denna tjänst.", "Tjänst 211 – Dagl – Sida 2 Tjänst 211 – Dagl – Sida 3"],
    cover("Tjeneste", "212"), ["Tjeneste 212 – Daglig – Side 8"], ["Skifteoppdrag CN", "Rise 08:30 11:30"], ["Tjeneste 212 – Daglig – Side 2 Tjeneste 212 – Daglig – Side 7"],
    ["Tjeneste 212 – Daglig – Side 6"], ["fortsatt"], ["mer"], ["Tjeneste 212 – Daglig – Side 4 Tjeneste 212 – Daglig – Side 5"],
    cover("Tjänst", "213"), ["Tjänst 213 – M,Ti – Sida 4"], ["Avg Spiten 23:40 G 5481"], ["Tjänst 213 – M,Ti – Sida 2 Tjänst 213 – M,Ti – Sida 3"],
  ];
  const result = reader.matchPages(pages, [
    { number: "211", trains: [{ number: "G 5453" }] }, { number: "212", trains: [{ number: "Skifteoppdrag" }] }, { number: "213", trains: [{ number: "G 5481" }] },
  ]);
  assert.deepEqual(result.pages, { 211: [1, 4], 212: [5, 12], 213: [13, 16] });
  assert.deepEqual(result.warnings, []);
});

test("saknade tjänster och sidor som inte nämner tjänstens tåg varnas", () => {
  const pages = [["Tjeneste 211", "Spiten 17:30 G 9999"], ["Tjeneste 212", "Tveiten 08:30 21"]];
  const result = reader.matchPages(pages, duties);
  assert.deepEqual(result.pages, { 211: [1, 1], 212: [2, 2] });
  assert.deepEqual(result.warnings, [
    "1 tjänster hittades inte i PDF:en: 213",
    "Sidorna 1–1 för tjänst 211 nämner inget av dess tåg (G 5453, G 5456)",
  ]);
});

test("utan rubriker känns tjänsten igen på sina tåg", () => {
  const pages = [["Spiten 17:30 G 5453", "Kjøllstad 19:35 G 5456"], ["fortsättning"], ["Spiten 23:40 G 5481"]];
  assert.deepEqual(reader.matchPages(pages, duties).pages, { 211: [1, 2], 213: [3, 3] });
});

test("text på samma höjd blir en rad, uppifrån och ned och från vänster", () => {
  const item = (str, x, y) => ({ str, transform: [11, 0, 0, 11, x, y], width: str.length * 5 });
  const rows = reader.lines([
    item("17:10", 112, 700.4), item("Spiten", 12, 701), item("Tjeneste 211", 6, 760),
    item("  ", 300, 700), item("G 5453", 154, 699.2), item("Tveiten", 12, 680),
  ]);
  assert.deepEqual(rows, ["Tjeneste 211", "Spiten 17:10 G 5453", "Tveiten"]);
});

test("pdf.js läser varje sida i en riktig PDF, med nordiska tecken, och sidorna hittas", async () => {
  const pdfjs = await import(path.join(PDFJS, "pdf.min.mjs"));
  pdfjs.GlobalWorkerOptions.workerSrc = path.join(PDFJS, "pdf.worker.min.mjs");
  const pdf = tinyPdf([
    [[40, 780, "Tjeneste 211"], [40, 740, "Spiten"], [160, 740, "17:10"], [240, 740, "G 5453"], [40, 720, "Tveiten"], [240, 720, "Rundgång"]],
    [[40, 740, "Kjøllstad"], [160, 740, "19:35"], [240, 740, "G 5456"]],
    [[40, 780, "Tjeneste 212"], [40, 740, "Tveiten"], [160, 740, "08:30"], [240, 740, "Ålö æøå 21"]],
  ]);
  const progress = [];
  const warn = console.log; console.log = () => {}; // pdf.js säger att canvas saknas i Node; texten behöver inget
  let pages;
  try {
    pages = await reader.extract(pdf, (step) => progress.push(step), pdfjs);
  } finally {
    console.log = warn;
  }
  assert.deepEqual(progress, [{ page: 1, pages: 3 }, { page: 2, pages: 3 }, { page: 3, pages: 3 }]);
  assert.deepEqual(pages, [
    ["Tjeneste 211", "Spiten 17:10 G 5453", "Tveiten Rundgång"],
    ["Kjøllstad 19:35 G 5456"],
    ["Tjeneste 212", "Tveiten 08:30 Ålö æøå 21"],
  ]);
  assert.deepEqual(reader.matchPages(pages, duties.slice(0, 2)).pages, { 211: [1, 2], 212: [3, 3] });
});

test("förlagan delas i en liten PDF per tjänst med bara dess sidor", async () => {
  const pdf = tinyPdf([
    [[40, 780, "Tjeneste 211"]], [[40, 780, "G 5453"]], [[40, 780, "Tjeneste 212"]], [[40, 780, "Tjeneste 213"]], [[40, 780, "G 5481"]],
  ]);
  const parts = [];
  const count = await reader.split(pdf, { 211: [1, 2], 212: [3, 3], 213: [4, 5] }, async (part) => { parts.push(part); }, PDFLib);
  assert.equal(count, 3);
  assert.deepEqual(parts.map(({ number, done, total }) => [number, done, total]), [["211", 1, 3], ["212", 2, 3], ["213", 3, 3]]);
  const pageCounts = [];
  for (const part of parts) {
    const document = await PDFLib.PDFDocument.load(part.bytes);
    pageCounts.push([document.getPageCount(), document.getTitle()]);
  }
  assert.deepEqual(pageCounts, [[2, "Tjänst 211"], [1, "Tjänst 212"], [2, "Tjänst 213"]]);
});
