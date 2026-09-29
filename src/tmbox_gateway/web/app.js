const { t, html } = globalThis.TrainMeetI18n;
const serverUI = globalThis.TrainMeetServerUI;
function editorActive(form) { return Boolean(form?.closest("dialog")?.open || form?.dataset.dirty === "true" || form?.dataset.busy === "true"); }

let simulationState = null;
let simulationConfirmation = null;
let simulationRefreshing = false;
let simulationStationSignature = null;
let simulationTrainSignature = null;
const simulationReasons = {
  channel_occupied: "Sträckan är upptagen", track_occupied: "Planerat spår är upptaget",
  simulation_train_not_ready: "Tåget är inte färdigt", simulation_station_control: "Stationen är manuellt bemannad",
};

function renderSimulation(data) {
  simulationState = data;
  document.querySelector("#drift-simulation-details").hidden = !data.active || state.serverContext?.operating_region === "us";
  const clock = data.clock || {};
  document.querySelector("#simulation-summary").textContent = data.active
    ? `${clock.running ? t("Går") : t("Pausad")} · ${clock.time} · ${clock.speed}× · ${t("Trafikdag")} ${data.day} · ${t("Scenario")} ${data.seed}${data.notice ? " · " + data.notice : ""}`
    : t(data.supported ? "Ingen simulering är aktiv. När du startar pausas det vanliga spelet och dess trafikläge sparas." : "Koppla en EU-träff från Cloud för att simulera stationsarbetet.");
  document.querySelector("#simulation-start-open").hidden = data.active;
  document.querySelector("#simulation-start-open").disabled = !data.supported;
  for (const id of ["simulation-pause", "simulation-reset-open", "simulation-finish-open", "simulation-stations-card", "simulation-trains-card"]) document.getElementById(id).hidden = !data.active;
  document.querySelector("#simulation-pause").textContent = t(clock.running ? "Pausa" : "Fortsätt");
  const stations = document.querySelector("#simulation-stations");
  const stationSignature = JSON.stringify([document.documentElement.lang, data.stations]);
  const names = new Map((data.stations || []).map(station => [station.id, station.code]));
  if (stationSignature !== simulationStationSignature) {
    simulationStationSignature = stationSignature;
    stations.replaceChildren();
    for (const station of data.stations || []) {
      const card = document.createElement("div"); card.className = "simulation-station";
      const title = document.createElement("strong"); title.textContent = `${station.code} · ${station.name}`;
      const mode = document.createElement("p"); mode.textContent = t({automatic: "Automatisk", manual: "Manuell", disconnected: "Kontakt saknas – väntar"}[station.mode]);
      card.append(title, mode);
      if (station.operator) {
        const operator = document.createElement("p"); operator.textContent = station.operator; card.append(operator);
        const button = document.createElement("button"); button.type = "button"; button.className = "secondary";
        button.textContent = t("Lämna till simulatorn");
        button.addEventListener("click", () => confirmSimulation("automatic", t("Lämna till simulatorn"), `${station.name}: ${t("automatiken fortsätter från nuvarande trafikläge. Den anslutna klienten kan inte längre styra stationen.")}`, station.id));
        card.append(button);
      }
      for (const device of station.available_operators || []) {
        if (station.operator === device) continue;
        const button = document.createElement("button"); button.type = "button"; button.className = "secondary";
        button.textContent = `${t("Låt klient ta över")}: ${device}`;
        button.addEventListener("click", () => confirmSimulation("manual", t("Lämna till operatör"), `${station.name}: ${device}`, station.id, device));
        card.append(button);
      }
      stations.append(card);
    }
  }
  const trainSignature = JSON.stringify([stationSignature, data.trains]);
  if (trainSignature === simulationTrainSignature) return;
  simulationTrainSignature = trainSignature;
  const trains = document.querySelector("#simulation-trains"); trains.replaceChildren();
  for (const train of data.trains || []) {
    const tr = document.createElement("tr");
    for (const value of [train.train_number, `${names.get(train.from_station_id)} → ${names.get(train.to_station_id)}`,
      t({waiting: "Väntar", in_transit: "På väg", arrived: "Ankommet", stabled: "Uppställt"}[train.status]),
      t(simulationReasons[train.reason] || train.reason) || (train.delay_seconds ? `${train.delay_seconds / 60} ${t("min extra stationsarbete")}` : "–")]) {
      const td = document.createElement("td"); td.textContent = value; tr.append(td);
    }
    trains.append(tr);
  }
}

async function refreshSimulation() {
  if (simulationRefreshing || !["simulation", "kor"].includes(document.body.dataset.mode) || state.serverContext?.operating_region === "us") return;
  simulationRefreshing = true;
  try {
    const response = await authorizedFetch("/v1/simulation", {cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || t("Kunde inte läsa simuleringen"));
    renderSimulation(data);
  } catch (error) { setMessage(document.querySelector("#simulation-error"), error.message, "error"); }
  finally { simulationRefreshing = false; }
}

async function sendSimulation(action, options = {}, context = simulationState) {
  const response = await authorizedFetch("/v1/simulation", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action, run_id: context?.run_id, meet_generation: context?.meet_generation, ...options})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.message || t("Simuleringen kunde inte ändras"));
  renderSimulation(data);
  // Run transitions fence old requests. Refresh the shell's command context too.
  const server = await authorizedFetch("/v1/server-context", {cache: "no-store"});
  if (server.ok) state.serverContext = await server.json();
  await refreshLocalClock();
}

function confirmSimulation(action, title, description, station_id, device_id) {
  simulationConfirmation = {action, station_id, device_id, context: {...simulationState}};
  document.querySelector("#simulation-confirm-title").textContent = title;
  document.querySelector("#simulation-confirm-description").textContent = description;
  openModal("simulation-confirm-modal");
}

function bindSimulationUI() {
  document.querySelector("#simulation-start-open").addEventListener("click", () => {
    const clock = simulationState?.clock || {};
    document.querySelector("#simulation-time").value = String(clock.time || "12:00").slice(0, 5);
    document.querySelector("#simulation-speed").value = clock.speed || 1;
    document.querySelector("#simulation-day").textContent = `${t("Vald trafikdag")}: ${simulationState?.active_day || "–"}`;
    simulationConfirmation = {context: {...simulationState}};
    openModal("simulation-start-modal");
  });
  document.querySelector("#simulation-pause").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try { await sendSimulation(simulationState.clock.running ? "pause" : "resume"); }
    catch (error) { setMessage(document.querySelector("#simulation-error"), error.message, "error"); }
    finally { button.disabled = false; }
  });
  document.querySelector("#simulation-reset-open").addEventListener("click", () => confirmSimulation("reset", t("Återställ vid aktuell tid"),
    t("Simuleringen pausas och får ett nytt läge enligt tidtabellen vid klockans aktuella tid när du bekräftar. Gamla störningar och förfrågningar ersätts. Stationstilldelningarna behålls. Vanlig drift påverkas inte.")));
  document.querySelector("#simulation-finish-open").addEventListener("click", () => confirmSimulation("finish", t("Avsluta simulering"),
    t("Alla klienter återgår till det sparade vanliga spelet med pausad klocka. Anslutningar och stationstilldelningar behålls. Simuleringens data sparas separat.")));
  for (const id of ["simulation-start-form", "simulation-confirm-form"]) {
    document.getElementById(id).addEventListener("submit", async event => {
      event.preventDefault();
      const form = event.target;
      if (!beginModalAction(form)) return;
      try {
        if (id === "simulation-start-form") await sendSimulation("start", {
          confirmed: true,
          time: document.querySelector("#simulation-time").value, speed: Number(document.querySelector("#simulation-speed").value),
          profile: document.querySelector("#simulation-profile").value, seed: document.querySelector("#simulation-seed").value,
          stabling_minutes: Number(document.querySelector("#simulation-stabling").value),
        }, simulationConfirmation.context);
        else await sendSimulation(simulationConfirmation.action, {confirmed: true, station_id: simulationConfirmation.station_id, device_id: simulationConfirmation.device_id}, simulationConfirmation.context);
        finishModal(form, t("Simuleringen uppdaterades."));
      } catch (error) { setMessage(form.querySelector(".form-message"), error.message, "error"); }
      finally { endModalAction(form); }
    });
  }
  setInterval(refreshSimulation, 2000);
}

