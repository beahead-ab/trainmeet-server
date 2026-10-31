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
// med "$id", sedan {"$ref": id}; listor i {"$values": [...]}.
function tinyPlan([first, middle, last] = ["Spiten", "Tveiten", "Kjøllstad"]) {
  let next = 1;
  const id = (object) => Object.assign({ $id: String(next++) }, object);
  const ref = (object) => ({ $ref: object.$id });
  const values = (items) => ({ $id: String(next++), $values: items });
  const companies = [id({ Id: 2, Name: "CargoNet AS", Signature: "CN" }), id({ Id: 13, Name: "SJ AB", Signature: "SJ" })];
  const categories = [
    id({ Id: 1, Prefix: "G", Suffix: "", Name: "CN gods", DefaultPreparationMinutes: 20, DefaultFinishingMinutes: 20, CompanyId: 2 }),
    id({ Id: 2, Prefix: "", Suffix: "", Name: "Växling", IsShunting: true, DefaultPreparationMinutes: 10, DefaultFinishingMinutes: 10, CompanyId: null }),
  ];
  const station = (Id, Name) => id({ $type: "Station", Id, Name, Signature: Name.slice(0, 3), Tracks: values([]) });
  const spiten = station(1, first), tveiten = station(2, middle), kjollstad = station(3, last);
  // Spåren står i sin station; anropen pekar ut dem.
  const track = (Id, stationObject, Number) => {
    const object = id({ Id, Number, IsScheduled: true, StationId: stationObject.Id, Station: ref(stationObject) });
    stationObject.Tracks.$values.push(object);
    return object;
  };
  const s1 = track(11, spiten, "1"), s5 = track(15, spiten, "5"), t2 = track(21, tveiten, "2"), k1 = track(31, kjollstad, "1");
  const call = (Sequence, trackObject, Arrival, Departure, extra = {}) => id({ Sequence, TrackId: trackObject.Id, Track: ref(trackObject),
    Arrival, Departure, TurnLoco: false, Runaround: 0, PushWagons: false, Notes: values([]), ...extra });
  const train = (Id, Number, CategoryId, calls) => {
    const object = id({ Id, Number, CategoryId, MaxSpeed: 80, Calls: values(calls) });
    for (const item of calls) { item.TrainId = Id; item.Train = ref(object); }
    return object;
  };
  const g5453 = train(1, 5453, 1, [call(1, s1, "17:30:00", "17:30:00"), call(2, t2, "17:58:00", "18:14:00", { Runaround: 1, PushWagons: true }), call(3, k1, "18:25:00", "18:25:00")]);
  const g5456 = train(2, 5456, 1, [call(1, k1, "19:35:00", "19:35:00"), call(2, t2, "20:02:00", "20:02:00"), call(3, s1, "20:42:00", "20:42:00")]);
  const shunt = train(3, 21, 2, [call(1, t2, "08:30:00", "11:30:00")]);
  const night = train(4, 5481, 1, [call(1, s1, "23:40:00", "23:40:00"), call(2, k1, "00:25:00", "00:25:00", { Notes: values([id({ Text: "Ring tågklareraren" })]) })]);
  const schedule = id({ Id: 7, Number: 102, Parts: values([]) });
  const part = (from, to, extra = {}) => id({ ScheduleId: 7, Schedule: ref(schedule), FromId: from.Id, From: ref(from), ToId: to.Id, To: ref(to), FromTrack: null, ToTrack: null, ...extra });
  const callsOf = (object) => object.Calls.$values;
  const duty = (Identity, parts, extra = {}) => id({ Identity, Parts: values(parts), Notes: values([]), OverriddenStartTime: null, OverriddenEndTime: null, CompanyId: null, ...extra });
  return {
    $id: "0", Name: "Grimslöv 2027",
    Timetable: id({ Name: "Grimslöv 2027", TrainCategories: values(categories), Trains: values([g5453, g5456, shunt, night]),
      Layout: id({ Companies: values(companies), OperationLocations: values([spiten, tveiten, kjollstad]) }) }),
    ScheduledObjects: values([id({ Id: 1, Number: 1, Class: "CD312", ScheduleAssignments: values([id({ ScheduleId: 7, Schedule: ref(schedule) })]) })]),
    Schedules: values([schedule]),
    DriverDuties: values([
      duty("213", [part(callsOf(night)[0], callsOf(night)[1])], { OverriddenStartTime: "23:15:00" }),
      duty("211", [part(callsOf(g5453)[0], callsOf(g5453)[2], { FromTrack: ref(s5) }), part(callsOf(g5456)[0], callsOf(g5456)[2])]),
      duty("212", [part(callsOf(shunt)[0], callsOf(shunt)[0], { ScheduleId: null, Schedule: null })]),
      duty("214", []),
    ]),
  };
}

module.exports = { tinyPdf, tinyPlan };
