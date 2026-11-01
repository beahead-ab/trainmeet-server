/* Inställningar → Klarering → Tellurian Dispatch (#222).
 *
 * Webbläsaren pratar bara med TrainMeet Server (administratörens vägar under
 * /v1/clearance/tellurian), aldrig med Tellurian. Läget Skugga läser Tellurian
 * bredvid TrainMeets egen klarering och visar skillnaderna här; inget skrivs.
 * Läget där Tellurian bestämmer (etapp 4) går inte att välja än.
 *
 * Drift får en rad om klareringen så länge Tellurian läses i skugga: kontakt,
 * senaste kontakt och antal avvikelser, med väg till Inställningar. Raden läser
 * samma status och syns inte alls när läget är Av.
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const t = (source, values) => globalThis.TrainMeetI18n.t(source, values);
  const app = () => globalThis.TrainMeetApp;
  const form = $("tellurian-form");
  if (!form) return;
  let data = null, loading = false, busy = false;

  const STATES = {
    starting: "Skugga · ansluter …",
    connected: "Skugga · ansluten",
    polling: "Skugga · ansluten, läser regelbundet",
    no_contact: "Skugga · ingen kontakt",
  };
  const ERRORS = {
    unreachable: "Tellurian svarar inte på adressen.",
    timeout: "Tellurian svarade inte i tid.",
    redirect: "Tellurian svarade med en omdirigering. Den följs inte.",
    too_large: "Svaret från Tellurian var för stort.",
    bad_json: "Svaret från Tellurian gick inte att läsa.",
    bad_shape: "Svaret från Tellurian har oväntad form.",
    server_error: "Tellurian svarade med ett serverfel.",
    not_found: "Adressen svarar, men inte som Tellurian Dispatch.",
    bad_status: "Tellurian svarade med ett oväntat fel.",
    connection_lost: "Anslutningen till Tellurian bröts.",
    address_not_local: "Namnet pekar inte på en lokal adress.",
    refused: "Tellurian avvisade frågan.",
    budget: "Alla stationer hann inte läsas.",
    stream_refused: "Tellurians händelseström svarar inte.",
  };
  const DEVIATIONS = { only_trainmeet: "Bara i TrainMeet", only_tellurian: "Bara i Tellurian", state_differs: "Olika läge" };
  const CASE_STATES = { requested: "begärd", accepted: "klarerad", departed: "avgått" };

  const visible = () => document.body.dataset.mode === "installningar" && location.hash === "#klarering";
  const drift = $("drift-clearance");
  const onDrift = () => Boolean(drift) && document.body.dataset.mode === "kor" && !document.hidden;
  const message = (text, kind = "error") => app().setMessage($("tellurian-message"), text, kind);
  const errorText = (code) => t(ERRORS[code] || "Tellurian svarar inte som väntat.");
  const stateText = (state) => t(CASE_STATES[state] || "inget");

  async function request(path, payload) {
    const response = await app().fetch(path, payload === undefined ? { cache: "no-store" } : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || "Inställningen för Tellurian Dispatch är ogiltig.");
    return result;
  }

  function fill() {
    if (form.dataset.dirty === "true" || busy) return;
    const settings = data.settings;
    $("tellurian-address").value = settings.base_url || "";
    $("tellurian-test-instance").checked = Boolean(settings.test_instance);
    $("tellurian-mode").value = settings.mode;
    globalThis.TrainMeetSettings?.rebase(form);
  }

  function setTag(element, kind) { element.className = "kr-tag " + kind; }
  const shadowKind = (shadow) => shadow.state === "connected" ? "ok"
    : shadow.state === "polling" || shadow.state === "starting" ? "auto" : "warn";
  const lastContact = (shadow) => shadow.last_contact_local
    ? t("senast kontakt {time}", { time: shadow.last_contact_local }) : t("ingen kontakt ännu");

  // Drift: bara i skugga, och bara det som behövs under spelet. Detaljerna står i Inställningar.
  function renderDrift() {
    if (!drift) return;
    const shadow = data?.available && data.settings?.mode === "shadow" ? data.shadow : null;
    drift.hidden = !shadow;
    if (!shadow) return;
    const kind = shadowKind(shadow);
    setTag($("drift-clearance-status"), kind);
    drift.classList.toggle("warn", kind === "warn");
    $("drift-clearance-text").textContent = t(STATES[shadow.state] || STATES.starting);
    const parts = [];
    if (shadow.state === "no_contact") parts.push(lastContact(shadow));
    if (shadow.error) parts.push(errorText(shadow.error));
    // Utan kontakt är jämförelsen gammal; då står bara när kontakten bröts.
    const deviations = ["connected", "polling"].includes(shadow.state) ? shadow.deviations : null;
    // "Skillnader", inte "avvikelser": det ordet har Drift redan för tågen.
    if (deviations) parts.push(deviations.count ? t("skillnader mot Tellurian: {count}", { count: deviations.count }) : t("inga skillnader mot Tellurian"));
    if (shadow.simulated) parts.push(t("simulerad"));
    $("drift-clearance-note").textContent = parts.join(" · ");
  }

  function renderStatus() {
    const tag = $("tellurian-status"), text = $("tellurian-status-text"), note = $("tellurian-status-note");
    const disabled = !data.available;
    for (const id of ["tellurian-address", "tellurian-test-instance", "tellurian-mode", "tellurian-test"]) $(id).disabled = disabled || busy;
    if (!data.available) {
      setTag(tag, "off");
      text.textContent = t("Inte tillgänglig");
      note.textContent = t(data.reason === "us" ? "Tellurian Dispatch gäller bara EU-trafikspel." : "Koppla en träff först.");
      return;
    }
    const shadow = data.shadow;
    if (data.settings.mode !== "shadow" || !shadow) {
      setTag(tag, "off");
      text.textContent = t("Av");
      note.textContent = t("TrainMeet klarerar själv. Ingenting läses från Tellurian.");
      return;
    }
    setTag(tag, shadowKind(shadow));
    text.textContent = t(STATES[shadow.state] || STATES.starting);
    const parts = [];
    if (shadow.state === "no_contact") parts.push(lastContact(shadow));
    if (shadow.state === "polling") parts.push(t("händelseströmmen saknas"));
    if (shadow.error) parts.push(errorText(shadow.error));
    parts.push(t("stationer: {count} · tågsektioner: {sections}", { count: shadow.dispatchers, sections: shadow.sections }));
    if (shadow.simulated) parts.push(t("simulerad"));
    note.textContent = parts.join(" · ");
  }

  function item(list, label, value, kind = "ok") {
    const row = document.createElement("li");
    const head = document.createElement("span");
    head.className = "kr-tag " + kind;
    const dot = document.createElement("span"); dot.className = "kr-dot";
    head.append(dot, label);
    const body = document.createElement("span");
    body.textContent = value;
    row.append(head, body);
    list.append(row);
  }

  const codes = (rows, key = "code") => rows.map((row) => row[key]).filter(Boolean).join(", ");

  function renderReport() {
    const panel = $("tellurian-report"), list = $("tellurian-report-body"), report = data.report;
    panel.hidden = !report;
    if (!report) return;
    list.replaceChildren();
    $("tellurian-report-meta").textContent = t("{address} · kl. {time}", { address: report.address, time: report.checked_at_local });
    if (report.simulated) item(list, t("Källa"), t("Simulerad: TrainMeets testsimulator, inte Stefans server."), "warn");
    if (report.api.status === "failed") {
      item(list, t("API"), errorText(report.api.error), "warn");
      return;
    }
    item(list, t("API"), t("{version} svarar · {count} stationer i Tellurian", { version: report.api.version, count: report.api.dispatchers })
      + (report.api.error ? " · " + errorText(report.api.error) : ""), report.api.status === "passed" ? "ok" : "warn");
    const stations = report.stations;
    item(list, t("Stationer"), [t("{count} matchade", { count: stations.matched.length }),
      stations.unmatched_trainmeet.length ? t("saknas i Tellurian: {codes}", { codes: codes(stations.unmatched_trainmeet) }) : "",
      stations.unmatched_tellurian.length ? t("bara i Tellurian: {codes}", { codes: codes(stations.unmatched_tellurian, "signature") }) : ""]
      .filter(Boolean).join(" · "), stations.unmatched_trainmeet.length ? "warn" : "ok");
    const stretches = report.stretches;
    const passes = stretches.matched.reduce((sum, row) => sum + row.passes, 0);
    item(list, t("Sträckor"), [t("{count} matchade", { count: stretches.matched.length }),
      passes ? t("passager vid signalkontrollerade platser: {count}", { count: passes }) : "",
      stretches.unseen.length ? t("utan tåg i Tellurian just nu: {pairs}", { pairs: stretches.unseen.map((r) => r.from + "–" + r.to).join(", ") }) : "",
      stretches.unmatched_tellurian.length ? t("bara i Tellurian: {pairs}", { pairs: stretches.unmatched_tellurian.map((r) => r.from + "–" + r.to).join(", ") }) : ""]
      .filter(Boolean).join(" · "), stretches.unmapped.length || stretches.unmatched_tellurian.length ? "warn" : "ok");
    const trains = report.trains;
    item(list, t("Tåg"), [t("{count} matchade", { count: trains.matched.length }),
      trains.unmatched_trainmeet.length ? t("saknas i Tellurian: {numbers}", { numbers: codes(trains.unmatched_trainmeet, "number") }) : "",
      trains.ambiguous.length ? t("tvetydiga, inte matchade: {numbers}", { numbers: codes(trains.ambiguous, "number") }) : "",
      trains.unmatched_tellurian.length ? t("bara i Tellurian: {trains}", { trains: trains.unmatched_tellurian.join(", ") }) : ""]
      .filter(Boolean).join(" · "), trains.unmatched_trainmeet.length || trains.ambiguous.length ? "warn" : "ok");
    const clock = report.clock;
    item(list, t("Klocka"), clock.warning
      ? t("TrainMeet använder serverns egen klocka, inte FastClock. Välj samma FastClock som Tellurian under Skärmar och klocka.")
      : t("TrainMeet följer FastClock {name}. Kontrollera att Tellurian följer samma klocka.", { name: clock.name }),
    clock.warning ? "warn" : "ok");
    item(list, t("Trafikspel"), t(report.dispatch_mode.ok ? "EU-trafikspel i läget klarering." : "Trafikspelet är inte i läget klarering. Tellurian kan bara användas med klarering."),
      report.dispatch_mode.ok ? "ok" : "warn");
    if (report.unknown_actions?.length) item(list, t("Okända åtgärder"), t("Visas inte: {names}", { names: report.unknown_actions.join(", ") }), "warn");
  }

  function renderDeviations() {
    const panel = $("tellurian-deviations"), list = $("tellurian-deviations-list");
    const result = data.shadow?.deviations;
    panel.hidden = !result;
    if (!result) return;
    list.replaceChildren();
    $("tellurian-deviations-meta").textContent = result.count
      ? t("avvikelser just nu: {count}", { count: result.count }) : t("Inga avvikelser just nu");
    for (const row of result.items) {
      const label = `${row.tellurian_train || row.train} ${row.from}→${row.to}`;
      item(list, label, t("{kind}: TrainMeet {trainmeet} · Tellurian {tellurian}", {
        kind: t(DEVIATIONS[row.kind] || row.kind), trainmeet: stateText(row.trainmeet), tellurian: stateText(row.tellurian),
      }), "warn");
    }
    if (result.unmapped) item(list, t("Omatchade"), t("tågsektioner på stationer som inte matchar: {count}", { count: result.unmapped }), "off");
  }

  function render() {
    if (!data) return;
    fill();
    renderStatus();
    renderReport();
    renderDeviations();
    renderDrift();
  }

  let lastRefresh = 0, wasVisible = false, wasOnDrift = false;
  async function refresh() {
    const settingsShown = visible();
    if (loading || !(settingsShown || onDrift()) || document.body.dataset.signedIn !== "yes") return;
    loading = true;
    lastRefresh = Date.now();
    try { data = await request("/v1/clearance/tellurian"); render(); }
    catch (error) {
      if (settingsShown) message(error.message);
      else if (drift) drift.hidden = true; // Drift visar ingen gissning om klareringen.
    }
    finally { loading = false; }
  }

  const values = () => ({ base_url: $("tellurian-address").value.trim(), test_instance: $("tellurian-test-instance").checked });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !data) return;
    busy = true; form.dataset.busy = "true";
    try {
      data = await request("/v1/clearance/tellurian", { ...values(), mode: $("tellurian-mode").value, meet_id: data.meet_id });
      busy = false; delete form.dataset.busy; delete form.dataset.dirty;
      render();
      globalThis.TrainMeetSettings?.saved(form);
      message("Sparat.", "success");
    } catch (error) { message(error.message); }
    finally { busy = false; delete form.dataset.busy; globalThis.TrainMeetSettings?.refresh(form); }
  });

  $("tellurian-test").addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    const button = $("tellurian-test");
    button.disabled = true;
    message("Testar anslutningen …", "");
    try {
      const report = await request("/v1/clearance/tellurian/test", values());
      data = { ...(data || {}), report };
      renderReport();
      message(report.api.status === "failed" ? errorText(report.api.error) : "Rapporten är klar. Inget skrevs till Tellurian.",
        report.api.status === "failed" ? "error" : "success");
    } catch (error) { message(error.message); }
    finally { busy = false; button.disabled = !data?.available; }
  });

  addEventListener("hashchange", refresh);
  // Läs direkt när avsnittet visas, sedan var tredje sekund medan det är öppet.
  // På Drift var femte sekund i skugga (så att "ingen kontakt" syns i tid), annars
  // var trettionde, för att märka när en annan administratör slår på skuggan.
  setInterval(() => {
    const shown = visible(), driftShown = onDrift();
    const shadowing = data?.available && data.settings?.mode === "shadow";
    const age = Date.now() - lastRefresh;
    if (shown && (!wasVisible || age >= 3000)) refresh();
    else if (driftShown && (!wasOnDrift || age >= (shadowing ? 5000 : 30000))) refresh();
    wasVisible = shown;
    wasOnDrift = driftShown;
  }, 400);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", refresh); else refresh();
  globalThis.TrainMeetTellurian = { refresh };
})();