function createWebClientID() {
  const browserCrypto = globalThis.crypto;
  if (typeof browserCrypto?.randomUUID === "function") {
    return `web-${browserCrypto.randomUUID()}`;
  }
  if (typeof browserCrypto?.getRandomValues === "function") {
    const bytes = browserCrypto.getRandomValues(new Uint8Array(12));
    return `web-${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
  }
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}

// Three keys used to carry a tambox. prefix while the rest already used
// trainmeet. Move them once so an open browser keeps its session instead of
// being logged out by the rename.
for (const key of ["accessToken", "clientID", "panelID"]) {
  const legacy = localStorage.getItem(`tambox.${key}`);
  if (legacy !== null) {
    if (localStorage.getItem(`trainmeet.${key}`) === null) {
      localStorage.setItem(`trainmeet.${key}`, legacy);
    }
    localStorage.removeItem(`tambox.${key}`);
  }
}

//: Tangenterna i den fysiska lådans 4×4-matris. Används av v2-simulatorn.
const keypadKeys = ["1", "2", "3", "A", "4", "5", "6", "B", "7", "8", "9", "C", "*", "0", "#", "D"];

const state = {
  token: localStorage.getItem("trainmeet.accessToken"),
  clientID: localStorage.getItem("trainmeet.clientID") || createWebClientID(),
  selectedView: localStorage.getItem("trainmeet.view") === "server"
    ? "overview"
    : (localStorage.getItem("trainmeet.view") || "overview"),
  snapshots: new Map(),
  selectedPanelID: localStorage.getItem("trainmeet.panelID"),
  snapshotTimer: null,
  adminTimer: null,
  sending: false,
  serverContext: null,
  restartRequired: false,
  restarting: false,
  authStatus: null,
  pendingPublicationID: null,
  overviewSnapshot: null,
  selectedTrainNumber: null,
  selectedStationID: null,
  hoveredOverviewTrainNumber: null,
  overviewDataSignature: null,
  overviewSelectionInitialized: false,
  displaySelectedTrainNumber: null,
  displaySelectedStationID: null,
  displayHoveredTrainNumber: null,
  setupStatus: null,
  pendingImportPackage: null,
  pendingImportValidation: null,
  runtimeLinkInitialized: false,
};

const setup = document.querySelector("#setup");
const login = document.querySelector("#login");
const appView = document.querySelector("#app-view");
const loginForm = document.querySelector("#login-form");
const loginError = document.querySelector("#login-error");
const setupAdminForm = document.querySelector("#setup-admin-form");
const setupServerForm = document.querySelector("#setup-server-form");
// Six single-digit boxes standing in for one text field, kept in sync with a
// hidden input so the rest of the app can go on reading/clearing `.value`
// exactly as it did with a plain input. Typing advances focus, backspace on
// an empty box steps back, and pasting anywhere in the group (with or
// without the "123-456" dash) spreads the digits across all six boxes.
function wireCodeBoxes(containerSelector, hiddenInputSelector) {
  const container = document.querySelector(containerSelector);
  const hidden = document.querySelector(hiddenInputSelector);
  if (!container || !hidden) return { reset() {} };
  const boxes = [...container.querySelectorAll("input")];

  const sync = () => {
    hidden.value = boxes.map((box) => box.value).join("");
  };

  const fillFrom = (raw, startIndex) => {
    const digits = raw.replace(/\D/g, "").slice(0, boxes.length - startIndex);
    for (let offset = 0; offset < digits.length; offset += 1) {
      boxes[startIndex + offset].value = digits[offset];
    }
    sync();
    const lastFilled = Math.min(startIndex + digits.length, boxes.length - 1);
    boxes[lastFilled].focus();
    boxes[lastFilled].select();
  };

  boxes.forEach((box, index) => {
    box.addEventListener("input", () => {
      const digits = box.value.replace(/\D/g, "");
      if (digits.length > 1) {
        fillFrom(digits, index);
        return;
      }
      box.value = digits;
      sync();
      if (digits && index < boxes.length - 1) boxes[index + 1].focus();
    });
    box.addEventListener("keydown", (event) => {
      if (event.key === "Backspace" && !box.value && index > 0) {
        event.preventDefault();
        boxes[index - 1].value = "";
        boxes[index - 1].focus();
        sync();
      } else if (event.key === "ArrowLeft" && index > 0) {
        boxes[index - 1].focus();
      } else if (event.key === "ArrowRight" && index < boxes.length - 1) {
        boxes[index + 1].focus();
      }
    });
    box.addEventListener("paste", (event) => {
      event.preventDefault();
      fillFrom((event.clipboardData || window.clipboardData).getData("text"), 0);
    });
    box.addEventListener("focus", () => box.select());
  });

  return {
    reset() {
      boxes.forEach((box) => { box.value = ""; });
      hidden.value = "";
    },
  };
}

const setupSyncCodeBoxes = wireCodeBoxes("#setup-sync-code-boxes", "#setup-sync-code");
const runtimeSyncCodeBoxes = wireCodeBoxes("#runtime-sync-code-boxes", "#runtime-sync-code");

const setupCentralForm = document.querySelector("#setup-central-form");
const setupFinishForm = document.querySelector("#setup-finish-form");
const connectionStatus = document.querySelector("#connection");
const deviceForm = document.querySelector("#device-form");
const deviceMessage = document.querySelector("#device-message");
const deviceStation = document.querySelector("#device-station");
const runtimeForm = document.querySelector("#runtime-sync-form");
const runtimeMessage = document.querySelector("#runtime-message");
const runtimeCheckUpdate = document.querySelector("#runtime-check-update");
// Omstartsknappen står på två ställen och betyder samma sak på båda: en i
// stationsplanens knapprad, där en aktivering just har begärt omstart, och en
// i BYGG 5 där paketet placerar den. De delar tillstånd i stället för att
// hålla var sin sanning om huruvida en omstart behövs.
const restartButtons = ["#restart-server", "#software-restart"]
  .map((selector) => document.querySelector(selector))
  .filter(Boolean);
const logoutButton = document.querySelector("#logout");
const adminAccessForm = document.querySelector("#admin-access-form");
const adminAccessMessage = document.querySelector("#admin-access-message");
const serverIdentityForm = document.querySelector("#server-identity-form");
const serverIdentityMessage = document.querySelector("#server-identity-message");
const clockControlForm = document.querySelector("#clock-control-form");
const clockControlMessage = document.querySelector("#clock-control-message");
const softwareCheck = document.querySelector("#software-check");
const softwareInstall = document.querySelector("#software-install");
const softwareUpdateMessage = document.querySelector("#software-update-message");
const softwareVersion = document.querySelector("#software-version");
const factoryResetConfirmation = document.querySelector("#factory-reset-confirmation");
const factoryResetButton = document.querySelector("#factory-reset-server");
const factoryResetMessage = document.querySelector("#factory-reset-message");
const overviewRouteSearch = document.querySelector("#overview-route-search");
const overviewRouteList = document.querySelector("#overview-route-list");
const overviewRouteDetail = document.querySelector("#overview-route-detail");
const overviewStationCounts = document.querySelector("#overview-station-counts");
const closeStationInspector = document.querySelector("#close-station-inspector");

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(loginError, "");
  const button = loginForm.querySelector("button");
  button.disabled = true;
  try {
    const response = await fetch("/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.querySelector("#login-username").value,
        password: document.querySelector("#login-password").value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || t("Inloggningen misslyckades"));
    document.querySelector("#login-password").value = "";
    // Continue to the protected workspace selected before signing in.
    if (!location.hash || location.hash === "#workspaces") {
      sessionStorage.setItem("trainmeet.workspace", "administration");
      history.replaceState(null, "", location.pathname + "#overview");
    }
    await refreshAuthStatus();
    const installation = await refreshSetupStatus();
    if (installation.required) {
      showSetup(installation);
    } else {
      await openApplication();
    }
  } catch (error) {
    setMessage(loginError, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

// Inlösning av inbjudan. Den som har en kod har ännu inget lösenord och kan
// alltså inte logga in för att sätta det - därför går det här utan session.
const redeemForm = document.querySelector("#redeem-form");

function showRedeem(open) {
  redeemForm?.classList.toggle("hidden", !open);
  loginForm.classList.toggle("hidden", open);
  document.querySelector("#login-intro")?.classList.toggle("hidden", open);
  document.querySelector("#redeem-intro")?.classList.toggle("hidden", !open);
  document.querySelector("#redeem-open")?.classList.toggle("hidden", open);
  if (open) document.querySelector("#redeem-username").focus();
}

document.querySelector("#redeem-open")?.addEventListener("click", () => showRedeem(true));
document.querySelector("#redeem-cancel")?.addEventListener("click", () => showRedeem(false));

redeemForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#redeem-message");
  setMessage(message, "");
  const button = redeemForm.querySelector("button.primary");
  button.disabled = true;
  try {
    const response = await fetch("/v1/admin/users/redeem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.querySelector("#redeem-username").value,
        code: document.querySelector("#redeem-code").value,
        password: document.querySelector("#redeem-password").value,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || "Koden gick inte att lösa in");
    document.querySelector("#redeem-password").value = "";
    document.querySelector("#redeem-code").value = "";
    showRedeem(false);
    // The username field is left alone here too: the browser's own password
    // manager may offer the account, and that is the user's choice.
    document.querySelector("#login-username").focus();
    setMessage(loginError, "Lösenordet är satt. Logga in.", "success");
  } catch (error) {
    setMessage(message, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

setupAdminForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#setup-admin-message");
  setMessage(message, "");
  const password = document.querySelector("#setup-password").value;
  if (password !== document.querySelector("#setup-password-confirm").value) {
    setMessage(message, "Lösenorden är inte likadana.", "error");
    return;
  }
  const button = setupAdminForm.querySelector("button");
  button.disabled = true;
  try {
    const response = await fetch("/v1/setup/admin", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.querySelector("#setup-username").value,
        password,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Administratören kunde inte skapas");
    document.querySelector("#setup-password").value = "";
    document.querySelector("#setup-password-confirm").value = "";
    await refreshAuthStatus();
    showSetup(await refreshSetupStatus());
  } catch (error) {
    setMessage(message, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

setupServerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#setup-server-message");
  const button = setupServerForm.querySelector("button");
  button.disabled = true;
  try {
    const response = await authorizedFetch("/v1/setup/server", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server_name: document.querySelector("#setup-server-name").value }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Servernamnet kunde inte sparas");
    showSetup(await refreshSetupStatus());
  } catch (error) {
    setMessage(message, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

setupCentralForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#setup-central-message");
  const button = setupCentralForm.querySelector("button");
  const syncCode = document.querySelector("#setup-sync-code").value;
  if (syncCode.length !== 6) {
    setMessage(message, "Fyll i alla sex siffror i träffkoden.", "error");
    return;
  }
  setMessage(message, "Hämtar träffen …", "notice");
  button.disabled = true;
  try {
    const response = await authorizedFetch("/v1/runtime/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        central_url: document.querySelector("#setup-central-url").value,
        sync_code: syncCode,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Träffen kunde inte hämtas");
    setupSyncCodeBoxes.reset();
    showSetup(await refreshSetupStatus());
  } catch (error) {
    setMessage(message, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

setupFinishForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = document.querySelector("#setup-finish-message");
  const button = setupFinishForm.querySelector("button");
  button.disabled = true;
  try {
    const response = await authorizedFetch("/v1/setup/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active_day: document.querySelector("#setup-active-day").value }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Installationen kunde inte slutföras");
    setMessage(message, payload.message, "success");
    state.restartRequired = true;
    state.restarting = true;
    setConnection("waiting", "Startar om");
    const restart = await authorizedFetch("/v1/server/restart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!restart.ok) {
      const restartPayload = await restart.json();
      throw new Error(restartPayload.message || "Servern kunde inte startas om");
    }
    await waitForServerReturn(message);
  } catch (error) {
    state.restarting = false;
    setMessage(message, error.message, "error");
    button.disabled = false;
  }
});


// Server workspaces select an interface, never a different meet or engine.
const SETTINGS_SECTIONS = ["language", "meet", "identity", "users", "software", "cloud", "system"];
const WORKSPACE_PANELS = {
  kor: "#overview-view", installningar: "#admin-view",
  simulation: "#simulation-view",
  skarmar: "#displays-view", tmbox: "#tmbox-v2-view",
  help: "#help-view",
};
const MODES = ["workspaces", ...Object.keys(WORKSPACE_PANELS)];
const WORKSPACE_KEY = "trainmeet.workspace";
// "/" is the participant view (SPEC A4): no picker, no stored choice. The
// names below still fence what a browser client may enroll as; TKL has its
// own platform and is no longer offered by the server.
const WORKSPACES = {
  administration: { title: "Drift och administration", detail: "Trafikläge, klocka och serverinställningar", path: "/drift" },
  tmbox: { title: "TMBox", detail: "Starta klienten – administratören tilldelar station", path: "/tmbox/" },
  dispatcher: { title: "Dispatcher", detail: "Trafikledning för träffens territorier", path: "/us/dispatcher" },
  conductor: { title: "Conductor", detail: "Tåguppdrag och körtillstånd", path: "/us/conductor" },
};

function currentMode() { return document.body.dataset.mode || "workspaces"; }
function storedMode() { sessionStorage.removeItem(WORKSPACE_KEY); return "workspaces"; }

// "/" is for guests. Signed in, home is Drift: the logo, the screens' back link
// and "/" itself all lead there, so an administrator never lands on the
// participant view by accident. Signing out leads back to it.
function workspaceHome() { return state.authStatus?.authenticated ? "/drift" : "/"; }

function availableWorkspaces() {
  // Fail closed: a stale browser choice must not grant a role or select EU/US.
  return (state.serverContext?.available_workspaces || []).filter((key) => WORKSPACES[key]);
}

async function refreshServerContext() {
  const response = await authorizedFetch(state.authStatus?.authenticated ? "/v1/server-context" : "/v1/workspaces", { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "Serverns träff kunde inte läsas.");
  state.serverContext = payload;
  serverUI.context = payload;
  const warning = document.querySelector("#server-context-warning");
  setMessage(warning, payload.error?.message || (typeof payload.error === "string" ? payload.error : "") || (payload.transition_pending ? "Byte av träff pågår. Trafikkommandon är tillfälligt spärrade." : ""), "error");
  warning.classList.toggle("hidden", !warning.textContent);
  const meet = payload.selected_meet;
  const name = meet?.name || t("Ingen träff vald");
  document.querySelector("#app-meet-name").textContent = name;
  const selected = sessionStorage.getItem(WORKSPACE_KEY);
  if (selected && !availableWorkspaces().includes(selected)) sessionStorage.removeItem(WORKSPACE_KEY);
  globalThis.TrainMeetParticipant?.refresh();
  renderCloudStatus();
  const us = payload.operating_region === "us";
  document.querySelector("#us-runtime-summary").classList.toggle("hidden", !us);
  document.querySelectorAll('.display-launch-card[href="/display/topology"], .display-launch-card[href="/display/graph"], .display-launch-card[href="/display/dashboard"], .connection-badge-card')
    .forEach((node) => node.classList.toggle("hidden", us));
  document.querySelectorAll("#overview-view .topology-overview-card, #overview-traffic, #overview-timetable")
    .forEach((node) => node.classList.toggle("hidden", us));
  document.querySelector("#workspace-home").href = workspaceHome();
  serverUI.refreshHeader();
  return payload;
}

globalThis.TrainMeetI18n.subscribe(() => {
  globalThis.TrainMeetParticipant?.refresh();
  renderCloudStatus();
  renderUsers();
  renderCloudPresentation();
  configureResetMode();
});

function setMode(mode) {
  const next = MODES.includes(mode) ? mode : "workspaces";
  document.body.dataset.mode = next;
  document.querySelector("#workspace-home").href = workspaceHome();
  document.querySelector(".server-admin-shell").classList.toggle("hidden", next === "workspaces");
  if (next === "workspaces") globalThis.TrainMeetParticipant?.start(); else globalThis.TrainMeetParticipant?.stop();
  for (const [name, selector] of Object.entries(WORKSPACE_PANELS)) {
    document.querySelector(selector).classList.toggle("hidden", name !== next);
  }
  document.querySelector("#application-menu").open = false;
  document.querySelector("#settings-heading").classList.toggle("hidden", next !== "installningar");
  if (next !== "tmbox") stopTMBoxV2();
  if (next === "tmbox") {
    startTMBoxV2();
  } else if (next === "installningar") showSettings();
  else if (next === "simulation") refreshSimulation();
  else if (next === "kor" && state.serverContext?.operating_region === "eu") renderOverview(state.overviewSnapshot);
  if (next === "kor") refreshSimulation();
  if (next === "help") { buildTMBoxGuide(); buildScreenCatalog(); buildFlowList(); }
  serverUI.mode(next);
  window.scrollTo({ top: 0, behavior: "auto" });
}

function showSettings() {
  document.querySelectorAll(".admin-section-panel").forEach((panel) => {
    panel.classList.toggle("hidden", !SETTINGS_SECTIONS.includes(panel.dataset.adminSection));
  });
  Promise.allSettled([checkSoftwareUpdate(), refreshUsers(), refreshBackups(), refreshRuntime(), refreshDevices()]);
}

function applyWorkspaceRoute() {
  let route = location.hash.slice(1);
  const legacy = {overview: "/drift", traffic: "/drift", settings: "/installningar", simulation: "/drift#drift-simulation", screens: "/installningar#skarmar"};
  if (legacy[route]) { history.replaceState(null, "", legacy[route]); route = location.hash.slice(1); }
  if (route === "workspaces") { history.replaceState(null, "", "/"); route = ""; }
  const path = location.pathname.replace(/\/$/, "") || "/";
  if (state.authStatus?.authenticated && path === "/" && !route) {
    history.replaceState(null, "", "/drift");
    applyWorkspaceRoute();
    return;
  }
  const protectedMode = {"/drift": "kor", "/installningar": "installningar", "/hjalp": "help", "/login": "kor"}[path];
  if (protectedMode) {
    if (!state.authStatus?.authenticated) { showLogin(); return; }
    setup.classList.add("hidden"); login.classList.add("hidden"); appView.classList.remove("hidden");
    if (path === "/drift" || path === "/login") sessionStorage.setItem(WORKSPACE_KEY, "administration");
    setMode(protectedMode);
    return;
  }
  if (!state.authStatus?.authenticated && !["", "workspaces", "tmbox"].includes(route)) {
    showLogin();
    return;
  }
  setup.classList.add("hidden");
  login.classList.add("hidden");
  appView.classList.remove("hidden");
  document.querySelector("#application-menu").open = false;
  if (route === "settings") setMode("installningar");
  else if (route === "simulation") setMode("simulation");
  else if (route === "screens") setMode("skarmar");
  else if (route === "tmbox") {
    if (!availableWorkspaces().includes("tmbox")) { setMode("workspaces"); return; }
    location.replace("/tmbox/");
  }
  else if (route === "traffic" && availableWorkspaces().includes("administration")) {
    // Deprecated separate Traffic route: keep bookmarks, not the old view.
    sessionStorage.setItem(WORKSPACE_KEY, "administration");
    history.replaceState(null, "", "/#overview");
    setMode("kor");
    if (state.serverContext?.operating_region === "eu") document.querySelector("#overview-traffic").scrollIntoView();
  }
  else setMode("workspaces");
}

document.body.dataset.mode = storedMode();
window.addEventListener("hashchange", applyWorkspaceRoute);
window.addEventListener("popstate", applyWorkspaceRoute);
document.querySelector("#workspace-home").addEventListener("click", (event) => {
  event.preventDefault();
  history.pushState(null, "", workspaceHome());
  applyWorkspaceRoute();
});
bindUsersSection();
bindRestore();

// Native dialog supplies focus trapping; every editor shares cancellation,
// dirty-state protection and focus restoration. Background refresh never
// rewrites fields in an open editor.
const modalOrigins = new WeakMap();
const modalSections = new WeakMap();
const modalValues = new WeakMap();
const modalControls = new WeakMap();
function modalChanged(dialog) {
  return (modalValues.get(dialog) || []).some(([input, value, checked]) => input.value !== value || input.checked !== checked);
}
function beginModalAction(element) {
  const dialog = element?.closest("dialog") || element?.closest("form");
  if (!dialog) return true;
  if (dialog.dataset.busy === "true") return false;
  dialog.dataset.busy = "true";
  dialog.setAttribute("aria-busy", "true");
  const controls = [...dialog.querySelectorAll("button, input, select, textarea")].map(control => [control, control.disabled]);
  modalControls.set(dialog, controls);
  controls.forEach(([control]) => { control.disabled = true; });
  return true;
}
function endModalAction(element) {
  const dialog = element?.closest("dialog") || element?.closest("form");
  if (!dialog) return;
  (modalControls.get(dialog) || []).forEach(([control, disabled]) => { control.disabled = disabled; });
  modalControls.delete(dialog);
  dialog.dataset.busy = "false";
  dialog.removeAttribute("aria-busy");
}
function openModal(id, trigger = document.activeElement) {
  const dialog = document.getElementById(id);
  if (!dialog || dialog.open || document.querySelector("dialog[open]")) return;
  if (id === "device-form-modal" && trigger?.dataset.openModal === id) {
    // Manual code entry is only for explicitly restoring a removed client.
    document.querySelector("#device-code").readOnly = false;
    document.querySelector("#device-code").value = "";
    deviceStation.value = "";
    document.querySelector("#device-form-title").textContent = t("Återanslut borttagen klient");
    setMessage(deviceMessage, "");
  }
  modalOrigins.set(dialog, trigger);
  if (id === "clock-source-modal") dialog.dataset.meetGeneration = String(state.serverContext?.selected_meet?.generation ?? "");
  modalSections.set(dialog, trigger?.closest("section"));
  modalValues.set(dialog, [...dialog.querySelectorAll("input, select, textarea")].map((input) => [input, input.value, input.checked]));
  dialog.dataset.dirty = "false";
  dialog.querySelectorAll(".form-message").forEach((message) => setMessage(message, ""));
  document.body.append(dialog);
  dialog.showModal();
  const initial = dialog.querySelector('input:not([type="hidden"]):not(:disabled):not([readonly]), select:not(:disabled), textarea:not(:disabled):not([readonly])')
    || dialog.querySelector('.modal-actions [data-close-modal]');
  initial?.focus({ preventScroll: true });
}
function cancelModal(dialog) {
  if (dialog.dataset.busy === "true") return;
  if (modalChanged(dialog) && !window.confirm(t("Stäng utan att spara ändringarna?"))) return;
  for (const [input, value, checked] of modalValues.get(dialog) || []) { input.value = value; input.checked = checked; }
  // Restore derived validation too, without triggering the code fields' input
  // handlers (which move keyboard focus as digits are entered).
  for (const [input] of modalValues.get(dialog) || []) input.dispatchEvent(new Event("change", { bubbles: true }));
  if (dialog.id === "restore-modal") {
    restore.chosen = dialog.querySelector('input[name="restore-backup"]:checked')?.value || null;
    updateRestoreButton();
  }
  if (dialog.id === "reset-modal") factoryResetButton.disabled = factoryResetConfirmation.value.trim().toUpperCase() !== "NOLLSTÄLL";
  dialog.close();
}
let modalResultTimer;
function finishModal(form, confirmation = null) {
  delete form.dataset.dirty;
  delete form.dataset.meetGeneration;
  const dialog = form.closest("dialog");
  if (dialog) {
    let receipt = document.querySelector("#modal-result");
    if (!receipt) {
      receipt = document.createElement("p");
      receipt.id = "modal-result";
      receipt.setAttribute("role", "status");
      document.body.append(receipt);
    }
    receipt.hidden = false;
    receipt.textContent = t(confirmation || form.querySelector(".form-message.success")?.textContent || "Sparat.");
    clearTimeout(modalResultTimer);
    modalResultTimer = setTimeout(() => { receipt.hidden = true; }, 8000);
    dialog.dataset.dirty = "false";
    dialog.close();
  }
}
function bindAdminModals() {
  document.querySelectorAll("dialog.admin-modal").forEach((dialog) => {
    const close = document.createElement("button");
    close.type = "button";
    close.className = "modal-close";
    close.dataset.closeModal = "";
    close.dataset.tmAriaLabel = "Stäng";
    close.setAttribute("aria-label", t("Stäng"));
    close.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';
    dialog.prepend(close);
    const dirty = () => { dialog.dataset.dirty = String(modalChanged(dialog)); };
    dialog.addEventListener("input", dirty);
    dialog.addEventListener("change", dirty);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); cancelModal(dialog); });
    dialog.addEventListener("close", () => {
      endModalAction(dialog);
      dialog.querySelectorAll('input[type="password"]').forEach(input => { input.value = ""; });
      const origin = modalOrigins.get(dialog);
      const fallback = document.querySelector(`[data-open-modal="${dialog.id}"]`)
        || modalSections.get(dialog)?.querySelector('button:not(:disabled)')
        || document.querySelector('#admin-view:not(.hidden) button:not(:disabled)')
        || document.querySelector('#workspace-home');
      if (origin?.isConnected) origin.focus({ preventScroll: true });
      else fallback?.focus({ preventScroll: true });
      modalOrigins.delete(dialog);
      modalSections.delete(dialog);
      modalValues.delete(dialog);
    });
    dialog.querySelectorAll("[data-close-modal]").forEach((button) => button.addEventListener("click", () => cancelModal(dialog)));
  });
  document.querySelectorAll("[data-open-modal]").forEach((button) => button.addEventListener("click", () => openModal(button.dataset.openModal, button)));
}
bindAdminModals();
bindSimulationUI();
document.querySelector("#overview-clock-start").addEventListener("click", () => controlLocalClock({ action: "start" }));
document.querySelector("#overview-clock-stop").addEventListener("click", () => controlLocalClock({ action: "stop" }));

overviewRouteSearch.addEventListener("input", renderRouteExplorer);
overviewRouteList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-train-number]");
  if (!button) {
    clearOverviewSelection();
    return;
  }
  selectOverviewTrain(button.dataset.trainNumber);
});

overviewRouteDetail.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-station-id]");
  if (button) selectOverviewStation(button.dataset.stationId, true);
});

overviewStationCounts.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-station-id]");
  if (button) selectOverviewStation(button.dataset.stationId, false);
});

closeStationInspector.addEventListener("click", () => selectOverviewStation(null, true));


logoutButton.addEventListener("click", async () => {
  await fetch("/v1/auth/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  localStorage.removeItem("trainmeet.accessToken");
  localStorage.removeItem("trainmeet.panelID");
  sessionStorage.removeItem(WORKSPACE_KEY);
  history.replaceState(null, "", "/");
  state.token = null;
  state.snapshots.clear();
  clearTimeout(state.snapshotTimer);
  clearTimeout(state.adminTimer);
  setConnection("offline", t("Ej ansluten"));
  appView.classList.add("hidden");
  await bootstrap();
});

adminAccessForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(adminAccessMessage, "");
  const password = document.querySelector("#admin-password").value;
  const confirmation = document.querySelector("#admin-password-confirm").value;
  if (password !== confirmation) {
    setMessage(adminAccessMessage, "Lösenorden är inte likadana.", "error");
    return;
  }
  if (!beginModalAction(adminAccessForm)) return;
  try {
    const response = await authorizedFetch("/v1/admin/access", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.querySelector("#admin-username").value,
        password,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Inloggningen kunde inte sparas");
    document.querySelector("#admin-password").value = "";
    document.querySelector("#admin-password-confirm").value = "";
    setMessage(
      adminAccessMessage,
      payload.password_configured
        ? "Extern admininloggning är klar."
        : "Användarnamnet är sparat. Välj även ett lösenord för extern åtkomst.",
      payload.password_configured ? "success" : "notice",
    );
    await refreshAuthStatus();
    logoutButton.classList.toggle("hidden", !state.authStatus?.authenticated);
    await refreshAdminAccess();
    finishModal(adminAccessForm);
  } catch (error) {
    setMessage(adminAccessMessage, error.message, "error");
  } finally {
    endModalAction(adminAccessForm);
  }
});

serverIdentityForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(serverIdentityMessage, "");
  if (!beginModalAction(serverIdentityForm)) return;
  try {
    const response = await authorizedFetch("/v1/setup/server", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server_name: document.querySelector("#admin-server-name").value }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Servernamnet kunde inte sparas");
    setMessage(serverIdentityMessage, `Servernamnet är nu ${payload.server_name}.`, "success");
    await refreshInfo();
    finishModal(serverIdentityForm);
  } catch (error) {
    setMessage(serverIdentityMessage, error.message, "error");
  } finally {
    endModalAction(serverIdentityForm);
  }
});

// ⚙ › Skärmar och klocka: each part saves only its own fields.
document.querySelector("#connection-badge-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const screens = [...document.querySelectorAll("#connection-badge-screens input[type=checkbox]")]
    .filter((input) => input.checked).map((input) => input.value);
  saveConnectionPart(event.currentTarget, "#connection-badge-message", { screens },
    () => screens.length ? "Sparat. QR-koderna visas på {n} skärmar." : "Sparat. Ingen skärm visar QR-koderna.", { n: screens.length });
});
document.querySelector("#connection-code-form").addEventListener("submit", (event) => {
  event.preventDefault();
  saveConnectionPart(event.currentTarget, "#connection-code-message", {
    validity_hours: Number(document.querySelector("#connection-badge-validity").value),
    web_client_ttl_minutes: Number(document.querySelector("#web-client-ttl").value),
  }, (payload) => payload.restart_required ? "Sparat. Starta om servern för att ge koden den nya giltighetstiden." : "Sparat.");
});
document.querySelector("#connection-wifi-form").addEventListener("submit", (event) => {
  event.preventDefault();
  saveConnectionPart(event.currentTarget, "#connection-wifi-message", {
    wifi_name: document.querySelector("#connection-wifi-name").value,
    wifi_password: document.querySelector("#connection-wifi-password").value,
  }, () => "Sparat.");
});

// The field offers whole numbers in a dropdown but accepts anything typed, so
// a Swedish decimal comma has to read as a decimal point.
function clockSpeedValue() {
  const raw = document.querySelector("#local-clock-speed").value.trim().replace(",", ".");
  const speed = Number(raw);
  return Number.isFinite(speed) && speed > 0 ? speed : null;
}

clockControlForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const speed = clockSpeedValue();
  if (speed === null) {
    setMessage(clockControlMessage, "Ange en hastighet större än noll, till exempel 4,3.", "error");
    return;
  }
  await controlLocalClock({
    action: "set",
    time: document.querySelector("#local-clock-time").value,
    speed,
    meet_generation: clockControlForm.dataset.meetGeneration ? Number(clockControlForm.dataset.meetGeneration) : state.serverContext?.selected_meet?.generation,
  });
});

document.querySelector("#stop-local-clock").addEventListener("click", async () => {
  await controlLocalClock({
    action: "stop",
    reason: document.querySelector("#local-clock-reason").value.trim(),
  });
});

function renderClockSourceFields() {
  const fields = document.querySelector("#fastclock-fields");
  fields.hidden = fields.disabled = document.querySelector("#clock-source").value !== "fastclock";
}
document.querySelector("#clock-source").addEventListener("change", renderClockSourceFields);
document.querySelector("#clock-source-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.querySelector("#clock-source-message");
  if (!beginModalAction(form)) return;
  setMessage(message, t("Kontrollerar klockans anslutning …"), "notice");
  const source = document.querySelector("#clock-source").value;
  const data = { source, meet_generation: Number(form.closest("dialog").dataset.meetGeneration) };
  if (source === "fastclock") {
    Object.assign(data, { clock_name: document.querySelector("#fastclock-name").value.trim(),
      user: document.querySelector("#fastclock-user").value.trim(),
      poll_interval: Number(document.querySelector("#fastclock-interval").value) });
    const password = document.querySelector("#fastclock-password").value;
    if (password || document.querySelector("#fastclock-clear-password").checked) data.password = document.querySelector("#fastclock-clear-password").checked ? "" : password;
  }
  try {
    const response = await authorizedFetch("/v1/clock/source", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || t("Klockan kunde inte uppdateras"));
    document.querySelector("#fastclock-password").value = "";
    finishModal(form);
    await refreshLocalClock();
  } catch (error) { setMessage(message, error.message, "error"); }
  finally { endModalAction(form); }
});

document.querySelector("#clock-appearance-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.querySelector("#clock-appearance-message");
  if (!beginModalAction(form)) return;
  try {
    const response = await authorizedFetch("/v1/clock", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "appearance", style: document.querySelector("#meet-clock-style").value,
        show_seconds: document.querySelector("#meet-clock-seconds").checked }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || t("Klockan kunde inte uppdateras"));
    finishModal(form);
    await refreshLocalClock();
  } catch (error) { setMessage(message, error.message, "error"); }
  finally { endModalAction(form); }
});

document.querySelector("#device-language-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.dataset.deviceId || !beginModalAction(form)) return;
  try {
    const response = await authorizedFetch("/v1/devices/language", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: form.dataset.deviceId, language: document.querySelector("#device-language").value }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || "Språket kunde inte sparas.");
    finishModal(form);
    setMessage(document.querySelector("#device-list-message"), "Språket är sparat. Boxen får det nu eller vid nästa anslutning.", "success");
    await refreshDevices();
  } catch (error) { setMessage(document.querySelector("#device-language-message"), error.message, "error"); }
  finally { endModalAction(form); }
});

deviceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = deviceForm.querySelector('button[type="submit"]');
  if (button.disabled || !beginModalAction(deviceForm)) return;
  setMessage(deviceMessage, "");
  try {
    const response = await authorizedFetch("/v1/devices/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        device_code: document.querySelector("#device-code").value,
        station_id: deviceStation.value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "TMBoxen kunde inte kopplas");
    setMessage(deviceMessage, "TMBoxen är kopplad och hämtar sin station vid nästa kontakt.", "success");
    document.querySelector("#device-code").value = "";
    await refreshDevices();
    finishModal(deviceForm);
  } catch (error) {
    setMessage(deviceMessage, error.message, "error");
  } finally {
    endModalAction(deviceForm);
  }
});

document.querySelector("#device-remove-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('[type="submit"]');
  if (button.disabled || !form.dataset.deviceId || !beginModalAction(form)) return;
  const message = document.querySelector("#device-remove-message");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  setMessage(message, "");
  button.disabled = true;
  try {
    const response = await authorizedFetch("/v1/devices/remove", {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: form.dataset.deviceId }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || t("TMBoxen kunde inte tas bort."));
    finishModal(form, "TMBoxen är borttagen.");
    setMessage(document.querySelector("#device-list-message"), "TMBoxen är borttagen.", "success");
    // Show the confirmed result without depending on another network request.
    state.devices = state.devices.filter(device => device.device_id !== form.dataset.deviceId);
    renderDevices({ devices: state.devices, stations: state.stations });
  } catch (error) {
    setMessage(message, error.name === "AbortError" ? "Servern svarade inte. Kontrollera listan innan du försöker igen." : error.message, "error");
  } finally { clearTimeout(timeout); endModalAction(form); }
});

runtimeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const syncCode = document.querySelector("#runtime-sync-code").value;
  if (syncCode.length !== 6) {
    setMessage(runtimeMessage, "Fyll i alla sex siffror i träffkoden.", "error");
    return;
  }
  setMessage(runtimeMessage, "1/3 · Kontaktar Config-servern och kontrollerar träffkoden …");
  document.querySelector("#cloud-connection-state").textContent = "Kopplar …";
  if (!beginModalAction(runtimeForm)) return;
  try {
    const response = await authorizedFetch("/v1/runtime/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        central_url: document.querySelector("#runtime-central-url").value,
        sync_code: syncCode,
        confirm_meet_change: document.querySelector("#confirm-meet-change").checked,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Träffen kunde inte hämtas");
    setMessage(runtimeMessage, `3/3 · ${payload.message} Cloud-kopplingen är sparad på servern.`, payload.restart_required ? "notice" : "success");
    runtimeSyncCodeBoxes.reset();
    await Promise.all([refreshServerContext(), refreshRuntime(), refreshInfo()]);
    finishModal(runtimeForm);
  } catch (error) {
    setMessage(runtimeMessage, error.message, "error");
    document.querySelector("#cloud-connection-state").textContent = "Kopplingen misslyckades";
  } finally {
    endModalAction(runtimeForm);
  }
});

// ── Återställning från säkerhetskopia ──────────────────────────────────
//
// Listan kommer från servern, som läser varje kopia och säger vilken träff den
// bär och om den går att lita på. En trasig kopia visas ändå, gråad: den som
// letar efter sin backup ska få veta att den finns och att den inte duger.
//
// Bekräftelsen är namnet på det som skrivs över, inte ett fast ord. Man ska
// behöva läsa vad man håller på att förlora för att kunna skriva det.

const restore = { chosen: null, overwrites: "" };

function restoreEl(name) {
  return document.querySelector(`#restore-${name}`);
}

function restoreClock(value) {
  if (!value) return "okänt datum";
  const when = new Date(value);
  return Number.isNaN(when.getTime())
    ? "okänt datum"
    : when.toLocaleString("sv-SE", { dateStyle: "medium", timeStyle: "short" });
}

function restoreSize(bytes) {
  const mb = Number(bytes) / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(Number(bytes) / 1024))} kB`;
}

function renderBackups(backups) {
  const list = restoreEl("list");
  restoreEl("empty").classList.toggle("hidden", backups.length > 0);
  list.replaceChildren(...backups.map((item) => {
    const row = document.createElement("label");
    row.className = item.usable ? "restore-row" : "restore-row is-broken";

    const pick = document.createElement("input");
    pick.type = "radio";
    pick.name = "restore-backup";
    pick.value = item.name;
    pick.disabled = !item.usable;
    pick.addEventListener("change", () => {
      restore.chosen = item.name;
      updateRestoreButton();
    });
    row.append(pick);

    const text = document.createElement("span");
    const when = document.createElement("b");
    when.textContent = restoreClock(item.taken_at);
    const what = document.createElement("small");
    what.textContent = item.usable
      ? `${item.meet_name || t("Ingen aktiv träff")} · ${restoreSize(item.size_bytes)}`
      : item.problem || "kopian går inte att använda";
    text.append(when, what);
    row.append(text);
    return row;
  }));
}

function updateRestoreButton() {
  const typed = restoreEl("confirmation").value.trim().toLocaleLowerCase("sv-SE");
  const expected = restore.overwrites.trim().toLocaleLowerCase("sv-SE");
  restoreEl("start").disabled = !restore.chosen || !expected || typed !== expected;
}

async function refreshBackups() {
  try {
    const response = await authorizedFetch("/v1/server/backups");
    if (!response.ok) return;
    const payload = await response.json();
    if (document.querySelector("#restore-modal").open) return;
    restore.overwrites = payload.overwrites || "";
    restoreEl("overwrites").textContent = restore.overwrites || "–";
    restoreEl("confirmation").placeholder = restore.overwrites || "Namnet på det som skrivs över";
    renderBackups(payload.backups || []);
    updateRestoreButton();
  } catch {
    setMessage(restoreEl("message"), "Säkerhetskopiorna kunde inte läsas", "error");
  }
}

function bindRestore() {
  restoreEl("confirmation")?.addEventListener("input", updateRestoreButton);
  restoreEl("start")?.addEventListener("click", async () => {
    const message = restoreEl("message");
    const dialog = document.querySelector("#restore-modal");
    if (dialog.dataset.busy === "true") return;
    if (!window.confirm(
      `Servern återställs och startar om. Allt som hänt efter kopian försvinner, ${restore.overwrites} inkluderat.`,
    )) return;
    if (!beginModalAction(dialog)) return;
    try {
      const response = await authorizedFetch("/v1/server/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          backup: restore.chosen,
          confirmation: restoreEl("confirmation").value,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        setMessage(message, payload.message || "Återställningen gick inte att starta", "error");
        updateRestoreButton();
        return;
      }
      const box = restoreEl("consequences");
      box.replaceChildren(...(payload.consequences || []).map((line) => {
        const p = document.createElement("p");
        p.textContent = line;
        return p;
      }));
      box.classList.remove("hidden");
      setMessage(message, payload.message || "Servern återställs och startar om.", "notice");
      // Samma väg som nollställningen: servern går ner en stund och
      // webbläsaren väntar in den i stället för att visa ett tomt fel.
      // Inloggningen kommer ur databasen som just byttes ut, så sessionen
      // gäller inte nödvändigtvis efteråt - webbläsaren får fråga om på nytt.
      state.restarting = true;
      localStorage.removeItem("trainmeet.accessToken");
      state.token = null;
      setConnection("waiting", "Återställer och startar om");
      await waitForServerReturn(message);
    } catch {
      setMessage(message, "Återställningen gick inte att starta", "error");
    } finally {
      endModalAction(dialog);
      updateRestoreButton();
    }
  });
}

factoryResetConfirmation.addEventListener("input", () => {
  factoryResetButton.disabled = factoryResetConfirmation.value.trim().toUpperCase() !== "NOLLSTÄLL";
});

factoryResetButton.addEventListener("click", async () => {
  const dialog = document.querySelector("#reset-modal");
  if (dialog.dataset.busy === "true") return;
  if (factoryResetConfirmation.value.trim().toUpperCase() !== "NOLLSTÄLL") return;
  const localFactoryReset = state.authStatus?.at_the_machine === true;
  const question = localFactoryReset
    ? "All lokal TrainMeet-data och administratören tas bort. Vill du fabriksåterställa nu?"
    : "Träffdata och anslutningar tas bort. Din administratörsinloggning behålls. Vill du fortsätta?";
  if (!window.confirm(question)) return;
  if (!beginModalAction(dialog)) return;
  setMessage(factoryResetMessage, localFactoryReset
    ? "Fabriksåterställer servern och startar första installationen …"
    : "Nollställer träffdata och behåller din inloggning …", "notice");
  state.restarting = true;
  try {
    const response = await authorizedFetch(localFactoryReset
      ? "/v1/server/factory-reset"
      : "/v1/server/operational-reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmation: "NOLLSTÄLL" }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Servern kunde inte nollställas");
    if (localFactoryReset) {
      localStorage.removeItem("trainmeet.accessToken");
      state.token = null;
    }
    setConnection("waiting", "Nollställer");
    setMessage(factoryResetMessage, payload.message, "notice");
    await waitForServerReturn(factoryResetMessage);
  } catch (error) {
    state.restarting = false;
    setMessage(factoryResetMessage, error.message, "error");
  } finally {
    endModalAction(dialog);
  }
});

runtimeCheckUpdate.addEventListener("click", async () => {
  setMessage(document.querySelector("#cloud-update-message"), "Söker efter publicerad config …");
  runtimeCheckUpdate.disabled = true;
  try {
    const response = await authorizedFetch("/v1/config/check", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Configuppdateringen kunde inte kontrolleras.");
    setMessage(document.querySelector("#cloud-update-message"), payload.message || "Kontrollen är klar. Uppdateringar används när det är säkert.", "success");
    await Promise.all([refreshServerContext(), refreshRuntime(), refreshLocalClock()]);
  } catch (error) {
    setMessage(document.querySelector("#cloud-update-message"), error.message, "error");
  } finally { runtimeCheckUpdate.disabled = false; }
});

document.querySelector("#cloud-auto-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const enabled = document.querySelector("#cloud-auto-enabled").checked;
  if (!beginModalAction(form)) return;
  try {
    const response = await authorizedFetch("/v1/cloud/auto-sync", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || t("Inställningen kunde inte sparas."));
    await refreshServerContext();
    finishModal(form);
  } catch (error) {
    setMessage(form.querySelector(".form-message"), error.message, "error");
  } finally { endModalAction(form); }
});

function renderCloudStatus() {
  const context = state.serverContext || {};
  const update = context.cloud_update || {};
  const meet = context.selected_meet;
  document.querySelector("#cloud-connection-meet").textContent = meet?.name || t("Ingen träff vald");
  document.querySelector("#cloud-connection-meta").textContent = meet?.publication_id
    ? t("Publicerad config · {version}", { version: meet.publication_id }) : t("Koppla en publicerad träff med koden från Cloud.");
  document.querySelector("#cloud-connection-state").textContent = t(update.linked ? "Kopplad" : "Inte kopplad");
  document.querySelector("#cloud-connection-state").classList.toggle("active", Boolean(update.linked));
  document.querySelector("#cloud-auto-status").textContent = t(update.linked
    ? (update.auto_sync ? "Automatisk configuppdatering är aktiv." : "Automatisk configuppdatering är pausad.")
    : "Automatisk uppdatering aktiveras när servern kopplas till Cloud.");
  document.querySelector("#cloud-version-state").textContent = update.message || t(
    update.pending_publication_id ? "Ny config hämtad – väntar på säker aktivering." :
    "Senaste fungerande config används även utan internet.");
  runtimeCheckUpdate.disabled = !update.linked;
  document.querySelector("#cloud-auto-edit").disabled = !update.linked;
  if (!editorActive(document.querySelector("#cloud-auto-form"))) {
    document.querySelector("#cloud-auto-enabled").checked = Boolean(update.auto_sync);
  }
  serverUI.refreshHeader();
}

softwareCheck.addEventListener("click", checkSoftwareUpdate);
softwareInstall.addEventListener("click", async () => {
  if (!confirm("Uppdateringen säkerhetskopierar databasen och startar om servern. Pågående trafik avbryts. Fortsätta?")) return;
  softwareInstall.disabled = true;
  setMessage(softwareUpdateMessage, "Startar uppdateringen …", "notice");
  try {
    const response = await authorizedFetch("/v1/server/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Uppdateringen kunde inte startas");
    setMessage(softwareUpdateMessage, "Uppdaterar i bakgrunden. Sidan ansluter igen efter omstart.", "notice");
    await waitForSoftwareUpdate();
    await checkSoftwareUpdate();
  } catch (error) {
    setMessage(softwareUpdateMessage, error.message, "error");
  } finally {
    softwareInstall.disabled = false;
  }
});

async function waitForSoftwareUpdate() {
  // Poll the stage rather than guess from symptoms. The old version of this
  // watched for the server going away and the version string changing, which
  // was the best it could do when the only statuses were downloading,
  // installing and complete. Now the updater says where it is, so the
  // progress bar shows the real step and `complete` means the health check
  // passed - not merely that files were copied.
  for (let attempt = 0; attempt < 240; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    try {
      const response = await authorizedFetch("/v1/server/update");
      if (!response.ok) continue;
      const payload = await response.json();
      renderSoftwareUpdate(payload);
      if (payload.status === "failed") {
        throw new Error(payload.message || "Uppdateringen misslyckades");
      }
      if (payload.status === "complete") {
        window.location.reload();
        return;
      }
    } catch (error) {
      // The server is unreachable while it restarts, which is a stage, not a
      // failure. A real failure carries a message and is rethrown.
      if (error.message && /misslyckades/i.test(error.message)) throw error;
    }
  }
  throw new Error("Uppdateringen tar längre tid än väntat. Ladda om sidan om en stund.");
}

// The update view. Its twin lives in trainmeet-cloud's SettingsPage: the same
// seven steps, the same texts, the same states. The two update by completely
// different means, so only the presentation is shared - deliberately, because
// what an operator needs to know is identical either way.
const updateProgress = document.querySelector("#update-progress");
const softwareRetry = document.querySelector("#software-retry");
const softwareVersionMove = document.querySelector("#software-version-move");
const softwareTechnical = document.querySelector("#software-technical");

//: Vad varje tillstånd heter i högerkanten. Nycklarna är update_contract.py:s
//: fyra tillstånd; översättningen är presentation, inte ett nytt tillstånd.
const UPDATE_STATE_WORDS = {
  done: "Klar",
  active: "Pågår",
  pending: "Väntar",
  failed: "Fel",
};

function renderUpdateProgress(payload) {
  // Paketets DEL 3.11 visar räckan av sju steg, inte bara de som hänt: en
  // operatör ska kunna se vad en uppdatering innebär innan den startas.
  // Stegen, ordningen och tillstånden kommer oförändrat ur serverns svar.
  const steps = payload.steps || [];
  updateProgress.classList.toggle("hidden", steps.length === 0);
  updateProgress.replaceChildren(...steps.map((step) => {
    const item = document.createElement("li");
    item.className = `update-step ${step.state}`;
    const label = document.createElement("span");
    label.className = "update-step-label";
    label.textContent = step.label;
    const word = document.createElement("span");
    word.className = "update-step-state";
    word.textContent = UPDATE_STATE_WORDS[step.state] || "";
    item.append(label, word);
    return item;
  }));
}

function renderVersionMove(payload) {
  // "Installerad version 1.3.2 → Tillgänglig version 1.4.0". Only shown when
  // there is actually somewhere to move to.
  const show = payload.update_available && payload.latest_version;
  softwareVersionMove.classList.toggle("hidden", !show);
  if (!show) return;
  softwareVersionMove.replaceChildren();
  const installed = document.createElement("span");
  installed.textContent = "Installerad version ";
  const from = document.createElement("b");
  from.textContent = payload.installed_version;
  const arrow = document.createElement("span");
  arrow.className = "arrow";
  arrow.textContent = "→";
  const available = document.createElement("span");
  available.textContent = "Tillgänglig version ";
  const to = document.createElement("b");
  to.textContent = payload.latest_version;
  softwareVersionMove.append(installed, from, arrow, available, to);
}

function renderTechnicalDetails(payload) {
  const rows = [["Installerad build", payload.installed_build || "okänd"]];
  if (payload.latest_build) rows.push(["Tillgänglig build", payload.latest_build]);
  if (payload.failed_stage) rows.push(["Fel i steget", payload.failed_stage]);
  softwareTechnical.replaceChildren(...rows.flatMap(([label, value]) => {
    const term = document.createElement("dt");
    term.textContent = label;
    const definition = document.createElement("dd");
    definition.textContent = value;
    return [term, definition];
  }));
}

function renderSoftwareUpdate(payload) {
  // The version comes first because that is what a person reads; the rest of
  // the commit metadata is under "Teknisk information". The package puts the
  // build id on the same line - "1.2.0 · build 4bd9c9a" - because it is what
  // an operator reads back over the phone.
  const build = payload.installed_build || "";
  softwareVersion.textContent = build
    ? `${payload.installed_version} · build ${build}`
    : `${payload.installed_version}`;
  softwareVersion.dataset.version = payload.installed_version;
  softwareVersion.dataset.build = build;
  // Stegräckan visar samma version som kortet, ur samma svar - annars kan de
  // stå och säga olika saker om vilken programvara som kör.

  renderUpdateProgress(payload);
  renderVersionMove(payload);
  renderTechnicalDetails(payload);

  const failed = payload.status === "failed";
  const running = (payload.steps || []).some((step) => step.state === "active");
  document.querySelector("#update-progress").classList.toggle("hidden", !running && !failed);
  softwareRetry.classList.toggle("hidden", !failed);
  softwareCheck.disabled = running;

  if (!payload.supported) {
    softwareInstall.classList.add("hidden");
    setMessage(softwareUpdateMessage, "Den här miljön uppdateras via Docker eller driftplattformen.", "notice");
    return;
  }
  if (failed) {
    softwareInstall.classList.add("hidden");
    setMessage(softwareUpdateMessage, payload.message || "Uppdateringen misslyckades", "error");
    return;
  }
  if (running) {
    softwareInstall.classList.add("hidden");
    setMessage(softwareUpdateMessage, payload.message || "Uppdateringen pågår", "notice");
    return;
  }
  if (payload.check_error) {
    softwareInstall.classList.add("hidden");
    setMessage(softwareUpdateMessage, payload.check_error, "error");
    return;
  }
  softwareInstall.classList.toggle("hidden", !payload.update_available);
  setMessage(softwareUpdateMessage, payload.update_available
    ? `Version ${payload.latest_version || payload.latest_build} finns tillgänglig.`
    : "Servern har senaste versionen.", payload.update_available ? "notice" : "success");
}

async function checkSoftwareUpdate() {
  softwareCheck.disabled = true;
  try {
    const response = await authorizedFetch("/v1/server/update");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Versionskontrollen misslyckades");
    renderSoftwareUpdate(payload);
  } catch (error) {
    setMessage(softwareUpdateMessage, error.message, "error");
  } finally {
    softwareCheck.disabled = false;
  }
}

softwareRetry.addEventListener("click", async () => {
  softwareRetry.disabled = true;
  try {
    await authorizedFetch("/v1/server/update", { method: "POST" });
    await checkSoftwareUpdate();
  } catch (error) {
    setMessage(softwareUpdateMessage, error.message, "error");
  } finally {
    softwareRetry.disabled = false;
  }
});

restartButtons.forEach((button) => button.addEventListener("click", restartServer));

async function openApplication() {
  document.body.dataset.signedIn = state.authStatus?.authenticated ? "yes" : "no";
  setup.classList.add("hidden");
  login.classList.add("hidden");
  appView.classList.remove("hidden");
  logoutButton.classList.toggle("hidden", !state.authStatus?.authenticated);
  try {
    await refreshServerContext();
    if (!state.authStatus?.authenticated) {
      applyWorkspaceRoute();
      setConnection("online", "Lokalt ansluten");
      return;
    }
    await Promise.all([
      refreshInfo(),
      refreshAdminAccess(),
      refreshDevices(),
      refreshRuntime(),
      refreshLocalClock(),
    ]);
    applyWorkspaceRoute();
    setConnection(
      "online",
      state.authStatus?.at_the_machine ? "Lokalt ansluten" : "Externt ansluten",
    );
    scheduleAdminRefresh();
  } catch (error) {
    handleConnectionError(error);
  }
}



function updateRuntimeNavigation(configured) {
  document.querySelectorAll("[data-requires-runtime]").forEach((element) => {
    element.classList.toggle("hidden", !configured);
  });
}


function scheduleAdminRefresh() {
  clearTimeout(state.adminTimer);
  state.adminTimer = setTimeout(async () => {
    if (!state.authStatus?.authenticated) return;
    await Promise.allSettled([refreshServerContext(), refreshInfo(), refreshDevices(), refreshRuntime(), refreshAdminAccess(), refreshLocalClock()]);
    scheduleAdminRefresh();
  }, 5000);
}


async function refreshInfo() {
  const response = await authorizedFetch("/v1/info");
  if (!response.ok) return;
  const info = await response.json();
  serverUI.info = info;
  serverUI.refreshHeader();
  document.querySelector("#server-name").textContent = info.gateway_id || "TrainMeet Server";
  document.querySelector("#server-detail").textContent =
    `Kör lokalt · aktiv trafiksession: ${info.traffic_session_name}`;
  document.querySelector("#system-server-name").textContent = info.runtime?.server_name || info.gateway_id || "TrainMeet Server";
  document.querySelector("#system-runtime-name").textContent = state.serverContext?.selected_meet?.name || t("Ingen aktiv träff");
  document.querySelector("#system-cloud-state").textContent = state.serverContext?.cloud_update?.linked ? t("Kopplad") : t("Inte kopplad");
  const serverNameInput = document.querySelector("#admin-server-name");
  if (!editorActive(serverIdentityForm)) {
    serverNameInput.value = info.runtime?.server_name || info.gateway_id || "";
  }
  const pill = document.querySelector("#runtime-pill");
  if (state.serverContext?.selected_meet) {
    pill.textContent = state.serverContext.selected_meet.name;
    pill.classList.add("active");
    document.querySelector("#overview-runtime-state").textContent = "Lokalt aktiv";
  } else {
    pill.textContent = info.runtime?.error ? t("Konfigurationen behöver rättas") : t("Ingen träff aktiverad");
    pill.classList.remove("active");
    document.querySelector("#overview-runtime-state").textContent = info.runtime?.error
      ? "Konfigurationsfel"
      : "Ej konfigurerad";
  }
  updateRuntimeNavigation(Boolean(state.serverContext?.selected_meet));
  updateRestartButton(Boolean(info.restart_required));
}

async function refreshAdminAccess() {
  const response = await authorizedFetch("/v1/admin/access");
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "Åtkomstinställningen kunde inte läsas");
  if (!adminAccessForm.closest("dialog").open) document.querySelector("#admin-username").value = payload.username || "";

  // Chippet svarade förr på "hur är jag inne", och svaret var alltid samma
  // sak som var man stod. Nu kräver servern inloggning överallt, så frågan
  // som återstår är var den här webbläsaren står - det avgör om
  // fabriksåterställningen är hela servern eller bara träffdata.
  const atTheMachine = state.authStatus?.at_the_machine === true;
  const badge = document.querySelector("#access-mode");
  badge.textContent = atTheMachine ? t("Vid servern") : t("Över nätet");
  badge.classList.toggle("active", !atTheMachine);

  const passwordState = document.querySelector("#access-password-state");
  passwordState.textContent = payload.password_configured
    ? "Ett lösenord är satt, så andra enheter kan logga in."
    : "Inget lösenord är satt än, så inloggning utifrån är avstängd.";
  passwordState.classList.toggle("is-missing", !payload.password_configured);
}


async function restartServer() {
  if (state.restarting || !state.restartRequired) return;
  if (!window.confirm("Starta om TrainMeet Server och börja använda den aktiverade stationsplanen?")) return;
  state.restarting = true;
  setRestartButtonsDisabled(true);
  setMessage(softwareUpdateMessage, "Startar om TrainMeet Server …", "notice");
  clearTimeout(state.snapshotTimer);
  clearTimeout(state.adminTimer);
  try {
    const response = await authorizedFetch("/v1/server/restart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Servern kunde inte startas om");
    setConnection("waiting", "Startar om");
    setMessage(softwareUpdateMessage, payload.message, "notice");
    await waitForServerReturn(softwareUpdateMessage);
  } catch (error) {
    state.restarting = false;
    setRestartButtonsDisabled(false);
    setMessage(softwareUpdateMessage, error.message, "error");
    scheduleAdminRefresh();
  }
}

// Every caller says where to report; there is no shared message line.
async function waitForServerReturn(message) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    try {
      const response = await fetch("/v1/info", { cache: "no-store" });
      if (response.ok) {
        window.location.reload();
        return;
      }
    } catch {
      // A short connection loss is expected while the service restarts.
    }
  }
  state.restarting = false;
  setRestartButtonsDisabled(false);
  setConnection("waiting", "Kontrollera servern");
  setMessage(message, "Servern har inte kommit tillbaka ännu. Kontrollera ström och nätverk.", "error");
}

function setRestartButtonsVisible(required) {
  restartButtons.forEach((button) => button.classList.toggle("hidden", !required));
}

function setRestartButtonsDisabled(disabled) {
  restartButtons.forEach((button) => { button.disabled = disabled; });
}

function updateRestartButton(required) {
  state.restartRequired = required;
  setRestartButtonsVisible(required);
  setRestartButtonsDisabled(state.restarting);
}


async function refreshDevices() {
  const response = await authorizedFetch("/v1/devices");
  if (!response.ok) return;
  const payload = await response.json();
  // Admin sees physical TMBox and managed browser clients in the same list.
  state.devices = payload.devices || [];
  state.deviceLanguages = payload.languages || [];
  state.stations = payload.stations || [];
  renderDevices({ devices: state.devices, stations: state.stations });
}

function renderDevices(payload) {
  document.querySelector("#app-devices").textContent = `${state.devices.length} ${t("klienter")}`;
  const list = document.querySelector("#device-list");
  updateStationOptions(payload.stations || []);
  if (list.querySelector(".device-inline-edit")) return;
  const signature = JSON.stringify([payload, document.documentElement.lang]);
  if (list.dataset.signature === signature) return;
  list.dataset.signature = signature;
  list.replaceChildren();
  const waiting = payload.devices.filter(device => !device.station_id).length;
  const waitingMessage = document.querySelector("#device-awaiting");
  waitingMessage.hidden = !waiting;
  waitingMessage.textContent = waiting ? `${t("Väntar på station")} · ${waiting}` : "";
  if (!payload.devices.length) {
    list.innerHTML = html`<div class="empty-status">${t("Inga anslutna klienter.")}</div>`;
    return;
  }
  for (const device of [...payload.devices].sort((a, b) => Number(!!a.station_id) - Number(!!b.station_id))) {
    const row = document.createElement("div");
    row.className = "status-row";
    const identity = document.createElement("div");
    const code = document.createElement("b");
    code.textContent = device.device_code;
    const model = document.createElement("small");
    model.textContent = `${device.model} · ${device.device_id}`;
    identity.append(code, model);
    const station = (payload.stations || []).find((entry) => entry.id === device.station_id);
    const assignment = document.createElement("span");
    assignment.textContent = station
      ? `${station.code} · ${station.name}`
      : t("Väntar på station");
    row.append(identity, assignment);
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "secondary";
    const editLabel = device.station_id ? "Ändra station" : "Tilldela station";
    edit.dataset.tmText = editLabel;
    edit.textContent = t(editLabel);
    edit.addEventListener("click", () => {
      const meetGeneration = state.serverContext?.selected_meet?.generation;
      const form = document.createElement("form"); form.className = "device-inline-edit server-actions";
      const select = document.createElement("select"); select.required = true; select.setAttribute("aria-label", t("Station"));
      select.append(new Option(t("Välj station"), ""));
      (payload.stations || []).forEach(s => select.append(new Option(`${s.code} · ${s.name}`, s.id)));
      select.value = device.station_id || "";
      const save = document.createElement("button"); save.type = "submit"; save.textContent = t("Spara");
      const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = t("Avbryt");
      const message = document.createElement("span"); message.setAttribute("role", "status");
      const close = () => { form.remove(); delete list.dataset.signature; renderDevices({devices: state.devices, stations: state.stations}); };
      cancel.addEventListener("click", close);
      form.append(select, save, cancel, message); actions.replaceChildren(form); select.focus();
      form.addEventListener("submit", async event => {
        event.preventDefault(); if (save.disabled) return; save.disabled = cancel.disabled = select.disabled = true;
        try {
          const response = await authorizedFetch("/v1/devices/assign", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({device_code: device.device_code, station_id: select.value, meet_generation: meetGeneration})});
          const result = await response.json(); if (!response.ok) throw new Error(result.message || t("Kunde inte tilldela station"));
          close(); await refreshDevices();
        } catch (error) { message.textContent = error.message; }
        finally { save.disabled = cancel.disabled = select.disabled = false; }
      });
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "secondary device-remove";
    remove.dataset.tmText = "Ta bort";
    remove.textContent = t("Ta bort");
    remove.addEventListener("click", () => {
      const meetGeneration = state.serverContext?.selected_meet?.generation;
      const confirm = document.createElement("div"); confirm.className = "device-inline-edit server-actions";
      const question = document.createElement("span"); question.textContent = t("Koppla bort klienten? Trafik och historik behålls.");
      const yes = document.createElement("button"); yes.type = "button"; yes.textContent = t("Ta bort");
      const no = document.createElement("button"); no.type = "button"; no.textContent = t("Avbryt");
      no.addEventListener("click", () => { confirm.remove(); delete list.dataset.signature; renderDevices(payload); });
      yes.addEventListener("click", async () => {
        yes.disabled = no.disabled = true;
        try {
          const response = await authorizedFetch("/v1/devices/remove", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({device_id: device.device_id, meet_generation: meetGeneration})});
          const result = await response.json(); if (!response.ok) throw new Error(result.message || t("Kunde inte ta bort klienten"));
          confirm.remove(); delete list.dataset.signature; renderDevices({devices: state.devices, stations: state.stations}); await refreshDevices();
        } catch (error) { question.textContent = error.message; }
        finally { yes.disabled = no.disabled = false; }
      });
      confirm.append(question, yes, no); actions.replaceChildren(confirm); no.focus();
    });
    const actions = document.createElement("div");
    actions.className = "device-actions";
    actions.append(edit, remove);
    if (device.language) {
      const language = document.createElement("button");
      language.type = "button";
      language.className = "secondary device-language";
      const current = (state.deviceLanguages || []).find(item => item.code === device.language);
      language.textContent = "Språk / " + (current?.name || device.language);
      language.addEventListener("click", () => {
        document.querySelector("#device-language-form").dataset.deviceId = device.device_id;
        document.querySelector("#device-language-code").textContent = device.device_code;
        const select = document.querySelector("#device-language");
        select.replaceChildren(...(state.deviceLanguages || []).map(item => new Option(item.name, item.code)));
        select.value = device.language;
        openModal("device-language-modal");
      });
      actions.insertBefore(language, remove);
    }
    row.append(actions);
    list.append(row);
  }
}

function updateStationOptions(stations) {
  if (deviceStation.closest("dialog")?.open) return;
  const signature = stations.map((station) => `${station.id}:${station.code}`).join("|");
  if (deviceStation.dataset.signature === signature) return;
  deviceStation.dataset.signature = signature;
  const previous = deviceStation.value;
  deviceStation.replaceChildren();
  deviceStation.append(new Option(t("Välj station"), ""));
  for (const station of stations) {
    const option = document.createElement("option");
    option.value = station.id;
    option.textContent = `${station.code} · ${station.name}`;
    deviceStation.append(option);
  }
  if (previous) deviceStation.value = previous;
}

async function refreshRuntime() {
  const response = await authorizedFetch("/v1/runtime");
  if (!response.ok) return;
  state.runtime = await response.json();
  const input = document.querySelector("#runtime-central-url");
  if (state.runtime.central_url && !input.closest("dialog")?.open) input.value = state.runtime.central_url;
  renderCloudStatus();
  await refreshCloudPresentation();
}

let cloudPresentation = null;
let placementEdit = null;
let presentationRequest = 0;
async function refreshCloudPresentation() {
  const request = ++presentationRequest;
  try {
    const response = await authorizedFetch("/v1/cloud/presentation", {cache: "no-store"});
    if (request !== presentationRequest) return;
    const data = response.ok ? await response.json() : null;
    if (request !== presentationRequest) return;
    cloudPresentation = data;
    renderCloudPresentation();
  } catch (_) { /* An offline refresh must not destroy an open editor. */ }
}

function renderCloudPresentation() {
  const data = cloudPresentation;
  serverUI.presentation = data;
  serverUI.refreshHeader();
  // Keep the displayed publication/configuration fence with an inline draft.
  if (document.querySelector(".placement-inline-edit")) return;
  document.querySelector("#display-placement-section").hidden = !data?.supported;
  document.querySelector("#published-findings").hidden = !data?.supported;
  const rows = document.querySelector("#display-placement-rows");
  rows.replaceChildren();
  for (const station of data?.stations || []) {
    const tr = document.createElement("tr");
    for (const value of [`${station.code} · ${station.name}`,
      ...["left", "right"].map(side => station.connections.filter(c => c.side === side).map(c => c.other_station_code).join(", ") || "—")]) {
      const td = document.createElement("td"); td.textContent = value; tr.append(td);
    }
    if (station.legacy_layout_limited) {
      const note = document.createElement("small");
      note.className = "placement-legacy-note";
      note.textContent = t("Äldre klienter med fast layout behåller Cloud-placeringen. Den nya TMBox-vyn använder ditt val.");
      tr.firstChild.append(note);
    }
    const action = document.createElement("td");
    const button = document.createElement("button"); button.type = "button"; button.className = "secondary";
    button.textContent = t("Redigera"); button.disabled = !station.connections.length;
    button.setAttribute("aria-label", `${t("TMBox-placering")} · ${station.name}`);
    button.addEventListener("click", () => station.connections.length <= 2
      ? editInlinePlacement(station, action, data)
      : editDisplayPlacement(station.station_id, button));
    const dialog = document.querySelector("#display-placement-modal");
    if (dialog.open && placementEdit?.station_id === station.station_id) modalOrigins.set(dialog, button);
    action.append(button); tr.append(action); rows.append(tr);
  }
  const findings = data?.findings;
  const summary = document.querySelector("#published-findings-summary");
  summary.textContent = !Array.isArray(findings) ? t("Den här Cloud-versionen innehåller inga kontrolluppgifter.")
    : findings.length ? t("{count} noterade uppgifter", {count: findings.length}) : t("Inga konflikter eller observationer noterade.");
  const list = document.querySelector("#published-findings-list"); list.replaceChildren();
  for (const finding of findings || []) {
    const li = document.createElement("li");
    const kind = finding.level === "conflict" ? t("Konflikt") : finding.level === "observation" ? t("Observation") : t("Uppgift");
    li.textContent = `${kind}${finding.rule ? ` ${finding.rule}` : ""} · ${typeof finding.message === "string" ? finding.message : "—"}`;
    list.append(li);
  }
  serverUI.stationRows(rows);
}

function editInlinePlacement(station, host, data) {
  const fence = {publication_id: data.publication_id, config_version: data.config_version, station_id: station.station_id, meet_generation: state.serverContext?.selected_meet?.generation};
  const form = document.createElement("form"); form.className = "placement-inline-edit server-actions";
  station.connections.forEach(connection => {
    const label = document.createElement("label"); label.textContent = connection.other_station_code;
    const select = document.createElement("select"); select.name = connection.connection_id;
    select.append(new Option(`${t("Följ Cloud")} · ${t(connection.default_side === "left" ? "Vänster" : "Höger")}`, ""), new Option(t("Vänster"), "left"), new Option(t("Höger"), "right"));
    select.value = connection.overridden ? connection.side : ""; label.append(select); form.append(label);
  });
  const save = document.createElement("button"); save.type = "submit"; save.textContent = t("Spara");
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = t("Avbryt");
  const message = document.createElement("span"); message.setAttribute("role", "status");
  const close = () => { form.remove(); renderCloudPresentation(); };
  cancel.addEventListener("click", close); form.append(save, cancel, message); host.replaceChildren(form);
  form.querySelector("select").focus();
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (save.disabled) return;
    const selects = [...form.querySelectorAll("select")];
    const sides = Object.fromEntries(selects.filter(s => s.value).map(s => [s.name, s.value]));
    save.disabled = cancel.disabled = true; selects.forEach(s => { s.disabled = true; });
    try {
      const response = await authorizedFetch("/v1/cloud/display-placement", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...fence,sides})});
      const result = await response.json(); if (!response.ok) throw new Error(result.message || t("Inställningen kunde inte sparas."));
      ++presentationRequest; cloudPresentation = result; close();
    } catch(error) { message.textContent = error.message; }
    finally { save.disabled = cancel.disabled = false; selects.forEach(s => { s.disabled = false; }); }
  });
}

function editDisplayPlacement(stationId, origin) {
  const data = cloudPresentation;
  const station = data?.stations?.find(s => s.station_id === stationId);
  if (!station) return;
  placementEdit = {publication_id: data.publication_id, config_version: data.config_version, station_id: stationId};
  document.querySelector("#display-placement-title").textContent = `${t("TMBox-placering")} · ${station.code}`;
  const fields = document.querySelector("#display-placement-fields"); fields.replaceChildren();
  for (const connection of station.connections) {
    const label = document.createElement("label");
    const title = document.createElement("span"); title.textContent = `${connection.other_station_code} · ${connection.other_station_name}`;
    const select = document.createElement("select"); select.name = connection.connection_id;
    for (const [value, text] of [["", `${t("Följ Cloud")} · ${t(connection.default_side === "left" ? "Vänster" : "Höger")}`], ["left", t("Vänster")], ["right", t("Höger")]]) {
      const option = document.createElement("option"); option.value = value; option.textContent = text; select.append(option);
    }
    select.value = connection.overridden ? connection.side : "";
    label.append(title, select); fields.append(label);
  }
  setMessage(document.querySelector("#display-placement-form .form-message"), "");
  openModal("display-placement-modal", origin);
}

document.querySelector("#display-placement-defaults").addEventListener("click", () => {
  for (const select of document.querySelectorAll("#display-placement-fields select")) {
    select.value = ""; select.dispatchEvent(new Event("change", {bubbles: true}));
  }
});
document.querySelector("#display-placement-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const sides = Object.fromEntries([...form.querySelectorAll("select")].filter(s => s.value).map(s => [s.name, s.value]));
  if (!placementEdit || !beginModalAction(form)) return;
  try {
    const response = await authorizedFetch("/v1/cloud/display-placement", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({...placementEdit, sides})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.message || t("Inställningen kunde inte sparas."));
    ++presentationRequest;
    cloudPresentation = data; renderCloudPresentation();
    finishModal(form);
  } catch (error) { setMessage(form.querySelector(".form-message"), error.message, "error"); }
  finally { endModalAction(form); }
});

async function refreshLocalClock() {
  const response = await authorizedFetch("/v1/clock", { cache: "no-store" });
  if (!response.ok) return;
  const clock = await response.json();
  state.clock = clock;
  if (state.serverContext?.operating_region !== "us") {
    const display = await fetch("/v1/display", { cache: "no-store" });
    if (display.ok) {
      const payload = await display.json();
      state.overviewSnapshot = payload;
      renderOverview(payload);
    }
  } else {
    const display = await fetch("/v1/display", {cache:"no-store"});
    if (display.ok) { const payload = await display.json(); serverUI.us(payload.us); }
  }
  const connection = await authorizedFetch("/v1/display/connection", {cache: "no-store"});
  if (connection.ok) renderConnectionBadgeSettings(await connection.json());
  const timeInput = document.querySelector("#local-clock-time");
  if (!editorActive(clockControlForm)) timeInput.value = clock.time || "12:00:00";
  const speedInput = document.querySelector("#local-clock-speed");
  if (!editorActive(clockControlForm)) speedInput.value = Number(clock.speed || 1);
  const stateLabel = document.querySelector("#clock-state");
  if (!editorActive(document.querySelector("#clock-appearance-form"))) {
    const styleSelect = document.querySelector("#meet-clock-style");
    const styles = ["digital", "analog", "stationsur", "swiss"];
    if (clock.style && !styles.includes(clock.style)) styles.push(clock.style);
    styleSelect.replaceChildren(...styles.map(value => new Option(t(clockStyleLabels[value] || value), value)));
    styleSelect.value = clock.style || styles[0];
    document.querySelector("#meet-clock-seconds").checked = clock.show_seconds !== false;
  }
  stateLabel.textContent = clock.running ? t("Går · {speed}×", { speed: Number(clock.speed || 1) }) : t("Stoppad");
  stateLabel.classList.toggle("clock-running", Boolean(clock.running));
  const external = clock.source === "fastclock";
  const sourceStatus = document.querySelector("#clock-source-status");
  sourceStatus.textContent = external
    ? `${t("FastClock")} · ${clock.external_name || ""} · ${t(clock.available ? "Ansluten" : "Kontakt saknas – senast mottagna tid visas")}`
    : t("Intern serverklocka");
  sourceStatus.classList.toggle("error", external && !clock.available);
  document.querySelector("#clock-adjust").disabled = external;
  if (!document.querySelector("#clock-source-modal").open) {
    const sourceResponse = await authorizedFetch("/v1/clock/source", { cache: "no-store" });
    if (sourceResponse.ok && !document.querySelector("#clock-source-modal").open) {
      const settings = await sourceResponse.json();
      document.querySelector("#clock-source").value = settings.source || "internal";
      document.querySelector("#fastclock-name").value = settings.clock_name || "";
      document.querySelector("#fastclock-user").value = settings.user || "";
      document.querySelector("#fastclock-password").value = "";
      document.querySelector("#fastclock-clear-password").checked = false;
      document.querySelector("#fastclock-interval").value = settings.poll_interval || 2;
      document.querySelector("#fastclock-password-note").textContent = settings.has_password ? t("Sparat lösenord behålls om fältet lämnas tomt.") : "";
      renderClockSourceFields();
    }
  }
  document.querySelector("#overview-clock").textContent = String(clock.time || "--:--").slice(0, 5);
  document.querySelector("#app-clock").textContent = String(clock.time || "--:--").slice(0, 5);
  updateClockControlAvailability();
  serverUI.refreshClock(clock);
}

function updateClockControlAvailability() {
  const clock = state.clock || {};
  const readOnly = clock.source === "fastclock" && !clock.can_control;
  document.querySelector("#overview-clock-start").disabled = readOnly || Boolean(clock.running) || !state.serverContext?.selected_meet;
  // Allow a deliberate stop even if external status is unknown.
  document.querySelector("#overview-clock-stop").disabled = readOnly || (!clock.running && !(clock.source === "fastclock" && !clock.available));
}

function renderConnectionBadgeSettings(connection) {
  serverUI.network(connection);
  const container = document.querySelector("#connection-badge-screens");
  if (!container) return;
  // Never rewrite a part someone is editing; each part is its own form.
  const idle = (selector) => !editorActive(document.querySelector(selector));
  if (idle("#connection-badge-form")) {
    const screens = connection.screens || [];
    for (const input of container.querySelectorAll("input[type=checkbox]")) input.checked = screens.includes(input.value);
  }
  if (idle("#connection-code-form")) {
    document.querySelector("#connection-badge-validity").value = String(connection.validity_hours ?? 0);
    document.querySelector("#web-client-ttl").value = String(connection.web_client_ttl_minutes ?? 30);
  }
  if (idle("#connection-wifi-form")) {
    const wifi = connection.wifi || {};
    document.querySelector("#connection-wifi-name").value = wifi.name || "";
    document.querySelector("#connection-wifi-password").value = wifi.password || "";
  }
  const badge = document.querySelector("#connection-badge-code");
  badge.textContent = connection.code
    ? `${connection.host}:${connection.port} · ${t("Kod")} ${connection.code}`
    : t("Ingen kod utfärdad");
}

async function saveConnectionPart(form, messageSelector, body, success, parameters = {}) {
  const message = document.querySelector(messageSelector);
  if (!beginModalAction(form)) return;
  try {
    const response = await authorizedFetch("/v1/display/connection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Inställningen kunde inte sparas");
    finishModal(form);
    setMessage(message, success(payload), "success");
    if (Object.keys(parameters).length) message.textContent = t(message.dataset.tmText, parameters);
    renderConnectionBadgeSettings(payload);
  } catch (error) {
    setMessage(message, error.message, "error");
  } finally {
    endModalAction(form);
  }
}

async function controlLocalClock(command) {
  if (!beginModalAction(clockControlForm)) return;
  const buttons = [...clockControlForm.querySelectorAll("button"), document.querySelector("#overview-clock-start"), document.querySelector("#overview-clock-stop")];
  buttons.forEach((button) => { button.disabled = true; });
  const message = clockControlMessage;
  setMessage(message, "Uppdaterar den lokala klockan …", "notice");
  try {
    const response = await authorizedFetch("/v1/clock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Klockan kunde inte uppdateras");
    setMessage(
      message,
      payload.running ? `Klockan går från ${payload.time.slice(0, 5)} i ${Number(payload.speed)}×.` : "Klockan är stoppad.",
      "success",
    );
    finishModal(clockControlForm);
    await refreshLocalClock();
  } catch (error) {
    setMessage(message, error.message, "error");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
    endModalAction(clockControlForm);
    updateClockControlAvailability();
  }
}



function uniqueOverviewServices(snapshot) {
  const byTrainNumber = new Map();
  for (const service of snapshot?.services || []) {
    const trainNumber = String(service.train_number || "").trim();
    if (!trainNumber) continue;
    const existing = byTrainNumber.get(trainNumber);
    if (!existing || (service.stops?.length || 0) > (existing.stops?.length || 0)) {
      byTrainNumber.set(trainNumber, service);
    }
  }
  return [...byTrainNumber.values()].sort((a, b) =>
    String(a.train_number).localeCompare(String(b.train_number), "sv", { numeric: true })
  );
}

function serviceForTrain(snapshot, trainNumber) {
  if (!trainNumber) return null;
  return uniqueOverviewServices(snapshot).find((service) => String(service.train_number) === String(trainNumber)) || null;
}

function selectOverviewTrain(trainNumber) {
  state.selectedTrainNumber = trainNumber ? String(trainNumber) : null;
  state.selectedStationID = null;
  renderRouteExplorer();
  renderOverviewTopology();
  renderOverviewGraph(state.overviewSnapshot);
}

function selectOverviewStation(stationID, preserveTrain = false) {
  state.selectedStationID = stationID || null;
  if (!preserveTrain && stationID) state.selectedTrainNumber = null;
  renderRouteExplorer();
  renderOverviewTopology();
  renderOverviewGraph(state.overviewSnapshot);
  renderStationInspector();
}

function clearOverviewSelection() {
  state.selectedTrainNumber = null;
  state.selectedStationID = null;
  renderRouteExplorer();
  renderOverviewTopology();
  renderOverviewGraph(state.overviewSnapshot);
  renderStationInspector();
}

function renderOverviewTopology() {
  if (!state.overviewSnapshot) return;
  renderTopology(state.overviewSnapshot, document.querySelector("#overview-topology"), {
    selectedTrainNumber: state.selectedTrainNumber,
    selectedStationID: state.selectedStationID,
    showBadge: false,
    onTrainSelect: (trainNumber) => selectOverviewTrain(trainNumber),
    onStationSelect: (stationID) => selectOverviewStation(stationID, Boolean(state.selectedTrainNumber)),
    onClear: clearOverviewSelection,
  });
}

function stationTrafficRows(snapshot, stationID) {
  const currentMinute = minuteValue(snapshot.clock?.time) ?? 0;
  return uniqueOverviewServices(snapshot).flatMap((service) => {
    const stop = (service.stops || []).find((item) => item.station_id === stationID);
    if (!stop) return [];
    const time = stop.departure_time || stop.arrival_time;
    const minute = minuteValue(time);
    return [{
      trainNumber: String(service.train_number),
      time: time ? String(time).slice(0, 5) : "–",
      kind: stop.departure_time ? "avg" : "ank",
      sort: minute === null ? 2880 : (minute < currentMinute ? minute + 1440 : minute),
    }];
  }).sort((a, b) => a.sort - b.sort || a.trainNumber.localeCompare(b.trainNumber, "sv", { numeric: true }));
}

function renderStationInspector() {
  const inspector = document.querySelector("#overview-station-inspector");
  const snapshot = state.overviewSnapshot;
  const station = snapshot?.stations?.find((item) => item.id === state.selectedStationID);
  inspector.classList.toggle("hidden", !station);
  if (!station) return;
  const connections = (snapshot.connections || []).filter((connection) =>
    connection.station_a_id === station.id || connection.station_b_id === station.id
  );
  const stationByID = new Map((snapshot.stations || []).map((item) => [item.id, item]));
  const neighbors = connections.map((connection) => {
    const otherID = connection.station_a_id === station.id ? connection.station_b_id : connection.station_a_id;
    return { station: stationByID.get(otherID), connection };
  }).filter((item) => item.station);
  const trains = stationTrafficRows(snapshot, station.id);
  document.querySelector("#station-inspector-name").textContent = station.name;
  document.querySelector("#station-inspector-meta").textContent = `${station.code || "–"} · ${trains.length} tåg · ${connections.length} anslutna sträckor`;
  document.querySelector("#station-inspector-connections").innerHTML = neighbors.length
    ? neighbors.map(({ station: neighbor, connection }) => html`<span>${escapeHTML(neighbor.name)} · ${connection.track_type === "double" ? t("dubbelspår") : t("enkelspår")}</span>`).join("")
    : html`<span>Fristående station</span>`;
  document.querySelector("#station-inspector-trains").innerHTML = trains.length
    ? trains.slice(0, 5).map((train) => html`<li><button type="button" data-train-number="${escapeHTML(train.trainNumber)}"><b>${escapeHTML(train.trainNumber)}</b><span>${escapeHTML(train.kind)} ${escapeHTML(train.time)}</span></button></li>`).join("")
    : html`<li>Inga tåg i tidtabellen.</li>`;
  document.querySelector("#station-inspector-trains").querySelectorAll("button[data-train-number]").forEach((button) => {
    button.addEventListener("click", () => selectOverviewTrain(button.dataset.trainNumber));
  });
}

function updateRuntimeDataViews(snapshot, services) {
  const stations = snapshot.stations || [];
  const connections = snapshot.connections || [];
  const activeConnections = (snapshot.connection_states || []).filter((item) => item.state !== "free").length;
  const activeTrains = (snapshot.train_positions || []).length;
  const clockTime = String(snapshot.clock?.time || "--:--").slice(0, 5);
  const clockState = snapshot.clock?.running ? `${clockTime} · ${Number(snapshot.clock?.speed || 1)}×` : `${clockTime} · stoppad`;


  document.querySelector("#display-card-topology").textContent = `${stations.length} stationer · ${connections.length} sträckor`;
  document.querySelector("#display-card-graph").textContent = `${services.length} tåg · ${snapshot.active_day || "Dagl"}`;
  document.querySelector("#display-card-clock").textContent = clockState;
  document.querySelector("#display-card-dashboard").textContent = `${activeTrains} aktiva tåg · ${activeConnections} upptagna sträckor`;
}



function renderOverview(snapshot) {
  if (!snapshot) return;
  const services = uniqueOverviewServices(snapshot);
  const stations = snapshot.stations || [];
  const mode = snapshot.meet?.default_dispatch_mode === "direct" ? "Direkttrafik" : "Tåganmälan";
  document.querySelector("#overview-meet-name").textContent = snapshot.meet?.name || "TrainMeet Server";
  document.querySelector("#overview-runtime-meta").textContent = `${snapshot.active_day || "Dagl"} · ${mode} · lokal runtime`;
  document.querySelector("#overview-station-meta").textContent = `${stations.length} stationer · ${services.length} tåg`;
  document.querySelector("#overview-clock").textContent = String(snapshot.clock?.time || "--:--").slice(0, 5);
  document.querySelector("#overview-day").textContent = snapshot.active_day || "Dagl";
  document.querySelector("#overview-route-count").textContent = services.length;
  updateRuntimeDataViews(snapshot, services);

  const signature = `${snapshot.publication_id || "unconfigured"}:${snapshot.active_day || ""}:${services.length}:${stations.length}`;
  if (state.overviewDataSignature !== signature) {
    state.overviewDataSignature = signature;
    renderRouteExplorer();
  }
  renderOverviewTopology();
  renderStationInspector();
  renderTraffic(snapshot);
  if (document.querySelector("#overview-timetable").open) renderOverviewGraph(snapshot);
}

document.querySelector("#overview-timetable").addEventListener("toggle", (event) => {
  if (event.target.open) renderOverviewGraph(state.overviewSnapshot);
});

function renderRouteExplorer() {
  const snapshot = state.overviewSnapshot;
  if (!snapshot) return;
  const services = uniqueOverviewServices(snapshot);
  const stationByID = new Map((snapshot.stations || []).map((station) => [station.id, station]));
  const query = overviewRouteSearch.value.trim().toLocaleLowerCase("sv");
  const visibleServices = services.filter((service) =>
    String(service.train_number).toLocaleLowerCase("sv").includes(query)
  );
  if (!state.overviewSelectionInitialized && services.length) {
    state.selectedTrainNumber = String(services[0].train_number);
    state.overviewSelectionInitialized = true;
  } else if (state.selectedTrainNumber !== null && !services.some((service) => String(service.train_number) === state.selectedTrainNumber)) {
    state.selectedTrainNumber = services[0] ? String(services[0].train_number) : null;
  }

  overviewRouteList.innerHTML = visibleServices.length
    ? visibleServices.map((service) => {
        const trainNumber = String(service.train_number);
        return html`<button type="button" data-train-number="${escapeHTML(trainNumber)}" class="route-number${trainNumber === state.selectedTrainNumber ? " active" : ""}">${escapeHTML(trainNumber)}</button>`;
      }).join("")
    : html`<p class="route-empty">Inga tåg hittades.</p>`;

  const selected = services.find((service) => String(service.train_number) === state.selectedTrainNumber);
  const detail = document.querySelector("#overview-route-detail");
  if (!selected) {
    detail.innerHTML = services.length
      ? html`<div class="route-detail-empty">Välj ett tåg i listan, banöversikten eller tågdiagrammet.</div>`
      : html`<div class="route-detail-empty">Tidtabellen saknar tågrutter.</div>`;
  } else {
    const stops = [...(selected.stops || [])].sort((a, b) => Number(a.stop_order) - Number(b.stop_order));
    detail.innerHTML = html`
      <div class="route-detail-heading">
        <h3>${escapeHTML(selected.train_number)}</h3>
        <span>${stops.length} stopp</span>
      </div>
      <ol class="route-stops">
        ${stops.map((stop, index) => {
          const station = stationByID.get(stop.station_id);
          const arrival = stop.arrival_time ? `ank ${String(stop.arrival_time).slice(0, 5)}` : "";
          const departure = stop.departure_time ? `avg ${String(stop.departure_time).slice(0, 5)}` : "";
          const times = [arrival, departure].filter(Boolean).join(" · ") || "tid saknas";
          const positionClass = index === 0 ? " first" : (index === stops.length - 1 ? " last" : "");
          return html`<li class="route-stop${positionClass}${stop.station_id === state.selectedStationID ? " selected" : ""}">
            <i></i>
            <button type="button" data-station-id="${escapeHTML(stop.station_id)}"><b>${escapeHTML(station?.name || stop.station_name || t("Okänd station"))}</b><span>${escapeHTML(times)}</span></button>
          </li>`;
        }).join("")}
      </ol>`;
  }

  const counts = new Map((snapshot.stations || []).map((station) => [station.id, 0]));
  for (const service of services) {
    const visited = new Set((service.stops || []).map((stop) => stop.station_id));
    for (const stationID of visited) counts.set(stationID, (counts.get(stationID) || 0) + 1);
  }
  const stationCounts = [...(snapshot.stations || [])]
    .map((station) => ({ station, count: counts.get(station.id) || 0 }))
    .sort((a, b) => b.count - a.count || a.station.name.localeCompare(b.station.name, "sv"));
  const maximum = Math.max(...stationCounts.map((item) => item.count), 1);
  const selectedRouteStationIDs = new Set((selected?.stops || []).map((stop) => stop.station_id));
  // Byggd med DOM-anrop i stället för innerHTML: ett style="width:N%" i en
  // HTML-sträng är ett inline-attribut, och serverns egen CSP (style-src
  // 'self') avvisar det. Staplarna har alltså aldrig fått sin bredd - felet
  // låg tyst i konsolen. CSSOM (element.style.width) omfattas inte.
  const countsHost = document.querySelector("#overview-station-counts");
  countsHost.replaceChildren(...stationCounts.map(({ station, count }) => {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.stationId = station.id;
    button.className = "station-count-row";
    if (station.id === state.selectedStationID) button.classList.add("selected");
    if (selectedRouteStationIDs.has(station.id)) button.classList.add("on-route");

    const label = document.createElement("div");
    const name = document.createElement("b");
    name.textContent = station.name;
    const total = document.createElement("span");
    total.textContent = String(count);
    label.append(name, total);

    const track = document.createElement("i");
    const fill = document.createElement("span");
    fill.style.width = `${Math.round(count / maximum * 100)}%`;
    track.append(fill);

    button.append(label, track);
    return button;
  }));

  const badge = document.querySelector("#overview-route-badge");
  badge.classList.toggle("hidden", !selected);
  if (selected) badge.textContent = `Tåg ${selected.train_number} · ${(selected.stops || []).length} stopp`;
}

function authorizedFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  // Bind a command to the context already shown, never silently refresh a
  // changed meet and then replay the user's old action against it.
  if (options.method && options.method !== "GET" && typeof options.body === "string" && headers.get("Content-Type") === "application/json") {
    const body = JSON.parse(options.body);
    if (body.meet_generation === undefined && state.serverContext?.selected_meet?.generation !== undefined) {
      body.meet_generation = state.serverContext.selected_meet.generation;
    }
    options = { ...options, body: JSON.stringify(body) };
  }
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  return fetch(path, { ...options, headers, credentials: "same-origin" });
}

function handleConnectionError(error) {
  setConnection("waiting", "Återansluter");
  // v1-simuleringens meddelanderad är borta med den vyn; v2 har en egen.
  const target = document.querySelector("#tmbox-v2-message");
  if (target) setMessage(target, error.message, "error");
  if (!state.authStatus?.authenticated && !["workspaces", "tmbox"].includes(currentMode())) showLogin();
}

async function refreshAuthStatus() {
  const response = await fetch("/v1/auth/status", { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) throw new Error("Serverns åtkomstläge kunde inte läsas");
  state.authStatus = await response.json();
  state.token = null;
  localStorage.removeItem("trainmeet.accessToken");
  // Ett ställe bestämmer om locket visar knappar. Flaggan sattes tidigare i
  // showLogin och openApplication, och missade därmed vägen in via en
  // halvfärdig installation: efter en återställning stod "Tillbaka till
  // träffen" kvar över inloggningsrutan. Uppmätt i en riktig omstart.
  document.body.dataset.signedIn = state.authStatus?.authenticated ? "yes" : "no";
  // The username field is left alone. The application never fills it in -
  // the browser's own password manager may still offer a saved login, which
  // is the user's choice rather than ours.
  configureResetMode();
  return state.authStatus;
}

function configureResetMode() {
  const localFactoryReset = state.authStatus?.at_the_machine === true;
  // Sammanfattningen är det enda som syns när blocket är hopfällt, så den ska
  // säga vilken av de två nollställningarna som gäller den här webbläsaren.
  document.querySelector("#reset-mode-summary").textContent = t(localFactoryReset
    ? "Fabriksåterställ servern"
    : "Nollställ träffdata");
  document.querySelector("#reset-mode-title").textContent = t(localFactoryReset
    ? "Börja om från en helt ren TrainMeet Server"
    : "Börja om utan att förlora administratörsåtkomsten");
  document.querySelector("#reset-mode-description").textContent = t(localFactoryReset
    ? "Tar bort administratör, träffkonfiguration, lokal trafikhistorik, Cloud-koppling och parkopplade enheter. Första installationen öppnas efter omstarten."
    : "Tar bort träffkonfiguration, lokal trafikhistorik, Cloud-koppling och parkopplade enheter. Administratören, servernamnet och din aktiva webbinloggning behålls.");
  factoryResetButton.textContent = t(localFactoryReset
    ? "Fabriksåterställ servern"
    : "Nollställ träffdata");
}

async function refreshSetupStatus() {
  const response = await fetch("/v1/setup", { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) throw new Error("Installationsläget kunde inte läsas");
  state.setupStatus = await response.json();
  return state.setupStatus;
}

function showSetup(installation) {
  clearTimeout(state.snapshotTimer);
  clearTimeout(state.adminTimer);
  login.classList.add("hidden");
  appView.classList.add("hidden");
  setup.classList.remove("hidden");
  const order = ["admin", "server", "central", "finish"];
  const activeIndex = Math.max(0, order.indexOf(installation.step));
  for (const item of document.querySelectorAll("[data-setup-progress]")) {
    const index = order.indexOf(item.dataset.setupProgress);
    item.classList.toggle("active", index === activeIndex);
    item.classList.toggle("done", index < activeIndex);
  }
  for (const form of [setupAdminForm, setupServerForm, setupCentralForm, setupFinishForm]) {
    form.classList.add("hidden");
  }
  const current = {
    admin: setupAdminForm,
    server: setupServerForm,
    central: setupCentralForm,
    finish: setupFinishForm,
  }[installation.step] || setupAdminForm;
  current.classList.remove("hidden");
  if (installation.server_name) {
    document.querySelector("#setup-server-name").value = installation.server_name;
  }
  if (installation.central_url) {
    document.querySelector("#setup-central-url").value = installation.central_url;
    document.querySelector("#runtime-central-url").value = installation.central_url;
  }
  if (installation.runtime?.configured) {
    document.querySelector("#setup-active-day").value = installation.runtime.active_day || "Dagl";
    document.querySelector("#setup-runtime-summary").innerHTML = html`
      <b>${escapeHTML(installation.runtime.meet_name)}</b>
      <span>${Number(installation.runtime.station_count || 0)} stationer · ${Number(installation.runtime.train_count || 0)} tågrörelser</span>
    `;
  }
  setConnection("waiting", t("Installation pågår"));
}

async function showLogin() {
  clearTimeout(state.snapshotTimer);
  clearTimeout(state.adminTimer);
  stopTMBoxV2();
  globalThis.TrainMeetParticipant?.stop();
  document.querySelector("#application-menu").open = false;
  state.authStatus = { ...(state.authStatus || {}), authenticated: false };
  // Flikar och lägesknappar leder ingenstans utan inloggning. De stod kvar
  // bakom inloggningsrutan så länge servern ändå släppte in på maskinen -
  // nu gör den inte det, och då ska de inte se ut som att de går att trycka på.
  document.body.dataset.signedIn = "no";
  setup.classList.add("hidden");
  appView.classList.add("hidden");
  login.classList.remove("hidden");
  loginForm.classList.remove("hidden");
  setConnection("offline", "Inloggning krävs");
}

async function bootstrap() {
  try {
    const installation = await refreshSetupStatus();
    const status = await refreshAuthStatus();
    if (installation.required && !installation.admin_configured) {
      showSetup(installation);
      return;
    }
    if (installation.required && status.authenticated) {
      showSetup(installation);
      return;
    }
    if (status.authenticated) {
      await openApplication();
      return;
    }
    await openApplication();
    if (installation.required) {
      setMessage(
        loginError,
        "Logga in för att fortsätta den påbörjade installationen.",
        "notice",
      );
    }
  } catch (error) {
    setConnection("waiting", "Servern svarar inte");
    setup.classList.add("hidden");
    login.classList.remove("hidden");
    setMessage(loginError, error.message, "error");
  }
}

function setConnection(kind, text) {
  connectionStatus.className = `status ${kind}`;
  const label = connectionStatus.querySelector("b");
  label.dataset.tmText = text;
  label.textContent = t(text);
}

function setMessage(element, text, kind = "") {
  const modalFeedback = element.classList.contains("modal-feedback");
  const contextWarning = element.classList.contains("context-warning");
  element.dataset.tmText = text || "";
  element.textContent = t(text || "");
  element.className = `form-message ${kind}${modalFeedback ? " modal-feedback" : ""}${contextWarning ? " context-warning" : ""}`.trim();
}




function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


const displayKind = location.pathname.startsWith("/display/")
  ? location.pathname.split("/").filter(Boolean).pop()
  : null;

const svgNS = "http://www.w3.org/2000/svg";
let displaySnapshot = null;
let displaySnapshotReceivedAt = null;
let displayPollTimer = null;
let displayRequest = null;
let displayToolbarTimer = null;
let displayTickTimer = null;
let displayClockAnchorSeconds = null;
let displayClockAnchorAt = null;
let displayClockAnchorRunning = null;
let displayClockAnchorSpeed = null;
let swissMinuteKey = null;
let swissMinuteWobbleStartedAt = null;

function svgElement(name, attrs = {}, textValue = null) {
  const element = document.createElementNS(svgNS, name);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
  if (textValue !== null) element.textContent = String(textValue);
  return element;
}

function orderedStations(snapshot) {
  const byID = new Map(snapshot.stations.map((station) => [station.id, station]));
  const explicit = snapshot.display?.graph_station_order || [];
  const result = explicit.map((id) => byID.get(id)).filter(Boolean);
  for (const station of snapshot.stations) if (!result.some((value) => value.id === station.id)) result.push(station);
  return result;
}

function topologyLayout(snapshot) {
  const stations = snapshot.stations || [];
  const stationIDs = new Set(stations.map((station) => station.id));
  const branchIDs = new Set(snapshot.display?.topology_branch_station_ids
    || stations.filter((station) => station.is_topology_branch).map((station) => station.id));
  const adjacency = new Map(stations.map((station) => [station.id, new Set()]));
  const edges = [];
  const addEdge = (from, to, source = null, autonomous = false) => {
    if (!stationIDs.has(from) || !stationIDs.has(to) || from === to) return;
    adjacency.get(from).add(to);
    adjacency.get(to).add(from);
    if (!edges.some((edge) => (edge.from === from && edge.to === to) || (edge.from === to && edge.to === from))) {
      edges.push({ from, to, source, autonomous });
    }
  };
  for (const connection of snapshot.connections || []) {
    addEdge(connection.station_a_id, connection.station_b_id, connection, false);
  }
  for (const link of snapshot.autonomous_links || []) {
    addEdge(link.autonomous_station_id, link.related_station_id, link, true);
  }

  const connectedIDs = stations.map((station) => station.id).filter((id) => adjacency.get(id)?.size);
  if (!connectedIDs.length) {
    const positions = new Map(stations.map((station, index) => [station.id, { x: index * 100, y: 0 }]));
    return topologyBounds(positions, edges);
  }

  const bfsFarthest = (start, excluded) => {
    const parent = new Map([[start, null]]);
    const queue = [start];
    let farthest = start;
    while (queue.length) {
      const node = queue.shift();
      for (const neighbor of adjacency.get(node) || []) {
        if (parent.has(neighbor) || excluded.has(neighbor)) continue;
        parent.set(neighbor, node);
        queue.push(neighbor);
        farthest = neighbor;
      }
    }
    return { farthest, parent };
  };

  const start = connectedIDs.find((id) => !branchIDs.has(id)) || connectedIDs[0];
  const endA = bfsFarthest(start, branchIDs).farthest;
  const secondPass = bfsFarthest(endA, branchIDs);
  const spine = [];
  let current = secondPass.farthest;
  while (current !== null && current !== undefined) {
    spine.push(current);
    current = secondPass.parent.get(current) ?? null;
  }
  spine.reverse();

  const positions = new Map();
  spine.forEach((id, index) => positions.set(id, { x: index * 100, y: 0 }));
  let sideFlip = -1;
  const placeBranch = (id, parentID, x, y, horizontalDirection, verticalDirection) => {
    if (positions.has(id)) return;
    positions.set(id, { x, y });
    const children = [...(adjacency.get(id) || [])].filter((neighbor) => !positions.has(neighbor));
    children.forEach((child, index) => {
      if (index === 0) placeBranch(child, id, x + 100 * horizontalDirection, y, horizontalDirection, verticalDirection);
      else placeBranch(child, id, x, y + 65 * verticalDirection, horizontalDirection, verticalDirection);
    });
  };
  spine.forEach((id, index) => {
    const roots = [...(adjacency.get(id) || [])].filter((neighbor) => !positions.has(neighbor));
    const horizontalDirection = index < spine.length / 2 ? -1 : 1;
    for (const root of roots) {
      const side = sideFlip;
      sideFlip *= -1;
      const junction = positions.get(id);
      placeBranch(root, id, junction.x, junction.y + 65 * side, horizontalDirection, side);
    }
  });
  let isolatedX = Math.max(spine.length, 1) * 100;
  for (const station of stations) {
    if (!positions.has(station.id)) {
      positions.set(station.id, { x: isolatedX, y: 0 });
      isolatedX += 100;
    }
  }
  return topologyBounds(positions, edges);
}

function topologyBounds(sourcePositions, edges) {
  const portrait = innerHeight > innerWidth * 1.2;
  const positions = new Map([...sourcePositions].map(([id, point]) => [
    id,
    portrait ? { x: point.y, y: point.x } : point,
  ]));
  const xs = [...positions.values()].map((point) => point.x);
  const ys = [...positions.values()].map((point) => point.y);
  const minX = Math.min(...xs, 0), maxX = Math.max(...xs, 0);
  const minY = Math.min(...ys, 0), maxY = Math.max(...ys, 0);
  const padding = 50;
  const contentWidth = maxX - minX + padding * 2;
  const contentHeight = maxY - minY + padding * 2;
  const width = Math.max(contentWidth, portrait ? 300 : 700);
  const height = Math.max(contentHeight, portrait ? 700 : 300);
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;
  return {
    positions,
    edges,
    viewBox: `${centerX - width / 2} ${centerY - height / 2} ${width} ${height}`,
  };
}

// A train on the map is a blue tag whose nose points the way the train is
// going, so the direction reads without an arrow character or a legend.
// Size follows the surface: small on Drift, large on a TV.
function trainBadgePath(width, height, nose, radius) {
  const left = -width / 2, right = width / 2, top = -height / 2, bottom = height / 2;
  return `M${left + radius},${top} H${right - nose} L${right},0 L${right - nose},${bottom} H${left + radius} A${radius},${radius} 0 0 1 ${left},${bottom - radius} V${top + radius} A${radius},${radius} 0 0 1 ${left + radius},${top} Z`;
}

// Same tag with the nose at the bottom (mirrored for up), for track that runs
// top to bottom – the phone gets the line in portrait.
function trainBadgePathVertical(width, height, nose, radius) {
  const left = -width / 2, right = width / 2, top = -height / 2, bottom = height / 2;
  return `M${left + radius},${top} H${right - radius} A${radius},${radius} 0 0 1 ${right},${top + radius} V${bottom - radius} A${radius},${radius} 0 0 1 ${right - radius},${bottom} H${nose} L0,${bottom + nose} L${-nose},${bottom} H${left + radius} A${radius},${radius} 0 0 1 ${left},${bottom - radius} V${top + radius} A${radius},${radius} 0 0 1 ${left + radius},${top} Z`;
}

function appendTrainBadge(target, point, trainNumber, direction = 1, options = {}) {
  const label = String(trainNumber);
  const tv = Boolean(options.tv);
  const fontSize = tv ? 28 : 9, nose = tv ? 16 : 6, height = tv ? 44 : 16, radius = tv ? 8 : 4;
  const vertical = options.vertical || 0; // 1 = down, -1 = up, 0 = sideways
  const width = Math.max(tv ? 96 : 30, label.length * fontSize * 0.62 + (tv ? 36 : 14)) + (vertical ? 0 : nose);
  const group = svgElement("g", {
    transform: `translate(${point.x},${point.y})`,
    class: `topology-train${tv ? " topology-train-badge" : ""}${options.selected ? " selected" : ""}${options.dimmed ? " dimmed" : ""}${options.clickable ? " clickable" : ""}`,
    role: options.clickable ? "button" : "img",
    tabindex: options.clickable ? "0" : "-1",
    "aria-label": `Tåg ${label} ${vertical ? (vertical > 0 ? "↓" : "↑") : direction > 0 ? "→" : "←"}`,
  });
  if (options.selected) group.append(svgElement("rect", { x: -width / 2 - 3, y: -height / 2 - 3, width: width + 6, height: height + 6, rx: radius + 3, class: "topology-train-ring" }));
  group.append(vertical
    ? svgElement("path", { d: trainBadgePathVertical(width, height, nose, radius), transform: `scale(1,${vertical})`, class: "topology-train-tag" })
    : svgElement("path", { d: trainBadgePath(width, height, nose, radius), transform: `scale(${direction},1)`, class: "topology-train-tag" }));
  group.append(svgElement("text", { x: vertical ? 0 : -direction * nose / 2, y: tv ? 10 : 3.2, "text-anchor": "middle", class: "train-number" }, label));
  if (options.clickable) {
    const activate = (event) => {
      event.stopPropagation();
      options.onSelect?.(String(trainNumber));
    };
    group.addEventListener("click", activate);
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") activate(event);
    });
  }
  target.append(group);
  return group;
}

function topologyEdgeKey(a, b) {
  return [String(a), String(b)].sort().join("::");
}

// Station names sit centred under their node. That is the right place on a
// straight line, but not where a short branch runs close to the main line
// (two names in one another) or where a track leaves the node downwards (the
// line runs through the name). Only those names move, to the free side of
// their node; a map where nothing collides looks exactly as before. Each move
// strictly lowers the total overlap, so the search always ends.
const TOPOLOGY_LABEL_SIDES = {
  // [horizontal −1 left · 0 centred · 1 right, vertical −1 above · 0 centred · 1 below, preference]
  below: [0, 1, 0], above: [0, -1, 3], right: [1, 0, 4], left: [-1, 0, 5],
  "below-right": [1, 1, 6], "above-right": [1, -1, 7], "below-left": [-1, 1, 8], "above-left": [-1, -1, 9],
};

function placeTopologyLabels(items, segments, viewBox, options = {}) {
  if (!items.length) return;
  const tv = Boolean(options.tv);
  const gap = tv ? 14 : 5;
  const lineGap = 30; // TV: from the name's baseline to the code line's
  const measure = (element) => {
    const size = parseFloat(getComputedStyle(element).fontSize) || (tv ? 30 : 11);
    let width = 0;
    try { width = element.getComputedTextLength(); } catch { width = 0; }
    return { size, width: width > 0 ? width : element.textContent.length * size * 0.58 };
  };
  for (const item of items) {
    item.nameSize = measure(item.name);
    item.codeSize = item.code ? measure(item.code) : null;
    item.below = Number(item.name.getAttribute("y")) - item.point.y;
    item.node = { x1: item.point.x - item.radius - 2, y1: item.point.y - item.radius - 2, x2: item.point.x + item.radius + 2, y2: item.point.y + item.radius + 2 };
  }
  const layout = (item, side) => {
    const [horizontal, vertical, preference] = TOPOLOGY_LABEL_SIDES[side];
    const { x, y } = item.point, radius = item.radius, name = item.nameSize, code = item.codeSize;
    const width = Math.max(name.width, code ? code.width : 0);
    const ascent = name.size * 0.75;
    const height = ascent + (code ? lineGap + code.size * 0.22 : name.size * 0.22);
    const corner = horizontal && vertical ? radius * 0.75 + (tv ? 6 : 2) : 0;
    let baseline;
    // A name beside and below its node keeps the row's baseline when there is room.
    if (vertical > 0) baseline = horizontal ? Math.max(y + corner + ascent, y + item.below) : y + item.below;
    else if (vertical < 0) baseline = (horizontal ? y - corner : y - radius - gap) - height + ascent;
    else baseline = y - height / 2 + ascent;
    const anchor = horizontal > 0 ? "start" : horizontal < 0 ? "end" : "middle";
    const textX = horizontal ? x + horizontal * (corner || radius + gap) : x;
    const x1 = anchor === "start" ? textX : anchor === "end" ? textX - width : textX - width / 2;
    return { side, preference, anchor, textX, baseline, box: { x1, x2: x1 + width, y1: baseline - ascent, y2: baseline - ascent + height } };
  };
  const overlap = (a, b, margin = 0) => Math.max(0, Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1) + margin) * Math.max(0, Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1) + margin);
  const clearance = tv ? 10 : 6; // two names never closer than this
  const inside = (box, p) => p.x >= box.x1 && p.x <= box.x2 && p.y >= box.y1 && p.y <= box.y2;
  const crosses = (box, [a, b]) => {
    if (inside(box, a) || inside(box, b)) return true;
    const side = (p, q, r) => Math.sign((q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x));
    const corners = [{ x: box.x1, y: box.y1 }, { x: box.x2, y: box.y1 }, { x: box.x2, y: box.y2 }, { x: box.x1, y: box.y2 }];
    return corners.some((c, i) => {
      const d = corners[(i + 1) % 4];
      return side(a, b, c) !== side(a, b, d) && side(c, d, a) !== side(c, d, b);
    });
  };
  const [viewX, viewY, viewWidth, viewHeight] = String(viewBox).split(" ").map(Number);
  const placed = new Map(items.map((item) => [item, layout(item, "below")]));
  const penalty = (item, spot) => {
    let cost = spot.preference;
    for (const other of items) {
      if (other === item) continue;
      const labels = overlap(spot.box, placed.get(other).box, clearance);
      const node = overlap(spot.box, other.node);
      if (labels > 0) cost += 1000 + labels;
      if (node > 0) cost += 1000 + node;
    }
    for (const segment of segments) if (crosses(spot.box, segment)) cost += 300;
    if (!options.refit && Number.isFinite(viewWidth)
      && (spot.box.x1 < viewX || spot.box.x2 > viewX + viewWidth || spot.box.y1 < viewY || spot.box.y2 > viewY + viewHeight)) cost += 600;
    return cost;
  };
  for (let round = 0; round < items.length * 4; round += 1) {
    let best = null;
    for (const item of items) {
      const current = penalty(item, placed.get(item));
      if (current < 100) continue;
      for (const side of Object.keys(TOPOLOGY_LABEL_SIDES)) {
        const spot = layout(item, side);
        const gain = current - penalty(item, spot);
        if (gain > 0 && (!best || gain > best.gain)) best = { item, spot, gain };
      }
    }
    if (!best) break;
    placed.set(best.item, best.spot);
  }
  for (const [item, spot] of placed) {
    if (spot.side === "below") continue;
    for (const [element, offset] of [[item.name, 0], [item.code, lineGap]]) {
      if (!element) continue;
      element.setAttribute("x", spot.textX);
      element.setAttribute("y", spot.baseline + offset);
      element.classList.toggle("label-start", spot.anchor === "start");
      element.classList.toggle("label-end", spot.anchor === "end");
    }
  }
}

function renderTopology(snapshot, target = document.querySelector("#topology-svg"), options = {}) {
  if (!target) return;
  let { positions, edges, viewBox } = topologyLayout(snapshot);
  target.classList.toggle("topology-tv", Boolean(options.tv));
  if (options.tv) {
    // Fit the actual nodes, not the editor's padded canvas. Small layouts
    // otherwise collapse to an unreadable cluster in the middle of a TV.
    const points = [...positions.values()], height = options.height || 680;
    const minX = Math.min(...points.map(p=>p.x)), maxX = Math.max(...points.map(p=>p.x));
    const minY = Math.min(...points.map(p=>p.y)), maxY = Math.max(...points.map(p=>p.y));
    const width = maxX-minX, depth = maxY-minY;
    // The line always uses the full width. A low map (the Översikt card) is
    // squeezed vertically rather than shrunk as a whole, which would crowd the
    // names on the main line; the drawing is never stretched upwards.
    const fitDepth = depth ? (height-180)/depth : Infinity;
    const scaleX = points.length > 1 ? (width ? 1480/width : fitDepth) : 1;
    const safeX = Number.isFinite(scaleX) ? scaleX : 1;
    const safeY = Math.min(safeX, fitDepth);
    positions = new Map([...positions].map(([id,p])=>[id,{x:(p.x-(minX+maxX)/2)*safeX+920,y:(p.y-(minY+maxY)/2)*safeY+height/2-30}]));
    viewBox = `0 0 1840 ${height}`;
  }
  target.setAttribute("viewBox", viewBox);
  target.replaceChildren();
  target.onclick = (event) => {
    if (event.target === target) options.onClear?.();
  };
  const stateByID = new Map((snapshot.connection_states || []).map((state) => [state.id, state]));
  const activeStationIDs = new Set((snapshot.train_positions || []).filter((p) => p.status === "station").map((p) => p.station_id));
  const selectedService = serviceForTrain(snapshot, options.selectedTrainNumber);
  const routeStops = [...(selectedService?.stops || [])].sort((a, b) => Number(a.stop_order) - Number(b.stop_order));
  const routeStationIDs = new Set(routeStops.map((stop) => stop.station_id));
  const routeEdgeKeys = new Set(routeStops.slice(1).map((stop, index) => topologyEdgeKey(routeStops[index].station_id, stop.station_id)));
  const stationEdgeKeys = new Set();
  const stationNeighborIDs = new Set(options.selectedStationID ? [options.selectedStationID] : []);
  if (!selectedService && options.selectedStationID) {
    for (const edge of edges) {
      if (edge.from === options.selectedStationID || edge.to === options.selectedStationID) {
        stationEdgeKeys.add(topologyEdgeKey(edge.from, edge.to));
        stationNeighborIDs.add(edge.from);
        stationNeighborIDs.add(edge.to);
      }
    }
  }
  const hasSelection = Boolean(selectedService || options.selectedStationID);
  for (const edge of edges) {
    const from = positions.get(edge.from);
    const to = positions.get(edge.to);
    if (!from || !to) continue;
    const state = stateByID.get(edge.source?.id);
    const active = state && state.state !== "free";
    const key = topologyEdgeKey(edge.from, edge.to);
    const routeHighlighted = routeEdgeKeys.has(key);
    const stationHighlighted = stationEdgeKeys.has(key);
    const dimmed = hasSelection && !routeHighlighted && !stationHighlighted;
    const lineClass = `topology-track${active ? " active" : ""}${routeHighlighted ? " route-highlight" : ""}${stationHighlighted ? " station-highlight" : ""}${dimmed ? " dimmed" : ""}`;
    if (edge.source?.track_type === "double") {
      const dx = to.x - from.x, dy = to.y - from.y, length = Math.hypot(dx, dy) || 1;
      const ox = -dy / length * 2.5, oy = dx / length * 2.5;
      target.append(svgElement("line", { x1: from.x + ox, y1: from.y + oy, x2: to.x + ox, y2: to.y + oy, class: lineClass }));
      target.append(svgElement("line", { x1: from.x - ox, y1: from.y - oy, x2: to.x - ox, y2: to.y - oy, class: lineClass }));
    } else {
      target.append(svgElement("line", { x1: from.x, y1: from.y, x2: to.x, y2: to.y, class: lineClass, "stroke-dasharray": edge.autonomous ? "4 3" : "none" }));
    }
  }
  const labels = [];
  for (const station of snapshot.stations || []) {
    const point = positions.get(station.id);
    if (!point) continue;
    const autonomous = Boolean(station.is_autonomous);
    const radius = autonomous ? 5 : 7;
    const onRoute = routeStationIDs.has(station.id);
    const inNeighborhood = stationNeighborIDs.has(station.id);
    const selected = station.id === options.selectedStationID;
    const dimmed = hasSelection && !onRoute && !inNeighborhood && !selected;
    const stationClickable = Boolean(options.onStationSelect);
    const group = svgElement("g", {
      class: `topology-node${stationClickable ? " clickable" : ""}${onRoute ? " on-route" : ""}${selected ? " selected" : ""}${dimmed ? " dimmed" : ""}`,
      role: stationClickable ? "button" : "img",
      tabindex: stationClickable ? "0" : "-1",
      "aria-label": `${station.name}, ${station.code || "station"}`,
    });
    if (onRoute || selected) group.append(svgElement("circle", { cx: point.x, cy: point.y, r: radius + 5, class: "topology-station-ring" }));
    group.append(svgElement("circle", { cx: point.x, cy: point.y, r: radius + 1, class: "topology-mask" }));
    group.append(svgElement("circle", { cx: point.x, cy: point.y, r: radius, class: `topology-station${autonomous ? " autonomous" : ""}${activeStationIDs.has(station.id) ? " active" : ""}${onRoute || selected ? " highlighted" : ""}` }));
    const name = svgElement("text", { x: point.x, y: point.y + (options.tv ? 40 : autonomous ? 16 : 20), class: "topology-name", "font-style": autonomous ? "italic" : "normal" }, station.name);
    group.append(name);
    let code = null;
    if (options.tv) {
      const count = (snapshot.train_positions || []).filter(p=>p.station_id===station.id && !p.connection_id).length;
      code = svgElement("text", {x:point.x,y:point.y+70,class:"topology-code"}, `${station.code || ""} · ${count} ${t("tåg")}`);
      group.append(code);
    }
    labels.push({ point, radius, name, code });
    const activate = (event) => {
      event.stopPropagation();
      options.onStationSelect?.(station.id);
    };
    if (stationClickable) {
      group.addEventListener("click", activate);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") activate(event);
      });
    }
    target.append(group);
  }
  placeTopologyLabels(labels, edges.map((edge) => [positions.get(edge.from), positions.get(edge.to)]).filter(([from, to]) => from && to), viewBox, options);
  for (const position of snapshot.train_positions || []) {
    let point = null;
    if (position.status === "station") point = positions.get(position.station_id);
    else {
      const from = positions.get(position.from_station_id), to = positions.get(position.to_station_id);
      if (from && to) point = { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
    }
    if (!point) continue;
    const fromPoint = positions.get(position.from_station_id), toPoint = positions.get(position.to_station_id);
    const dx = (toPoint?.x || 0) - (fromPoint?.x || 0), dy = (toPoint?.y || 0) - (fromPoint?.y || 0);
    const vertical = fromPoint && toPoint && Math.abs(dy) > Math.abs(dx) * 1.2 ? Math.sign(dy) : 0;
    const direction = fromPoint && toPoint ? (dx >= 0 ? 1 : -1) : 1;
    appendTrainBadge(target, { x: point.x + (vertical ? (options.tv ? 0 : 0) : 0), y: point.y - (vertical ? 0 : options.tv ? 28 : 15) }, position.train_number, direction, {
      tv: Boolean(options.tv),
      vertical,
      selected: String(position.train_number) === String(options.selectedTrainNumber),
      dimmed: Boolean(selectedService && String(position.train_number) !== String(options.selectedTrainNumber)),
      clickable: Boolean(options.onTrainSelect),
      onSelect: options.onTrainSelect,
    });
  }
  if (selectedService && options.showBadge !== false) {
    const [boxX, boxY, boxWidth, boxHeight] = viewBox.split(" ").map(Number);
    const label = `Tåg ${selectedService.train_number} · ${routeStops.length} stopp`;
    const badgeWidth = Math.max(118, label.length * 6.5 + 24);
    const badge = svgElement("g", { class: "topology-route-badge", transform: `translate(${boxX + boxWidth / 2},${boxY + boxHeight - 24})` });
    badge.append(svgElement("rect", { x: -badgeWidth / 2, y: -13, width: badgeWidth, height: 26, rx: 13 }));
    badge.append(svgElement("text", { y: 4, "text-anchor": "middle" }, label));
    target.append(badge);
  }
}

function minuteValue(value) {
  const match = String(value || "").match(/^(\d{1,2}):(\d{2})/);
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

function graphServices(snapshot) {
  if (snapshot.services?.length) return snapshot.services;
  const groups = new Map();
  for (const route of snapshot.routes || []) {
    const id = route.service_id || route.train_number;
    if (!groups.has(id)) groups.set(id, { id, train_number: route.train_number, stops: [] });
    groups.get(id).stops.push({ ...route, service_minute: minuteValue(route.departure_time || route.arrival_time) });
  }
  return [...groups.values()];
}

const trainPalette = [
  "hsl(220 70% 55%)", "hsl(350 70% 55%)", "hsl(140 60% 40%)", "hsl(30 80% 50%)",
  "hsl(270 60% 55%)", "hsl(180 60% 40%)", "hsl(45 90% 50%)", "hsl(0 0% 50%)",
];

let graphLastCenteredMinute = null;
let graphLastCenteredSelection = null;
let overviewGraphLastCenteredMinute = null;

function servicePoints(service, stationIndex) {
  const points = [];
  for (const stop of [...(service.stops || [])].sort((a, b) => a.stop_order - b.stop_order)) {
    const index = stationIndex.get(stop.station_id);
    if (index === undefined) continue;
    const offset = Number(stop.service_day_offset || 0) * 1440;
    let arrival = minuteValue(stop.arrival_time);
    let departure = minuteValue(stop.departure_time);
    if (arrival !== null) arrival += offset;
    if (departure !== null) {
      departure += offset;
      if (arrival !== null && departure < arrival) departure += 1440;
    }
    if (arrival !== null) points.push({ minute: arrival, station: index });
    if (departure !== null && departure !== arrival) points.push({ minute: departure, station: index });
    if (arrival === null && departure === null && Number.isFinite(Number(stop.service_minute))) {
      points.push({ minute: Number(stop.service_minute), station: index });
    }
  }
  return points;
}

function updateOverviewGraphSelection() {
  const active = state.hoveredOverviewTrainNumber || state.selectedTrainNumber;
  document.querySelectorAll("#overview-graph .overview-train-group").forEach((group) => {
    const selected = group.dataset.trainNumber === state.selectedTrainNumber;
    group.classList.toggle("selected", selected);
    group.classList.toggle("dimmed", Boolean(active && group.dataset.trainNumber !== active));
  });
}

function renderOverviewGraph(snapshot) {
  if (!snapshot) return;
  const svg = document.querySelector("#overview-graph");
  const canvas = document.querySelector("#overview-graph-canvas");
  const stationLabels = document.querySelector("#overview-graph-station-labels");
  const stationOverlay = document.querySelector(".overview-graph-stations");
  if (!svg || !canvas || !stationLabels || !stationOverlay) return;
  const stations = orderedStations(snapshot);
  const stationIndex = new Map(stations.map((station, index) => [station.id, index]));
  const lines = uniqueOverviewServices(snapshot).map((service, index) => ({
    service,
    points: servicePoints(service, stationIndex),
    color: trainPalette[index % trainPalette.length],
  })).filter((line) => line.points.length >= 2);
  const minutes = lines.flatMap((line) => line.points.map((point) => point.minute));
  const minMinute = minutes.length ? Math.floor(Math.min(...minutes) / 60) * 60 : 0;
  const maxMinute = minutes.length ? Math.max(minMinute + 60, Math.ceil(Math.max(...minutes) / 60) * 60) : 24 * 60;
  const left = 60, right = 16, top = 22, bottom = 28, stationStep = 26;
  const width = Math.max(1200, left + (maxMinute - minMinute) * 2.2 + right);
  const height = top + Math.max(stations.length - 1, 1) * stationStep + bottom;
  const x = (minute) => left + (minute - minMinute) / (maxMinute - minMinute) * (width - left - right);
  const y = (index) => top + index * stationStep;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  stationOverlay.style.height = `${height}px`;
  stationOverlay.style.marginTop = `-${height}px`;
  stationLabels.setAttribute("viewBox", `0 0 ${left} ${height}`);
  stationLabels.setAttribute("width", left);
  stationLabels.setAttribute("height", height);
  svg.replaceChildren();
  svg.onclick = (event) => {
    if (event.target === svg) clearOverviewSelection();
  };
  stationLabels.replaceChildren();
  stationLabels.append(svgElement("rect", { x: 0, y: 0, width: left, height, class: "overview-graph-label-bg" }));
  for (let minute = minMinute; minute <= maxMinute; minute += 60) {
    svg.append(svgElement("line", { x1: x(minute), y1: top - 5, x2: x(minute), y2: height - bottom + 3, class: "overview-graph-grid" }));
    svg.append(svgElement("text", { x: x(minute), y: height - 6, "text-anchor": "middle", class: "overview-graph-time" }, `${String(Math.floor(minute / 60) % 24).padStart(2, "0")}:00`));
  }
  stations.forEach((station, index) => {
    svg.append(svgElement("line", { x1: left, y1: y(index), x2: width - right, y2: y(index), class: "overview-graph-axis" }));
    stationLabels.append(svgElement("text", { x: left - 9, y: y(index) + 4, "text-anchor": "end", class: "overview-graph-station" }, station.code));
  });
  for (const { service, points: rawPoints, color } of lines) {
    const trainNumber = String(service.train_number);
    const points = rawPoints.map((point) => ({ x: x(point.minute), y: y(point.station) }));
    const pointText = points.map((point) => `${point.x},${point.y}`).join(" ");
    const group = svgElement("g", {
      class: "overview-train-group",
      "data-train-number": trainNumber,
      role: "button",
      tabindex: "0",
      "aria-label": `Tåg ${trainNumber}`,
    });
    group.dataset.trainNumber = trainNumber;
    group.append(svgElement("polyline", { points: pointText, class: "overview-train-line", stroke: color }));
    group.append(svgElement("polyline", { points: pointText, class: "overview-train-hit" }));
    const first = points[0], second = points[1];
    const labelX = first.x + (second.x - first.x) * .22;
    const labelY = first.y + (second.y - first.y) * .22;
    let angle = Math.atan2(second.y - first.y, second.x - first.x) * 180 / Math.PI;
    if (angle > 90) angle -= 180;
    if (angle < -90) angle += 180;
    group.append(svgElement("text", { x: labelX, y: labelY - 3, transform: `rotate(${angle} ${labelX} ${labelY})`, fill: color, class: "overview-train-label" }, trainNumber));
    const activate = (event) => {
      event.stopPropagation();
      selectOverviewTrain(trainNumber);
    };
    group.addEventListener("mouseenter", () => { state.hoveredOverviewTrainNumber = trainNumber; updateOverviewGraphSelection(); });
    group.addEventListener("mouseleave", () => { state.hoveredOverviewTrainNumber = null; updateOverviewGraphSelection(); });
    group.addEventListener("click", activate);
    group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") activate(event); });
    svg.append(group);
  }
  let current = minuteValue(snapshot.clock?.time);
  if (current !== null && current < minMinute && current + 1440 <= maxMinute) current += 1440;
  if (current !== null && current >= minMinute && current <= maxMinute) {
    const currentX = x(current);
    svg.append(svgElement("line", { x1: currentX, y1: top - 7, x2: currentX, y2: height - bottom + 3, class: "overview-graph-now" }));
    svg.append(svgElement("circle", { cx: currentX, cy: top - 7, r: 4, fill: "#ef4444" }));
    if (overviewGraphLastCenteredMinute === null || Math.abs(current - overviewGraphLastCenteredMinute) >= 5) {
      const scroller = document.querySelector("#overview-graph-scroll");
      scroller.scrollTo({ left: Math.max(0, currentX - scroller.clientWidth / 2), behavior: overviewGraphLastCenteredMinute === null ? "auto" : "smooth" });
      overviewGraphLastCenteredMinute = current;
    }
  }
  updateOverviewGraphSelection();
}

function updateDisplayGraphSelection() {
  const active = state.displayHoveredTrainNumber || state.displaySelectedTrainNumber;
  document.querySelectorAll("#graph-svg .graph-train-group").forEach((group) => {
    const selected = group.dataset.trainNumber === state.displaySelectedTrainNumber;
    group.classList.toggle("selected", selected);
    group.classList.toggle("dimmed", Boolean(active && group.dataset.trainNumber !== active));
  });
}

function renderDisplaySelection(snapshot) {
  const panel = document.querySelector("#display-selection");
  if (!panel) return;
  const service = serviceForTrain(snapshot, state.displaySelectedTrainNumber);
  const station = (snapshot.stations || []).find((item) => item.id === state.displaySelectedStationID);
  panel.classList.toggle("hidden", !service && !station);
  if (service) {
    const stops = [...(service.stops || [])].sort((a, b) => Number(a.stop_order) - Number(b.stop_order));
    panel.innerHTML = html`<p>TÅG</p><b>${escapeHTML(service.train_number)}</b><span>${stops.length} stopp</span><small>${stops.map((stop) => escapeHTML((snapshot.stations || []).find((item) => item.id === stop.station_id)?.name || "?")).join(" → ")}</small>`;
  } else if (station) {
    const rows = stationTrafficRows(snapshot, station.id);
    const connected = (snapshot.connections || []).filter((connection) => connection.station_a_id === station.id || connection.station_b_id === station.id).length;
    panel.innerHTML = html`<p>STATION</p><b>${escapeHTML(station.name)}</b><span>${escapeHTML(station.code || "–")} · ${rows.length} tåg · ${connected} sträckor</span><small>${rows.slice(0, 4).map((row) => `${escapeHTML(row.trainNumber)} ${escapeHTML(row.kind)} ${escapeHTML(row.time)}`).join(" · ") || "Inga tidtabellslag"}</small>`;
  }
}

function renderGraph(snapshot) {
  const svg = document.querySelector("#graph-svg");
  const width = 1840, height = 850, left = 270, top = 65, bottom = 50;
  const now = currentClockSeconds(snapshot) / 60;
  const min = now - 60, max = now + 120;
  const stations = orderedStations(snapshot);
  const stationIndex = new Map(stations.map((s, i) => [s.id, i]));
  const x = minute => left + (minute - min) / (max - min) * (width - left - 30);
  const y = i => top + i * (height - top - bottom) / Math.max(1, stations.length - 1);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`); svg.removeAttribute("width"); svg.removeAttribute("height"); svg.replaceChildren();
  const defs = svgElement("defs"), clip = svgElement("clipPath", {id: "screen-graph-clip"});
  clip.append(svgElement("rect", {x: left, y: top - 24, width: width - left, height: height - top - bottom + 48})); defs.append(clip); svg.append(defs);
  for (let minute = Math.ceil(min / 30) * 30; minute <= max; minute += 30) {
    svg.append(svgElement("line", {x1:x(minute), x2:x(minute), y1:top - 24, y2:height - bottom, class:"graph-grid", "stroke-dasharray":minute%60?"6 8":"none"}));
    const normalized = (Math.floor(minute) % 1440 + 1440) % 1440;
    svg.append(svgElement("text", {x:x(minute), y:height - 12, "text-anchor":"middle", class:"sc-graph-label"}, `${String(Math.floor(normalized/60)).padStart(2,"0")}:${String(normalized%60).padStart(2,"0")}`));
  }
  stations.forEach((station, i) => {
    svg.append(svgElement("line", {x1:left, x2:width, y1:y(i), y2:y(i), class:"graph-grid"}));
    svg.append(svgElement("text", {x:10, y:y(i)-7, class:"sc-graph-label"}, station.name));
    svg.append(svgElement("text", {x:10, y:y(i)+24, class:"sc-graph-code"}, station.code || ""));
  });
  const trains = svgElement("g", {"clip-path":"url(#screen-graph-clip)"});
  const active = new Set((snapshot.train_positions || []).filter(p=>p.connection_id).map(p => String(p.train_number)));
  for (const service of graphServices(snapshot)) {
    const points = servicePoints({...service, stops:[...service.stops].sort((a,b)=>a.stop_order-b.stop_order)}, stationIndex);
    if (points.length < 2) continue;
    // Choose the daily occurrence intersecting this rolling window. This also
    // keeps a 23:55–00:15 train continuous when the clock crosses midnight.
    const centre = (points[0].minute + points.at(-1).minute) / 2;
    const shift = Math.round((now - centre) / 1440) * 1440;
    if (points.at(-1).minute + shift < min || points[0].minute + shift > max) continue;
    const colour = active.has(String(service.train_number)) ? "#7fa3ea" : "#737373";
    const group = svgElement("g",{class:"graph-train-group",role:"button",tabindex:0,"aria-label":`Tåg ${service.train_number}`});
    group.dataset.trainNumber=String(service.train_number);
    const line = points.map(p=>`${x(p.minute+shift)},${y(p.station)}`).join(" ");
    group.append(svgElement("polyline", {points:line, fill:"none", stroke:colour, "stroke-width":active.has(String(service.train_number))?6:3}));
    group.append(svgElement("polyline",{points:line,fill:"none",stroke:"transparent","stroke-width":20}));
    const select=()=>{state.displaySelectedTrainNumber=state.displaySelectedTrainNumber===String(service.train_number)?null:String(service.train_number); document.querySelector("#display-train-select").value=state.displaySelectedTrainNumber||"";updateDisplayGraphSelection();renderDisplaySelection(snapshot);};
    group.addEventListener("click",select);group.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();select();}});
    const visible = points.find(p=>p.minute+shift >= min && p.minute+shift <= max) || points[0];
    group.append(svgElement("text", {x:Math.max(left+8,x(visible.minute+shift)+8), y:y(visible.station)+(visible.station===0?32:-10), fill:colour, "font-size":26}, service.train_number));
    trains.append(group);
  }
  svg.append(trains, svgElement("line", {x1:x(now), x2:x(now), y1:top-24, y2:height-bottom, stroke:"#f2c230", "stroke-width":3}));
  svg.append(svgElement("text",{x:x(now),y:top-35,"text-anchor":"middle",fill:"#f2c230","font-size":28},currentClockTime(snapshot).slice(0,5)));
  updateDisplayGraphSelection();
}

