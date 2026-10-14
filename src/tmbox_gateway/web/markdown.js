/* Träffens texter: en liten, säker delmängd av Markdown.
 *
 * Berättelsen, de allmänna instruktionerna, programmet och stationernas
 * instruktioner skrivs i Cloud (eller kommer ur planeringsprogrammets plan)
 * och läses på servern, i TKL, i iPhone-appen och i Cloud. Samma regler
 * överallt: rubriker (#), punktlistor (- * •), numrerade listor (1.),
 * stycken, linjer (---), **fet**, *kursiv*, `kod` och länkar [text](https://…).
 *
 * `parse` ger ett träd utan DOM och körs i proven. `render` bygger noderna
 * med createElement och textContent, aldrig som HTML-text, så en text kan inte
 * köra något i sidan. Länkar får bara http, https och mailto.
 *
 * Filen kopieras oförändrad till Cloud och TKL (tools/sync-i18n.mjs
 * --siblings) och iPhone-appen har samma regler i Swift.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.TrainMeetMarkdown = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const LANGUAGES = ["sv", "da", "nb", "de", "en"];
  const BULLET = /^\s*(?:[-*•–])\s+(.*)$/;
  const NUMBERED = /^\s*(\d{1,3})[.)]\s+(.*)$/;
  const HEADING = /^\s*(#{1,6})\s+(.*?)\s*#*\s*$/;
  const RULE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
  const SAFE_LINK = /^(?:https?:\/\/|mailto:)/i;

  /** Inline: **fet**, __fet__, *kursiv*, `kod` och [text](länk). */
  function inline(text) {
    const out = [];
    let buffer = "";
    const flush = () => { if (buffer) { out.push({ type: "text", text: buffer }); buffer = ""; } };
    let i = 0;
    const source = String(text ?? "");
    while (i < source.length) {
      const rest = source.slice(i);
      let match;
      if ((match = /^`([^`]+)`/.exec(rest))) {
        flush(); out.push({ type: "code", text: match[1] }); i += match[0].length; continue;
      }
      if ((match = /^\*\*(?=\S)([\s\S]*?\S)\*\*/.exec(rest)) || (match = /^__(?=\S)([\s\S]*?\S)__/.exec(rest))) {
        flush(); out.push({ type: "strong", children: inline(match[1]) }); i += match[0].length; continue;
      }
      if ((match = /^\*(?=\S)([^*]*?\S)\*/.exec(rest))) {
        flush(); out.push({ type: "em", children: inline(match[1]) }); i += match[0].length; continue;
      }
      if ((match = /^\[([^\]]+)\]\(([^)\s]+)\)/.exec(rest))) {
        flush();
        if (SAFE_LINK.test(match[2])) out.push({ type: "link", href: match[2], children: inline(match[1]) });
        else out.push(...inline(match[1]));
        i += match[0].length; continue;
      }
      buffer += source[i]; i += 1;
    }
    flush();
    // Text i följd blir en nod, också när en osäker länk blev text.
    return out.reduce((merged, node) => {
      const last = merged[merged.length - 1];
      if (node.type === "text" && last?.type === "text") last.text += node.text;
      else merged.push(node);
      return merged;
    }, []);
  }

  /**
   * Texten som block: rubrik, stycke, lista och linje. Rader i följd blir ett
   * stycke; en tom rad, en rubrik, en linje eller en listrad avslutar det.
   */
  function parse(text) {
    const blocks = [];
    let paragraph = [];
    let list = null;
    const endParagraph = () => {
      if (paragraph.length) blocks.push({ type: "paragraph", children: inline(paragraph.join(" ")) });
      paragraph = [];
    };
    const endList = () => { if (list) blocks.push(list); list = null; };
    for (const raw of String(text ?? "").replace(/\r\n?/g, "\n").split("\n")) {
      const line = raw.replace(/\s+$/, "");
      let match;
      if (!line.trim()) { endParagraph(); endList(); continue; }
      if ((match = HEADING.exec(line))) {
        endParagraph(); endList();
        blocks.push({ type: "heading", level: match[1].length, children: inline(match[2]) });
        continue;
      }
      if (RULE.test(line)) { endParagraph(); endList(); blocks.push({ type: "rule" }); continue; }
      const bullet = BULLET.exec(line), numbered = bullet ? null : NUMBERED.exec(line);
      if (bullet || numbered) {
        endParagraph();
        const ordered = Boolean(numbered);
        if (!list || list.ordered !== ordered) {
          endList();
          list = { type: "list", ordered, start: ordered ? Number(numbered[1]) : 1, items: [] };
        }
        list.items.push(inline((bullet || numbered)[bullet ? 1 : 2]));
        continue;
      }
      if (list && /^\s{2,}\S/.test(raw) && list.items.length) {
        // En indragen rad fortsätter listpunkten ovanför.
        const last = list.items[list.items.length - 1];
        list.items[list.items.length - 1] = [...last, { type: "text", text: " " }, ...inline(line.trim())];
        continue;
      }
      endList();
      paragraph.push(line.trim());
    }
    endParagraph(); endList();
    return blocks;
  }

  /** Texten utan formatering, till exempel för en förhandsvisning. */
  function plain(text) {
    const flat = (nodes) => nodes.map((node) => node.type === "text" || node.type === "code" ? node.text : flat(node.children || [])).join("");
    return parse(text).map((block) => block.type === "list" ? block.items.map(flat).join("\n")
      : block.type === "rule" ? "" : flat(block.children)).filter(Boolean).join("\n\n");
  }

  /**
   * Ritar texten i `container` (tömmer den först). Rubriknivåerna förskjuts
   * med `headingBase` så att textens # blir en underrubrik på sidan där den
   * står (förval h3).
   */
  function render(container, text, { headingBase = 3 } = {}) {
    const doc = container.ownerDocument || document;
    const make = (tag, className) => { const node = doc.createElement(tag); if (className) node.className = className; return node; };
    const fill = (parent, nodes) => {
      for (const node of nodes) {
        if (node.type === "text") parent.append(doc.createTextNode(node.text));
        else if (node.type === "code") { const code = make("code"); code.textContent = node.text; parent.append(code); }
        else if (node.type === "strong" || node.type === "em") { const el = make(node.type); fill(el, node.children); parent.append(el); }
        else if (node.type === "link") {
          const link = make("a"); link.href = node.href; link.target = "_blank"; link.rel = "noopener noreferrer";
          fill(link, node.children); parent.append(link);
        }
      }
      return parent;
    };
    container.replaceChildren();
    container.classList?.add("tm-md");
    for (const block of parse(text)) {
      if (block.type === "heading") container.append(fill(make(`h${Math.min(6, headingBase + block.level - 1)}`), block.children));
      else if (block.type === "paragraph") container.append(fill(make("p"), block.children));
      else if (block.type === "rule") container.append(make("hr"));
      else if (block.type === "list") {
        const list = make(block.ordered ? "ol" : "ul");
        if (block.ordered && block.start !== 1) list.start = block.start;
        for (const item of block.items) list.append(fill(make("li"), item));
        container.append(list);
      }
    }
    return container;
  }

  /**
   * Texten på läsarens språk ur { sv, da, nb, de, en }: läsarens språk, sedan
   * träffens, sedan svenska, sedan det första som finns. Ger { language, text }
   * eller null när inget språk har text.
   */
  function pick(byLanguage, preferred, fallback) {
    const texts = byLanguage && typeof byLanguage === "object" ? byLanguage : {};
    const order = [preferred, fallback, "sv", ...LANGUAGES, ...Object.keys(texts)];
    for (const language of order) {
      const value = language && texts[language];
      if (typeof value === "string" && value.trim()) return { language, text: value };
    }
    return null;
  }

  return { parse, inline, plain, render, pick, LANGUAGES };
});
