// Köruppdragens PDF läses i administratörens webbläsare med pdf.js (#225).
// Här: att texten på varje sida blir rader i rätt ordning, och att den
// medföljande pdf.js läser en riktig PDF, också å, ä, ö, æ och ø.
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const reader = require("../../src/tmbox_gateway/web/lokforaren-koruppdrag.js");

const PDFJS = path.join(__dirname, "../../src/tmbox_gateway/web/pdfjs");

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

test("text på samma höjd blir en rad, uppifrån och ned och från vänster", () => {
  const item = (str, x, y) => ({ str, transform: [11, 0, 0, 11, x, y], width: str.length * 5 });
  const rows = reader.lines([
    item("17:10", 112, 700.4), item("Spiten", 12, 701), item("Tjeneste 211", 6, 760),
    item("  ", 300, 700), item("G 5453", 154, 699.2), item("Tveiten", 12, 680),
  ]);
  assert.deepEqual(rows.map((row) => row.text), ["Tjeneste 211", "Spiten 17:10 G 5453", "Tveiten"]);
  assert.deepEqual(rows[1].parts.map((part) => part.x), [12, 112, 154]);
});

test("pdf.js läser varje sida i en riktig PDF, med nordiska tecken", async () => {
  const pdfjs = await import(path.join(PDFJS, "pdf.min.mjs"));
  pdfjs.GlobalWorkerOptions.workerSrc = path.join(PDFJS, "pdf.worker.min.mjs");
  const pdf = tinyPdf([
    [[40, 780, "Tjeneste 211"], [40, 740, "Spiten"], [160, 740, "17:10"], [240, 740, "G 5453"], [40, 720, "Tveiten"], [240, 720, "Rundgång"]],
    [[40, 780, "Tjeneste 212"], [40, 740, "Kjøllstad"], [160, 740, "19:00"], [240, 740, "Ålö æøå"]],
  ]);
  const progress = [];
  const warn = console.log; console.log = () => {}; // pdf.js säger att canvas saknas i Node; texten behöver inget
  let pages;
  try {
    pages = await reader.extract(pdf, (step) => progress.push(step), pdfjs);
  } finally {
    console.log = warn;
  }
  assert.deepEqual(progress, [{ page: 1, pages: 2 }, { page: 2, pages: 2 }]);
  assert.deepEqual(pages.map((rows) => rows.map((row) => row.text)), [
    ["Tjeneste 211", "Spiten 17:10 G 5453", "Tveiten Rundgång"],
    ["Tjeneste 212", "Kjøllstad 19:00 Ålö æøå"],
  ]);
});