function renderScrollableGraph(snapshot) {
  const svg = document.querySelector("#graph-svg");
  const canvas = document.querySelector("#graph-canvas");
  const stationOverlay = document.querySelector("#graph-station-overlay");
  const stationLabels = document.querySelector("#graph-station-labels");
  const timeOverlay = document.querySelector("#graph-time-overlay");
  const timeLabels = document.querySelector("#graph-time-labels");
  const stations = orderedStations(snapshot);
  const stationIndex = new Map(stations.map((station, index) => [station.id, index]));
  const services = graphServices(snapshot).map((service) => ({ ...service, stops: [...service.stops].sort((a, b) => a.stop_order - b.stop_order) }));
  const lines = services.map((service, index) => ({
    service,
    points: servicePoints(service, stationIndex),
    color: trainPalette[index % trainPalette.length],
  })).filter((line) => line.points.length >= 2);
  const minutes = lines.flatMap((line) => line.points.map((point) => point.minute));
  const minMinute = minutes.length ? Math.floor(Math.min(...minutes) / 60) * 60 : 0;
  const maxMinute = minutes.length ? Math.max(minMinute + 60, Math.ceil(Math.max(...minutes) / 60) * 60) : 24 * 60;
  const left = 70, right = 20, top = 30, bottom = 35;
  const width = Math.max(1000, left + (maxMinute - minMinute) * 5 + right);
  // Never fall below the CSS floor for .graph-canvas > .display-visual. A
  // viewBox smaller than the rendered box makes the SVG scale and centre its
  // own contents, which silently stretched the timeline off its minute grid.
  const height = Math.max(600, innerHeight - 50, top + Math.max(stations.length, 1) * 60 + bottom);
  const x = (minute) => left + (minute - minMinute) / (maxMinute - minMinute) * (width - left - right);
  const y = (index) => top + index * 60;
  let selectedStartX = null;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  stationOverlay.style.height = `${height}px`;
  stationOverlay.style.marginTop = `-${height}px`;
  stationLabels.setAttribute("viewBox", `0 0 ${left} ${height}`);
  stationLabels.setAttribute("width", left);
  stationLabels.setAttribute("height", height);
  timeOverlay.style.width = `${width}px`;
  timeOverlay.style.height = `${bottom}px`;
  timeOverlay.style.marginTop = `-${bottom}px`;
  timeLabels.setAttribute("viewBox", `0 0 ${width} ${bottom}`);
  timeLabels.setAttribute("width", width);
  timeLabels.setAttribute("height", bottom);
  svg.replaceChildren();
  stationLabels.replaceChildren();
  timeLabels.replaceChildren();
  stationLabels.append(svgElement("rect", { x: 0, y: 0, width: left, height, class: "graph-station-label-bg" }));
  timeLabels.append(svgElement("rect", { x: 0, y: 0, width, height: bottom, class: "graph-time-label-bg" }));
  for (let minute = minMinute + 30; minute < maxMinute; minute += 60) {
    svg.append(svgElement("line", { x1: x(minute), y1: top - 5, x2: x(minute), y2: height - bottom, class: "graph-grid-half" }));
  }
  for (let minute = minMinute; minute <= maxMinute; minute += 60) {
    const lineX = x(minute);
    svg.append(svgElement("line", { x1: lineX, y1: top - 5, x2: lineX, y2: height - bottom, class: "graph-grid" }));
    // Drawn in the pinned overlay, whose x axis matches the graph's own.
    timeLabels.append(svgElement("text", { x: lineX, y: 18, "text-anchor": "middle", class: "graph-label" }, `${String(Math.floor(minute / 60) % 24).padStart(2, "0")}:00`));
  }
  stations.forEach((station, index) => {
    const lineY = y(index);
    svg.append(svgElement("line", { x1: left - 5, y1: lineY, x2: width - right, y2: lineY, class: "graph-axis" }));
    stationLabels.append(svgElement("text", { x: left - 12, y: lineY + 5, "text-anchor": "end", class: "graph-station-label" }, station.code));
  });
  for (const { service, points: rawPoints, color } of lines) {
    const points = rawPoints.map((point) => ({ x: x(point.minute), y: y(point.station) }));
    const trainNumber = String(service.train_number);
    const pointText = points.map((point) => `${point.x},${point.y}`).join(" ");
    if (trainNumber === state.displaySelectedTrainNumber) selectedStartX = points[0]?.x ?? null;
    const trainGroup = svgElement("g", {
      class: "graph-train-group",
      "data-train-number": trainNumber,
      role: "button",
      tabindex: "0",
      "aria-label": `Tåg ${trainNumber}`,
    });
    trainGroup.dataset.trainNumber = trainNumber;
    trainGroup.append(svgElement("polyline", { points: pointText, class: "graph-train-line", stroke: color }));
    trainGroup.append(svgElement("polyline", { points: pointText, class: "graph-train-hit" }));
    const first = points[0], second = points[1];
    const labelX = first.x + (second.x - first.x) * .2;
    const labelY = first.y + (second.y - first.y) * .2;
    let angle = Math.atan2(second.y - first.y, second.x - first.x) * 180 / Math.PI;
    if (angle > 90) angle -= 180;
    if (angle < -90) angle += 180;
    const label = String(service.train_number);
    const labelGroup = svgElement("g", { transform: `translate(${labelX},${labelY}) rotate(${angle})` });
    labelGroup.append(svgElement("rect", { x: -(label.length * 6 + 4) / 2, y: -12, width: label.length * 6 + 4, height: 12, rx: 2, class: "graph-label-bg" }));
    labelGroup.append(svgElement("text", { x: 0, y: -2, "text-anchor": "middle", fill: color, class: "graph-train-label" }, label));
    trainGroup.append(labelGroup);
    const activate = (event) => {
      event.stopPropagation();
      state.displaySelectedTrainNumber = state.displaySelectedTrainNumber === trainNumber ? null : trainNumber;
      state.displaySelectedStationID = null;
      const selector = document.querySelector("#display-train-select");
      if (selector) selector.value = state.displaySelectedTrainNumber || "";
      updateDisplayGraphSelection();
      renderDisplaySelection(snapshot);
    };
    trainGroup.addEventListener("mouseenter", () => { state.displayHoveredTrainNumber = trainNumber; updateDisplayGraphSelection(); });
    trainGroup.addEventListener("mouseleave", () => { state.displayHoveredTrainNumber = null; updateDisplayGraphSelection(); });
    trainGroup.addEventListener("click", activate);
    trainGroup.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") activate(event); });
    svg.append(trainGroup);
  }
  let current = minuteValue(snapshot.clock?.time);
  if (current !== null && current < minMinute && current + 1440 <= maxMinute) current += 1440;
  if (current !== null && current >= minMinute && current <= maxMinute) {
    const currentX = x(current);
    svg.append(svgElement("line", { x1: currentX, y1: top - 8, x2: currentX, y2: height - bottom, class: "graph-now" }));
    svg.append(svgElement("circle", { cx: currentX, cy: top - 8, r: 5, fill: "#ef4444" }));
    if (graphLastCenteredMinute === null || Math.abs(current - graphLastCenteredMinute) >= 5) {
      const scroller = document.querySelector("#graph-scroll");
      scroller.scrollTo({ left: Math.max(0, currentX - scroller.clientWidth / 2), behavior: graphLastCenteredMinute === null ? "auto" : "smooth" });
      graphLastCenteredMinute = current;
    }
  }
  if (selectedStartX !== null && graphLastCenteredSelection !== state.displaySelectedTrainNumber) {
    const scroller = document.querySelector("#graph-scroll");
    scroller.scrollTo({ left: Math.max(0, selectedStartX - scroller.clientWidth / 2), behavior: "smooth" });
    graphLastCenteredSelection = state.displaySelectedTrainNumber;
  }
  if (!state.displaySelectedTrainNumber) graphLastCenteredSelection = null;
  updateDisplayGraphSelection();
  renderDisplaySelection(snapshot);
}

