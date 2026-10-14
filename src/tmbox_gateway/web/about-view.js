/* Om trafikspelet: berättelsen, instruktionerna, programmet och stationernas
 * instruktioner, i ett blad som deltagarvyn och sidhuvudet (Drift,
 * Inställningar) öppnar. Texterna hämtas från /v1/about när bladet öppnas och
 * ritas av markdown.js; instruktionerna visas på läsarens språk, annars på
 * träffens. */
(function (root) {
  "use strict";
  const tt = (source, values) => (typeof root.TrainMeetI18n?.t === "function" ? root.TrainMeetI18n.t(source, values) : source);
  const md = () => root.TrainMeetMarkdown;
  let sheet = null, data = null, current = "story", focusStation = null;

  const make = (tag, attrs = {}, ...kids) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    node.append(...kids.flat().filter(Boolean));
    return node;
  };
  const language = () => String(root.TrainMeetI18n?.getLanguage?.() || "sv");

  /** Flikarna som har något att visa, i den här ordningen. */
  function tabs() {
    const about = data?.about || {};
    const instructions = md().pick(about.instructions, language(), data?.meet?.language || "sv");
    return [
      ["story", tt("Berättelsen"), about.story || ""],
      ["instructions", tt("Instruktioner"), instructions?.text || ""],
      ["program", tt("Program"), about.program || ""],
      ["stations", tt("Stationerna"), (data?.stations || []).length ? "x" : ""],
    ].filter(([, , text]) => String(text).trim());
  }

  function paint() {
    const body = sheet.querySelector(".tm-about-sheet__body");
    const nav = sheet.querySelector(".tm-about-sheet__tabs");
    sheet.querySelector(".tm-about-sheet__meet").textContent = data?.meet?.name || "";
    // Språket kan ha bytts sedan bladet skapades.
    sheet.querySelector("#tm-about-sheet-title").textContent = tt("Om trafikspelet");
    sheet.querySelector(".tm-about-sheet__close").setAttribute("aria-label", tt("Stäng"));
    const available = tabs();
    if (!available.some(([id]) => id === current)) current = available[0]?.[0] || "story";
    nav.replaceChildren(...(available.length > 1 ? available.map(([id, label]) => make("button", {
      type: "button", role: "tab", class: `tm-about-sheet__tab${id === current ? " is-active" : ""}`, "aria-selected": id === current ? "true" : "false",
      onclick: () => { current = id; paint(); },
    }, label)) : []));
    nav.hidden = available.length <= 1;
    if (!available.length) { body.replaceChildren(make("p", { class: "tm-about-sheet__empty", text: tt("Trafikspelet har ingen berättelse ännu.") })); return; }
    const text = make("div", { class: "tm-md" });
    if (current === "stations") {
      const stations = [...(data.stations || [])].sort((a, b) => (b.id === focusStation) - (a.id === focusStation));
      body.replaceChildren(...stations.map((station) => {
        const block = make("div", { class: "tm-md" });
        md().render(block, station.instructions, { headingBase: 4 });
        return make("section", { class: `tm-about-sheet__station${station.id === focusStation ? " is-focus" : ""}` }, make("h3", { text: station.name }), block);
      }));
      return;
    }
    md().render(text, available.find(([id]) => id === current)[2], { headingBase: 3 });
    body.replaceChildren(text);
    body.scrollTop = 0;
  }

  function ensureSheet() {
    if (sheet) return sheet;
    sheet = make("dialog", { id: "tm-about-sheet", class: "tm-about-sheet", "aria-labelledby": "tm-about-sheet-title" },
      make("div", { class: "tm-about-sheet__head" },
        make("div", {}, make("span", { class: "tm-about-sheet__meet" }), make("h2", { id: "tm-about-sheet-title", text: tt("Om trafikspelet") })),
        make("button", { type: "button", class: "tm-about-sheet__close", "aria-label": tt("Stäng"), onclick: () => sheet.close() }, "×")),
      make("div", { class: "tm-about-sheet__tabs", role: "tablist" }),
      make("div", { class: "tm-about-sheet__body" }));
    sheet.addEventListener("click", (event) => { if (event.target === sheet) sheet.close(); });
    document.body.append(sheet);
    return sheet;
  }

  /** Öppnar bladet. `station` ställer den stationen först under Stationerna. */
  async function open({ tab = null, station = null } = {}) {
    ensureSheet();
    focusStation = station; if (tab) current = tab;
    sheet.querySelector(".tm-about-sheet__body").replaceChildren(make("p", { class: "tm-about-sheet__empty", text: tt("Läser texterna …") }));
    if (!sheet.open) sheet.showModal?.() ?? sheet.setAttribute("open", "");
    try {
      const response = await fetch("/v1/about", { cache: "no-store" });
      if (!response.ok) throw new Error(String(response.status));
      data = await response.json();
      paint();
    } catch {
      sheet.querySelector(".tm-about-sheet__body").replaceChildren(make("p", { class: "tm-about-sheet__empty", text: tt("Texterna gick inte att läsa. Försök igen.") }));
    }
  }

  /** De första raderna av berättelsen, för ett kort som leder in i bladet. */
  function teaser(about, limit = 260) {
    const plain = md().plain(about?.story || "").split("\n\n").filter((part) => part.trim());
    const first = plain.find((part, index) => index > 0 || plain.length === 1) || plain[0] || "";
    return first.length > limit ? `${first.slice(0, limit).replace(/\s+\S*$/, "")} …` : first;
  }

  // Boken i sidhuvudet, och Öppna-menyn på telefonen, öppnar bladet var man än är.
  if (typeof document !== "undefined") {
    // Menyn stänger sig själv när ett val görs (server-ui.js).
    const wire = () => ["header-about", "menu-about"].forEach((id) => document.getElementById(id)?.addEventListener("click", () => open()));
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire); else wire();
  }

  root.TrainMeetAbout = { open, teaser, tabsFor: (value) => { data = value; return tabs().map(([id]) => id); } };
})(typeof globalThis !== "undefined" ? globalThis : this);
