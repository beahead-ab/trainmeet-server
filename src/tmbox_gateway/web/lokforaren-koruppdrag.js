/* Köruppdragen till Lokföraren, lästa i webbläsaren (Server #225, Cloud #113).
 *
 * Samma fil på två ställen, byte för byte: serverns uppladdning under
 * Inställningar → Lokförare (web/lokforaren-koruppdrag.js) och fliken
 * Lokförare i Cloud (src/driver/koruppdrag.js). Servern äger den: ändra här och
 * kopiera till Cloud med `node tools/sync-i18n.mjs --siblings`.
 *
 * Två filer, med var sin uppgift:
 *
 *   1. Planeringsfilen (JSON ur planeringsprogrammet) ger tjänsterna: tåg,
 *      stationer, spår, tider och anvisningar. fromPlan() gör om dem till
 *      paketets form för driver_duties.
 *   2. Köruppdragens PDF (utskriften av samma tjänster, gärna 600 sidor) är
 *      förlagan. extract() tar fram texten på varje sida med pdf.js,
 *      matchPages() hittar vilka sidor som hör till vilken tjänst och split()
 *      gör en liten PDF per tjänst med pdf-lib, så att föraren bara får sina.
 *
 * Servern och Cloud får bara färdiga tjänster och sidnummer och kontrollerar
 * formen (serverns driver.py, Clouds cloud/drivers.py); ingen av dem tolkar
 * planen eller PDF:en. På servern läser readPdf() och loadPdfLib() pdf.js och
 * pdf-lib ur serverns egna filer; i Cloud ges de in (src/driver/reader.ts).
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

  /** "06:41:00" → minuter efter midnatt. Efter midnatt skriver planen dygnet
   *  först: "1.00:01:00" är 00:01 dygnet efter. */
  function clock(value) {
    const match = /^(?:(\d+)\.)?(\d{1,2}):(\d{2})/.exec(String(value || ""));
    return match ? Number(match[1] || 0) * DAY + Number(match[2]) * 60 + Number(match[3]) : null;
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

  // Planens pass eller dagar: 14 bitar, i dagläge veckodagarna från planens
  // första dag (StartDay, 0 = söndag) och upprepade i de övre sju.
  const SESSION_BITS = 0x3fff;

  /** Tjänsterna i planeringsfilen, i samma form som Clouds driver_duties:
   *  { name, duties, warnings }. Kastar ett fel om filen inte är en plan
   *  med tjänster.
   *
   *  Varje händelse får sina anvisningar som uppgifter (instructions), inte
   *  som färdig text, så att Lokföraren skriver dem på förarens språk:
   *  möten och förbigångar, omstigande resande, inget utbyte, koppla till och
   *  från (med veckodagar där fordonen växlar), uppställning, lyft, spår och
   *  rundgång. Reglerna följer planeringsprogrammets köruppdrag. */
  function fromPlan(plan) {
    if (!plan || typeof plan !== "object" || Array.isArray(plan)) throw new Error("Filen är ingen planeringsfil.");
    const { deref, list, nodes } = graph(plan);
    if (!("DriverDuties" in plan)) throw new Error("Filen saknar köruppdrag (DriverDuties). Är det planeringsfilen?");
    const timetable = deref(plan.Timetable) || {};
    const layout = deref(timetable.Layout) || {};
    const general = deref(deref(layout.Settings)?.General) || {};
    const useDays = general.UseDays !== false;
    const startDay = Number.isInteger(general.StartDay) ? general.StartDay : 1;
    const period = Math.max(1, Math.min(Number(general.MaxSessions) || 14, useDays ? 7 : 14));
    const tickets = general.UsePassengerTickets !== false;
    const byKey = (items) => new Map(items.filter((item) => item.Id !== undefined).map((item) => [item.Id, item]));
    const categories = byKey(list(timetable.TrainCategories));
    const companies = byKey(list(layout.Companies));
    const trains = byKey(nodes.filter((node) => node.Calls && node.Number !== undefined && "CategoryId" in node));
    const tracks = byKey(nodes.filter((node) => "StationId" in node && "IsScheduled" in node));
    const stations = byKey(nodes.filter((node) => node.Name && "Signature" in node && "Tracks" in node));

    const stationOf = (track) => deref(track?.Station) || stations.get(track?.StationId) || null;
    const trackOf = (call) => deref(call?.Track) || tracks.get(call?.TrackId) || null;
    const trainOf = (call) => deref(call?.Train) || trains.get(call?.TrainId) || null;
    const locationOf = (call) => stationOf(trackOf(call));
    const categoryOf = (train) => categories.get(train?.CategoryId) || deref(train?.Category) || {};
    const companyOf = (train) => companies.get(train?.CompanyId) || companies.get(categoryOf(train).CompanyId) || null;
    const sessionsOf = (item) => (Number.isInteger(item?.Sessions) ? item.Sessions : SESSION_BITS);
    const runs = new Map();
    const callsOf = (train) => {
      if (!runs.has(train)) runs.set(train, list(train?.Calls).sort((a, b) => (a.Sequence ?? 0) - (b.Sequence ?? 0)));
      return runs.get(train);
    };
    const labelOf = (train) => {
      const category = categoryOf(train);
      // Växling har inget tågnummer i köruppdraget, bara tågslagets namn.
      if (category.IsShunting) return text(category.Name) || "Växling";
      return text(`${text(category.Prefix)} ${train.Number}${text(category.Suffix)}`);
    };
    // Ett annat tåg som det står i köruppdraget: bolag, tågslag och nummer.
    const otherTrainName = (train) => text(`${text(companyOf(train)?.Signature)} ${labelOf(train)}`);

    // Pass eller dagar som uppgift: null = alla, undefined = inga inom planens period.
    const qualifier = (flags) => {
      if (flags === null) return null;
      const numbers = [];
      for (let index = 0; index < period; index += 1) if (flags & (1 << index)) numbers.push(index + 1);
      if (!numbers.length) return undefined;
      if (numbers.length === period) return null;
      return useDays ? { days: numbers.map((number) => ((startDay + number - 1) % 7) || 7) } : { sessions: numbers };
    };
    const sharedPart = (flags, whole) => {
      const running = whole & SESSION_BITS;
      const shared = running & flags;
      return shared === running ? null : shared;
    };

    // Riktning: sträckorna har en början och ett slut, och åt vilket håll tåget
    // kör över dem skiljer ett möte från en förbigång.
    const stretches = new Map();
    for (const stretch of list(layout.TrackStretches)) {
      const start = stretch.StartId ?? deref(stretch.Start)?.Id;
      const end = stretch.EndId ?? deref(stretch.End)?.Id;
      if (start === undefined || end === undefined) continue;
      stretches.set(`${start}>${end}`, "forward");
      if (!stretches.has(`${end}>${start}`)) stretches.set(`${end}>${start}`, "backward");
    }
    const between = (from, to) => stretches.get(`${locationOf(from)?.Id}>${locationOf(to)?.Id}`) || null;
    const neighbours = (call, train) => {
      const calls = callsOf(train);
      const index = calls.indexOf(call);
      return { before: index > 0 ? calls[index - 1] : null, after: index >= 0 && index < calls.length - 1 ? calls[index + 1] : null, index, calls };
    };
    const arrivesWay = (call, train) => { const { before } = neighbours(call, train); return before ? between(before, call) : null; };
    const leavesWay = (call, train) => { const { after } = neighbours(call, train); return after ? between(call, after) : null; };
    const directionInto = (call, train) => arrivesWay(call, train) || leavesWay(call, train);
    const isEnd = (call, train) => { const calls = callsOf(train); return calls[0] === call || calls[calls.length - 1] === call; };
    const minutesOf = (value) => clock(value);

    // Uppehåll: tåget ankommer eller avgår här, och tågslaget får stanna här.
    const isStop = (call, train) => {
      const location = locationOf(call);
      if (!location || location.$type === "SignalControlled" || !(call.IsArrival || call.IsDeparture)) return false;
      const content = Number(categoryOf(train).Content) || 0;
      if (!content) return true;
      return Boolean(((content & 1) && location.HasPassengerExchange) || ((content & 2) && location.HasCargoExchange));
    };

    // Alla anrop per station, för möten och förbigångar.
    const byStation = new Map();
    for (const train of trains.values()) {
      for (const call of callsOf(train)) {
        const id = locationOf(call)?.Id;
        if (id === undefined) continue;
        if (!byStation.has(id)) byStation.set(id, []);
        byStation.get(id).push({ call, train });
      }
    }

    // Omloppen: vilka delar som börjar och slutar vid varje anrop, och vilka
    // omlopp som håller samma del (samma två anrop).
    const callIds = new Map();
    const callId = (call) => { if (!callIds.has(call)) callIds.set(call, callIds.size + 1); return callIds.get(call); };
    const partKey = (part) => `${callId(deref(part.From))}|${callId(deref(part.To))}`;
    const holding = new Map();
    const departing = new Map();
    const arriving = new Map();
    const add = (map, key, value) => { if (!map.has(key)) map.set(key, []); map.get(key).push(value); };
    const schedules = list(plan.Schedules);
    for (const schedule of schedules) {
      for (const part of list(schedule.Parts)) {
        const from = deref(part.From);
        const to = deref(part.To);
        if (!from || !to) continue;
        add(holding, partKey(part), schedule.Id);
        add(departing, from, { part, schedule });
        add(arriving, to, { part, schedule });
      }
    }
    const vehicles = list(plan.ScheduledObjects).map((object) => ({
      object,
      assignments: list(object.ScheduleAssignments).map((assignment) => ({
        schedule: assignment.ScheduleId ?? deref(assignment.Schedule)?.Id,
        sessions: Number(assignment.Sessions) || 0,
        role: Number(assignment.Role) || 0,
      })),
    }));
    const LOCO = 1, TRAINSET = 2, WAGONSET = 3, CARGO_FLOW = 4;
    const isTraction = (object) => object.ObjectType === LOCO || object.ObjectType === TRAINSET;
    const turnsWithoutRunaround = (object) => object.ObjectType === TRAINSET || (object.ObjectType === LOCO && object.IsReversibleTrain);
    const vehicleName = (object) => text(object.ExternalId) || text(`${text((deref(object.Company) || companies.get(object.CompanyId) || {}).Signature)} ${String(object.Number ?? "").padStart(2, "0")} ${text(object.Class)}`);
    const onSchedules = (ids) => vehicles.filter((entry) => entry.assignments.some((assignment) => ids.has(assignment.schedule)));
    const assigned = (entry, ids, train) => sharedPart(
      entry.assignments.filter((assignment) => ids.has(assignment.schedule)).reduce((all, assignment) => all | assignment.sessions, 0),
      sessionsOf(train));
    const working = (entries, ids, train) => entries
      .map((entry) => ({ entry, when: qualifier(assigned(entry, ids, train)) }))
      .filter((worked) => worked.when !== undefined);
    const roleOf = (entry, ids) => (entry.assignments.find((assignment) => ids.has(assignment.schedule)) || {}).role || 0;
    const moves = (object, scheduleId) => object.ObjectType === LOCO && object.IsReversibleTrain
      && vehicles.some((entry) => entry.object.ObjectType === WAGONSET && entry.assignments.some((assignment) => assignment.schedule === scheduleId));
    const otherTrack = (reference, call) => {
      const track = deref(reference);
      const own = trackOf(call);
      if (!track || track === own || (own && track.Id === own.Id)) return null;
      return (track.StationId ?? stationOf(track)?.Id) === locationOf(call)?.Id ? track : null;
    };
    const instruction = (kind, extra = {}, when = null) => ({ kind, ...extra, ...(when || {}) });
    // Fordon med samma anvisning samma dagar delar en rad.
    const together = (worked, kind, scheduleId, wagonset = false) => {
      const groups = new Map();
      for (const { entry, when } of worked) {
        const movesTrain = moves(entry.object, scheduleId);
        const key = JSON.stringify([movesTrain, when]);
        if (!groups.has(key)) groups.set(key, { movesTrain, when, names: [] });
        groups.get(key).names.push(vehicleName(entry.object));
      }
      return [...groups.values()].map(({ movesTrain, when, names }) =>
        instruction(kind, movesTrain ? { train: true } : { vehicles: names, ...(wagonset ? { wagonset: true } : {}) }, when));
    };
    const joining = (layover, couple, use, worked, scheduleId, onArrival, wagonset = false) => {
      if (layover === 1) return together(worked, onArrival ? "to_parking" : "from_parking", scheduleId, wagonset);
      if (layover === 2) return together(worked, onArrival ? "lift_off" : "lift_on", scheduleId);
      if (couple) {
        // Ett motorvagnståg är tåget självt; det finns inget att koppla.
        return worked.filter(({ entry }) => entry.object.ObjectType !== TRAINSET)
          .map(({ entry, when }) => instruction(onArrival ? "uncouple" : "couple", { vehicles: [vehicleName(entry.object)] }, when));
      }
      if (use && !onArrival) return worked.map(({ entry, when }) => instruction("use", { vehicles: [vehicleName(entry.object)] }, when));
      return [];
    };
    const trackNotes = (track, schedule, train, onArrival) => {
      const own = working(onSchedules(new Set([schedule.Id])), new Set([schedule.Id]), train);
      const movedWithTrain = own.some(({ entry }) => moves(entry.object, schedule.Id));
      const where = { track: text(track.Number), ...(text(track.Usage) ? { usage: text(track.Usage) } : {}) };
      const out = own.filter(({ entry }) => entry.object.ObjectType !== WAGONSET).map(({ entry, when }) =>
        instruction(onArrival ? "put_on_track" : "fetch_from_track",
          { ...(moves(entry.object, schedule.Id) ? { train: true } : { vehicles: [vehicleName(entry.object)] }), ...where }, when));
      const wagons = movedWithTrain ? [] : own.filter(({ entry }) => entry.object.ObjectType === WAGONSET);
      if (wagons.length) out.push(instruction(onArrival ? "shunt_wagons_to_arrival" : "shunt_wagons_to_departure"));
      return out;
    };
    const partContext = (part, schedule) => {
      const ids = new Set(holding.get(partKey(part)) || [schedule.Id]);
      const train = trainOf(deref(part.From));
      const here = onSchedules(ids);
      return { ids, train, here, traction: here.filter((entry) => isTraction(entry.object)), wagons: here.filter((entry) => entry.object.ObjectType === WAGONSET) };
    };
    const needsRunaround = ({ ids, train, here, traction }) => {
      if (!traction.length) return !train?.IsReversible;
      const carries = list(train?.CargoFlows).length > 0 || here.some((entry) => [WAGONSET, CARGO_FLOW].includes(entry.object.ObjectType));
      const roles = traction.map((entry) => ({ object: entry.object, role: roleOf(entry, ids) }));
      if (!carries && roles.every(({ object }) => object.ObjectType === LOCO)) return false;
      if (roles.some(({ role }) => role === 1)) return false;
      const leading = roles.filter(({ role }) => role === 0);
      if (leading.length && leading.every(({ object }) => turnsWithoutRunaround(object))) return false;
      return !roles.some(({ object, role }) => role === 2 && turnsWithoutRunaround(object));
    };
    const scheduleHasTraction = (scheduleId) => vehicles.some((entry) => isTraction(entry.object) && entry.assignments.some((assignment) => assignment.schedule === scheduleId));
    // Tågets egna fordon, för om tåget som helhet behöver rundgång där det vänder.
    const trainNeeds = new Map();
    const trainNeedsRunaround = (train) => {
      if (trainNeeds.has(train)) return trainNeeds.get(train);
      const ids = new Set();
      for (const schedule of schedules) if (list(schedule.Parts).some((part) => trainOf(deref(part.From)) === train)) ids.add(schedule.Id);
      const here = onSchedules(ids);
      const result = needsRunaround({ ids, train, here, traction: here.filter((entry) => isTraction(entry.object)) });
      trainNeeds.set(train, result);
      return result;
    };
    const endsTractionPart = (call) => (arriving.get(call) || []).some(({ schedule }) => scheduleHasTraction(schedule.Id));
    const reversesHere = (call, train) => {
      const arrives = arrivesWay(call, train);
      const leaves = leavesWay(call, train);
      return Boolean(arrives && leaves && arrives !== leaves);
    };
    const reversesAtEnd = (part, schedule) => {
      const ordered = list(schedule.Parts).sort((a, b) => (minutesOf(deref(a.From)?.Departure) ?? 0) - (minutesOf(deref(b.From)?.Departure) ?? 0));
      const next = ordered[ordered.indexOf(part) + 1];
      if (!next || locationOf(deref(next.From))?.Id !== locationOf(deref(part.To))?.Id) return false;
      if ([1, 2].includes(deref(part.TractionOptions)?.ToLayover)) return false;
      if ([1, 2].includes(deref(next.TractionOptions)?.FromLayover)) return false;
      const arrives = arrivesWay(deref(part.To), trainOf(deref(part.To)));
      const leaves = leavesWay(deref(next.From), trainOf(deref(next.From)));
      return Boolean(arrives && leaves && arrives !== leaves);
    };

    // Nycklar: ett godståg som stannar där nyckeln hålls hämtar den före
    // avgång om det sedan stannar på platsen nyckeln låser upp, och lämnar den
    // vid ankomsten tillbaka.
    const keyed = (call, train, step) => {
      const { calls, index } = neighbours(call, train);
      const holder = locationOf(call);
      const out = [];
      for (let at = index + step; at >= 0 && at < calls.length; at += step) {
        const other = calls[at];
        if (!isStop(other, train)) continue;
        const location = locationOf(other);
        if (!location || location === holder || location.Id === holder?.Id) break;
        const key = deref(location.LockKey);
        const heldAt = deref(key?.HeldAt);
        if (key && heldAt && (heldAt === holder || heldAt.Id === holder?.Id)) {
          out.push({ ...(text(key.Name) ? { key: text(key.Name) } : {}), place: text(location.Name) });
        }
      }
      return out;
    };
    const keyInstructions = (call, train, pickUp) => {
      if (!(Number(categoryOf(train).Content) & 2) || !isStop(call, train)) return [];
      return keyed(call, train, pickUp ? 1 : -1).map((item) => instruction(pickUp ? "pick_up_key" : "leave_key", item));
    };

    const departureInstructions = (call, train) => {
      const out = [...keyInstructions(call, train, true)];
      for (const { part, schedule } of departing.get(call) || []) {
        const context = partContext(part, schedule);
        const options = deref(part.TractionOptions);
        const wagonOptions = deref(part.WagonSetOptions);
        const other = otherTrack(part.FromTrack, call);
        if (options && !other) out.push(...joining(options.FromLayover, options.HasCoupleNote, options.DisplayUseNote, working(context.traction, context.ids, context.train), schedule.Id, false));
        if (wagonOptions && !other) out.push(...joining(wagonOptions.FromLayover, wagonOptions.HasCoupleNote, false, working(context.wagons, context.ids, context.train), schedule.Id, false, true));
        if (other) out.push(...trackNotes(other, schedule, context.train, false));
      }
      if (call.PushWagons && !isEnd(call, train)) out.push(instruction("push_wagons"));
      return out;
    };

    const arrivalInstructions = (call, train, readerSessions) => {
      const out = [];
      const location = locationOf(call);
      const stop = isStop(call, train);
      const arrival = minutesOf(call.Arrival);
      const departure = minutesOf(call.Departure);
      if (!stop && arrival !== departure) out.push(instruction("no_exchange"));
      out.push(...keyInstructions(call, train, false));
      if (stop && tickets && (Number(categoryOf(train).Content) & 1) && location?.IsPassengerInterchange && location?.HasPassengerExchange) {
        out.push(instruction("exchange_passengers"));
      }
      // Möten och förbigångar, inte där stationen döljer dem och inte vid
      // tågens första eller sista station (där står de, de kör inte).
      if (location && !location.HideMeets && !isEnd(call, train)) {
        const way = directionInto(call, train);
        const found = { crosses: [], overtakes: [], overtaken_by: [] };
        for (const { call: other, train: otherTrain } of way ? byStation.get(location.Id) || [] : []) {
          if (otherTrain === train || isEnd(other, otherTrain)) continue;
          const otherWay = directionInto(other, otherTrain);
          if (!otherWay) continue;
          const [a1, d1, a2, d2] = [arrival, departure, minutesOf(other.Arrival), minutesOf(other.Departure)];
          if ([a1, d1, a2, d2].some((value) => value === null)) continue;
          const kind = otherWay !== way ? (a2 < d1 && d2 > a1 ? "crosses" : null)
            : a2 < a1 && d2 > d1 ? "overtakes"
            : a1 < a2 && d1 > d2 ? "overtaken_by" : null;
          if (!kind) continue;
          const shared = sessionsOf(train) & sessionsOf(otherTrain) & SESSION_BITS;
          if (!shared) continue;
          const when = shared === (readerSessions & SESSION_BITS) ? null : qualifier(shared);
          if (when === undefined) continue;
          found[kind].push({ train: otherTrainName(otherTrain), from: hhmm(Math.max(a1, a2)), to: hhmm(Math.min(d1, d2)), ...(when || {}) });
        }
        for (const [kind, meets] of Object.entries(found)) {
          if (!meets.length) continue;
          meets.sort((a, b) => a.from.localeCompare(b.from) || a.to.localeCompare(b.to) || a.train.localeCompare(b.train, "sv", { numeric: true }));
          out.push(instruction(kind, { meets }));
        }
      }
      for (const { part, schedule } of arriving.get(call) || []) {
        const context = partContext(part, schedule);
        const options = deref(part.TractionOptions);
        const wagonOptions = deref(part.WagonSetOptions);
        const other = otherTrack(part.ToTrack, call);
        const traction = working(context.traction, context.ids, context.train);
        if (options && !other) out.push(...joining(options.ToLayover, options.HasUncoupleNote, false, traction, schedule.Id, true));
        if (wagonOptions && !other) out.push(...joining(wagonOptions.ToLayover, wagonOptions.HasUncoupleNote, false, working(context.wagons, context.ids, context.train), schedule.Id, true, true));
        if (other) out.push(...trackNotes(other, schedule, context.train, true));
        if (options || scheduleHasTraction(schedule.Id)) {
          const needed = needsRunaround(context);
          const turn = Boolean(options?.TurnLoco) && needed;
          const mode = Number(options?.Runaround) || 0;
          const circulate = needed && (mode === 2 ? true : mode === 1 ? false : reversesAtEnd(part, schedule));
          const kind = turn && circulate ? "turn_and_circulate" : turn ? "turn" : circulate ? "circulate" : null;
          if (kind) {
            const when = traction.length ? qualifier(sharedPart(traction.reduce((all, { entry }) => all | (assigned(entry, context.ids, context.train) ?? SESSION_BITS), 0), sessionsOf(context.train))) : null;
            out.push(instruction(kind, {}, when === undefined ? null : when));
          }
        }
      }
      // Rundgång där tåget vänder på vägen, och det som står på anropet.
      if (trainNeedsRunaround(train)) {
        const turning = out.filter((item) => ["turn", "circulate", "turn_and_circulate"].includes(item.kind));
        const fromParts = turning.some((item) => item.kind !== "turn");
        const mode = Number(call.Runaround) || 0;
        const circulate = mode === 2 ? true : mode === 1 ? false
          : fromParts || (reversesHere(call, train) && !endsTractionPart(call));
        if (call.TurnLoco || circulate !== fromParts) {
          const turn = Boolean(call.TurnLoco) || turning.some((item) => item.kind.startsWith("turn"));
          const kind = turn && circulate ? "turn_and_circulate" : turn ? "turn" : circulate ? "circulate" : null;
          const when = turning.find((item) => item.days || item.sessions) || null;
          const rest = out.filter((item) => !turning.includes(item));
          out.length = 0;
          out.push(...rest);
          if (kind) out.push(instruction(kind, {}, when ? (when.days ? { days: when.days } : { sessions: when.sessions }) : null));
        }
      }
      return out;
    };

    // Samma anvisning för olika fordon olika dagar sägs en gång, med dagarna
    // tillsammans; täcker de hela perioden behövs inga dagar alls.
    const unique = (items) => {
      const groups = new Map();
      for (const item of items) {
        const { days, sessions, ...rest } = item;
        const key = JSON.stringify(rest);
        if (!groups.has(key)) groups.set(key, { rest, sets: [] });
        groups.get(key).sets.push(days || sessions || null);
      }
      return [...groups.values()].map(({ rest, sets }) => {
        if (sets.some((set) => set === null)) return rest;
        const union = [...new Set(sets.flat())].sort((a, b) => a - b);
        return union.length >= period ? rest : { ...rest, [useDays ? "days" : "sessions"]: union };
      });
    };

    const warnings = [];
    const duties = [];
    for (const raw of list(plan.DriverDuties)) {
      const number = text(raw.Identity ?? raw.Number ?? raw.Id);
      const readerSessions = sessionsOf(raw);
      const events = [];
      const trainsOut = [];
      const classes = new Set();
      let firstCall = null;
      let lastCall = null;
      for (const part of list(raw.Parts)) {
        const from = deref(part.From);
        const to = deref(part.To);
        const train = trainOf(from);
        if (!from || !to || !train) continue;
        const scheduleId = part.ScheduleId ?? deref(part.Schedule)?.Id;
        for (const entry of vehicles) {
          if (entry.object.Class && entry.assignments.some((assignment) => assignment.schedule === scheduleId)) classes.add(text(entry.object.Class));
        }
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
          const name = (call) => text(locationOf(call)?.Name);
          firstCall = firstCall || calls[0];
          lastCall = calls[calls.length - 1];
          const previous = trainsOut[trainsOut.length - 1];
          if (previous && previous.number === label && previous.to === name(calls[0])) {
            previous.to = name(calls[calls.length - 1]); // samma tåg vidare i nästa del
          } else {
            trainsOut.push({
              number: label, from: name(calls[0]), to: name(calls[calls.length - 1]),
              ...(Number.isInteger(segmentTrain.MaxSpeed) && segmentTrain.MaxSpeed > 0 ? { vmax: segmentTrain.MaxSpeed } : {}),
            });
          }
          calls.forEach((call, index) => {
            const track = trackOf(call);
            const event = { station: name(call), train: label };
            if (track?.Number !== undefined && text(track.Number)) event.track = text(track.Number);
            const arrival = clock(call.Arrival);
            const departure = clock(call.Departure);
            if (arrival === null && departure === null) return;
            const single = calls.length === 1;
            const firstOfPart = index === 0 && !single;
            const lastOfPart = index === calls.length - 1 && !single;
            const stop = isStop(call, segmentTrain);
            if (firstOfPart) {
              Object.assign(event, { kind: "departure", time: hhmm(departure ?? arrival) });
            } else if (lastOfPart) {
              Object.assign(event, { kind: "arrival", time: hhmm(arrival ?? departure) });
            } else if (arrival !== null && departure !== null && (stop || arrival !== departure)) {
              Object.assign(event, { kind: "stop", arrival: hhmm(arrival), departure: hhmm(departure) });
            } else {
              Object.assign(event, { kind: "pass", time: hhmm(departure ?? arrival) });
            }
            const instructions = unique([
              ...(firstOfPart ? [] : arrivalInstructions(call, segmentTrain, readerSessions)),
              ...(lastOfPart ? [] : departureInstructions(call, segmentTrain)),
            ]);
            if (instructions.length) event.instructions = instructions;
            const notes = list(call.Notes).map(noteText).filter(Boolean);
            if (notes.length) event.notes = notes;
            events.push(event);
          });
        }
      }
      if (!number || !events.length) {
        warnings.push(number ? `Tjänst ${number} har inga tåg med tider och togs inte med` : "En tjänst utan nummer togs inte med");
        continue;
      }
      // Tjänsten börjar vid första tågets ankomsttid (då föraren infinner sig)
      // och slutar vid sista tågets avgångstid, om planen inte anger egna tider.
      const firstAt = clock(firstCall.Arrival) ?? clock(firstCall.Departure);
      const lastAt = clock(lastCall.Departure) ?? clock(lastCall.Arrival);
      const startAt = clock(raw.OverriddenStartTime) ?? firstAt;
      const endAt = clock(raw.OverriddenEndTime) ?? lastAt;
      const firstEvent = events[0];
      const lastEvent = events[events.length - 1];
      const eventStart = clock(firstEvent.time ?? firstEvent.arrival);
      const eventEnd = clock(lastEvent.time ?? lastEvent.departure);
      if (hhmm(startAt) !== hhmm(eventStart)) events.unshift({ kind: "duty_start", time: hhmm(startAt), station: firstEvent.station, notes: ["Förberedelse"] });
      if (hhmm(endAt) !== hhmm(eventEnd)) events.push({ kind: "duty_end", time: hhmm(endAt), station: lastEvent.station, notes: ["Avslutning"] });
      const firstTrain = trainOf(firstCall);
      const company = companies.get(raw.CompanyId) || companyOf(firstTrain) || {};
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