const clockStyleConfig = {
  analog: { hourMarkerWidth: 3, hourMarkerLength: 10, minuteMarkerWidth: 1, minuteMarkerLength: 4, hourHandWidth: 5, hourHandLength: 50, minuteHandWidth: 3, minuteHandLength: 75, secondHandColor: "#7fa3ea", secondHandWidth: 1, secondHandLength: 80, secondBallRadius: 0, hasNumbers: true, centerDotRadius: 3, bezelWidth: 1 },
  stationsur: { hourMarkerWidth: 4, hourMarkerLength: 14, minuteMarkerWidth: 1.5, minuteMarkerLength: 6, hourHandWidth: 7, hourHandLength: 52, minuteHandWidth: 5, minuteHandLength: 76, secondHandColor: "#2256c3", secondHandWidth: 1.5, secondHandLength: 78, secondBallRadius: 0, hasNumbers: false, centerDotRadius: 4, bezelWidth: 3 },
  swiss: { hourMarkerWidth: 6, hourMarkerLength: 18, minuteMarkerWidth: 2, minuteMarkerLength: 8, hourHandWidth: 8, hourHandLength: 55, minuteHandWidth: 6, minuteHandLength: 78, secondHandColor: "#e2000a", secondHandWidth: 2, secondHandLength: 70, secondBallRadius: 7, secondBallOffset: 62, hasNumbers: false, centerDotRadius: 5, bezelWidth: 4 },
  swedish: { hourMarkerWidth: 4, hourMarkerLength: 14, minuteMarkerWidth: 1.5, minuteMarkerLength: 6, hourHandWidth: 7, hourHandLength: 52, minuteHandWidth: 5, minuteHandLength: 76, secondHandColor: "#1a5276", secondHandWidth: 1.5, secondHandLength: 72, secondBallRadius: 0, secondBallOffset: 0, hasNumbers: true, centerDotRadius: 4, bezelWidth: 3 },
  norwegian: { hourMarkerWidth: 5, hourMarkerLength: 16, minuteMarkerWidth: 1.5, minuteMarkerLength: 7, hourHandWidth: 7, hourHandLength: 50, minuteHandWidth: 5, minuteHandLength: 75, secondHandColor: "#ba2025", secondHandWidth: 1.5, secondHandLength: 68, secondBallRadius: 5, secondBallOffset: 60, hasNumbers: false, centerDotRadius: 5, bezelWidth: 5 },
  danish: { hourMarkerWidth: 5, hourMarkerLength: 15, minuteMarkerWidth: 2, minuteMarkerLength: 6, hourHandWidth: 7, hourHandLength: 52, minuteHandWidth: 5, minuteHandLength: 76, secondHandColor: "#c1272d", secondHandWidth: 1.5, secondHandLength: 70, secondBallRadius: 4, secondBallOffset: 62, hasNumbers: false, centerDotRadius: 5, bezelWidth: 4 },
  german: { hourMarkerWidth: 5, hourMarkerLength: 16, minuteMarkerWidth: 1.5, minuteMarkerLength: 7, hourHandWidth: 7, hourHandLength: 50, minuteHandWidth: 5, minuteHandLength: 74, secondHandColor: "#e30613", secondHandWidth: 1.5, secondHandLength: 68, secondBallRadius: 0, secondBallOffset: 0, hasNumbers: true, centerDotRadius: 4, bezelWidth: 4 },
  finnish: { hourMarkerWidth: 3, hourMarkerLength: 14, minuteMarkerWidth: 1, minuteMarkerLength: 5, hourHandWidth: 6, hourHandLength: 48, minuteHandWidth: 4, minuteHandLength: 74, secondHandColor: "#003580", secondHandWidth: 1, secondHandLength: 70, secondBallRadius: 0, secondBallOffset: 0, hasNumbers: false, centerDotRadius: 3, bezelWidth: 3 },
  polish: { hourMarkerWidth: 5, hourMarkerLength: 16, minuteMarkerWidth: 2, minuteMarkerLength: 7, hourHandWidth: 7, hourHandLength: 52, minuteHandWidth: 5, minuteHandLength: 76, secondHandColor: "#d4213d", secondHandWidth: 1.5, secondHandLength: 68, secondBallRadius: 3, secondBallOffset: 60, hasNumbers: true, centerDotRadius: 5, bezelWidth: 4 },
  dutch: { hourMarkerWidth: 4, hourMarkerLength: 14, minuteMarkerWidth: 1.5, minuteMarkerLength: 6, hourHandWidth: 6, hourHandLength: 50, minuteHandWidth: 5, minuteHandLength: 76, secondHandColor: "#ffc917", secondHandWidth: 2, secondHandLength: 70, secondBallRadius: 4, secondBallOffset: 62, hasNumbers: false, centerDotRadius: 4, bezelWidth: 3 },
  french: { hourMarkerWidth: 5, hourMarkerLength: 16, minuteMarkerWidth: 1.5, minuteMarkerLength: 7, hourHandWidth: 7, hourHandLength: 50, minuteHandWidth: 5, minuteHandLength: 74, secondHandColor: "#1a237e", secondHandWidth: 1.5, secondHandLength: 68, secondBallRadius: 0, secondBallOffset: 0, hasNumbers: true, centerDotRadius: 5, bezelWidth: 5 },
  italian: { hourMarkerWidth: 5, hourMarkerLength: 15, minuteMarkerWidth: 1.5, minuteMarkerLength: 6, hourHandWidth: 7, hourHandLength: 52, minuteHandWidth: 5, minuteHandLength: 76, secondHandColor: "#006633", secondHandWidth: 1.5, secondHandLength: 70, secondBallRadius: 4, secondBallOffset: 62, hasNumbers: false, centerDotRadius: 5, bezelWidth: 4 },
  american: { hourMarkerWidth: 4, hourMarkerLength: 14, minuteMarkerWidth: 1.5, minuteMarkerLength: 6, hourHandWidth: 7, hourHandLength: 52, minuteHandWidth: 5, minuteHandLength: 76, secondHandColor: "#c8102e", secondHandWidth: 1.5, secondHandLength: 72, secondBallRadius: 0, secondBallOffset: 0, hasNumbers: true, centerDotRadius: 4, bezelWidth: 4 },
};

