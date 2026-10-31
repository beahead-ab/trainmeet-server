/* Köruppdragen till Lokföraren, lästa i administratörens webbläsare (#225).
 *
 * Två filer, med var sin uppgift:
 *
 *   1. Planeringsfilen (JSON ur planeringsprogrammet, samma fil som går till
 *      Cloud) ger tjänsterna: tåg, stationer, spår, tider och anvisningar.
 *      fromPlan() gör om dem till samma form som Clouds driver_duties.
 *   2. Köruppdragens PDF (utskriften av samma tjänster, gärna 600 sidor) är
 *      förlagan. readPdf() tar fram texten på varje sida med pdf.js,
 *      matchPages() hittar vilka sidor som hör till vilken tjänst och split()
 *      gör en liten PDF per tjänst med pdf-lib, så att föraren bara får sina.
 *
 * Servern får bara färdiga tjänster och sidnummer och kontrollerar formen;
 * den tolkar varken planen eller PDF:en.
 *
 * Samma fil körs i Node i testerna (module.exports), utan pdf.js där.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LokforarenKoruppdrag = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const PDFJS_VERSION = "6.4.299";
  const PDFJS = `/assets/pdfjs/pdf.min.mjs?v=${PDFJS_VERSION}`;
  const WORKER = `/assets/pdfjs/pdf.worker.min.mjs?v=${PDFJS_VERSION}`;
  const PDFLIB_VERSION = "1.17.1";
  const PDFLIB = `/assets/pdflib/pdf-lib.min.js?v=${PDFLIB_VERSION}`;
  const DAY = 1440;

  // ── Planeringsfilen ──────────────────────────────────────────────────

  /** Planeringsprogrammet skriver varje objekt en gång med "$id" och pekar
   *  sedan ut det med {"$ref": id}. Listor ligger i {"$values": [...]}. */
  function graph(plan) {
    const byId = new Map();
    const nodes = [];
    const stack = [plan];
    while (stack.length) {
      const node = stack.pop();
      if (Array.isArray(node)) { for (const item of node) if (item && typeof item === "object") stack.push(item); continue; }
      if (!node || typeof node !== "object") continue;
      nodes.push(node);
      if (typeof node.$id === "string") byId.set(node.$id, node);
      for (const key of Object.keys(node)) {
        const value = node[key];
        if (value && typeof value === "object") stack.push(value);
      }
    }
    const deref = (node) => (node && typeof node === "object" && typeof node.$ref === "string" ? byId.get(node.$ref) || null : node ?? null);
    const list = (node) => {
      const value = deref(node);
      const items = Array.isArray(value) ? value : value && Array.isArray(value.$values) ? value.$values : [];
      return items.map(deref).filter(Boolean);
    };
    return { deref, list, nodes };
  }

  /** "06:41:00" → minuter efter midnatt. */
  function clock(value) {
    const match = /^(\d{1,2}):(\d{2})/.exec(String(value || ""));
    return match ? Number(match[1]) * 60 + Number(match[2]) : null;
  }

  function hhmm(total) {
    const value = ((Math.round(total) % DAY) + DAY) % DAY;
    return String(Math.floor(value / 60)).padStart(2, "0") + ":" + String(value % 60).padStart(2, "0");
  }

  const text = (value) => String(value ?? "").replace(/\s+/g, " ").trim();

  /** Text ur en anteckning, vilken form den än har i exporten. */
  function noteText(note) {
    if (typeof note === "string") return text(note);
    if (!note || typeof note !== "object") return "";
    for (const key of ["Text", "Contents", "Content", "Note", "Description", "Value"]) {
      if (typeof note[key] === "string" && note[key].trim()) return text(note[key]);
    }
    return "";
  }

  /** Tjänsterna i planeringsfilen, i samma form som Clouds driver_duties:
   *  { name, duties, warnings }. Kastar ett fel om filen inte är en plan
   *  med tjänster. */
  function fromPlan(plan) {
    if (!plan || typeof plan !== "object" || Array.isArray(plan)) throw new Error("Filen är ingen planeringsfil.");
    const { deref, list, nodes } = graph(plan);
    if (!("DriverDuties" in plan)) throw new Error("Filen saknar köruppdrag (DriverDuties). Är det planeringsfilen?");
    const timetable = deref(plan.Timetable) || {};
    const layout = deref(timetable.Layout) || {};
    const byKey = (items) => new Map(items.filter((item) => item.Id !== undefined).map((item) => [item.Id, item]));
    const categories = byKey(list(timetable.TrainCategories));
    const companies = byKey(list(layout.Companies));
    const trains = byKey(nodes.filter((node) => node.Calls && node.Number !== undefined && "CategoryId" in node));
    const tracks = byKey(nodes.filter((node) => "StationId" in node && "IsScheduled" in node));
    const stations = byKey(nodes.filter((node) => node.Name && "Signature" in node && "Tracks" in node));
    // Vilka fordon som går i varje omlopp (planeringsprogrammets Schedules).
    const vehicles = new Map();
    for (const object of list(plan.ScheduledObjects)) {
      for (const assignment of list(object.ScheduleAssignments)) {
        const id = assignment.ScheduleId ?? deref(assignment.Schedule)?.Id;
        if (id === undefined || !object.Class) continue;
        if (!vehicles.has(id)) vehicles.set(id, new Set());
        vehicles.get(id).add(text(object.Class));
      }
    }

    const stationOf = (track) => deref(track?.Station) || stations.get(track?.StationId) || null;
    const trackOf = (call) => deref(call?.Track) || tracks.get(call?.TrackId) || null;
    const trainOf = (call) => deref(call?.Train) || trains.get(call?.TrainId) || null;
    const callsOf = (train) => list(train?.Calls).sort((a, b) => (a.Sequence ?? 0) - (b.Sequence ?? 0));
    const labelOf = (train) => {
      const category = categories.get(train.CategoryId) || deref(train.Category) || {};
      // Växling har inget tågnummer i köruppdraget, bara tågslagets namn.
      if (category.IsShunting) return text(category.Name) || "Växling";
      return text(`${text(category.Prefix)} ${train.Number}${text(category.Suffix)}`);
    };
    const notesOf = (call) => {
      const notes = [];
      if (call.TurnLoco) notes.push("Vänd loket");
      if (Number(call.Runaround) > 0) notes.push("Rundgång");
      if (call.PushWagons) notes.push("Skjut vagnar");
      for (const note of list(call.Notes)) { const value = noteText(note); if (value) notes.push(value); }
      return notes;
    };

    const warnings = [];
    const duties = [];
    for (const raw of list(plan.DriverDuties)) {
      const number = text(raw.Identity ?? raw.Number ?? raw.Id);
      const events = [];
      const trainsOut = [];
      const classes = new Set();
      let firstTrain = null;
      let lastTrain = null;
      for (const part of list(raw.Parts)) {
        const from = deref(part.From);
        const to = deref(part.To);
        const train = trainOf(from);
        if (!from || !to || !train) continue;
        for (const vehicle of vehicles.get(part.ScheduleId ?? deref(part.Schedule)?.Id) || []) classes.add(vehicle);
        // Ett tåg i taget; en del som slutar på ett annat tåg (tåget byter
        // nummer) tar resten av det första och början av det andra.
        const segments = [[train, from.Sequence, Infinity]];
        const other = trainOf(to);
        if (other && other !== train) segments.push([other, -Infinity, to.Sequence]);
        else segments[0][2] = to.Sequence;
        for (const [segmentTrain, first, last] of segments) {
          const calls = callsOf(segmentTrain).filter((call) => call.Sequence >= first && call.Sequence <= last);
          if (!calls.length) continue;
          const label = labelOf(segmentTrain);
          firstTrain = firstTrain || segmentTrain;
          lastTrain = segmentTrain;
          const name = (call) => text(stationOf(trackOf(call))?.Name);
          trainsOut.push({
            number: label, from: name(calls[0]), to: name(calls[calls.length - 1]),
            ...(Number.isInteger(segmentTrain.MaxSpeed) && segmentTrain.MaxSpeed > 0 ? { vmax: segmentTrain.MaxSpeed } : {}),
          });
          // Delens FromTrack/ToTrack är uppställningsspåret där fordonet hämtas
          // eller lämnas, inte avgångs- eller ankomstspåret.
          const fromTrack = segmentTrain === train ? deref(part.FromTrack) : null;
          const toTrack = segmentTrain === (other || train) ? deref(part.ToTrack) : null;
          const where = (track) => `spår ${text(track.Number)}${text(track.Usage) ? ` (${text(track.Usage)})` : ""}`;
          calls.forEach((call, index) => {
            const track = trackOf(call);
            const event = { station: text(stationOf(track)?.Name || name(call)), train: label };
            if (track?.Number !== undefined && text(track.Number)) event.track = text(track.Number);
            const arrival = clock(call.Arrival);
            const departure = clock(call.Departure);
            if (calls.length === 1) {
              // Ett pass på en station, till exempel växling: hela tiden där.
              Object.assign(event, arrival !== null && departure !== null && arrival !== departure
                ? { kind: "stop", arrival: hhmm(arrival), departure: hhmm(departure) }
                : { kind: "pass", time: hhmm(departure ?? arrival) });
            } else if (index === 0) {
              Object.assign(event, { kind: "departure", time: hhmm(departure ?? arrival) });
            } else if (index === calls.length - 1) {
              Object.assign(event, { kind: "arrival", time: hhmm(arrival ?? departure) });
            } else if (arrival !== null && departure !== null && arrival !== departure) {
              Object.assign(event, { kind: "stop", arrival: hhmm(arrival), departure: hhmm(departure) });
            } else {
              Object.assign(event, { kind: "pass", time: hhmm(departure ?? arrival) });
            }
            const notes = notesOf(call);
            if (index === 0 && fromTrack) notes.unshift(`Före avgång: hämta fordonet från ${where(fromTrack)}`);
            if (index === calls.length - 1 && toTrack) notes.push(`Efter ankomst: ställ fordonet på ${where(toTrack)}`);
            if (notes.length) event.notes = notes;
            if (departure === null && arrival === null) return;
            events.push(event);
          });
        }
      }
      if (!number || !events.length) {
        warnings.push(number ? `Tjänst ${number} har inga tåg med tider och togs inte med` : "En tjänst utan nummer togs inte med");
        continue;
      }
      // Förberedelse och avslutning: tjänstens egna tider om planen har dem,
      // annars tågslagets standard före första och efter sista tåget.
      const firstCategory = categories.get(firstTrain?.CategoryId) || {};
      const lastCategory = categories.get(lastTrain?.CategoryId) || {};
      const firstEvent = events[0];
      const lastEvent = events[events.length - 1];
      const firstAt = clock(firstEvent.time ?? firstEvent.arrival);
      const lastAt = clock(lastEvent.time ?? lastEvent.departure);
      const startAt = clock(raw.OverriddenStartTime) ?? (firstAt - (Number(firstCategory.DefaultPreparationMinutes) || 0));
      const endAt = clock(raw.OverriddenEndTime) ?? (lastAt + (Number(lastCategory.DefaultFinishingMinutes) || 0));
      if (hhmm(startAt) !== hhmm(firstAt)) events.unshift({ kind: "duty_start", time: hhmm(startAt), station: firstEvent.station, notes: ["Förberedelse"] });
      if (hhmm(endAt) !== hhmm(lastAt)) events.push({ kind: "duty_end", time: hhmm(endAt), station: lastEvent.station, notes: ["Avslutning"] });
      const company = companies.get(raw.CompanyId) || companies.get(firstTrain?.CompanyId) || companies.get(firstCategory.CompanyId) || {};
      const vmax = trainsOut.map((item) => item.vmax).filter(Boolean);
      duties.push({
        number,
        operator: text(company.Name),
        vehicle: [...classes].join(" + "),
        vmax: vmax.length ? Math.max(...vmax) : null,
        start: hhmm(startAt),
        end: hhmm(endAt),
        trains: trainsOut,
        notes: list(raw.Notes).map(noteText).filter(Boolean),
        events,
      });
    }
    const seen = new Map();
    for (const duty of duties) seen.set(duty.number, (seen.get(duty.number) || 0) + 1);
    for (const [number, count] of seen) if (count > 1) warnings.push(`Tjänst ${number} finns ${count} gånger i planen`);
    duties.sort((a, b) => a.number.localeCompare(b.number, "sv", { numeric: true }));
    return { name: text(plan.Name || timetable.Name), duties, warnings };
  }

  /** En kort kontrollsumma för att känna igen samma fil igen (cyrb53). Den
   *  är inget säkerhetsskydd; crypto.subtle finns inte på en lokal server
   *  över http. */
  function fingerprint(value) {
    let h1 = 0xdeadbeef;
    let h2 = 0x41c6ce57;
    for (let index = 0; index < value.length; index += 1) {
      const code = value.charCodeAt(index);
      h1 = Math.imul(h1 ^ code, 2654435761);
      h2 = Math.imul(h2 ^ code, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return (h2 >>> 0).toString(16).padStart(8, "0") + (h1 >>> 0).toString(16).padStart(8, "0") + value.length.toString(16);
  }

  // ── PDF:en ───────────────────────────────────────────────────────────

  /** Textbitarna på en sida som rader uppifrån och ned, varje rad med sina
   *  bitar från vänster till höger. y växer uppåt i PDF, därför vänds den. */
  function lines(items, tolerance = 2.5) {
    const parts = [];
    for (const item of items || []) {
      const value = String(item.str || "").replace(/\s+/g, " ");
      if (!value.trim()) continue;
      const transform = item.transform || [1, 0, 0, 1, 0, 0];
      parts.push({ x: transform[4], y: transform[5], text: value });
    }
    parts.sort((a, b) => b.y - a.y || a.x - b.x);
    const rows = [];
    for (const part of parts) {
      const row = rows.find((candidate) => Math.abs(candidate.y - part.y) <= tolerance);
      if (row) row.parts.push(part); else rows.push({ y: part.y, parts: [part] });
    }
    for (const row of rows) {
      row.parts.sort((a, b) => a.x - b.x);
      row.text = row.parts.map((part) => part.text.trim()).filter(Boolean).join(" ");
    }
    rows.sort((a, b) => b.y - a.y);
    return rows.map((row) => row.text);
  }

  /** Alla sidor som textrader. onProgress({page, pages}) efter varje sida. */
  async function extract(data, onProgress, pdfjs) {
    // En kopia: pdf.js lämnar över sin buffert till arbetstråden, och
    // samma PDF behövs efteråt för att dela den per tjänst.
    const task = pdfjs.getDocument({ data: new Uint8Array(data).slice(), isEvalSupported: false, disableFontFace: true });
    const pages = [];
    try {
      const pdf = await task.promise;
      for (let number = 1; number <= pdf.numPages; number += 1) {
        const page = await pdf.getPage(number);
        const content = await page.getTextContent();
        pages.push(lines(content.items));
        page.cleanup();
        if (onProgress) onProgress({ page: number, pages: pdf.numPages });
      }
    } finally {
      await task.destroy();
    }
    return pages;
  }

  async function readPdf(data, onProgress) {
    const pdfjs = await import(PDFJS);
    pdfjs.GlobalWorkerOptions.workerSrc = WORKER;
    return extract(data, onProgress, pdfjs);
  }

  // Tjänstens nummer i utskriften, på de språk köruppdragen skrivs på:
  // "Tjänst 39", "Tjeneste nr. 39", sidfoten "Tjänst 39 – Dagl – Sida 2".
  const HEADING = /(?:^|[^\p{L}])(?:lokförartjänst|förartjänst|köruppdrag|tjänst|tjeneste(?:nummer)?|tjenste|dienst|duty|turnus|uppdrag)\s*(?:nr\.?|no\.?|nummer|#)?\s*[:.]?\s*(\d{1,4}[a-z]?)(?![\p{L}\d])/giu;
  // Omslaget i planeringsprogrammets häften: ordet på en rad, numret ensamt på en annan.
  const COVER_WORD = /(?:^|[^\p{L}])(?:tjänst|tjeneste|tjenste|dienst|duty)\s*$/iu;
  const GAP = 2; // sidor utan nummer som fylls i från grannarna; längre luckor är annat (instruktioner, bilagor)

  // Tågen kan vara {number: "X 104"} (planen) eller "X 104" (granskningen).
  const trainLabel = (train) => String(train && typeof train === "object" ? train.number : train);
  const trainNumbers = (duty) => (duty.trains || []).map((train) => (trainLabel(train).match(/\d+/g) || []).pop()).filter(Boolean);

  /** Sidorna för varje tjänst: { pages: {nummer: [från, till]}, total, warnings }.
   *
   *  Varje sida ägs av tjänsten vars nummer står på den, i en rubrik eller
   *  sidfot ("Tjänst 39 – Dagl – Sida 2") eller på ett omslag (ordet Tjänst
   *  och numret ensamt på en rad). En sida med många tjänster (en innehålls-
   *  förteckning) ägs av ingen. Sidor utan nummer inne i en tjänst hör till
   *  den, och en eller två efter en tjänst fortsätter den; sidor före den
   *  första tjänsten hör inte till någon. Hittas inga nummer alls används
   *  tågnumren: en sida där alla en tjänsts tåg står hör till den. Till sist
   *  kontrolleras att varje tjänsts sidor nämner minst ett av dess tåg. */
  function matchPages(pages, duties) {
    const known = new Map(duties.map((duty) => [duty.number.toLowerCase(), duty.number]));
    const result = {};
    const warnings = [];
    const owner = new Array(pages.length).fill(null);
    // Sidor som inte hör till någon av planens tjänster: en innehållsförteckning,
    // eller en tjänst som inte finns i planen.
    const foreign = new Array(pages.length).fill(false);
    pages.forEach((rows, index) => {
      const counts = new Map();
      const all = new Set();
      for (const row of rows) {
        for (const match of row.matchAll(HEADING)) {
          all.add(match[1].toLowerCase());
          const number = known.get(match[1].toLowerCase());
          if (number) counts.set(number, (counts.get(number) || 0) + 1);
        }
      }
      if (all.size >= 3) { foreign[index] = true; return; } // innehållsförteckning
      if (!counts.size && rows.some((row) => COVER_WORD.test(row))) {
        const alone = rows.map((row) => known.get(row.trim().toLowerCase())).filter(Boolean);
        if (alone.length === 1) counts.set(alone[0], 1);
      }
      if (counts.size) owner[index] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0];
      else if (all.size) foreign[index] = true;
    });

    if (owner.some(Boolean)) {
      // Luckor utan nummer: inne i en tjänst hör de till den; en eller två
      // efter en tjänst är en fortsättning på den (omslagen känns igen för
      // sig). Sidor före den första tjänsten är titel och instruktioner.
      let index = 0;
      while (index < pages.length) {
        if (owner[index] || foreign[index]) { index += 1; continue; }
        let end = index;
        while (end < pages.length && !owner[end] && !foreign[end]) end += 1;
        const before = index > 0 ? owner[index - 1] : null;
        const after = end < pages.length ? owner[end] : null;
        const short = end - index <= GAP;
        const fill = before && (before === after || short) ? before : null;
        if (fill) for (let page = index; page < end; page += 1) owner[page] = fill;
        index = end;
      }
    } else {
      // Inga nummer: känn igen tjänsten på dess tåg.
      let current = null;
      pages.forEach((rows, index) => {
        const words = new Set(rows.join(" ").match(/\d+/g) || []);
        const candidates = duties
          .map((duty) => ({ duty, numbers: trainNumbers(duty) }))
          .filter(({ numbers }) => numbers.length && numbers.every((value) => words.has(value)))
          .sort((a, b) => b.numbers.length - a.numbers.length);
        if (candidates.length && (candidates.length === 1 || candidates[0].numbers.length > candidates[1].numbers.length)) {
          current = candidates[0].duty.number;
        }
        owner[index] = current;
      });
    }

    owner.forEach((number, index) => {
      if (number === null) return;
      const page = index + 1;
      const range = result[number];
      if (!range) { result[number] = [page, page]; return; }
      if (range[1] === page - 1) { range[1] = page; return; }
      if (!range.split) { range.split = true; warnings.push(`Tjänst ${number} står på flera ställen i PDF:en; sidorna ${range[0]}–${range[1]} används`); }
    });
    for (const range of Object.values(result)) delete range.split;
    const missing = duties.filter((duty) => !result[duty.number]).map((duty) => duty.number);
    if (missing.length) warnings.push(`${missing.length} tjänster hittades inte i PDF:en: ${missing.slice(0, 12).join(", ")}${missing.length > 12 ? " …" : ""}`);
    for (const duty of duties) {
      const range = result[duty.number];
      if (!range) continue;
      const pageText = pages.slice(range[0] - 1, range[1]).flat().join(" ");
      const words = new Set(pageText.match(/\d+/g) || []);
      const numbers = trainNumbers(duty);
      if (numbers.length && !numbers.some((value) => words.has(value))) {
        warnings.push(`Sidorna ${range[0]}–${range[1]} för tjänst ${duty.number} nämner inget av dess tåg (${duty.trains.map(trainLabel).join(", ")})`);
      }
    }
    return { pages: result, total: pages.length, warnings };
  }

  function loadPdfLib() {
    if (globalThis.PDFLib) return Promise.resolve(globalThis.PDFLib);
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = PDFLIB;
      script.addEventListener("load", () => (globalThis.PDFLib ? resolve(globalThis.PDFLib) : reject(new Error("pdf-lib saknas"))));
      script.addEventListener("error", () => reject(new Error("pdf-lib kunde inte laddas")));
      document.head.append(script);
    });
  }

  /** En liten PDF per tjänst med bara dess sidor ur förlagan. onPart({number,
   *  bytes, done, total}) väntas in efter varje tjänst, så att bara en del åt
   *  gången hålls i minnet. Ger antalet delar. */
  async function split(data, ranges, onPart, lib) {
    const { PDFDocument } = lib || await loadPdfLib();
    const source = await PDFDocument.load(new Uint8Array(data), { updateMetadata: false });
    const entries = Object.entries(ranges).sort((a, b) => a[1][0] - b[1][0]);
    let done = 0;
    for (const [number, [from, to]] of entries) {
      const out = await PDFDocument.create({ updateMetadata: false });
      const indices = [];
      for (let page = from; page <= to; page += 1) indices.push(page - 1);
      for (const page of await out.copyPages(source, indices)) out.addPage(page);
      out.setTitle(`Tjänst ${number}`);
      const bytes = await out.save({ useObjectStreams: false });
      done += 1;
      await onPart({ number, bytes, done, total: entries.length });
    }
    return done;
  }

  return { fromPlan, fingerprint, readPdf, extract, lines, matchPages, split, PDFJS_VERSION, PDFLIB_VERSION };
});
