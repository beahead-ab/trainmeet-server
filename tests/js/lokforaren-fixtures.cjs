// Små filer för Lokförarens köruppdrag i testerna (#225): en planeringsfil i
// planeringsprogrammets form och en PDF, båda byggda här så att inga riktiga
// underlag behöver ligga i det publika repot.

// En liten PDF med standardtypsnittet Helvetica, skriven för hand så att
// testet inte behöver någon fil i repot. Sidor: [[x, y, text], …].
function tinyPdf(pages) {
  const winAnsi = (text) => [...text].map((char) => {
    const code = char.codePointAt(0);
    if (char === "(" || char === ")" || char === "\\") return "\\" + char;
    return code < 128 ? char : "\\" + code.toString(8);
  }).join("");
  const objects = [];
  const add = (body) => { objects.push(body); return objects.length; };
  const font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>");
  const pagesId = objects.length + 1 + pages.length * 2;
  const kids = [];
  for (const items of pages) {
    const stream = items.map(([x, y, text]) => `BT /F1 11 Tf ${x} ${y} Td (${winAnsi(text)}) Tj ET`).join("\n");
    const content = add(`<< /Length ${Buffer.byteLength(stream, "latin1")} >>\nstream\n${stream}\nendstream`);
    kids.push(add(`<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 ${font} 0 R >> >> /Contents ${content} 0 R >>`));
  }
  add(`<< /Type /Pages /Kids [${kids.map((id) => `${id} 0 R`).join(" ")}] /Count ${kids.length} >>`);
  const catalog = add(`<< /Type /Catalog /Pages ${pagesId} 0 R >>`);
  let out = "%PDF-1.4\n";
  const offsets = objects.map((body, index) => { const at = Buffer.byteLength(out, "latin1"); out += `${index + 1} 0 obj\n${body}\nendobj\n`; return at; });
  const xref = Buffer.byteLength(out, "latin1");
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n` + offsets.map((at) => `${String(at).padStart(10, "0")} 00000 n \n`).join("");
  out += `trailer\n<< /Size ${objects.length + 1} /Root ${catalog} 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

// En liten planeringsfil i planeringsprogrammets form: varje objekt en gång
// med "$id", sedan {"$ref": id}; listor i {"$values": [...]}. Godståget G 5453
// går ut och G 5456 tillbaka med ett av två lok beroende på veckodag, möter
// persontåget RE 2511, gör rundgång och hämtar en nyckel; tjänst 212 är
// växling och 213 går över midnatt.
function tinyPlan([first, middle, last] = ["Spiten", "Tveiten", "Kjøllstad"]) {
  let next = 1;
  const id = (object) => Object.assign({ $id: String(next++) }, object);
  const ref = (object) => ({ $ref: object.$id });
  const values = (items) => ({ $id: String(next++), $values: items });
  const companies = [id({ Id: 2, Name: "CargoNet AS", Signature: "CN" }), id({ Id: 3, Name: "Vy Tog AS", Signature: "VY" })];
  const categories = [
    id({ Id: 1, Prefix: "G", Suffix: "", Name: "CN gods", Content: 2, DefaultPreparationMinutes: 20, DefaultFinishingMinutes: 20, CompanyId: 2 }),
    id({ Id: 2, Prefix: "", Suffix: "", Name: "Växling", Content: 2, IsShunting: true, CompanyId: null }),
    id({ Id: 3, Prefix: "RE", Suffix: "", Name: "Vy regional", Content: 1, CompanyId: 3 }),
  ];
  const station = (Id, Name, flags) => id({ $type: "Station", Id, Name, Signature: Name.slice(0, 3), Tracks: values([]), HasPassengerExchange: true, HasCargoExchange: true, ...flags });
  const spiten = station(1, first, { IsPassengerInterchange: true, HideMeets: true });
  const tveiten = station(2, middle, { IsPassengerInterchange: true, HideMeets: false });
  const kjollstad = station(3, last, { IsPassengerInterchange: false, HideMeets: false });
  // Spåren står i sin station; anropen pekar ut dem.
  const track = (Id, stationObject, Number, Usage = "") => {
    const object = id({ Id, Number, Usage, IsScheduled: true, StationId: stationObject.Id, Station: ref(stationObject) });
    stationObject.Tracks.$values.push(object);
    return object;
  };
  const s1 = track(11, spiten, "1"), s5 = track(15, spiten, "5", "Uppställning"), t2 = track(21, tveiten, "2"), k1 = track(31, kjollstad, "1");
  kjollstad.LockKey = id({ Name: "K16", HeldAt: ref(tveiten) });
  const call = (Sequence, trackObject, Arrival, Departure, extra = {}) => id({ Sequence, TrackId: trackObject.Id, Track: ref(trackObject),
    Arrival, Departure, IsArrival: false, IsDeparture: false, TurnLoco: false, Runaround: 0, PushWagons: false, Notes: values([]), ...extra });
  const train = (Id, Number, CategoryId, calls, extra = {}) => {
    const object = id({ Id, Number, CategoryId, MaxSpeed: 80, Sessions: 16383, IsReversible: false, Calls: values(calls), CargoFlows: values([]), ...extra });
    for (const item of calls) { item.TrainId = Id; item.Train = ref(object); }
    return object;
  };
  const A = { IsArrival: true }, D = { IsDeparture: true }, AD = { IsArrival: true, IsDeparture: true };
  const g5453 = train(1, 5453, 1, [call(1, s1, "17:10:00", "17:30:00", D), call(2, t2, "17:58:00", "18:14:00", { ...AD, Runaround: 2, PushWagons: true }), call(3, k1, "18:25:00", "18:25:00", A)]);
  const g5456 = train(2, 5456, 1, [call(1, k1, "19:15:00", "19:35:00", D), call(2, t2, "20:02:00", "20:02:00"), call(3, s1, "20:42:00", "21:02:00", A)]);
  const re2511 = train(3, 2511, 3, [call(1, k1, "17:40:00", "17:50:00", D), call(2, t2, "18:00:00", "18:05:00", AD), call(3, s1, "18:30:00", "18:40:00", A)]);
  const shunt = train(4, 21, 2, [call(1, t2, "08:30:00", "11:30:00", AD)]);
  const night = train(5, 5481, 1, [call(1, s1, "23:15:00", "23:40:00", D), call(2, k1, "1.00:25:00", "1.00:45:00", { ...A, Notes: values([id({ Text: "Ring tågklareraren" })]) })]);
  const stretch = (Id, from, to) => id({ Id, StartId: from.Id, Start: ref(from), EndId: to.Id, End: ref(to) });
  const loco = (Id, Number, days) => {
    let flags = 0x8000;
    for (const day of days) flags |= (1 << (day - 1)) | (1 << (day - 1 + 7));
    return { object: id({ Id, Number, Class: "Di4", ObjectType: 1, CompanyId: 2, IsReversibleTrain: false, ScheduleAssignments: values([]) }), flags };
  };
  const callsOf = (object) => object.Calls.$values;
  const part = (from, to, schedule, extra = {}) => id({ ScheduleId: schedule.Id, Schedule: ref(schedule), FromId: from.Id, From: ref(from), ToId: to.Id, To: ref(to), FromTrack: null, ToTrack: null, ...extra });
  // Lokomloppet: från uppställningen, koppla loss och till, till uppställningen.
  const locos = id({ Id: 7, Number: 102, Parts: values([]) });
  const out = part(callsOf(g5453)[0], callsOf(g5453)[2], locos, { TractionOptions: id({ FromLayover: 1, ToLayover: 0, HasCoupleNote: true, HasUncoupleNote: true, Runaround: 0 }) });
  const back = part(callsOf(g5456)[0], callsOf(g5456)[2], locos, { TractionOptions: id({ FromLayover: 0, ToLayover: 1, HasCoupleNote: true, HasUncoupleNote: true, Runaround: 0 }) });
  locos.Parts.$values.push(out, back);
  // Vagnarna i samma tåg: därför behöver loket gå runt där tåget vänder.
  const wagons = id({ Id: 8, Number: 501, Parts: values([part(callsOf(g5453)[0], callsOf(g5453)[2], { Id: 8, $id: "x" })]) });
  wagons.Parts.$values[0].Schedule = ref(wagons);
  const units = [loco(1, 2, [1, 3, 5]), loco(2, 3, [2, 4, 6])];
  for (const { object, flags } of units) object.ScheduleAssignments.$values.push(id({ ScheduleId: 7, Schedule: ref(locos), Sessions: flags, Role: 0 }));
  const wagonset = id({ Id: 3, Number: 11, Class: "Sgns", ObjectType: 3, CompanyId: 2, ScheduleAssignments: values([id({ ScheduleId: 8, Schedule: ref(wagons), Sessions: 16383, Role: 0 })]) });
  const duty = (Identity, parts, extra = {}) => id({ Identity, Sessions: 16383, Parts: values(parts), Notes: values([]), OverriddenStartTime: null, OverriddenEndTime: null, CompanyId: null, ...extra });
  return {
    $id: "0", Name: "Grimslöv 2027",
    Timetable: id({ Name: "Grimslöv 2027", TrainCategories: values(categories), Trains: values([g5453, g5456, re2511, shunt, night]),
      Layout: id({ Companies: values(companies), OperationLocations: values([spiten, tveiten, kjollstad]),
        TrackStretches: values([stretch(1, spiten, tveiten), stretch(2, tveiten, kjollstad)]),
        Settings: id({ General: id({ UseDays: true, StartDay: 1, MaxSessions: 6, UsePassengerTickets: true }) }) }) }),
    ScheduledObjects: values([...units.map(({ object }) => object), wagonset]),
    Schedules: values([locos, wagons]),
    DriverDuties: values([
      duty("213", [part(callsOf(night)[0], callsOf(night)[1], { Id: 0 })], { OverriddenStartTime: "23:00:00" }),
      duty("211", [ref(out), ref(back)]),
      duty("212", [part(callsOf(shunt)[0], callsOf(shunt)[0], { Id: 0 })]),
      duty("214", []),
    ]),
    // Utan den här står s5 oanvänd; den hör till stationen ändå.
    _unused: ref(s5),
  };
}

module.exports = { tinyPdf, tinyPlan };