const clockStyleLabels = {
  analog: "Analog", stationsur: "Stationsur",
  swiss: "Schweizisk (SBB)", swedish: "Svensk (SJ)", norwegian: "Norsk (NSB)",
  danish: "Dansk (DSB)", german: "Tysk (DB)", finnish: "Finsk (VR)",
  polish: "Polsk (PKP)", dutch: "Nederländsk (NS)", french: "Fransk (SNCF)",
  italian: "Italiensk (FS)", american: "Amerikansk", digital: "Digital",
};

function clockSVG(style, darkBackground, showSeconds, stopped) {
  const config = clockStyleConfig[style] || clockStyleConfig.swiss;
  const faceColor = darkBackground ? "#1a1a1a" : "#fff";
  const handColor = darkBackground ? "#e0e0e0" : "#1a1a1a";
  const markerColor = darkBackground ? "#d0d0d0" : "#1a1a1a";
  const bezelColor = darkBackground ? "#444" : "#333";
  const numberColor = darkBackground ? "#ccc" : "#333";
  const marks = Array.from({ length: 60 }, (_, index) => {
    const major = index % 5 === 0;
    const length = major ? config.hourMarkerLength : config.minuteMarkerLength;
    const width = major ? config.hourMarkerWidth : config.minuteMarkerWidth;
    const start = 8 + config.bezelWidth;
    return html`<line x1="100" y1="${start}" x2="100" y2="${start + length}" stroke="${markerColor}" stroke-width="${width}" transform="rotate(${index * 6} 100 100)"/>`;
  }).join("");
  const numbers = config.hasNumbers ? Array.from({ length: 12 }, (_, index) => {
    const value = index === 0 ? 12 : index;
    const angle = (index * 30 - 90) * Math.PI / 180;
    return html`<text x="${100 + 68 * Math.cos(angle)}" y="${100 + 68 * Math.sin(angle)}" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="700" fill="${numberColor}" class="clock-numeral">${value}</text>`;
  }).join("") : "";
  const secondHand = showSeconds ? html`<g data-clock-hand="second" transform="rotate(0 100 100)">
    <line x1="100" y1="118" x2="100" y2="${100 - config.secondHandLength}" stroke="${config.secondHandColor}" stroke-width="${config.secondHandWidth}" stroke-linecap="round"/>
    ${config.secondBallRadius > 0 ? html`<circle cx="100" cy="${100 - config.secondBallOffset}" r="${config.secondBallRadius}" fill="${config.secondHandColor}"/>` : ""}
  </g>` : "";
  return html`<svg class="clock-face${stopped ? " stopped" : ""}" viewBox="0 0 200 200">
    <circle cx="100" cy="100" r="96" fill="none" stroke="${bezelColor}" stroke-width="${config.bezelWidth}"/>
    <circle cx="100" cy="100" r="${94 - config.bezelWidth / 2}" fill="${faceColor}"/>
    ${marks}
    ${numbers}
    <line data-clock-hand="hour" x1="100" y1="100" x2="100" y2="${100 - config.hourHandLength}" stroke="${handColor}" stroke-width="${config.hourHandWidth}" stroke-linecap="round" transform="rotate(0 100 100)"/>
    <line data-clock-hand="minute" x1="100" y1="100" x2="100" y2="${100 - config.minuteHandLength}" stroke="${handColor}" stroke-width="${config.minuteHandWidth}" stroke-linecap="round" transform="rotate(0 100 100)"/>
    ${secondHand}
    <circle cx="100" cy="100" r="${config.centerDotRadius}" fill="${handColor}"/>
  </svg>`;
}

