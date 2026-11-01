/* Inställningar → Lokförare och köruppdrag (#225).
 *
 * Tre saker på en sida: vägen till förarnas app (adress och QR-kod), träffens
 * köruppdrag och vilka förare som kör just nu.
 *
 * Köruppdragen läggs in och görs tillgängliga i TrainMeet Cloud, under
 * Lokförare på träffen: en träff är en träff, och köruppdragen är dess första
 * lager. Servern visar bara vad den fått med träffen och hämtar varje tjänsts
 * egna sidor med serverkopplingen. Köruppdrag som lades in här innan de
 * flyttade till Cloud (4.20–4.21) går att granska, dra tillbaka och ta bort.
 */
(() => {
  "use strict";
  const $ = (selector, root = document) => root.querySelector(selector);
  const t = (source, values) => globalThis.TrainMeetI18n ? globalThis.TrainMeetI18n.t(source, values) : source;
  const plural = (count, one, many) => t(count === 1 ? one : many, { count });
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

  // ── Köruppdragen från Cloud ────────────────────────────────────────
  function renderCloud() {
    const cloud = state.overview?.cloud || null;
    const duties = cloud?.duties || 0;
    const facts = $("#lf-cloud-facts");
    const note = $("#lf-cloud-note");
    const status = $("#lf-cloud-status");
    const link = $("#lf-cloud-link");
    let notice = "";
    let warn = false;
    if (!cloud) {
      facts.textContent = t("Servern kör ingen träff från Cloud än. Välj träff under Träff och Cloud.");
    } else if (!duties) {
      facts.textContent = [cloud.meet, t("inga köruppdrag än")].join(" · ");
      notice = t("Förarna kan öva med tjänst 132 tills träffens köruppdrag är tillgängliga i Cloud.");
    } else {
      facts.textContent = [cloud.meet, plural(duties, "{count} tjänst", "{count} tjänster"),
        t("publicerad {when}", { when: when(cloud.published_at) })].join(" · ");
      if (!cloud.parts) {
        notice = t("Ingen PDF i Cloud: förarna ser körplanen men inte förlagan.");
      } else if (cloud.parts_here < cloud.parts) {
        warn = !cloud.linked;
        notice = cloud.linked
          ? t("Hämtar tjänsternas sidor från Cloud: {done} av {total}", { done: cloud.parts_here, total: cloud.parts })
          : t("Servern är inte kopplad till Cloud och kan inte hämta tjänsternas sidor.");
      }
    }
    note.textContent = notice;
    note.hidden = !notice;
    note.classList.toggle("lf-docs__warn", warn);
    status.hidden = !duties;
    status.className = "kr-pill ok";
    status.textContent = t("Tillgängligt för förarna");
    link.hidden = !cloud?.url;
    if (cloud?.url) link.href = cloud.url;
    $("#lf-cloud-link-text").textContent = duties ? t("Ändra i Cloud") : t("Lägg in i Cloud");
  }

  // ── Köruppdrag som lades in på servern ─────────────────────────────
  function statusOf(doc, replaced) {
    if (!doc.duties) return { text: t("Inga tjänster"), kind: "warn" };
    if (!doc.current) return { text: t("Annan träff"), kind: "" };
    if (doc.published && replaced) return { text: t("Ersatt av Cloud"), kind: "" };
    return doc.published ? { text: t("Tillgängligt"), kind: "ok" } : { text: t("Inte tillgängligt"), kind: "" };
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
    const replaced = Boolean(state.overview?.cloud?.duties);
    $("#lf-local").hidden = !documents.length;
    const rows = $("#lf-docs-rows");
    rows.replaceChildren();
    for (const doc of documents) {
      const row = make("tr");
      if (state.review?.document.id === doc.id) row.className = "sel";
      const name = make("td", "lf-docs__name");
      name.append(make("b", "", doc.name || doc.filename));
      name.append(make("span", "kr-m", `${doc.name ? doc.filename + " · " : ""}${when(doc.uploaded_at)}`));
      row.append(name);
      row.append(make("td", "r", String(doc.duties)));
      const status = statusOf(doc, replaced);
      const cell = make("td");
      cell.append(make("span", "kr-pill" + (status.kind ? ` ${status.kind}` : ""), status.text));
      row.append(cell);

      const cellActions = make("td", "r lf-docs__actions");
      const actions = make("div", "lf-docs__buttons");
      cellActions.append(actions);
      if (doc.duties) actions.append(actionButton(t("Granska"), () => openReview(doc.id)));
      // Nya köruppdrag görs tillgängliga i Cloud; här går det bara att dra tillbaka.
      if (doc.duties && doc.current && doc.published) actions.append(actionButton(t("Dra tillbaka"), () => publish(doc, false)));
      else if (doc.duties && doc.current && !replaced) actions.append(actionButton(t("Gör tillgängligt"), () => publish(doc, true)));
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
    renderDocuments();
    try {
      await work();
    } catch (error) {
      message(error.message || t("Det gick inte. Försök igen."), true);
    } finally {
      state.busy = false;
      await refresh();
    }
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
      renderCloud();
      renderDocuments();
      renderDrivers();
    } catch (error) {
      if (!state.busy) message(error.message, true);
    }
    state.timer = setTimeout(refresh, POLL);
  }

  function bind() {
    if (!$("#lokforare")) return;
    $("#lf-review-search").addEventListener("input", renderReview);
    $("#lf-review-close").addEventListener("click", closeReview);
    addEventListener("hashchange", () => setTimeout(refresh, 0));
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); });
    // Sidan byter läge (Drift, Inställningar …) utan att adressens hash ändras.
    new MutationObserver(() => refresh()).observe(document.body, { attributes: true, attributeFilter: ["data-mode"] });
    globalThis.TrainMeetI18n?.subscribe?.(() => { renderCloud(); renderDocuments(); renderDrivers(); renderReview(); });
    refresh();
  }

  globalThis.TrainMeetLokforareAdmin = { refresh };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind); else bind();
})();
