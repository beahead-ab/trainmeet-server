/* Inställningar → Lokförare och köruppdrag (#225).
 *
 * Tre saker på en sida: vägen till förarnas app (adress och QR-kod), träffens
 * köruppdrag och vilka förare som kör just nu. Köruppdraget läggs in i
 * uttryckliga steg så att inget når förarna av misstag:
 *
 *   1. Planeringsfilen (JSON) läses här i webbläsaren (LokforarenKoruppdrag)
 *      och tjänsterna sparas på servern, som bara kontrollerar formen.
 *   2. PDF:en med köruppdragen laddas upp oförändrad som förlaga, och sidorna
 *      för varje tjänst hittas här i webbläsaren.
 *   3. Administratören granskar och gör köruppdraget tillgängligt.
 */
(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const t = (source, values) => globalThis.TrainMeetI18n ? globalThis.TrainMeetI18n.t(source, values) : source;
  const POLL = 15000;
  const state = { overview: null, review: null, busy: false, timer: null, confirm: null, pdfTarget: null };

  class ApiError extends Error {}

  async function api(path, { method = "GET", body, headers = {} } = {}) {
    const response = await fetch(path, {
      method, credentials: "same-origin", cache: "no-store",
      headers: body === undefined ? headers : { "Content-Type": "application/json", ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new ApiError(payload.message || t("Det gick inte. Försök igen."));
    return payload;
  }

  const make = (tag, className = "", text = "") => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  };

  function shown() {
    return document.body.dataset.mode === "installningar" && globalThis.TrainMeetSettings?.currentSection() === "lokforare";
  }

  function message(text, error = false) {
    const node = $("#lf-docs-message");
    node.textContent = text || "";
    node.classList.toggle("error", Boolean(error));
  }

  function progress(text, value = null) {
    const box = $("#lf-progress");
    box.hidden = !text;
    $("#lf-progress-text").textContent = text || "";
    const bar = $("#lf-progress-bar");
    if (value === null) bar.removeAttribute("value"); else bar.value = Math.max(0, Math.min(1, value));
  }

  const megabytes = (bytes) => (bytes / (1024 * 1024)).toLocaleString(globalThis.TrainMeetI18n?.getLanguage?.() || "sv", { maximumFractionDigits: 1 });
  const when = (iso) => {
    if (!iso) return "";
    const date = new Date(iso);
    return date.toLocaleString(globalThis.TrainMeetI18n?.getLanguage?.() || "sv", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  };

  // ── Hitta Lokföraren ───────────────────────────────────────────────
  function renderFind() {
    const link = new URL("/lokforaren/", location.href).href;
    $("#lf-find-link").textContent = link;
    const host = $("#lf-find-qr");
    if (host.dataset.link === link || !globalThis.TrainMeetServerUI?.qrSVG || typeof globalThis.qrcode !== "function") return;
    host.dataset.link = link;
    host.innerHTML = globalThis.TrainMeetServerUI.qrSVG(link);
  }

  // ── Köruppdragen ───────────────────────────────────────────────────
  function statusOf(doc) {
    if (!doc.duties) return { text: t("Inga tjänster"), kind: "warn" };
    if (!doc.current) return { text: t("Annan träff"), kind: "" };
    return doc.published ? { text: t("Tillgängligt"), kind: "ok" } : { text: t("Inte tillgängligt än"), kind: "" };
  }

  const size = (bytes) => bytes < 1048576 ? `${Math.max(1, Math.round(bytes / 1024))} kB` : `${megabytes(bytes)} MB`;

  function actionButton(label, handler, primary = false) {
    const button = make("button", "kr-btn sm" + (primary ? " primary" : ""), label);
    button.type = "button";
    button.disabled = state.busy;
    button.addEventListener("click", handler);
    return button;
  }

  function pdfCell(doc) {
    const cell = make("td", "lf-docs__pdf");
    const pdf = doc.pdf;
    if (!pdf) {
      cell.append(make("span", "kr-m", t("Ingen förlaga än")));
      return cell;
    }
    cell.append(make("b", "", pdf.filename));
    const facts = [size(pdf.size)];
    if (pdf.pages) facts.push(t("{count} sidor", { count: pdf.pages }));
    cell.append(make("span", "kr-m", facts.join(" · ")));
    if (!pdf.has_file) cell.append(make("span", "kr-m lf-docs__warn", t("PDF-filen saknas på servern")));
    else if (!pdf.pages_read_at) cell.append(make("span", "kr-m lf-docs__warn", t("Sidorna är inte lästa")));
    else cell.append(make("span", "kr-m" + (pdf.duties_with_pages < doc.duties ? " lf-docs__warn" : ""),
      t("sidor för {found} av {count} tjänster", { found: pdf.duties_with_pages, count: doc.duties })));
    return cell;
  }

  function renderDocuments() {
    const documents = state.overview?.documents || [];
    const rows = $("#lf-docs-rows");
    rows.replaceChildren();
    $("#lf-docs-empty").hidden = documents.length > 0;
    for (const doc of documents) {
      const row = make("tr");
      if (state.review?.document.id === doc.id) row.className = "sel";
      const name = make("td", "lf-docs__name");
      name.append(make("b", "", doc.name || doc.filename));
      name.append(make("span", "kr-m", `${doc.name ? doc.filename + " · " : ""}${when(doc.uploaded_at)}`));
      row.append(name);
      row.append(make("td", "r", String(doc.duties)));
      row.append(pdfCell(doc));
      const status = statusOf(doc);
      const cell = make("td");
      cell.append(make("span", "kr-pill" + (status.kind ? ` ${status.kind}` : ""), status.text));
      const notes = (doc.warnings?.length || 0) + (doc.pdf?.warnings?.length || 0);
      if (notes) cell.append(make("span", "kr-m lf-docs__warn", t("{count} att titta på", { count: notes })));
      row.append(cell);

      const cellActions = make("td", "r lf-docs__actions");
      const actions = make("div", "lf-docs__buttons");
      cellActions.append(actions);
      if (doc.duties) actions.append(actionButton(t("Granska"), () => openReview(doc.id)));
      actions.append(actionButton(doc.pdf ? t("Byt PDF") : t("Lägg till PDF"), () => pickPdf(doc)));
      if (doc.pdf?.has_file) actions.append(actionButton(t("Läs sidorna igen"), () => readStored(doc)));
      if (doc.duties && doc.current) {
        actions.append(doc.published
          ? actionButton(t("Dra tillbaka"), () => publish(doc, false))
          : actionButton(t("Gör tillgängligt för förarna"), () => publish(doc, true), true));
      }
      const confirming = state.confirm === doc.id;
      const remove = actionButton(confirming ? t("Ta bort – säker?") : t("Ta bort"), () => removeDocument(doc));
      if (confirming) remove.classList.add("danger");
      actions.append(remove);
      row.append(cellActions);
      rows.append(row);
    }
  }

  const label = (doc) => doc.name || doc.filename;

  async function publish(doc, published) {
    await run(async () => {
      await api(`/v1/driver-admin/documents/${doc.id}/publish`, { method: "POST", body: { published } });
      message(published
        ? t("{name} är tillgängligt. Förarna hittar tjänsterna under Välj tjänst.", { name: label(doc) })
        : t("{name} är inte längre tillgängligt för förarna. Den som redan kör behåller sin tjänst.", { name: label(doc) }));
    });
  }

  async function removeDocument(doc) {
    if (state.confirm !== doc.id) {
      state.confirm = doc.id;
      renderDocuments();
      setTimeout(() => { if (state.confirm === doc.id) { state.confirm = null; renderDocuments(); } }, 4000);
      return;
    }
    state.confirm = null;
    await run(async () => {
      await api(`/v1/driver-admin/documents/${doc.id}/remove`, { method: "POST", body: {} });
      if (state.review?.document.id === doc.id) closeReview();
      message(t("{name} är borttaget.", { name: label(doc) }));
    });
  }

  async function run(work) {
    if (state.busy) return;
    state.busy = true;
    $("#lf-drop").setAttribute("aria-busy", "true");
    renderDocuments();
    try {
      await work();
    } catch (error) {
      message(error.message || t("Det gick inte. Försök igen."), true);
    } finally {
      state.busy = false;
      $("#lf-drop").removeAttribute("aria-busy");
      progress("");
      await refresh();
    }
  }

  const reader = () => {
    if (!globalThis.LokforarenKoruppdrag) throw new ApiError(t("Inläsningen kunde inte starta. Ladda om sidan och försök igen."));
    return globalThis.LokforarenKoruppdrag;
  };

  // Planeringsfilen: tjänsterna läses här och sparas som ett köruppdrag.
  async function addPlan(file) {
    progress(t("Läser planeringsfilen…"), null);
    const source = await file.text();
    let plan;
    try { plan = JSON.parse(source); } catch (error) { throw new ApiError(t("{name} är ingen läsbar JSON-fil.", { name: file.name })); }
    let result;
    try { result = reader().fromPlan(plan); } catch (error) { throw new ApiError(t(error.message)); }
    if (!result.duties.length) throw new ApiError(t("Inga köruppdrag hittades i {name}.", { name: file.name }));
    progress(t("Sparar {count} tjänster…", { count: result.duties.length }), null);
    const saved = await api("/v1/driver-admin/documents", { method: "POST", body: {
      filename: file.name, fingerprint: reader().fingerprint(source), name: result.name,
      duties: result.duties, warnings: result.warnings,
    } });
    const doc = saved.document;
    message(doc.duplicate
      ? t("{name} fanns redan. Tjänsterna är uppdaterade ({count} st).", { name: label(doc), count: doc.duties })
      : t("{count} tjänster lästes in ur {name}. Lägg till PDF:en som förlaga, granska och gör sedan köruppdraget tillgängligt.", { count: doc.duties, name: label(doc) }));
    await openReview(doc.id, false);
  }

  // PDF:en laddas upp som förlaga till ett köruppdrag. Uppladdningen visar
  // hur långt den kommit; fetch kan inte det, XHR kan.
  function uploadPdf(doc, file) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", `/v1/driver-admin/documents/${doc.id}/pdf`);
      request.setRequestHeader("Content-Type", "application/pdf");
      request.setRequestHeader("X-Filename", encodeURIComponent(file.name));
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) progress(t("Laddar upp {done} av {total} MB", { done: megabytes(event.loaded), total: megabytes(event.total) }), event.loaded / event.total);
      });
      request.addEventListener("load", () => {
        let payload = {};
        try { payload = JSON.parse(request.responseText); } catch (error) { /* svaret saknas */ }
        if (request.status >= 200 && request.status < 300) resolve(payload.document);
        else reject(new ApiError(payload.message || t("Det gick inte. Försök igen.")));
      });
      request.addEventListener("error", () => reject(new ApiError(t("Servern svarar inte. Försök igen."))));
      request.send(file);
    });
  }

  async function uploadPart(doc, number, bytes) {
    const response = await fetch(`/v1/driver-admin/documents/${doc.id}/parts`, {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/pdf", "X-Duty": encodeURIComponent(number) }, body: bytes,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new ApiError(payload.message || t("Det gick inte. Försök igen."));
    }
  }

  async function readPages(doc, data) {
    const { duties } = await api(`/v1/driver-admin/documents/${doc.id}`);
    progress(t("Läser PDF:en…"), null);
    const pages = await reader().readPdf(data, ({ page, pages: total }) => progress(t("Läser sidan {page} av {pages}", { page, pages: total }), page / total));
    const result = reader().matchPages(pages, duties);
    const saved = await api(`/v1/driver-admin/documents/${doc.id}/pages`, { method: "POST", body: result });
    const found = saved.document.pdf?.duties_with_pages || 0;
    if (!found) {
      message(t("Inga tjänster hittades i PDF:en. Är det köruppdragen till {name}?", { name: label(doc) }), true);
      await openReview(doc.id, false);
      return;
    }
    // Varje tjänsts sidor som en egen liten PDF, så att föraren inte behöver
    // hela häftet. Går det inte får föraren hela PDF:en på rätt sida.
    let parts = 0;
    try {
      parts = await reader().split(data, result.pages, async ({ number, bytes, done, total }) => {
        progress(t("Delar upp förlagan: tjänst {number} ({done} av {total})", { number, done, total }), done / total);
        await uploadPart(doc, number, bytes);
      });
    } catch (error) {
      parts = 0;
      result.warnings.push(error.message);
    }
    message(parts
      ? t("Sidorna hittades för {found} av {count} tjänster i {pages} sidor. Varje förare får bara sin tjänsts sidor.", { found, count: saved.document.duties, pages: result.total })
      : t("Sidorna hittades för {found} av {count} tjänster i {pages} sidor, men förlagan kunde inte delas upp. Föraren får hela PDF:en.", { found, count: saved.document.duties, pages: result.total }));
    await openReview(doc.id, false);
  }

  async function addPdf(doc, file) {
    const limit = state.overview?.limits?.pdf_bytes || 80 * 1024 * 1024;
    if (file.size > limit) throw new ApiError(t("PDF:en är större än {size} MB.", { size: Math.round(limit / 1048576) }));
    const saved = await uploadPdf(doc, file);
    if (saved.same_pdf && saved.pdf?.pages_read_at) {
      message(t("Samma PDF finns redan för {name}.", { name: label(doc) }));
      return;
    }
    await readPages(doc, await file.arrayBuffer());
  }

  // Vart en PDF ska: köruppdraget man valt i tabellen, annars det senaste
  // för den här träffen.
  function targetFor() {
    const documents = (state.overview?.documents || []).filter((doc) => doc.current);
    return documents.find((doc) => doc.id === state.pdfTarget) || documents[0] || null;
  }

  function pickPdf(doc) {
    state.pdfTarget = doc.id;
    $("#lf-pdf-file").click();
  }

  async function upload(file, target = null) {
    if (!file) return;
    message("");
    const isPdf = /\.pdf$/i.test(file.name) || file.type === "application/pdf";
    const isJson = /\.json$/i.test(file.name) || file.type === "application/json";
    if (!isPdf && !isJson) { message(t("Välj planeringsfilen (JSON) eller köruppdragens PDF."), true); return; }
    if (isPdf) {
      const doc = target || targetFor();
      if (!doc) { message(t("Börja med planeringsfilen (JSON). PDF:en blir sedan förlaga till tjänsterna i den."), true); return; }
      await run(() => addPdf(doc, file));
      return;
    }
    await run(() => addPlan(file));
  }

  async function readStored(doc) {
    message("");
    await run(async () => {
      progress(t("Hämtar PDF:en…"), null);
      const response = await fetch(`/v1/driver-admin/documents/${doc.id}.pdf`, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new ApiError(t("PDF:en finns inte längre på servern. Ladda upp den igen."));
      await readPages(doc, await response.arrayBuffer());
    });
  }

  // ── Granska ────────────────────────────────────────────────────────
  async function openReview(id, scroll = true) {
    try {
      state.review = await api(`/v1/driver-admin/documents/${id}`);
    } catch (error) {
      message(error.message, true);
      return;
    }
    $("#lf-review").hidden = false;
    $("#lf-review-name").textContent = state.review.document.name || state.review.document.filename;
    $("#lf-review-search").value = "";
    const warnings = $("#lf-review-warnings");
    const notes = [...state.review.document.warnings, ...(state.review.document.pdf?.warnings || [])];
    warnings.replaceChildren(...notes.map((text) => make("li", "", text)));
    warnings.hidden = !notes.length;
    renderReview();
    renderDocuments();
    if (scroll) $("#lf-review").scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function closeReview() {
    state.review = null;
    $("#lf-review").hidden = true;
    renderDocuments();
  }

  function renderReview() {
    if (!state.review) return;
    const query = $("#lf-review-search").value.trim().toLowerCase();
    const rows = $("#lf-review-rows");
    rows.replaceChildren();
    const duties = state.review.duties.filter((duty) => !query || duty.number.toLowerCase().startsWith(query));
    for (const duty of duties.slice(0, 400)) {
      const row = make("tr");
      row.append(make("td", "", duty.number));
      row.append(make("td", "kr-mono", `${duty.start}–${duty.end}`));
      row.append(make("td", "", duty.trains.join(" · ") || "–"));
      row.append(make("td", "m lf-review__route", duty.route.join(" → ")));
      const pages = duty.pages?.filter(Boolean) || [];
      const span = pages.length ? (pages[0] === pages[pages.length - 1] ? String(pages[0]) : `${pages[0]}–${pages[pages.length - 1]}`) : "–";
      const cell = make("td", "r");
      if (duty.part) {
        // Tjänstens egna sidor, som föraren får dem.
        const link = make("a", "", span);
        link.href = `/v1/driver-admin/duties/${encodeURIComponent(duty.id)}.pdf`;
        link.target = "_blank";
        link.rel = "noopener";
        cell.append(link);
      } else {
        cell.textContent = span;
      }
      row.append(cell);
      rows.append(row);
    }
    if (duties.length > 400) {
      const more = make("tr");
      const cell = make("td", "m", t("och {count} till – sök på tjänstens nummer", { count: duties.length - 400 }));
      cell.colSpan = 5;
      more.append(cell);
      rows.append(more);
    }
  }

  // ── Förarna ────────────────────────────────────────────────────────
  function renderDrivers() {
    const drivers = state.overview?.drivers || [];
    const rows = $("#lf-drivers-rows");
    rows.replaceChildren();
    $("#lf-drivers-empty").hidden = drivers.length > 0;
    $("#lf-drivers-count").textContent = String(drivers.filter((driver) => driver.duty_number).length) + " " + t("kör nu");
    for (const driver of drivers) {
      const row = make("tr");
      const cell = make("td");
      const who = make("div", "lf-driver");
      cell.append(who);
      if (driver.avatar_url) {
        const image = make("img", "lf-driver__avatar");
        image.src = driver.avatar_url; image.alt = ""; image.width = 24; image.height = 24;
        who.append(image);
      } else {
        who.append(make("span", "lf-driver__avatar lf-driver__initials", driver.initials));
      }
      who.append(make("span", "", driver.name));
      if (driver.invitation_pending) who.append(make("span", "kr-pill", t("Inbjuden")));
      row.append(cell);
      row.append(make("td", "", driver.duty_number
        ? `${driver.duty_demo ? t("Övning") + " " : ""}${driver.duty_number}${driver.trains.length ? " · " + driver.trains.join(", ") : ""}`
        : "–"));
      const checkin = driver.checkin;
      // Paus och inväntar start är lägen utan station: föraren är inte på banan.
      const where = checkin && (checkin.status === "pause" ? t("Paus")
        : checkin.status === "waiting" ? t("Inväntar start av uppdrag")
          : `${checkin.station}${checkin.track ? " " + t("spår {track}", { track: checkin.track }) : ""}`);
      row.append(make("td", checkin ? "" : "m", checkin
        ? `${where}${checkin.meet_time ? " · " + checkin.meet_time.slice(0, 5) : ""}`
        : "–"));
      rows.append(row);
    }
  }

  // ── Uppdatering ────────────────────────────────────────────────────
  async function refresh() {
    clearTimeout(state.timer);
    if (!shown()) return;
    renderFind();
    try {
      state.overview = await api("/v1/driver-admin/overview");
      renderDocuments();
      renderDrivers();
    } catch (error) {
      if (!state.busy) message(error.message, true);
    }
    state.timer = setTimeout(refresh, POLL);
  }

  function bind() {
    if (!$("#lokforare")) return;
    $("#lf-file").addEventListener("change", (event) => { const file = event.target.files[0]; event.target.value = ""; upload(file); });
    $("#lf-pdf-file").addEventListener("change", (event) => {
      const file = event.target.files[0];
      event.target.value = "";
      const doc = (state.overview?.documents || []).find((item) => item.id === state.pdfTarget);
      if (file && doc) upload(file, doc);
    });
    const drop = $("#lf-drop");
    drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("is-dragover"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("is-dragover"));
    drop.addEventListener("drop", async (event) => {
      event.preventDefault();
      drop.classList.remove("is-dragover");
      // Planeringsfilen och PDF:en kan släppas tillsammans: planen först.
      const files = [...event.dataTransfer.files].sort((a, b) => Number(/\.pdf$/i.test(a.name)) - Number(/\.pdf$/i.test(b.name)));
      for (const file of files.slice(0, 2)) await upload(file);
    });
    $("#lf-review-search").addEventListener("input", renderReview);
    $("#lf-review-close").addEventListener("click", closeReview);
    addEventListener("hashchange", () => setTimeout(refresh, 0));
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); });
    // Sidan byter läge (Drift, Inställningar …) utan att adressens hash ändras.
    new MutationObserver(() => refresh()).observe(document.body, { attributes: true, attributeFilter: ["data-mode"] });
    globalThis.TrainMeetI18n?.subscribe?.(() => { renderDocuments(); renderDrivers(); renderReview(); });
    refresh();
  }

  globalThis.TrainMeetLokforareAdmin = { refresh };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind); else bind();
})();