function parsedClockSeconds(snapshot) {
  const raw = String(snapshot.clock?.time || "12:00:00");
  const parts = raw.split(":").map(Number);
  return (parts[0] || 0) * 3600 + (parts[1] || 0) * 60 + (parts[2] || 0);
}

function circularClockDelta(left, right) {
  return ((left - right + 43200) % 86400 + 86400) % 86400 - 43200;
}

function syncDisplayClock(snapshot, receivedAt = performance.now()) {
  const serverSeconds = parsedClockSeconds(snapshot);
  const running = Boolean(snapshot.clock?.running);
  const speed = Number(snapshot.clock?.speed || 1);
  const stateChanged = displayClockAnchorRunning !== running || displayClockAnchorSpeed !== speed;
  let predicted = displayClockAnchorSeconds;
  if (predicted !== null && displayClockAnchorAt !== null && displayClockAnchorRunning) {
    predicted += (receivedAt - displayClockAnchorAt) / 1000 * Number(displayClockAnchorSpeed || 1);
  }
  const correction = predicted === null ? Infinity : Math.abs(circularClockDelta(serverSeconds, predicted));

  // Servern skickar hela sekunder. Små skillnader lämnas åt den lokala,
  // monotona klockan så att visarna inte rycker vid varje synkning.
  if (displayClockAnchorSeconds === null || stateChanged || !running || correction > 1.25) {
    displayClockAnchorSeconds = serverSeconds;
    displayClockAnchorAt = receivedAt;
  }
  displayClockAnchorRunning = running;
  displayClockAnchorSpeed = speed;
}

function currentClockSeconds(snapshot) {
  if (displayClockAnchorSeconds === null || displayClockAnchorAt === null) syncDisplayClock(snapshot);
  let seconds = Number(displayClockAnchorSeconds || 0);
  if (displayClockAnchorRunning) {
    seconds += (performance.now() - displayClockAnchorAt) / 1000 * Number(displayClockAnchorSpeed || 1);
  }
  return ((seconds % 86400) + 86400) % 86400;
}

function formatClockTime(seconds) {
  seconds = Math.floor(seconds) % 86400;
  const hour = Math.floor(seconds / 3600);
  const minute = Math.floor((seconds % 3600) / 60);
  const second = seconds % 60;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:${String(second).padStart(2, "0")}`;
}

function currentClockTime(snapshot) {
  if (snapshot.clock?.source === "fastclock" && !snapshot.clock?.last_sync) return "--:--:--";
  return formatClockTime(currentClockSeconds(snapshot));
}

function swissMinuteWobble(minuteKey, running) {
  if (swissMinuteKey === null) swissMinuteKey = minuteKey;
  if (minuteKey !== swissMinuteKey) {
    swissMinuteKey = minuteKey;
    swissMinuteWobbleStartedAt = running ? performance.now() : null;
  }
  if (swissMinuteWobbleStartedAt === null) return 0;
  const elapsed = (performance.now() - swissMinuteWobbleStartedAt) / 1000;
  const decay = Math.exp(-6 * elapsed);
  if (decay <= 0.01) {
    swissMinuteWobbleStartedAt = null;
    return 0;
  }
  return 1.8 * decay * Math.sin(8 * Math.PI * 2 * elapsed);
}

function updateAnalogClockHands(target, seconds, style, running) {
  const isSwiss = style === "swiss";
  const hour = Math.floor(seconds / 3600);
  const minute = Math.floor((seconds % 3600) / 60);
  const second = seconds % 60;
  const minuteKey = Math.floor(seconds / 60);
  const hourAngle = isSwiss
    ? (hour % 12 + minute / 60) * 30
    : (hour % 12 + minute / 60 + second / 3600) * 30;
  const minuteAngle = isSwiss
    ? minute * 6 + swissMinuteWobble(minuteKey, running)
    : (minute + second / 60) * 6;
  // Hilfikers SBB-klocka gör varvet på 58,5 s och väntar sedan vid 12.
  const secondAngle = isSwiss ? Math.min(second / 58.5, 1) * 360 : second * 6;
  target.querySelector('[data-clock-hand="hour"]')?.setAttribute("transform", `rotate(${hourAngle} 100 100)`);
  target.querySelector('[data-clock-hand="minute"]')?.setAttribute("transform", `rotate(${minuteAngle} 100 100)`);
  target.querySelector('[data-clock-hand="second"]')?.setAttribute("transform", `rotate(${secondAngle} 100 100)`);
}

// The clock styles offered on a screen, in the same order as under ⚙ Inställningar.
const DISPLAY_CLOCK_STYLES = ["digital", "analog", "stationsur", "swiss"];
const DISPLAY_CLOCK_STYLE_KEY = "trainmeet.displayClockStyle";
const DISPLAY_CLOCK_SECONDS_KEY = "trainmeet.displayClockSeconds";

// The appearance chosen under ⚙ Inställningar is the default for every screen.
// A screen may still choose its own style and seconds from its menu bar (the
// TV in the hall and the laptop by the desk are different screens); that
// choice lives in this browser only and an empty value follows the server.
function displayClockPreference() {
  try {
    return { style: localStorage.getItem(DISPLAY_CLOCK_STYLE_KEY) || "", seconds: localStorage.getItem(DISPLAY_CLOCK_SECONDS_KEY) || "" };
  } catch { return { style: "", seconds: "" }; }
}

function saveDisplayClockPreference(key, value) {
  try { if (value) localStorage.setItem(key, value); else localStorage.removeItem(key); } catch {}
}

function resolveClockAppearance(snapshot) {
  const available = snapshot.clock?.available_styles?.length ? snapshot.clock.available_styles : ["swiss", "swedish", "digital"];
  const preference = displayClockPreference();
  let style = available.includes(preference.style) ? preference.style : snapshot.clock?.style || available[0];
  if (!available.includes(style)) style = available[0];
  const showSeconds = preference.seconds === "on" ? true : preference.seconds === "off" ? false : snapshot.clock?.show_seconds !== false;
  return { available, style, showSeconds, preference };
}

function renderClockToolbar(snapshot) {
  const styleSelect = document.querySelector("#display-clock-style");
  const secondsSelect = document.querySelector("#display-clock-seconds");
  if (!styleSelect || !secondsSelect) return;
  const { available, preference } = resolveClockAppearance(snapshot);
  const styles = DISPLAY_CLOCK_STYLES.filter(value => available.includes(value));
  if (snapshot.clock?.style && !styles.includes(snapshot.clock.style)) styles.push(snapshot.clock.style);
  const serverStyle = t(clockStyleLabels[snapshot.clock?.style] || snapshot.clock?.style || "");
  const serverSeconds = snapshot.clock?.show_seconds !== false;
  const signature = [globalThis.TrainMeetI18n?.getLanguage?.(), styles.join(","), serverStyle, serverSeconds].join("|");
  if (styleSelect.dataset.signature !== signature) {
    styleSelect.dataset.signature = signature;
    styleSelect.replaceChildren(
      new Option(`${t("Som i inställningarna")}: ${serverStyle}`, ""),
      ...styles.map(value => new Option(t(clockStyleLabels[value] || value), value)));
    secondsSelect.replaceChildren(
      new Option(`${t("Som i inställningarna")}: ${serverSeconds ? t("med sekunder") : t("utan sekunder")}`, ""),
      new Option(t("Med sekunder"), "on"),
      new Option(t("Utan sekunder"), "off"));
  }
  styleSelect.value = styles.includes(preference.style) ? preference.style : "";
  secondsSelect.value = ["on", "off"].includes(preference.seconds) ? preference.seconds : "";
}

function renderClock(snapshot) {
  const target = document.querySelector("#clock-view");
  target.dataset.us = String(snapshot.meet?.operating_region === "us");
  const { style, showSeconds } = resolveClockAppearance(snapshot);
  const digital = style === "digital";
  const seconds = currentClockSeconds(snapshot);
  const time = currentClockTime(snapshot);
  let displayTime = showSeconds ? time : time.slice(0, 5);
  if (snapshot.meet?.operating_region === "us" && /^\d\d:/.test(time)) {
    const hour = Number(time.slice(0, 2)); displayTime = `${hour % 12 || 12}${displayTime.slice(2)} ${hour >= 12 ? "PM" : "AM"}`;
  }
  const darkBackground = !document.querySelector("#display-app").classList.contains("light");
  const stopped = !snapshot.clock?.running;
  const externalMissing = snapshot.clock?.source === "fastclock" && !snapshot.clock.available;
  const reason = snapshot.clock?.stopped_reason || (externalMissing ? t("Senast mottagna tid visas") : "");
  const meta = `${Number(snapshot.clock?.speed || 1)}× · ${snapshot.clock?.source === "fastclock" ? "FastClock" : t("Intern klocka")}`;
  const renderSignature = [style, darkBackground, showSeconds, stopped, externalMissing, reason, meta].join("|");
  if (target.dataset.clockSignature !== renderSignature) {
    target.dataset.clockSignature = renderSignature;
    // A running face fills the screen on its own; the status line is only for
    // digits (where it costs no size) and for a stopped or unreachable clock.
    const status = stopped || externalMissing
      ? html`<div class="sc-stopped"><div><div class="sc-stopped__title">${t(externalMissing ? "Kontakt saknas" : "Klockan stoppad")}</div><div class="sc-stopped__reason">${escapeHTML(reason)}</div><div class="sc-stopped__meta">${escapeHTML(meta)}</div></div></div>`
      : digital ? html`<div class="sc-run">${t("Klockan går")} · ${escapeHTML(meta)}</div>` : "";
    // One clock only: a face never has digits beside it, and digits never have a face.
    const clock = digital
      ? html`<div class="clock-digital${stopped ? " stopped" : ""}" data-seconds="${showSeconds}"></div>`
      : clockSVG(style, style === "stationsur" ? false : darkBackground, showSeconds, stopped);
    target.innerHTML = html`<div class="sc-clock-layout ${digital ? "sc-clock-layout--digital" : "sc-clock-layout--face"}${stopped || externalMissing ? " is-stopped" : ""}">${clock}${status}</div>`;
  }
  const digits = target.querySelector(".clock-digital");
  if (digits && digits.textContent !== displayTime) digits.textContent = displayTime;
  if (!digital) updateAnalogClockHands(target, seconds, style, !stopped);
}

function renderDashboard(snapshot) {
  const target = document.querySelector("#dashboard-view");
  const positions = snapshot.train_positions || [];
  const moving = positions.filter(p => p.connection_id);
  const now = currentClockTime(snapshot).slice(0, 5);
  const late = moving.filter(p => (snapshot.routes || []).some(r => r.train_number === p.train_number && r.station_id === p.to_station_id && r.arrival_time && r.arrival_time < now));
  const upcoming = (snapshot.routes || []).filter(r => (r.departure_time || r.arrival_time || "") >= now).sort((a,b)=>(a.departure_time||a.arrival_time).localeCompare(b.departure_time||b.arrival_time)).slice(0,4);
  const stationName = id => snapshot.stations?.find(s=>s.id===id)?.name || id || "—";
  const staffed = snapshot.staffed_station_count;
  target.innerHTML = html`<div class="dashboard-column">
    <section class="display-card"><div class="dashboard-clock">${escapeHTML(currentClockTime(snapshot).slice(0, 5))}</div><p class="clock-meta">${Number(snapshot.clock?.speed || 1)}× · ${snapshot.clock?.running ? "Klockan går" : "Klockan är stoppad"}</p></section>
    <section class="display-card dashboard-stats">
      <div class="dashboard-stat"><b>${moving.length}</b><span>tåg på linjen</span></div>
      <div class="dashboard-stat"><b>${positions.filter(p=>p.station_id && !p.connection_id).length}</b><span>inne på stationerna</span></div>
      <div class="dashboard-stat"><b>${staffed == null ? (snapshot.stations?.length || 0) : `${staffed} / ${snapshot.stations?.length || 0}`}</b><span>${staffed == null ? "stationer" : "stationer bemannade"}</span></div>
      <div class="dashboard-stat"><b>${late.length}</b><span>sena ankomster</span></div>
    </section>
  </div><section class="display-card"><svg id="dashboard-topology" class="display-visual" role="img" aria-label="Banöversikt"></svg></section>
  <div class="server-dashboard-bottom"><section class="display-card"><h3>Nästa händelser</h3>${upcoming.map(r=>html`<div class="server-event"><span>${escapeHTML(r.departure_time||r.arrival_time)}</span><b>${escapeHTML(r.train_number)}</b><span>${escapeHTML(stationName(r.station_id))} · ${r.departure_time?t("Avgång"):t("Ankomst")}</span></div>`).join("") || html`<p>Inga fler planerade händelser idag.</p>`}</section>
  <section class="display-card"><h3>På linjen just nu</h3>${moving.slice(0,4).map(p=>html`<div class="server-event"><b>${escapeHTML(p.train_number)}</b><span>${escapeHTML(stationName(p.from_station_id))} → ${escapeHTML(stationName(p.to_station_id))}</span></div>`).join("") || html`<p>Inget tåg är ute på linjen</p>`}</section></div>`;
  // Draw for the height the card really has, so station names stay at their 30 px.
  const dashboardMap = document.querySelector("#dashboard-topology");
  renderTopology(snapshot, dashboardMap, {tv:true, height: Math.max(300, Math.round(dashboardMap.clientHeight || 450))});
}

function renderDisplayTopology(snapshot) {
  renderTopology(snapshot, document.querySelector("#topology-svg"), {
    tv: true,
    selectedTrainNumber: state.displaySelectedTrainNumber,
    selectedStationID: state.displaySelectedStationID,
    onTrainSelect: (trainNumber) => {
      state.displaySelectedTrainNumber = state.displaySelectedTrainNumber === String(trainNumber) ? null : String(trainNumber);
      state.displaySelectedStationID = null;
      document.querySelector("#display-train-select").value = state.displaySelectedTrainNumber || "";
      renderDisplayTopology(snapshot);
    },
    onStationSelect: (stationID) => {
      state.displaySelectedStationID = state.displaySelectedStationID === stationID ? null : stationID;
      state.displaySelectedTrainNumber = null;
      document.querySelector("#display-train-select").value = "";
      renderDisplayTopology(snapshot);
    },
    onClear: () => {
      state.displaySelectedTrainNumber = null;
      state.displaySelectedStationID = null;
      document.querySelector("#display-train-select").value = "";
      renderDisplayTopology(snapshot);
    },
  });
  renderDisplaySelection(snapshot);
}

function renderConnectionBadge(snapshot) {
  const badge = document.querySelector("#display-connection");
  if (!badge) return;
  const connection = snapshot.connection || {};
  const screens = connection.screens || [];
  const address = connection.host ? `${connection.host}:${connection.port}` : "";
  const visible = screens.includes(displayKind);
  badge.classList.toggle("hidden", !visible);
  // The same setting (⚙ › Skärmar och klocka) governs the QR codes: the meet's
  // Wi-Fi first, then the link to this server. They replace the address line.
  serverUI.qr({ link: new URL("/", location.href).href, wifi: connection.wifi }, visible);
  badge.classList.add("hidden");
  if (!visible) return;
  document.querySelector("#display-connection-address").textContent = `TMBox ${address}`;
  document.querySelector("#display-connection-code").textContent = connection.code;
}

function renderDisplay(snapshot) {
  displaySnapshot = snapshot;
  serverUI.display(snapshot, displayKind, currentClockTime(snapshot));
  document.querySelector("#display-loading").classList.add("hidden");
  document.querySelector("#display-title").textContent = `${snapshot.meet?.name || "TrainMeet"} · ${({ topology: t("Banöversikt"), graph: "Tågdiagram", clock: t("Träffklocka"), dashboard: t("Översikt"), territories: t("Områdestavla") })[displayKind]}`;
  document.querySelector("#display-day").textContent = snapshot.active_day || "Dagl";
  const isClock = displayKind === "clock";
  document.querySelector("#display-speed").classList.toggle("hidden", !isClock);
  document.querySelector("#display-speed").textContent = `${Number(snapshot.clock?.speed || 1)}×`;
  document.querySelector("#display-clock-style").classList.toggle("hidden", !isClock);
  document.querySelector("#display-clock-seconds").classList.toggle("hidden", !isClock);
  const trainSelect = document.querySelector("#display-train-select");
  const trainSelectable = displayKind === "topology" || displayKind === "graph";
  const services = uniqueOverviewServices(snapshot);
  const trainSignature = services.map((service) => service.train_number).join("|");
  trainSelect.classList.toggle("hidden", !trainSelectable);
  if (trainSelect.dataset.signature !== trainSignature) {
    trainSelect.dataset.signature = trainSignature;
    trainSelect.innerHTML = html`<option value="">Alla tåg</option>${services.map((service) => html`<option value="${escapeHTML(service.train_number)}">Tåg ${escapeHTML(service.train_number)}</option>`).join("")}`;
  }
  if (!services.some((service) => String(service.train_number) === state.displaySelectedTrainNumber)) state.displaySelectedTrainNumber = null;
  trainSelect.value = state.displaySelectedTrainNumber || "";
  renderConnectionBadge(snapshot);
  const ids = { topology: "topology-svg", graph: "graph-scroll", clock: "clock-view", dashboard: "dashboard-view", territories:"territories-view" };
  for (const id of Object.values(ids)) document.querySelector(`#${id}`).classList.toggle("hidden", id !== ids[displayKind]);
  if (displayKind === "topology") renderDisplayTopology(snapshot);
  if (displayKind === "graph") renderGraph(snapshot);
  if (displayKind === "territories") serverUI.us(snapshot.us, true);
  if (displayKind === "clock") {
    document.querySelector("#display-selection").classList.add("hidden");
    renderClockToolbar(snapshot);
    renderClock(snapshot);
  }
  if (displayKind === "dashboard") {
    document.querySelector("#display-selection").classList.add("hidden");
    renderDashboard(snapshot);
  }
}

