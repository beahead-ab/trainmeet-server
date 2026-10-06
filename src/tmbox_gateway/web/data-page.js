/* Tidtabellen på servern (/tidtabell).
 *
 * Clouds Data-vy (data-workspace.js, kopierad från Cloud) med den tidtabell
 * servern kör. Det admin sparar här blir ett lokalt lager ovanpå Clouds
 * version och gäller direkt i driften (POST /v1/meet-data); Cloud ser det
 * inte. Raden överst säger hur många lokala ändringar som finns och kan gå
 * tillbaka till Clouds version. När Cloud har publicerat en ny version medan
 * lokala ändringar finns väljer admin här: Ta Cloud-versionen eller Behåll
 * mina ändringar.
 *
 * Vyn sparar hela tidtabellen. Därför skickas alltid revisionen av den
 * tidtabell vyn utgick från: har någon annan sparat under tiden svarar
 * servern 409, och ingens ändring skrivs över utan att synas. Ny data från
 * servern ges bara till vyn när inget är osparat; annars väntar den tills
 * admin har sparat eller kastat sina ändringar.
 */
(() => {
  const t = (text, values) => globalThis.TrainMeetI18n.t(text, values);
  const $ = (selector) => document.querySelector(selector);
  const EMPTY = { trains: {}, connections: {} };

  let request = (path, options) => fetch(path, { ...options, credentials: "same-origin" });
  let handle = null;          // TrainMeetDataWorkspace.mount(...)
  let visible = false;
  let latest = null;          // senaste svaret från GET /v1/meet-data
  let shown = null;           // det vyn utgår från: {meet, revision, base, generation}
  let pending = null;         // senaste svaret från GET /v1/runtime/pending
  let editState = { dirty: false, busy: false };
  let loading = null, again = false, unsubscribe = null;

  async function readJSON(response) {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(body.message || t("Tidtabellen kunde inte läsas."));
      error.status = response.status; error.code = body.code || body.error;
      throw error;
    }
    return body;
  }

  function setMessage(text, kind = "error") {
    const node = $("#data-message");
    node.textContent = text || "";
    node.className = `form-message${text ? " " + kind : ""}`;
  }

  function meetOf(data) {
    // Data-vyn läser kontrollen som Cloud lägger den: findings.conflicts och findings.sanity.
    const review = data.review || {};
    return { ...data.draft, findings: { ...(review.findings || {}), sanity: review.sanity || [] } };
  }

  function theme() { return document.documentElement.dataset.krTheme === "light" ? "light" : "dark"; }

  // Vyn får ny data bara när inget är osparat: annars skulle Spara skicka en
  // gammal tidtabell med en ny revision och skriva över någon annans ändring.
  function offer() {
    if (!latest || !handle || editState.dirty || editState.busy) return;
    if (shown && shown.revision === latest.revision && shown.base === latest.base_publication_id && shown.source === latest) return;
    shown = { meet: meetOf(latest), revision: latest.revision, base: latest.base_publication_id, generation: latest.meet_generation, source: latest };
    handle.update({ meet: shown.meet, localChanges: latest.local_changes || EMPTY, theme: theme() });
  }

  function onEditState(state) {
    editState = state;
    if (!state.dirty && !state.busy) offer();
    renderLocal();
  }

  async function save(draft) {
    const response = await request("/v1/meet-data", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ draft, expected_revision: shown.revision, base_publication_id: shown.base, meet_generation: shown.generation }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 409 && ["stale_local_edits", "stale_meet_context"].includes(body.code || body.error)) {
        load().catch(() => {});
        throw new Error(t("Någon annan har ändrat tidtabellen sedan du började. Tryck Avbryt för att se den nya och gör sedan om din ändring."));
      }
      throw new Error(body.message || t("Tidtabellen kunde inte sparas."));
    }
    // Sparat. Svaret saknar tidtabellen; den som nu gäller hämtas, så att vyn
    // utgår från exakt det servern har (dagar och tider som servern skriver dem).
    shown = { meet: draft, revision: body.revision, base: body.base_publication_id, generation: body.meet_generation, source: null };
    refreshPending();
    try {
      latest = await readJSON(await request("/v1/meet-data", { cache: "no-store" }));
    } catch {
      return draft;
    }
    renderLocal();
    // Har någon hunnit spara efter oss ges deras version till vyn av offer(), när den är ren.
    if (latest.revision !== shown.revision || latest.base_publication_id !== shown.base) return draft;
    shown = { meet: meetOf(latest), revision: latest.revision, base: latest.base_publication_id, generation: latest.meet_generation, source: latest };
    handle.update({ meet: shown.meet, localChanges: latest.local_changes || EMPTY });
    return shown.meet;
  }

  // Clouds vy är en halv megabyte. Den hämtas först när sidan öppnas, så
  // deltagarvyn och Drift laddar den aldrig.
  let script = null;
  function workspaceScript() {
    if (globalThis.TrainMeetDataWorkspace) return Promise.resolve();
    script ||= new Promise((resolve, reject) => {
      const element = document.createElement("script");
      element.src = "/assets/data-workspace.js";
      element.onload = () => resolve();
      element.onerror = () => { script = null; element.remove(); reject(new Error(t("Tidtabellsvyn kunde inte laddas. Ladda om sidan."))); };
      document.head.append(element);
    });
    return script;
  }

  function mount() {
    const host = $("#data-workspace");
    if (!globalThis.TrainMeetDataWorkspace) throw new Error(t("Tidtabellsvyn kunde inte laddas. Ladda om sidan."));
    shown = { meet: meetOf(latest), revision: latest.revision, base: latest.base_publication_id, generation: latest.meet_generation, source: latest };
    handle = globalThis.TrainMeetDataWorkspace.mount(host, { meet: shown.meet, save, localChanges: latest.local_changes || EMPTY, theme: theme(), onEditState });
  }

  // En hämtning åt gången; kommer fler händelser under tiden hämtas det en gång till efteråt.
  async function load() {
    if (loading) { again = true; return loading; }
    loading = (async () => {
      try {
        const [data] = await Promise.all([request("/v1/meet-data", { cache: "no-store" }).then(readJSON), workspaceScript()]);
        latest = data;
        // Ett fel från förra hämtningen gäller inte längre; ett besked om det som just gjordes står kvar.
        if ($("#data-message").classList.contains("error")) setMessage("");
        if (!handle) mount(); else offer();
        renderLocal();
      } catch (error) {
        setMessage(error.message);
        throw error;
      } finally {
        loading = null;
        if (again) { again = false; load().catch(() => {}); }
      }
    })();
    return loading;
  }

  function renderLocal() {
    const box = $("#data-local");
    if (!latest) { box.hidden = true; return; }
    const local = latest.local_edits || {};
    const count = local.count || 0;
    box.hidden = false;
    box.className = `kr-state${count ? "" : " ok"}`;
    $("#data-local-state").textContent = count === 0 ? t("Samma som Cloud-versionen.")
      : count === 1 ? t("1 lokal ändring ovanpå Cloud-versionen.") : t("{count} lokala ändringar ovanpå Cloud-versionen.", { count });
    $("#data-local-note").textContent = t("Det du sparar här gäller direkt i driften och ändrar inte Cloud.");
    const lines = $("#data-local-lines");
    lines.hidden = !count;
    lines.querySelector("ul").replaceChildren(...(local.lines || []).map((line) => {
      const item = document.createElement("li"); item.textContent = line; return item;
    }));
    const discard = $("#data-discard");
    discard.hidden = !count;
    discard.disabled = editState.busy;
  }

  async function discard() {
    if (!latest?.local_edits?.count) return;
    if (!confirm(t("Alla lokala ändringar tas bort och Clouds tidtabell gäller igen, direkt i driften. En säkerhetskopia tas först. Fortsätta?"))) return;
    const button = $("#data-discard");
    button.disabled = true;
    try {
      const response = await request("/v1/meet-data/discard", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: latest.revision, meet_generation: latest.meet_generation }),
      });
      await readJSON(response);
      setMessage(t("Clouds tidtabell gäller igen."), "success");
      await load();
      refreshPending();
    } catch (error) {
      setMessage(error.message);
    } finally { button.disabled = false; }
  }

  // ── Ny Cloud-version medan lokala ändringar finns ──────────────────────

  function changeLines(changes) {
    if (!changes || changes.first_activation) return [];
    const names = (part) => [...(part?.names || []), ...(part?.more ? [t("och {count} till", { count: part.more })] : [])].join(", ");
    const lines = [];
    const stations = changes.stations || {}, links = changes.connections || {}, table = changes.timetable || {};
    if (stations.added?.count) lines.push(t("Nya stationer: {names}", { names: names(stations.added) }));
    if (stations.removed?.count) lines.push(t("Stationer som tas bort: {names}", { names: names(stations.removed) }));
    if (stations.renamed?.count) lines.push(t("Stationer som byter namn: {names}", { names: names(stations.renamed) }));
    if (links.added) lines.push(t("{count} nya sträckor", { count: links.added }));
    if (links.removed) lines.push(t("{count} sträckor tas bort", { count: links.removed }));
    if (table.added) lines.push(t("{count} nya tågrörelser", { count: table.added }));
    if (table.removed) lines.push(t("{count} tågrörelser tas bort", { count: table.removed }));
    if (table.changed?.count) lines.push(t("Ändrade tåg: {names}", { names: names(table.changed) }));
    return lines;
  }

  function list(selector, lines, empty) {
    $(selector).replaceChildren(...(lines.length ? lines : [empty]).map((line) => {
      const item = document.createElement("li"); item.textContent = line; return item;
    }));
  }

  function renderChoice() {
    const box = $("#data-choice");
    const local = pending?.pending ? pending.local_edits : null;
    box.hidden = !local;
    if (!local) return;
    list("#data-choice-changes", changeLines(pending.changes), t("Inga skillnader i stationer, sträckor eller tågrörelser."));
    list("#data-choice-local", local.lines || [], t("Inga lokala ändringar."));
    const kept = pending.decision === "keep";
    $("#data-choice-note").textContent = kept
      ? t("Du har valt att behålla dina ändringar. Cloud-versionen väntar tills du tar den eller Cloud publicerar en nyare.")
      : t("Inget byts förrän du väljer. Ta Cloud-versionen tar en säkerhetskopia först och byter när trafiken tillåter.");
    $("#data-keep").hidden = kept;
  }

  async function refreshPending() {
    try {
      pending = await readJSON(await request("/v1/runtime/pending", { cache: "no-store" }));
    } catch { pending = null; }
    renderChoice();
  }

  async function decide(decision) {
    if (!pending?.pending || !pending.local_edits) return;
    if (decision === "take" && !confirm(t("Dina lokala ändringar försvinner och Cloud-versionen tas i bruk när trafiken tillåter. En säkerhetskopia tas först. Fortsätta?"))) return;
    const buttons = [$("#data-keep"), $("#data-take")];
    buttons.forEach((button) => { button.disabled = true; });
    const message = $("#data-choice-message");
    try {
      const response = await request("/v1/cloud/local-decision", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, publication_id: pending.publication_id, expected_revision: pending.local_edits.revision, meet_generation: latest?.meet_generation }),
      });
      const body = await readJSON(response);
      message.textContent = body.message_template ? t(body.message_template, body.message_values || {}) : (body.message ? t(body.message) : "");
      message.className = `form-message${message.textContent ? " success" : ""}`;
    } catch (error) {
      message.textContent = error.message;
      message.className = "form-message error";
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
      await Promise.allSettled([load(), refreshPending()]);
    }
  }

  // ── Sidan ──────────────────────────────────────────────────────────────

  function onLive(topics) {
    if (!visible || !topics.has("runtime")) return;
    load().catch(() => {});
    refreshPending();
  }

  function beforeUnload(event) {
    if (editState.dirty) { event.preventDefault(); event.returnValue = ""; }
  }

  let bound = false;
  function bind() {
    if (bound) return;
    bound = true;
    $("#data-discard").addEventListener("click", discard);
    $("#data-keep").addEventListener("click", () => decide("keep"));
    $("#data-take").addEventListener("click", () => decide("take"));
    // Temat följer serverns väljare i sidhuvudet.
    new MutationObserver(() => handle?.update({ theme: theme() }))
      .observe(document.documentElement, { attributes: true, attributeFilter: ["data-kr-theme"] });
    globalThis.TrainMeetI18n.subscribe(() => { renderLocal(); renderChoice(); });
    window.addEventListener("beforeunload", beforeUnload);
  }

  globalThis.TrainMeetDataPage = {
    /** Visar sidan. `fetch` är app.js:s authorizedFetch. Vyn står kvar när sidan döljs, så osparat finns kvar. */
    show(options = {}) {
      if (options.fetch) request = options.fetch;
      bind();
      visible = true;
      if (!unsubscribe) unsubscribe = globalThis.TrainMeetLive?.subscribe(onLive) || null;
      setMessage("");
      $("#data-choice-message").textContent = "";
      load().catch(() => {});
      refreshPending();
    },
    hide() {
      visible = false;
      unsubscribe?.();
      unsubscribe = null;
    },
    get dirty() { return editState.dirty; },
  };
})();
