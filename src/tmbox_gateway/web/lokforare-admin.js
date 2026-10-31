/* Inställningar → Lokförare och köruppdrag (#225).
 *
 * Tre saker på en sida: vägen till förarnas app (adress och QR-kod), träffens
 * köruppdrag (ladda upp, läs in, granska, gör tillgängliga) och vilka förare
 * som kör just nu. Köruppdraget går i tre uttryckliga steg så att inget når
 * förarna av misstag:
 *
 *   1. PDF:en laddas upp oförändrad till servern.
 *   2. Tjänsterna läses ur PDF:en här i webbläsaren (LokforarenKoruppdrag)
 *      och sparas på servern, som bara kontrollerar formen.
 *   3. Administratören granskar och gör köruppdraget tillgängligt.
 */
(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const t = (source, values) => globalThis.TrainMeetI18n ? globalThis.TrainMeetI18n.t(source, values) : source;
  const POLL = 15000;
  const state = { overview: null, review: null, busy: false, timer: null, confirm: null };

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
    if (doc.status !== "indexed") return { text: t("Inte inläst"), kind: "warn" };
    if (!doc.duties) return { text: t("Inga tjänster hittades"), kind: "warn" };
    if (!doc.current) return { text: t("Annan träff"), kind: "" };
    return doc.published ? { text: t("Tillgängligt"), kind: "ok" } : { text: t("Inte tillgängligt än"), kind: "" };
  }

  function actionButton(label, handler, primary = false) {
    const button = make("button", "kr-btn sm" + (primary ? " primary" : ""), label);
    button.type = "button";
    button.disabled = state.busy;
    button.addEventListener("click", handler);
    return button;
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
      name.append(make("b", "", doc.filename));
      name.append(make("span", "kr-m", `${when(doc.uploaded_at)} · ${doc.size < 1048576 ? `${Math.max(1, Math.round(doc.size / 1024))} kB` : `${megabytes(doc.size)} MB`}`));
      if (!doc.has_file) name.append(make("span", "kr-m", t("PDF-filen saknas på servern")));
      row.append(name);
      row.append(make("td", "r", doc.pages ? String(doc.pages) : "–"));
      row.append(make("td", "r", doc.status === "indexed" ? String(doc.duties) : "–"));
      const status = statusOf(doc);
      const cell = make("td");
      cell.append(make("span", "kr-pill" + (status.kind ? ` ${status.kind}` : ""), status.text));
      if (doc.warnings?.length) cell.append(make("span", "kr-m lf-docs__warn", t("{count} att titta på", { count: doc.warnings.length })));
      row.append(cell);

      const actions = make("td", "r lf-docs__actions");
      if (doc.status === "indexed" && doc.duties) {
        actions.append(actionButton(t("Granska"), () => openReview(doc.id)));
      }
      if (doc.has_file) {
        actions.append(actionButton(doc.status === "indexed" ? t("Läs in igen") : t("Läs in"), () => readStored(doc)));
      }
      if (doc.status === "indexed" && doc.duties && doc.current) {
        actions.append(doc.published
          ? actionButton(t("Dra tillbaka"), () => publish(doc, false))
          : actionButton(t("Gör tillgängligt för förarna"), () => publish(doc, true), true));
      }
      const confirming = state.confirm === doc.id;
      const remove = actionButton(confirming ? t("Ta bort – säker?") : t("Ta bort"), () => removeDocument(doc));
      if (confirming) remove.classList.add("danger");
      actions.append(remove);
      row.append(actions);
      rows.append(row);
    }
  }

  async function publish(doc, published) {
    await run(async () => {
      await api(`/v1/driver-admin/documents/${doc.id}/publish`, { method: "POST", body: { published } });
      message(published
        ? t("{name} är tillgängligt. Förarna hittar tjänsterna under Välj tjänst.", { name: doc.filename })
        : t("{name} är inte längre tillgängligt för förarna. Den som redan kör behåller sin tjänst.", { name: doc.filename }));
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
      message(t("{name} är borttaget.", { name: doc.filename }));
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

  // Uppladdningen visar hur långt den kommit; fetch kan inte det, XHR kan.
  function uploadFile(file) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", "/v1/driver-admin/documents");
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

  async function readAndSave(doc, data) {
    const reader = globalThis.LokforarenKoruppdrag;
    if (!reader) throw new ApiError(t("Inläsningen kunde inte starta. Ladda om sidan och försök igen."));
    progress(t("Läser köruppdragen…"), null);
    const result = await reader.read(data, ({ page, pages }) => progress(t("Läser sidan {page} av {pages}", { page, pages }), page / pages));
    progress(t("Sparar {count} tjänster…", { count: result.duties.length }), null);
    const saved = await api(`/v1/driver-admin/documents/${doc.id}/index`, { method: "POST", body: result });
    const found = saved.document.duties;
    message(found
      ? t("{count} tjänster lästes in från {pages} sidor. Granska dem och gör sedan köruppdraget tillgängligt.", { count: found, pages: saved.document.pages || result.pages })
      : t("Inga tjänster hittades i {name}. Är det rätt PDF?", { name: saved.document.filename }), !found);
    if (found) await openReview(doc.id, false);
  }

  async function upload(file) {
    if (!file) return;
    const limit = state.overview?.limits?.pdf_bytes || 80 * 1024 * 1024;
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") { message(t("Välj en PDF."), true); return; }
    if (file.size > limit) { message(t("PDF:en är större än {size} MB.", { size: Math.round(limit / 1048576) }), true); return; }
    message("");
    await run(async () => {
      const doc = await uploadFile(file);
      if (doc.duplicate && doc.status === "indexed") {
        message(t("{name} finns redan på servern för den här träffen.", { name: doc.filename }));
        return;
      }
      await readAndSave(doc, await file.arrayBuffer());
    });
  }

  async function readStored(doc) {
    message("");
    await run(async () => {
      progress(t("Hämtar PDF:en…"), null);
      const response = await fetch(`/v1/driver-admin/documents/${doc.id}.pdf`, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new ApiError(t("PDF:en finns inte längre på servern. Ladda upp den igen."));
      await readAndSave(doc, await response.arrayBuffer());
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
    $("#lf-review-name").textContent = state.review.document.filename;
    $("#lf-review-search").value = "";
    const warnings = $("#lf-review-warnings");
    warnings.replaceChildren(...state.review.document.warnings.map((text) => make("li", "", text)));
    warnings.hidden = !state.review.document.warnings.length;
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
      row.append(make("td", "r", pages.length ? (pages[0] === pages[pages.length - 1] ? String(pages[0]) : `${pages[0]}–${pages[pages.length - 1]}`) : "–"));
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
      const who = make("td", "lf-driver");
      if (driver.avatar_url) {
        const image = make("img", "lf-driver__avatar");
        image.src = driver.avatar_url; image.alt = ""; image.width = 24; image.height = 24;
        who.append(image);
      } else {
        who.append(make("span", "lf-driver__avatar lf-driver__initials", driver.initials));
      }
      who.append(make("span", "", driver.name));
      if (driver.invitation_pending) who.append(make("span", "kr-pill", t("Inbjuden")));
      row.append(who);
      row.append(make("td", "", driver.duty_number
        ? `${driver.duty_demo ? t("Övning") + " " : ""}${driver.duty_number}${driver.trains.length ? " · " + driver.trains.join(", ") : ""}`
        : "–"));
      const checkin = driver.checkin;
      row.append(make("td", checkin ? "" : "m", checkin
        ? `${checkin.station}${checkin.track ? " " + t("spår {track}", { track: checkin.track }) : ""}${checkin.meet_time ? " · " + checkin.meet_time.slice(0, 5) : ""}`
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
    const drop = $("#lf-drop");
    drop.addEventListener("dragover", (event) => { event.preventDefault(); drop.classList.add("is-dragover"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("is-dragover"));
    drop.addEventListener("drop", (event) => { event.preventDefault(); drop.classList.remove("is-dragover"); upload(event.dataTransfer.files[0]); });
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