async function pollDisplay() {
  clearTimeout(displayPollTimer);
  displayRequest?.abort();
  const request = new AbortController();
  displayRequest = request;
  const deadline = setTimeout(() => request.abort(), 5000);
  const live = document.querySelector("#display-live");
  try {
    const response = await fetch("/v1/display", { cache: "no-store", signal: request.signal });
    if (!response.ok) throw new Error("Servern svarade inte");
    const payload = await response.json();
    if (displayRequest !== request || request.signal.aborted) return;
    displaySnapshotReceivedAt = performance.now();
    syncDisplayClock(payload, displaySnapshotReceivedAt);
    live.classList.remove("offline");
    document.querySelector("#display-app").classList.remove("display-offline");
    live.lastChild.textContent = " Ansluten";
    renderDisplay(payload);
  } catch {
    if (displayRequest !== request) return;
    live.classList.add("offline");
    document.querySelector("#display-app").classList.add("display-offline");
    live.lastChild.textContent = " Återansluter";
  } finally {
    clearTimeout(deadline);
    // A resumed tab may have started a newer request; an old response must
    // neither replace the current clock nor schedule a second polling loop.
    if (displayRequest === request) {
      displayRequest = null;
      displayPollTimer = setTimeout(pollDisplay, 1000);
    }
  }
}

async function initDisplay() {
  serverUI.initDisplay();
  const displayApp = document.querySelector("#display-app");
  displayApp.classList.remove("hidden");
  const light = localStorage.getItem("trainmeet.displayTheme") === "light";
  displayApp.classList.toggle("light", light);
  document.querySelector("#display-theme").textContent = light ? "Mörkt" : "Ljust";
  document.title = "TrainMeet · Skärm";
  document.querySelector("#display-theme").addEventListener("click", () => {
    displayApp.classList.toggle("light");
    const isLight = displayApp.classList.contains("light");
    localStorage.setItem("trainmeet.displayTheme", isLight ? "light" : "dark");
    document.querySelector("#display-theme").textContent = isLight ? "Mörkt" : "Ljust";
    if (displaySnapshot) renderDisplay(displaySnapshot);
  });
  document.querySelector("#display-train-select").addEventListener("change", (event) => {
    state.displaySelectedTrainNumber = event.target.value || null;
    state.displaySelectedStationID = null;
    if (!displaySnapshot) return;
    if (displayKind === "topology") renderDisplayTopology(displaySnapshot);
    if (displayKind === "graph") {
      graphLastCenteredSelection = null;
      renderGraph(displaySnapshot);
    }
  });
  document.querySelector("#display-clock-style").addEventListener("change", (event) => {
    saveDisplayClockPreference(DISPLAY_CLOCK_STYLE_KEY, event.target.value);
    if (displaySnapshot) renderClock(displaySnapshot);
  });
  document.querySelector("#display-clock-seconds").addEventListener("change", (event) => {
    saveDisplayClockPreference(DISPLAY_CLOCK_SECONDS_KEY, event.target.value);
    if (displaySnapshot) renderClock(displaySnapshot);
  });
  document.querySelector("#display-fullscreen").addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await displayApp.requestFullscreen();
    } catch {}
  });
  const showToolbar = () => {
    clearTimeout(displayToolbarTimer);
    document.querySelector("#display-toolbar").classList.remove("hidden-toolbar");
    document.querySelector("#display-stage").classList.remove("toolbar-hidden");
    displayToolbarTimer = setTimeout(() => {
      document.querySelector("#display-toolbar").classList.add("hidden-toolbar");
      document.querySelector("#display-stage").classList.add("toolbar-hidden");
    }, 4000);
  };
  displayApp.addEventListener("mousemove", showToolbar);
  displayApp.addEventListener("click", showToolbar);
  displayApp.addEventListener("touchstart", showToolbar, {passive:true});
  document.addEventListener("keydown", showToolbar);
  showToolbar();
  try { await navigator.wakeLock?.request("screen"); } catch {}
  const animateClock = () => {
    if (displaySnapshot && displayKind === "clock") renderClock(displaySnapshot);
    displayTickTimer = requestAnimationFrame(animateClock);
  };
  displayTickTimer = requestAnimationFrame(animateClock);
  window.addEventListener("online", pollDisplay);
  window.addEventListener("pageshow", pollDisplay);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") pollDisplay();
  });
  pollDisplay();
}

if (displayKind && ["topology", "graph", "clock", "dashboard", "territories"].includes(displayKind)) initDisplay();
else bootstrap();

/* ---------------------------------------------------------------- TMBox v2

   The simulator stands in for a physical box, so it behaves like one: it
   reads the three payloads a box reads, renders them with the same renderer
   the firmware runs, and puts every key press through the same navigation
   state machine. Nothing here restates a rule either of those already owns.

   That is the whole point of the mirroring. If this file decided anything for
   itself, the simulator would prove only that the simulator works.         */

const V2_GEOMETRIES = {
  "16x2": { rows: 2, cols: 16, supportsSwedish: false },
  "20x2": { rows: 2, cols: 20, supportsSwedish: false },
  "16x4": { rows: 4, cols: 16, supportsSwedish: false },
  "20x4": { rows: 4, cols: 20, supportsSwedish: false },
};

const tmboxV2 = {
  browser: null,
  connecting: null,
  timer: null,
  nav: null,
  config: { tracks: [], connections: [] },
  configFor: null,
  snapshot: { movements: [], active_clearances: [], line_messages: [], clock: {} },
  assignment: null,
  flashUntil: 0,
  busy: false,
  attention: null,
  attentionTimer: null,
  audio: null,
};

function v2El(id) { return document.querySelector(`#tmbox-v2-${id}`); }

function v2Geometry() {
  const stored = localStorage.getItem("trainmeet.v2Geometry");
  // TMBox v2 är 20x4. Alla fyra går att välja - en box rapporterar sin egen
  // geometri och simulatorn ska kunna visa vilken som helst - men förvalet
  // ska vara produkten, inte den geometri v1-boxarna råkade ha.
  return V2_GEOMETRIES[stored] ? stored : "20x4";
}

function startTMBoxV2() {
  if (!tmboxV2.nav) {
    tmboxV2.nav = new TMBoxNav.LocalNavigationState();
    tmboxV2.attention = new TMBoxAttention.AttentionController();
    buildV2Keypad();
    bindV2Controls();
  }
  loadV2Stations().then(refreshTMBoxV2);
  clearInterval(tmboxV2.timer);
  tmboxV2.timer = setInterval(refreshTMBoxV2, 4000);
}

function stopTMBoxV2() {
  clearInterval(tmboxV2.timer);
  tmboxV2.timer = null;
}

function buildV2Keypad() {
  const keypad = document.querySelector("#keypad-v2");
  if (!keypad || keypad.children.length) return;
  for (const key of keypadKeys) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `key ${/[A-D]/.test(key) ? "letter" : ""}`;
    button.textContent = key;
    button.dataset.key = key;
    button.addEventListener("click", () => pressV2Key(key));
    keypad.append(button);
  }
}

//: De fyra vyerna under TMBox v2. Testklienten pratar med servern; de tre
//: andra är dokumentation som ritas lokalt ur firmwarens egna fixturer.
const TMBOX_PANES = ["klient", "floden", "skarmar", "referens"];

function tmboxPane() {
  const stored = localStorage.getItem("trainmeet.tmboxPane");
  return TMBOX_PANES.includes(stored) ? stored : "klient";
}

//: Ritar en ruta i ett godtyckligt LCD-element.
//
// Radantal och kolumnbredd sätts som anpassade egenskaper via CSSOM, inte som
// ett style-attribut i innerHTML. Serverns CSP är `style-src 'self'` utan
// unsafe-inline, så det senare hade tyst blockerats.
function paintFrame(element, geometry, lines) {
  element.style.setProperty("--lcd-rows", geometry.rows);
  element.style.setProperty("--lcd-cols", geometry.cols);
  element.replaceChildren(...lines.map((text) => {
    const line = document.createElement("div");
    line.className = "lcd-line";
    line.textContent = text;
    return line;
  }));
}

function tmboxDocGeometry() {
  return tmboxLegacyDocs() ? { cols: 16, rows: 2 }
    : V2_GEOMETRIES[localStorage.getItem("trainmeet.tmboxDocGeometry")] || V2_GEOMETRIES["20x4"];
}

function tmboxLegacyDocs() {
  return localStorage.getItem("trainmeet.tmboxDocProfile") === "esp8266";
}

function documentationFlows() {
  return tmboxLegacyDocs() ? TMBoxLegacyCatalog.flows.map((flow) => ({ ...flow, name: flow.id })) : TMBoxFixtures.TRACES;
}

function buildTMBoxGuide() {
  const legacy = tmboxLegacyDocs();
  const host = document.querySelector("#tmbox-operation-guide");
  host.replaceChildren(...(legacy ? [] : TMBoxGuide).map((flow, index) => {
    const section = document.createElement("details");
    section.className = "card";
    const title = document.createElement("summary");
    title.textContent = `${index + 1}. ${flow.title} — ${flow.status}`;
    const list = document.createElement("ol");
    list.replaceChildren(...flow.steps.map((text) => {
      const step = document.createElement("li"); step.textContent = text; return step;
    }));
    section.append(title, list); return section;
  }));
  document.querySelector("#tmbox-flow-scope").textContent = legacy
    ? "Åtta flöden genom den verkliga V1-servermotorn. Varje steg visar operatör, tangent, skärm och linjetillstånd efter åtgärden. Exemplen är skrivskyddade och skickar ingen trafik."
    : "Ovan: 13 granskade funktionsflöden med kända luckor. Nedan: 12 tekniska tangentsekvenser mot frysta data. Dessa visar lokal navigation, inte ett komplett flöde med serverkvittens eller uppdaterade tillstånd.";
  document.querySelector("#tmbox-screen-scope").textContent = legacy
    ? "Åtta interaktionslägen från V1-motorn och 18 start-/nät-/diagnostikexempel från ESP8266-firmwaren, på fast 16×2. Firmware-exemplen är dokumenterade texter, inte körda hårdvarutest. Sista två gäller bara diagnostikbygget; hårdvarutestets andra rad ändras om knappsatsen saknas."
    : "Samtliga 19 skärmtyper i ESP32-navigationen: 20 exempel, eftersom rörelsedetaljen har två varianter. De ursprungliga 15 exemplen jämförs med firmwarets guldfil; fem nät-/livscykelskärmar kompletterar katalogen. Alla fyra displayformat kan förhandsvisas.";
  const reference = document.querySelector("#tmbox-reference-content");
  const title = document.createElement("h3");
  title.textContent = legacy ? "ESP8266 · V1 · dagens knappmodell" : "ESP32 · V2 · dagens knappmodell";
  const lines = legacy ? [
    "A–D väljer motstation från viloläget (högst fyra). I dialoger ändras betydelsen: A ger klart, bekräftar avgång eller ankomst; B nekar eller lämnar bekräftelse.",
    "Siffror buffras lokalt. # skickar hela tågnumret. * lämnar inmatningen; inne i en väntande begäran återtar * däremot trafikärendet direkt.",
    "Reserverat tåg återtas med * följt av #. Ett redan avgånget tåg ska tas emot och kan inte återtas genom detta flöde.",
    "Både klartecken och direkttrafik finns. Faktiskt ankomstspår kan inte väljas här idag.",
    "Klockan visas bara i viloläget, och D-slotten kan ta dess plats. Profilen är fast 16×2 idag.",
  ] : [
    "Siffror buffras lokalt. A söker tåg, B suddar. C bläddrar bland resultat; # väljer. * är lokal Tillbaka och återtar inte en begäran.",
    "A väljer första tillåtna primärhandling. B kan betyda spårval eller neka beroende på vy. C bläddrar. D har ingen åtgärd idag.",
    "# öppnar klareringskorgen före linjemeddelanden från översikten. A ger klart eller kvitterar, B nekar klarering. Läsning av ett linjemeddelande är inte ett körtillstånd.",
    "500 ms lås efter skärmbyte skyddar nästa beslut; det är separat från fysisk knappavstudsning.",
    "Kända luckor: avgång efter klartecken kan fastna i Begär, direkttrafik och återtagning saknar fullständigt tangentflöde, ankomstspår kan inte sparas atomärt. Närmar sig finns ännu och föreslås tas bort ur boxflödet.",
  ];
  const list = document.createElement("ul");
  list.replaceChildren(...lines.map((text) => { const li = document.createElement("li"); li.textContent = text; return li; }));
  const scope = document.createElement("p");
  scope.textContent = "Granskat mot Server 1.8.0 och TMBox 0.4.6. En produktversion betyder ännu inte samma trafikflöde på båda enheterna. Planerade förändringar är inte tillgängliga funktioner.";
  reference.replaceChildren(title, list, scope);
  const picker = document.querySelector("#tmbox-doc-geometry");
  picker.disabled = legacy;
  picker.value = legacy ? "16x2" : localStorage.getItem("trainmeet.tmboxDocGeometry") || "20x4";
}

//: Skärmkatalogen: varje fall i fixturerna, renderat i vald geometri.
function buildScreenCatalog() {
  const host = document.querySelector("#tmbox-screen-catalog");
  if (!host || typeof TMBoxFixtures === "undefined") return;
  const geometry = tmboxDocGeometry();
  const { config, snapshot, CASES, EXTRA_CASES, viewFor } = TMBoxFixtures;
  const legacy = tmboxLegacyDocs();
  const cases = legacy ? [
    ...TMBoxLegacyCatalog.screens.map((screen) => [screen.name, `InteractionMode::${screen.name}`, screen.lines]),
    ...TMBoxLegacyDeviceScreens.map(([name, ...lines]) => [name, "Firmware · dokumenterat textexempel", lines.map(line => line.slice(0, 16).padEnd(16))]),
  ] : [...CASES, ...EXTRA_CASES];

  host.replaceChildren(...cases.map(([name, screen, movement]) => {
    const card = document.createElement("article");
    card.className = "tmbox-screen-card card";

    const heading = document.createElement("h4");
    heading.textContent = name;
    card.append(heading);

    const value = document.createElement("p");
    value.className = "tmbox-screen-value";
    value.textContent = legacy ? screen : `Screen::${screen}`;
    card.append(value);

    const lcd = document.createElement("div");
    lcd.className = "lcd tmbox-mini-lcd";
    lcd.setAttribute("aria-label", `Skärmen ${name}`);
    paintFrame(lcd, geometry, legacy ? movement : TMBoxRender.render(geometry, viewFor(screen, movement), config, snapshot));
    card.append(lcd);
    return card;
  }));
}

//: Flödeskartan. Sekvenserna körs genom samma tillståndsmaskin som boxen, så
//: stegen är härledda - ingen ruta och inget utfall är skrivet för hand.
function replayTrace(trace) {
  const { config, twoMovements, withCases } = TMBoxFixtures;
  const snapshot = trace.snapshot === "cases" ? withCases() : twoMovements();
  if (trace.allowed.length && snapshot.movements.length) {
    snapshot.movements[0].allowed_actions = trace.allowed;
  }
  const nav = new TMBoxNav.LocalNavigationState();
  nav.show("StationOverview", 0);
  let now = 10000;
  const steps = [];
  for (const key of trace.keys) {
    const result = nav.press(key, now, config, snapshot);
    now += trace.pace;
    steps.push({
      key,
      outcome: result.outcome,
      screen: nav.view.screen,
      command: result.outcome === "Send" ? result.command : null,
      // Rutan är den boxen visar *efter* trycket, vilket är det som gör
      // sekvensen läsbar: man ser vad tangenten ledde till.
      frame: TMBoxRender.render(tmboxDocGeometry(), nav.view, config, snapshot),
    });
  }
  return { steps, snapshot };
}

function showFlow(name) {
  const host = document.querySelector("#tmbox-flow-detail");
  const legacy = tmboxLegacyDocs();
  const trace = documentationFlows().find((item) => item.name === name);
  if (!host || !trace) return;
  localStorage.setItem("trainmeet.tmboxFlow", name);
  document.querySelectorAll("#tmbox-flow-list .tmbox-flow-item").forEach((button) => {
    const active = button.dataset.tmboxFlow === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });

  const geometry = tmboxDocGeometry();
  const parts = [];

  const heading = document.createElement("h3");
  heading.textContent = trace.title;
  parts.push(heading);

  const source = document.createElement("p");
  source.className = "tmbox-flow-source";
  source.textContent = legacy ? `V1-servermotor · ${trace.steps.length} steg` : `${trace.name} · tangenter ${trace.keys.split("").join(" ")} · ${trace.pace} ms mellan tryck`;
  parts.push(source);

  const note = document.createElement("p");
  note.className = "tmbox-flow-note";
  note.textContent = legacy ? "CDA och LEK är två olika operatörer. LCD-raderna kommer från servermotorn efter accepterat kommando." : trace.note;
  parts.push(note);

  const list = document.createElement("ol");
  list.className = "tmbox-step-list";
  const steps = legacy ? trace.steps.map((step) => ({ ...step, outcome: "Accepted", screen: step.mode, frame: step.lines })) : replayTrace(trace).steps;
  for (const step of steps) {
    const item = document.createElement("li");
    item.className = `tmbox-step tmbox-step-${step.outcome.toLowerCase()}`;

    const head = document.createElement("div");
    head.className = "tmbox-step-head";

    const pressed = document.createElement("span");
    pressed.className = "tmbox-step-key";
    pressed.textContent = step.key;
    head.append(pressed);

    const outcome = document.createElement("span");
    outcome.className = "tmbox-step-outcome";
    outcome.textContent = legacy ? `${step.actor} · ${step.line_state}` : { Send: "Skickar", Redraw: "Ritar om", Ignored: "Ignoreras" }[step.outcome] || step.outcome;
    head.append(outcome);

    const screen = document.createElement("span");
    screen.className = "tmbox-step-screen";
    screen.textContent = `Screen::${step.screen}`;
    head.append(screen);
    item.append(head);
    if (legacy) {
      const instruction = document.createElement("p"); instruction.textContent = step.instruction; item.append(instruction);
    }

    const lcd = document.createElement("div");
    lcd.className = "lcd tmbox-mini-lcd";
    paintFrame(lcd, geometry, step.frame);
    item.append(lcd);

    if (step.command) {
      const wire = document.createElement("pre");
      wire.className = "tmbox-step-command";
      wire.textContent = JSON.stringify(step.command, null, 2);
      item.append(wire);
    }
    list.append(item);
  }
  parts.push(list);
  host.replaceChildren(...parts);
}

function buildFlowList() {
  const host = document.querySelector("#tmbox-flow-list");
  if (!host || typeof TMBoxFixtures === "undefined") return;
  buildTMBoxGuide();
  const flows = documentationFlows();
  host.replaceChildren(...flows.map((trace) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tmbox-flow-item";
    button.dataset.tmboxFlow = trace.name;
    button.setAttribute("aria-pressed", "false");
    button.textContent = trace.title;
    return button;
  }));
  const remembered = localStorage.getItem("trainmeet.tmboxFlow");
  const known = flows.some((trace) => trace.name === remembered);
  showFlow(known ? remembered : flows[0].name);
}

//: Dokumentationsvyerna ritas om när geometrin byts, annars visar de rutor i
//: en bredd som inte längre är vald.
function redrawTMBoxDocs() {
  if (typeof TMBoxFixtures === "undefined") return;
  buildScreenCatalog();
  buildFlowList();
}

