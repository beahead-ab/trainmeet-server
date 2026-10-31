/* Läser köruppdragens PDF i administratörens webbläsare (#225).
 *
 * pdf.js (Mozilla, /assets/pdfjs/) tar fram texten på varje sida med sin
 * position. Raderna byggs av text som står på samma höjd, och tolkningen
 * (parse) gör tjänster av raderna i samma form som Clouds driver_duties.
 * Servern får bara tjänsterna och kontrollerar formen; den tolkar ingen PDF.
 *
 * Samma fil körs i Node i testerna (module.exports), utan pdf.js där
 * testerna ger raderna direkt.
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

  /** Textbitarna på en sida som rader uppifrån och ned, varje rad med sina
   *  bitar från vänster till höger. y växer uppåt i PDF, därför vänds den. */
  function lines(items, tolerance = 2.5) {
    const parts = [];
    for (const item of items || []) {
      const text = String(item.str || "").replace(/\s+/g, " ");
      if (!text.trim()) continue;
      const transform = item.transform || [1, 0, 0, 1, 0, 0];
      parts.push({ x: transform[4], y: transform[5], w: item.width || 0, h: Math.abs(transform[3]) || item.height || 0, text });
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
    return rows;
  }

  /** Alla sidor som rader. onProgress({page, pages}) efter varje sida. */
  async function extract(data, onProgress, pdfjs) {
    const task = pdfjs.getDocument({ data: new Uint8Array(data), isEvalSupported: false, disableFontFace: true });
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

  /** Tjänsterna i köruppdraget: { pages, duties, warnings }.
   *  Layouten tas fram mot träffens riktiga köruppdrag. */
  function parse(pages) {
    throw new Error("Köruppdragens layout är inte inlagd än.");
  }

  async function read(data, onProgress) {
    const pdfjs = await import(PDFJS);
    pdfjs.GlobalWorkerOptions.workerSrc = WORKER;
    return parse(await extract(data, onProgress, pdfjs));
  }

  return { read, extract, lines, parse, PDFJS_VERSION };
});
