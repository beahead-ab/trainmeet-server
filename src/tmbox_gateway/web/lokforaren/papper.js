/* Rätta en tjänst efter pappret (#225): fotona och jämförelsen.
 *
 * Pappret förarna har och tjänsten på servern kan komma från olika versioner
 * av planen. En administratör eller ägare fotar pappret, en bild per sida;
 * Cloud tolkar fotona och servern visar förslaget bredvid tjänsten som den är
 * nu. Här finns det som inte behöver sidan: förminskningen av fotona och
 * jämförelsen. Samma fil körs i Node i testerna (module.exports).
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LokforarenPapper = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  //: Längsta sidan efter förminskning. Räcker för att läsa en tidtabell och
  //: håller en sida under ungefär 1 MB.
  const LONG_SIDE = 2000;
  //: Serverns gräns per sida (driver_versions.MAX_PAGE_BYTES), med marginal.
  const MAX_BYTES = 1900 * 1024;

  /** Bredd och höjd efter förminskning, i fotots proportioner. */
  function fit(width, height, longSide = LONG_SIDE) {
    const scale = Math.min(1, longSide / Math.max(width, height));
    return {width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale))};
  }

  /** Fotot som JPEG, förminskat, och en liten bild att visa i listan.
   *  Ritas om på en canvas, så att plats och annan data i bildfilen inte
   *  följer med. Telefonens vridning (EXIF) följer webbläsaren när bilden
   *  ritas. Serverns CSP tillåter bara data:-bilder, inga blob:-adresser.
   *  Ger {blob, width, height, thumb}. */
  async function shrink(file, doc = document) {
    const source = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener("load", () => resolve(reader.result));
      reader.addEventListener("error", () => reject(new Error("read")));
      reader.readAsDataURL(file);
    });
    const image = await new Promise((resolve, reject) => {
      const element = new Image();
      element.addEventListener("load", () => resolve(element));
      element.addEventListener("error", () => reject(new Error("image")));
      element.src = source;
    });
    const natural = {width: image.naturalWidth || image.width, height: image.naturalHeight || image.height};
    const draw = (size) => {
      const canvas = doc.createElement("canvas");
      canvas.width = size.width;
      canvas.height = size.height;
      const context = canvas.getContext("2d");
      context.fillStyle = "#fff";
      context.fillRect(0, 0, size.width, size.height);
      context.drawImage(image, 0, 0, size.width, size.height);
      return canvas;
    };
    const thumb = draw(fit(natural.width, natural.height, 320)).toDataURL("image/jpeg", 0.7);
    for (const [longSide, quality] of [[LONG_SIDE, 0.85], [LONG_SIDE, 0.72], [1600, 0.72], [1300, 0.65]]) {
      const size = fit(natural.width, natural.height, longSide);
      const blob = await new Promise((resolve) => draw(size).toBlob(resolve, "image/jpeg", quality));
      if (blob && blob.size <= MAX_BYTES) return {blob, thumb, ...size};
    }
    throw new Error("too_large");
  }

  const timeOf = (event) => event.time || event.arrival || "";

  /** En händelse som en rad att jämföra: tid, vad, station, spår och tåg. */
  function rowOf(event) {
    const time = event.kind === "stop" ? `${event.arrival}–${event.departure}` : event.time || "";
    return {time, kind: event.kind, station: event.station || "", track: event.track || "", train: event.train || ""};
  }

  /** Tjänsten som den är nu och som den står på pappret, sida vid sida.
   *
   *  `facts` är tjänstens tider, tåg och fordon med `same` för det som inte
   *  ändras; `rows` är pappret rad för rad, där `change` säger om raden är ny
   *  (`added`), har annan tid eller annat spår (`changed`) eller är samma
   *  (`same`), och `gone` är raderna som bara finns nu. Raderna paras ihop på
   *  händelse, station och tåg i tur och ordning. */
  function compare(current, proposal) {
    const join = (values) => values.filter(Boolean).join(" · ");
    const trains = (duty) => (duty && duty.trains || []).map((train) => train.number || train).join(" · ");
    const fact = (key, now, paper) => ({key, now: now || "", paper: paper || "", same: (now || "") === (paper || "")});
    const facts = [
      fact("time", current ? `${current.start}–${current.end}` : "", `${proposal.start}–${proposal.end}`),
      fact("trains", trains(current), trains(proposal)),
      fact("vehicle", current ? join([current.operator, current.vehicle]) : "", join([proposal.operator, proposal.vehicle])),
    ];
    const before = (current && current.events || []).map(rowOf);
    const used = new Set();
    const key = (row) => `${row.kind}|${row.station}|${row.train}`;
    const rows = (proposal.events || []).map((event) => {
      const row = rowOf(event);
      const index = before.findIndex((other, at) => !used.has(at) && key(other) === key(row));
      if (index < 0) return {...row, change: "added"};
      used.add(index);
      const other = before[index];
      const same = other.time === row.time && other.track === row.track;
      return {...row, change: same ? "same" : "changed", was: same ? null : {time: other.time, track: other.track}};
    });
    const gone = before.filter((_, at) => !used.has(at));
    const changes = rows.filter((row) => row.change !== "same").length + gone.length
      + facts.filter((item) => !item.same).length;
    return {facts, rows, gone, changes};
  }

  return {LONG_SIDE, MAX_BYTES, fit, shrink, compare, rowOf, timeOf};
});