function selectTMBoxPane(pane) {
  const selected = TMBOX_PANES.includes(pane) ? pane : "klient";
  localStorage.setItem("trainmeet.tmboxPane", selected);
  document.querySelector("#tmbox-doc-controls")?.classList.toggle("hidden", selected === "klient");
  document.querySelector("#tmbox-v2-device")?.closest(".panel-picker")?.classList.toggle("hidden", selected !== "klient");
  if (selected !== "klient") buildTMBoxGuide();
  TMBOX_PANES.forEach((name) => {
    document.querySelector(`#tmbox-pane-${name}`)?.classList.toggle("hidden", name !== selected);
  });
  document.querySelectorAll(".tmbox-doc-tab").forEach((button) => {
    const active = button.dataset.tmboxPane === selected;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
  if (selected === "skarmar") buildScreenCatalog();
  if (selected === "floden" && !document.querySelector("#tmbox-flow-list")?.children.length) buildFlowList();
}

function bindTMBoxPanes() {
  const profile = document.querySelector("#tmbox-doc-profile");
  profile.value = tmboxLegacyDocs() ? "esp8266" : "esp32";
  profile.addEventListener("change", () => {
    localStorage.setItem("trainmeet.tmboxDocProfile", profile.value); redrawTMBoxDocs();
  });
  document.querySelector("#tmbox-doc-geometry")?.addEventListener("change", (event) => {
    localStorage.setItem("trainmeet.tmboxDocGeometry", event.target.value); redrawTMBoxDocs();
  });
  document.querySelectorAll(".tmbox-doc-tab").forEach((button) => {
    button.addEventListener("click", () => selectTMBoxPane(button.dataset.tmboxPane));
  });
  document.querySelector("#tmbox-flow-list")?.addEventListener("click", (event) => {
    const button = event.target.closest(".tmbox-flow-item");
    if (button) showFlow(button.dataset.tmboxFlow);
  });
  selectTMBoxPane(tmboxPane());
}

// ── Inställningar: användare ────────────────────────────────────────────
//
// Ägaren bjuder in; den inbjudne väljer sitt eget lösenord med en engångskod.
// Ägaren känner alltså aldrig till någon annans lösenord - inte ens en kort
// stund. Mönstret är be-a-legend-2:s, med koden överlämnad på plats i stället
// för skickad med mejl, eftersom servern inte har någon e-post.
//
// En administratör ser listan men får inte ändra i den. Att veta vilka som har
// tillgång är inte samma sak som att bestämma det.

const users = { list: [], role: "admin", code: null };

function usersEl(name) {
  return document.querySelector(`#users-${name}`);
}

function renderUsers() {
  const body = usersEl("rows");
  if (!body) return;
  const owner = users.role === "owner";

  usersEl("role-chip").textContent = owner ? t("Ägare") : t("Administratör");
  usersEl("invite-open")?.classList.toggle("hidden", !owner);

  body.replaceChildren(...users.list.map((user) => {
    const tr = document.createElement("tr");

    const state = user.invitation_pending
      ? "Inbjuden — har inte valt lösenord"
      : t("Aktiv");
    const roleLabel = user.role === "owner" ? t("Ägare") : t("Administratör");

    const name = document.createElement("td");
    name.className = "users-name";
    name.textContent = user.username;
    // Läget står två gånger med flit: som egen kolumn när det finns plats,
    // och som rad under namnet när kolumnen fälls bort på en telefon. CSS
    // väljer vilken som syns, så det behövs ingen brytpunkt i koden.
    const inline = document.createElement("small");
    inline.className = "users-state-inline";
    inline.textContent = `${roleLabel} · ${state}`;
    name.append(inline);
    tr.append(name);

    const role = document.createElement("td");
    role.className = "users-role";
    const chip = document.createElement("span");
    chip.className = user.role === "owner" ? "role-chip is-owner" : "role-chip";
    chip.textContent = roleLabel;
    role.append(chip);
    tr.append(role);

    const column = document.createElement("td");
    column.className = "users-state";
    column.textContent = state;
    tr.append(column);

    const actions = document.createElement("td");
    actions.className = "users-actions";
    if (owner) {
      actions.append(usersButton("Redigera", () => editUser(user)));
    }
    tr.append(actions);
    return tr;
  }));
}

function usersButton(label, onClick, kind = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = kind ? `link-button ${kind}` : "link-button";
  button.dataset.tmText = label;
  button.textContent = t(label);
  button.addEventListener("click", onClick);
  return button;
}

function showSetupCode(user) {
  const box = usersEl("invite-code");
  if (!box || !user?.setup_code) return;
  usersEl("code-for").textContent = user.username;
  usersEl("code-value").textContent = user.setup_code;
  box.classList.remove("hidden");
}

async function refreshUsers() {
  const message = usersEl("message");
  try {
    const response = await authorizedFetch("/v1/admin/users");
    if (!response.ok) {
      setMessage(message, "Användarna kunde inte läsas", "error");
      return;
    }
    const payload = await response.json();
    users.list = payload.users || [];
    users.role = payload.role || "admin";
    // Meddelandet lämnas som det är: varje åtgärd hämtar listan på nytt, och
    // en nollställning här skulle sudda kvittot i samma andetag som det sätts.
    renderUsers();
  } catch {
    setMessage(message, "Användarna kunde inte läsas", "error");
  }
}

async function usersPost(path, body, whenOk) {
  const modal = document.querySelector("dialog.admin-modal[open]");
  const message = modal?.querySelector(".modal-feedback") || usersEl("message");
  if (!beginModalAction(modal)) return null;
  setMessage(message, "Sparar …");
  try {
    const response = await authorizedFetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setMessage(message, payload.message || "Åtgärden gick inte att utföra", "error");
      return null;
    }
    whenOk?.(payload);
    await refreshUsers();
    return payload;
  } catch {
    setMessage(message, "Åtgärden gick inte att utföra", "error");
    return null;
  } finally {
    endModalAction(modal);
  }
}

async function inviteUser(event) {
  event.preventDefault();
  const name = usersEl("invite-name").value.trim();
  const owner = usersEl("invite-owner").checked;
  const result = await usersPost(
    "/v1/admin/users",
    { username: name, role: owner ? "owner" : "admin" },
    () => {
      usersEl("invite-name").value = "";
      usersEl("invite-owner").checked = false;
      setMessage(usersEl("message"), `${name} är inbjuden. Lämna över koden.`, "success");
    },
  );
  if (result) { finishModal(usersEl("invite-form")); showSetupCode(result.user); }
}

async function reissueUserCode(user) {
  const result = await usersPost("/v1/admin/users/reissue", { user_id: user.user_id });
  if (result) { finishModal(document.querySelector("#user-edit-form")); showSetupCode(result.user); }
}

async function setUserRole(user, role) {
  await usersPost("/v1/admin/users/update", { user_id: user.user_id, role }, () => {
    setMessage(usersEl("message"), `${user.username} är nu ${role === "owner" ? "ägare" : "administratör"}`, "success");
  });
}

async function removeUser(user) {
  if (!document.querySelector("#user-delete-confirm").checked) return;
  const result = await usersPost("/v1/admin/users/delete", { user_id: user.user_id }, () => {
    setMessage(usersEl("message"), `${user.username} är borttagen`, "success");
  });
  if (result) finishModal(document.querySelector("#user-edit-form"), `${user.username} är borttagen`);
}

function editUser(user) {
  users.editing = user;
  document.querySelector("#user-edit-name").textContent = user.username;
  document.querySelector("#user-edit-role").value = user.role;
  document.querySelector("#user-edit-password").value = "";
  document.querySelector("#user-edit-password-confirm").value = "";
  document.querySelector("#user-delete-confirm").checked = false;
  document.querySelector("#user-edit-delete").disabled = true;
  document.querySelector("#user-edit-reissue").hidden = !user.invitation_pending;
  openModal("user-edit-modal");
}

function bindUsersSection() {
  usersEl("invite-form")?.addEventListener("submit", inviteUser);
  document.querySelector("#user-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const password = document.querySelector("#user-edit-password").value;
    if (password !== document.querySelector("#user-edit-password-confirm").value) {
      setMessage(form.querySelector(".modal-feedback"), "Lösenorden är inte likadana.", "error");
      return;
    }
    const body = { user_id: users.editing.user_id };
    const role = document.querySelector("#user-edit-role").value;
    if (role !== users.editing.role) body.role = role;
    if (password) body.password = password;
    const result = await usersPost("/v1/admin/users/update", body);
    if (result) { finishModal(form); setMessage(usersEl("message"), "Användaren är uppdaterad.", "success"); }
  });
  document.querySelector("#user-delete-confirm").addEventListener("change", (event) => { document.querySelector("#user-edit-delete").disabled = !event.target.checked; });
  document.querySelector("#user-edit-delete").addEventListener("click", () => removeUser(users.editing));
  document.querySelector("#user-edit-reissue").addEventListener("click", () => reissueUserCode(users.editing));
}

function bindV2Controls() {
  document.querySelector("#tmbox-language-open").addEventListener("click", openBoxLanguage);
  document.querySelector("#tmbox-language-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!beginModalAction(form)) return;
    ++tmboxV2.uiEpoch;
    try {
      const response = await boxFetch("/v1/tmbox/preferences", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: document.querySelector("#tmbox-language").value }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.message || "Språket kunde inte sparas.");
      ++tmboxV2.uiEpoch;
      tmboxV2.ui = body.ui; drawV2(); finishModal(form);
    } catch (error) { setMessage(document.querySelector("#tmbox-language-message"), error.message, "error"); }
    finally { endModalAction(form); }
  });
  const device = v2El("device");
  const station = v2El("station");
  const geometry = v2El("geometry");
  device.value = "";
  geometry.value = v2Geometry();
  document.querySelector("#tmbox-browser-start").addEventListener("click", () => {
    localStorage.removeItem("trainmeet.browser-tmbox");
    tmboxV2.browser = null;
    loadV2Stations().then(refreshTMBoxV2);
  });
  geometry.addEventListener("change", () => {
    localStorage.setItem("trainmeet.v2Geometry", geometry.value);
    drawV2();
    redrawTMBoxDocs();
  });
  bindTMBoxPanes();
}

async function loadV2Stations() {
  if (tmboxV2.connecting) return tmboxV2.connecting;
  tmboxV2.connecting = connectBrowserTMBox();
  try { await tmboxV2.connecting; }
  finally { tmboxV2.connecting = null; }
}

async function connectBrowserTMBox() {
  try {
    let saved;
    try { saved = JSON.parse(localStorage.getItem("trainmeet.browser-tmbox") || "null"); } catch { saved = null; }
    const response = saved?.access_token
      ? await fetch("/v1/browser-clients/self", { headers: { Authorization: `Bearer ${saved.access_token}` }, credentials: "omit", cache: "no-store" })
      : await fetch("/v1/browser-clients", { method: "POST", credentials: "omit", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workspace: "tmbox" }) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body.workspace !== "tmbox") throw new Error(body.message || "TMBoxen kunde inte startas.");
    tmboxV2.browser = { ...body, access_token: saved?.access_token || body.access_token };
    tmboxV2.uiEpoch = (tmboxV2.uiEpoch || 0) + 1;
    localStorage.setItem("trainmeet.browser-tmbox", JSON.stringify(tmboxV2.browser));
    v2El("device").value = body.device_code;
    document.querySelector("#tmbox-browser-start").classList.add("hidden");
    setMessage(v2El("message"), "");
  } catch (error) {
    tmboxV2.browser = null;
    document.querySelector("#tmbox-browser-start").classList.remove("hidden");
    setMessage(v2El("message"), error.message, "error");
  }
}

function boxFetch(path, options = {}) {
  if (!tmboxV2.browser) throw new Error(t("TMBoxen kunde inte startas."));
  return fetch(path, { ...options, credentials: "omit", cache: "no-store",
    headers: { ...options.headers, Authorization: `Bearer ${tmboxV2.browser.access_token}` } });
}

async function openBoxLanguage() {
  if (!tmboxV2.browser || tmboxV2.busy) return;
  try {
    const response = await boxFetch("/v1/tmbox/preferences");
    const body = await response.json();
    if (!response.ok) throw new Error(body.message || "Språken kunde inte hämtas.");
    tmboxV2.ui = body.ui;
    const select = document.querySelector("#tmbox-language");
    select.replaceChildren(...body.ui.languages.map(item => new Option(item.name, item.code)));
    select.value = body.ui.language;
    openModal("tmbox-language-modal");
  } catch (error) { setMessage(v2El("message"), error.message, "error"); }
}

async function refreshTMBoxV2() {
  const deviceID = tmboxV2.browser?.client_id;
  const nav = tmboxV2.nav;
  if (!nav) return;

  if (deviceID) {
    try {
      const response = await boxFetch(
        `/v1/tmbox-v2/assignment?device_id=${encodeURIComponent(deviceID)}`);
      if (response.status === 401 || response.status === 403) {
        tmboxV2.browser = null;
        document.querySelector("#tmbox-browser-start").classList.remove("hidden");
        setMessage(v2El("message"), "Boxen är borttagen eller saknar behörighet. Be administratören om hjälp.", "error");
      }
      tmboxV2.assignment = response.ok ? await response.json() : null;
      if (tmboxV2.assignment?.language && tmboxV2.ui?.language !== tmboxV2.assignment.language) {
        const uiEpoch = tmboxV2.uiEpoch;
        const prefs = await boxFetch("/v1/tmbox/preferences");
        const latest = prefs.ok ? await prefs.json() : null;
        if (latest && uiEpoch === tmboxV2.uiEpoch) tmboxV2.ui = latest.ui;
      }
    } catch { tmboxV2.assignment = null; }
  } else {
    tmboxV2.assignment = null;
  }

  // A box that is not assigned shows KOPPLA BOXEN and nothing else - it has
  // no station to browse.
  if (!tmboxV2.assignment || tmboxV2.assignment.status !== "assigned") {
    tmboxV2.configFor = null;
    tmboxV2.config = { tracks: [], connections: [] };
    tmboxV2.snapshot = { movements: [], active_clearances: [], line_messages: [], clock: {} };
    v2El("station").replaceChildren();
    nav.show(tmboxV2.assignment ? "AwaitingAssignment" : "Identity", v2Now());
    drawV2();
    return;
  }

  const stationID = tmboxV2.assignment.station_id;
  if (!stationID) return;

  const assignment = tmboxV2.assignment;
  const configKey = JSON.stringify([stationID, assignment.meet_generation, assignment.publication_id, assignment.config_version]);
  const sameScope = payload => ["meet_generation", "publication_id"].every(
    key => assignment[key] === undefined || payload[key] === assignment[key]);
  if (tmboxV2.configFor !== configKey) {
    // A publication may remap the SAME station. Never keep its old config,
    // or reuse a picker index against the new connection order.
    tmboxV2.configFor = null;
    tmboxV2.config = { tracks: [], connections: [] };
    tmboxV2.snapshot = { movements: [], active_clearances: [], line_messages: [], clock: {} };
    nav.show("LoadingStation", v2Now());
    try {
      const response = await boxFetch(
        `/v1/tmbox-v2/config?station_id=${encodeURIComponent(stationID)}`);
      const payload = await response.json();
      if (response.ok && sameScope(payload)
          && (assignment.config_version === undefined || payload.config_version === assignment.config_version)) {
        tmboxV2.config = v2NormaliseConfig(payload);
        v2El("station").replaceChildren(new Option(tmboxV2.config.name || stationID, stationID));
        tmboxV2.configFor = configKey;
        tmboxV2.attention.forget();
      }
    } catch { /* the snapshot below reports the trouble */ }
    if (tmboxV2.configFor !== configKey) { drawV2(); return; }
  }

  try {
    const response = await boxFetch(
      `/v1/tmbox-v2/snapshot?station_id=${encodeURIComponent(stationID)}`);
    if (!response.ok) {
      // The box has no authoritative state either way, so the screen is
      // honest. But the reason is the server's to give, not ours to guess:
      // an unknown station, a missing meet and a client without admin all
      // land here and are not the same thing.
      const body = await response.json().catch(() => ({}));
      setMessage(v2El("message"), body.message || "Läget kunde inte hämtas", "error");
      v2Signal(tmboxV2.attention.observeLink(false));
      nav.show("ServerGone", v2Now());
      drawV2();
      return;
    }
    v2Signal(tmboxV2.attention.observeLink(true));
    const snapshot = await response.json();
    if (!sameScope(snapshot)
        || (assignment.config_version !== undefined
            && snapshot.revision?.config_version !== undefined
            && snapshot.revision.config_version !== assignment.config_version)) {
      tmboxV2.configFor = null;
      nav.show("LoadingStation", v2Now());
      drawV2();
      return;
    }
    tmboxV2.snapshot = snapshot;
    v2Signal(tmboxV2.attention.observe(tmboxV2.snapshot));
    if (["Identity", "AwaitingAssignment", "ServerGone", "SeekingServer", "LoadingStation"]
        .includes(nav.view.screen)) {
      nav.show("StationOverview", v2Now());
    }
    // A fresh snapshot replaces the cache wholesale, so a selection that no
    // longer exists must not survive it.
    nav.reconcile(tmboxV2.config, tmboxV2.snapshot, v2Now());
    drawV2();
  } catch (error) {
    setMessage(v2El("message"), error.message, "error");
  }
}

/** The wire nests the station under its own key; the renderer takes the same
    flat shape the firmware's StationConfig has. Adapting here keeps the
    renderer identical to the C++ one it is checked against. */
function v2NormaliseConfig(payload) {
  const station = payload.station || {};
  return {
    station_id: station.id || "",
    code: station.code || "",
    name: station.name || "",
    tracks: payload.tracks || [],
    connections: payload.connections || [],
    ui: payload.ui,
  };
}

function v2Now() { return Math.round(performance.now()); }

function drawV2() {
  const nav = tmboxV2.nav;
  if (!nav) return;
  const key = v2Geometry();
  const geometry = V2_GEOMETRIES[key];
  const lcd = document.querySelector("#lcd-v2");
  if (!lcd) return;

  lcd.style.setProperty("--lcd-rows", geometry.rows);
  lcd.style.setProperty("--lcd-cols", geometry.cols);
  while (lcd.children.length > geometry.rows) lcd.lastElementChild.remove();
  while (lcd.children.length < geometry.rows) {
    const line = document.createElement("div");
    line.className = "lcd-line";
    lcd.append(line);
  }

  nav.view.device_code = v2El("device").value.trim() || "TMBOX-------";
  const frame = TMBoxRender.render(geometry, nav.view, { ...tmboxV2.config, ui: tmboxV2.ui || tmboxV2.config.ui }, tmboxV2.snapshot);
  frame.forEach((line, row) => { lcd.children[row].textContent = line; });
}

async function pressV2Key(key) {
  const nav = tmboxV2.nav;
  if (!nav || tmboxV2.busy) return;

  // A flash is a screen the operator is reading; let it finish before a key
  // is taken against whatever is behind it.
  if (tmboxV2.flashUntil > Date.now()) return;
  if (document.querySelector("#tmbox-language-modal")?.open) return;
  if (key === "D" && ["StationOverview", "AwaitingAssignment"].includes(nav.view.screen)) {
    await openBoxLanguage(); return;
  }

  const result = nav.press(key, v2Now(), tmboxV2.config, tmboxV2.snapshot);
  if (result.outcome === "Ignored") return;
  if (result.outcome === "Redraw") { drawV2(); return; }

  const deviceID = tmboxV2.browser?.client_id;
  // A picker exists to answer one question. Once it is answered the operator
  // is back at the train, not still standing in the list.
  const previous = ["TrackPicker", "ConnectionPicker"].includes(nav.view.screen)
    ? "MovementDetail"
    : nav.view.screen;
  tmboxV2.busy = true;
  nav.show("Sending", v2Now());
  drawV2();

  // A box mints one id per command and reuses it on replay, so a reconnect
  // cannot turn one decision into two.
  const command = {
    protocol_version: 2,
    // Use the snapshot the operator acted on, not a newer polled context.
    meet_generation: tmboxV2.snapshot?.meet_generation,
    message_id: `sim-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    action: result.command.action,
    station_id: v2El("station").value,
    payload: v2Payload(result.command),
  };
  try {
    const response = await boxFetch("/v1/tmbox-v2/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: deviceID, meet_generation: command.meet_generation, command }),
    });
    const ack = await response.json();
    v2El("ack").textContent = JSON.stringify(ack, null, 2);
    if (!response.ok) throw new Error(ack.message || `HTTP ${response.status}`);

    // A lookup answers rather than changes anything, so it lands on a screen
    // instead of flashing KOMMANDO OK past the operator.
    if (result.command.action === "train.lookup" && ack.status !== "rejected") {
      nav.applyLookup(tmboxV2.snapshot, (ack.result && ack.result.matches) || [], v2Now());
      drawV2();
      tmboxV2.busy = false;
      return;
    }

    if (ack.status === "rejected") {
      nav.view.reason = ack.reason || "okant fel";
      nav.show("CommandRejected", v2Now());
    } else {
      nav.show("CommandAccepted", v2Now());
    }
    tmboxV2.flashUntil = Date.now() + 1200;
    drawV2();
    setTimeout(() => {
      nav.show(previous, v2Now());
      refreshTMBoxV2();
    }, 1200);
  } catch (error) {
    setMessage(v2El("message"), error.message, "error");
    nav.show(previous, v2Now());
    drawV2();
  } finally {
    tmboxV2.busy = false;
  }
}

/** Pack a command for the wire.

    Every id the state machine put on the command travels. An allow-list here
    is how a new field gets silently dropped: clearance.request went out
    without its connection_id for exactly that reason, and the server rejected
    every one of them as unknown_connection. */
const V2_PAYLOAD_FIELDS = [
  "movement_id", "track_id", "connection_id", "clearance_id", "message_id",
  "train_number",
];

// The simulator's attention sink. The controller decides *whether* — the same
// controller the box runs, held to golden_attention.txt — and this decides
// *how*. The box has no buzzer yet (Bennys svar 5.2), so this is currently the
// only place the signals can actually be heard.
const V2_ATTENTION = {
  ConnectionLost:     { hz: 700,  ms: 600, text: "Servern svarar inte" },
  ConnectionRestored: { hz: 1400, ms: 120, text: "Servern svarar igen" },
  IncomingRequest:    { hz: 2200, ms: 250, text: "Begäran om klarering hit" },
  RequestDenied:      { hz: 900,  ms: 250, text: "Er begäran nekades" },
  RequestApproved:    { hz: 2600, ms: 150, text: "Er begäran godkändes" },
  IncomingTrain:      { hz: 1800, ms: 150, text: "Linjen ledig mot er" },
};

function v2Signal(events) {
  if (!events || events.length === 0) return;
  const loudest = TMBoxAttention.AttentionController.loudest(events);
  const signal = V2_ATTENTION[loudest];
  if (!signal) return;

  const banner = v2El("attention");
  if (banner) {
    banner.textContent = signal.text;
    banner.hidden = false;
    clearTimeout(tmboxV2.attentionTimer);
    tmboxV2.attentionTimer = setTimeout(() => { banner.hidden = true; }, 4000);
  }
  v2Beep(signal.hz, signal.ms);
}

function v2Beep(hz, ms) {
  // Browsers refuse to start audio before the page has been interacted with,
  // and a simulator that threw on the first snapshot would be worse than a
  // silent one.
  try {
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!Ctor) return;
    if (!tmboxV2.audio) tmboxV2.audio = new Ctor();
    const context = tmboxV2.audio;
    if (context.state === "suspended") context.resume();

    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "square";
    oscillator.frequency.value = hz;
    // A square wave at full gain is unpleasant on headphones, and ramping the
    // tail off stops the click a hard stop makes.
    gain.gain.setValueAtTime(0.06, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + ms / 1000);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + ms / 1000);
  } catch {
    // No audio available. The banner already said what happened.
  }
}

function v2Payload(command) {
  const payload = {};
  for (const field of V2_PAYLOAD_FIELDS) {
    if (command[field]) payload[field] = command[field];
  }
  if (command.has_approved) payload.approved = command.approved;
  // A field the state machine set but nobody packed is a command that will be
  // rejected on arrival; say so here rather than let the server discover it.
  for (const field of Object.keys(command)) {
    if (field === "action" || field === "approved" || field === "has_approved") continue;
    if (command[field] && payload[field] === undefined) {
      throw new Error(`kommandofaltet ${field} packades inte for tradet`);
    }
  }
  return payload;
}

// ==================================================== Unified live overview
// Uses the overview's snapshot and refresh cycle, never a second polling loop.
//
// All data kommer från /v1/display — samma källa som trafikmotorn och
// hallskärmarna. Ingenting här är exempeldata, och ingenting härleds på ett
// annat sätt än motorn gör det.

const trafficState = { station: "", onlyDeviations: true };

document.querySelector("#traffic-station")?.addEventListener("change", (event) => {
  trafficState.station = event.target.value;
  renderTraffic(state.overviewSnapshot);
});
document.querySelector("#traffic-only-deviations")?.addEventListener("change", (event) => {
  trafficState.onlyDeviations = event.target.checked;
  renderTraffic(state.overviewSnapshot);
});

function renderTraffic(snapshot) {
  if (!snapshot) return;
  serverUI.traffic(snapshot, trafficState.station);

  fillTrafficStationFilter(snapshot);
  renderTrafficOnline(snapshot);
  renderTrafficStations(snapshot);
  renderTrafficTimeline(snapshot);
}

function fillTrafficStationFilter(snapshot) {
  const select = document.querySelector("#traffic-station");
  if (!select) return;
  const stations = snapshot.stations || [];
  const signature = JSON.stringify(stations.map(({ id, name }) => [id, name]));
  if (select.dataset.stations === signature) return;
  select.dataset.stations = signature;
  // Keep the all-stations option (and its localization metadata), replace data.
  while (select.options.length > 1) select.remove(1);
  for (const station of stations) {
    const option = document.createElement("option");
    option.value = station.id;
    option.textContent = station.name;
    select.append(option);
  }
  if (!stations.some((station) => station.id === trafficState.station)) trafficState.station = "";
  select.value = trafficState.station;
}

/** Minuter från träffklockan till `time`, som kan vara negativt. */
function minutesFromClock(clock, time) {
  const now = clockMinutes(clock);
  const then = clockMinutes(time);
  if (now === null || then === null) return null;
  return then - now;
}

function clockMinutes(value) {
  const parts = String(value || "").split(":");
  if (parts.length < 2) return null;
  const hours = Number(parts[0]);
  const minutes = Number(parts[1]);
  return Number.isFinite(hours) && Number.isFinite(minutes) ? hours * 60 + minutes : null;
}

function stationName(snapshot, id) {
  return (snapshot.stations || []).find((station) => station.id === id)?.name || id || "–";
}

/** Ett kort per tåg som är mellan två stationer. */
function renderTrafficOnline(snapshot) {
  const host = document.querySelector("#traffic-online");
  if (!host) return;
  const clock = snapshot.clock?.time || "";
  const moving = (snapshot.train_positions || []).filter((position) => position.connection_id);

  const cards = moving
    .filter((position) => !trafficState.station
      || position.from_station_id === trafficState.station
      || position.to_station_id === trafficState.station)
    .map((position) => {
      const route = (snapshot.routes || []).find(
        (row) => row.train_number === position.train_number
          && row.station_id === position.to_station_id,
      );
      const arrival = route?.arrival_time || route?.departure_time || "";
      const away = minutesFromClock(clock, arrival);
      const late = away !== null && away < 0;

      const card = document.createElement("article");
      card.className = "traffic-card";
      const number = document.createElement("b");
      number.className = "traffic-train-number";
      number.textContent = position.train_number;
      const leg = document.createElement("p");
      leg.className = "traffic-leg";
      leg.textContent = `${stationName(snapshot, position.from_station_id)} → ${stationName(snapshot, position.to_station_id)}`;

      const bar = document.createElement("div");
      bar.className = "traffic-progress";
      const fill = document.createElement("i");
      // Fyllnaden är hur långt tåget hunnit mot ankomsttiden. Utan ankomsttid
      // ritas ingen stapel alls hellre än en påhittad.
      if (away !== null) {
        const share = Math.max(0, Math.min(1, 1 - away / 12));
        fill.style.width = `${Math.round(share * 100)}%`;
      }
      bar.append(fill);

      const times = document.createElement("p");
      times.className = "traffic-times";
      times.textContent = arrival ? `Ankomst ${arrival}` : "Ingen ankomsttid";

      const chip = document.createElement("span");
      chip.className = `traffic-chip ${late ? "late" : "on-time"}`;
      chip.textContent = late ? `${Math.abs(away)} min sen` : t("I tid");

      card.append(number, leg, bar, times, chip);
      return { card, late };
    })
    .filter((entry) => !trafficState.onlyDeviations || entry.late)
    .map((entry) => entry.card);

  host.replaceChildren(...(cards.length ? cards : [emptyNote("Inget tåg är ute på linjen just nu.")]));
}

function emptyNote(text) {
  const note = document.createElement("p");
  note.className = "traffic-empty";
  note.textContent = text;
  return note;
}

/** Ett kort per station: spår, tåg, tider och status ur trafikmotorn. */
function renderTrafficStations(snapshot) {
  const host = document.querySelector("#traffic-stations");
  if (!host) return;
  const clock = snapshot.clock?.time || "";
  const stations = (snapshot.stations || [])
    .filter((station) => !trafficState.station || station.id === trafficState.station);

  const cards = stations.map((station) => {
    const rows = (snapshot.routes || [])
      .filter((row) => row.station_id === station.id)
      .map((row) => ({ row, away: minutesFromClock(clock, row.departure_time || row.arrival_time) }))
      .filter((entry) => entry.away === null || entry.away > -30)
      .sort((a, b) => (a.away ?? 0) - (b.away ?? 0))
      .slice(0, 6);

    const card = document.createElement("article");
    card.className = "traffic-card traffic-station-card";
    const heading = document.createElement("header");
    const name = document.createElement("b");
    name.textContent = station.name;
    const code = document.createElement("span");
    code.className = "traffic-station-code";
    code.textContent = station.code || "";
    heading.append(name, code);
    card.append(heading);

    if (!rows.length) {
      card.append(emptyNote("Inga rörelser kvar i dag."));
      return card;
    }

    const list = document.createElement("ul");
    list.className = "traffic-station-rows";
    for (const { row, away } of rows) {
      const item = document.createElement("li");
      const time = document.createElement("span");
      time.className = "traffic-time";
      time.textContent = row.departure_time || row.arrival_time || "–";
      const train = document.createElement("span");
      train.className = "traffic-train-number small";
      train.textContent = row.train_number;
      const status = document.createElement("span");
      status.className = "traffic-status";
      // Härledd, aldrig lagrad: ett lagrat statusfält blir inaktuellt.
      status.textContent = away === null ? "" : away < 0 ? t("Avgått") : away <= 2 ? "Strax" : `${away} min`;
      if (away !== null && away >= 0 && away <= 2) item.classList.add("needs-attention");
      item.append(time, train, status);
      list.append(item);
    }
    card.append(list);
    return card;
  });

  host.replaceChildren(...(cards.length ? cards : [emptyNote("Ingen station att visa.")]));
}

/** Hela träffdagen i en kolumn med en nu-linje som följer träffklockan. */
function renderTrafficTimeline(snapshot) {
  const host = document.querySelector("#traffic-timeline");
  if (!host) return;
  const clock = snapshot.clock?.time || "";
  const rows = (snapshot.routes || [])
    .filter((row) => !trafficState.station || row.station_id === trafficState.station)
    .map((row) => ({ row, away: minutesFromClock(clock, row.departure_time || row.arrival_time) }))
    .filter((entry) => entry.away !== null)
    .sort((a, b) => a.away - b.away);

  let upcomingMarked = 0;
  const items = rows.map(({ row, away }) => {
    const item = document.createElement("li");
    item.className = "traffic-timeline-row";
    if (away < 0) item.classList.add("past");
    else if (upcomingMarked < 2) { item.classList.add("next"); upcomingMarked += 1; }
    else item.classList.add("future");

    const time = document.createElement("span");
    time.className = "traffic-time";
    time.textContent = row.departure_time || row.arrival_time || "–";
    const text = document.createElement("span");
    text.textContent = `${row.train_number} · ${stationName(snapshot, row.station_id)}`;
    item.append(time, text);
    return item;
  });

  host.replaceChildren(...(items.length ? items : [emptyNote("Tidtabellen är tom.")]));
}
