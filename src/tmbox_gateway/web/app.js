const slotKeys = ["A", "B", "C", "D"];

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
  config: null,
  configRevision: 0,
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
const serverSidebarToggle = document.querySelector("#server-sidebar-toggle");
const serverSidebarOverlay = document.querySelector("#server-sidebar-overlay");
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
const runtimeDownloadUpdate = document.querySelector("#runtime-download-update");
const runtimeActivateUpdate = document.querySelector("#runtime-activate-update");
const runtimeAutoSync = document.querySelector("#runtime-auto-sync");
const runtimeAutoSyncHint = document.querySelector("#runtime-auto-sync-hint");
const configForm = document.querySelector("#config-form");
const configMessage = document.querySelector("#config-message");
const stationEditor = document.querySelector("#station-editor");
const connectionEditor = document.querySelector("#connection-editor");
const panelEditor = document.querySelector("#panel-editor");
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
const copyActiveRuntimeButton = document.querySelector("#copy-active-runtime");
const runtimeImportFile = document.querySelector("#runtime-import-file");
const runtimeImportValidate = document.querySelector("#runtime-import-validate");
const runtimeImportActivate = document.querySelector("#runtime-import-activate");
const runtimeImportMessage = document.querySelector("#runtime-import-message");
const runtimeImportReview = document.querySelector("#runtime-import-review");

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
    if (!response.ok) throw new Error(payload.message || "Inloggningen misslyckades");
    document.querySelector("#login-password").value = "";
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
    await waitForServerReturn();
  } catch (error) {
    state.restarting = false;
    setMessage(message, error.message, "error");
    button.disabled = false;
  }
});


// ============================================================ KÖR och BYGG
// Designpaketets DEL 5. `mode` är roten: den enda variabel som byter hela
// sidans skelett. Körläget är default, eftersom det är det man ser under en
// träff, och ingenting där får ändra konfigurationen.
//
// Kartan från gammalt till nytt (paketets DEL 2): de åtta adminsektionerna och
// fyra vyerna blir två lägen med fem flikar var. Ingen funktionalitet
// försvinner - allt flyttar - utom TMBox-simuleringen, som paketet tar bort
// eftersom v2 räcker.

const RUN_TABS = ["oversikt", "trafik", "skarmar", "tkl", "tmbox"];
const BUILD_STEPS = ["kalla", "bana", "tid", "boxar", "server"];

//: Vilka av dagens adminsektioner som hör till vilket byggsteg.
const STEP_SECTIONS = {
  kalla: ["runtime", "cloud", "local", "import"],
  bana: [],
  tid: [],
  boxar: ["devices"],
  //: Paketets DEL 3.11 i sin ordning: identitet, inloggning, uppdatering,
  //: nollställning. Ordningen i listan är inte den som gäller på skärmen -
  //: den kommer ur DOM-ordningen - men den här är densamma, så de inte
  //: kan glida isär utan att någon märker det.
  server: ["identity", "access", "software", "system"],
};

//: Vilken av dagens vypaneler som visas i vilken körflik.
const RUN_PANELS = {
  oversikt: "#overview-view",
  trafik: "#traffic-view",
  skarmar: "#displays-view",
  tkl: "#tkl-view",
  tmbox: "#tmbox-v2-view",
};

function currentMode() {
  return document.body.dataset.mode === "bygg" ? "bygg" : "kor";
}

function setMode(mode) {
  const next = mode === "bygg" ? "bygg" : "kor";
  document.body.dataset.mode = next;
  localStorage.setItem("trainmeet.mode", next);
  document.querySelector("#build-chrome").classList.toggle("hidden", next !== "bygg");
  document.querySelector("#build-sidebar").classList.toggle("hidden", next !== "bygg");
  if (next === "kor") selectRunTab(state.runTab || "oversikt");
  else selectBuildStep(state.buildStep || "kalla");
}

function selectRunTab(tab) {
  const selected = RUN_TABS.includes(tab) ? tab : "oversikt";
  state.runTab = selected;
  localStorage.setItem("trainmeet.runTab", selected);

  for (const [name, selector] of Object.entries(RUN_PANELS)) {
    const panel = document.querySelector(selector);
    if (panel) panel.classList.toggle("hidden", name !== selected);
  }
  document.querySelectorAll(".admin-section-panel").forEach((panel) => panel.classList.add("hidden"));
  const adminView = document.querySelector("#admin-view");
  if (adminView) adminView.classList.add("hidden");

  document.querySelectorAll(".run-tab").forEach((button) => {
    const active = button.dataset.runTab === selected;
    button.toggleAttribute("aria-current", active);
    if (active) button.setAttribute("aria-current", "page");
  });

  // Den inbäddade terminalen och v2-simulatorn kostar båda något att hålla
  // igång, så de startas och stoppas med sin flik.
  if (selected === "tmbox") startTMBoxV2(); else stopTMBoxV2();
  if (selected === "tkl") mountTklFrame(); else unmountTklFrame();
  if (selected === "trafik") startTrafficView(); else stopTrafficView();
  appView.classList.remove("sidebar-open");
  window.scrollTo({ top: 0, behavior: "auto" });
}

function selectBuildStep(step) {
  const selected = BUILD_STEPS.includes(step) ? step : "kalla";
  state.buildStep = selected;
  localStorage.setItem("trainmeet.buildStep", selected);

  Object.values(RUN_PANELS).forEach((selector) => {
    const panel = document.querySelector(selector);
    if (panel) panel.classList.add("hidden");
  });
  stopTMBoxV2();
  unmountTklFrame();
  stopTrafficView();

  const adminView = document.querySelector("#admin-view");
  if (adminView) adminView.classList.remove("hidden");
  const sections = STEP_SECTIONS[selected] || [];
  document.querySelectorAll(".admin-section-panel").forEach((panel) => {
    panel.classList.toggle("hidden", !sections.includes(panel.dataset.adminSection));
  });
  document.querySelectorAll(".build-panel").forEach((panel) => {
    panel.classList.toggle("hidden", panel.dataset.buildPanel !== selected);
  });
  document.querySelectorAll("[data-build-step]").forEach((button) => {
    const active = button.dataset.buildStep === selected;
    button.toggleAttribute("aria-current", active);
    if (active) button.setAttribute("aria-current", "step");
  });

  if (sections.includes("software")) checkSoftwareUpdate();
  if (sections.includes("cloud")) refreshRuntime();
  if (selected === "kalla") refreshSourceChoice();
  appView.classList.remove("sidebar-open");
  window.scrollTo({ top: 0, behavior: "auto" });
}

// Kroppen ska bära ett läge från första bildrutan, inte först efter
// inloggning: CSS hänger på body[data-mode] och en sida utan läge ritar
// applocket fel under den halvsekund som inloggningen tar.
document.body.dataset.mode = localStorage.getItem("trainmeet.mode") === "bygg" ? "bygg" : "kor";

document.querySelector("#enter-build").addEventListener("click", () => setMode("bygg"));
document.querySelector("#leave-build").addEventListener("click", () => setMode("kor"));
document.querySelectorAll(".run-tab").forEach((button) => {
  button.addEventListener("click", () => selectRunTab(button.dataset.runTab));
});
document.querySelectorAll("[data-build-step]").forEach((button) => {
  button.addEventListener("click", () => selectBuildStep(button.dataset.buildStep));
});

serverSidebarToggle.addEventListener("click", () => {
  appView.classList.toggle("sidebar-open");
});
serverSidebarOverlay.addEventListener("click", () => {
  appView.classList.remove("sidebar-open");
});

// Genvägar inne i vyerna pekade på de gamla vynamnen. De pekar nu på flikar,
// via en karta i stället för att varje knapp skrivs om i markupen.
const LEGACY_VIEW_TABS = {
  overview: "oversikt", displays: "skarmar", "tmbox-v2": "tmbox",
  simulator: "tmbox", admin: null,
};
document.querySelectorAll("[data-open-view]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = LEGACY_VIEW_TABS[button.dataset.openView];
    if (target) { setMode("kor"); selectRunTab(target); }
    else { setMode("bygg"); selectBuildStep("kalla"); }
  });
});

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

copyActiveRuntimeButton.addEventListener("click", () => copyActiveRuntimeToDraft());

runtimeImportFile.addEventListener("change", () => {
  state.pendingImportPackage = null;
  state.pendingImportValidation = null;
  runtimeImportValidate.disabled = !runtimeImportFile.files?.length;
  runtimeImportActivate.classList.add("hidden");
  runtimeImportReview.classList.add("hidden");
  document.querySelector("#runtime-import-state").textContent = runtimeImportFile.files?.[0]?.name || "Ingen fil vald";
  setMessage(runtimeImportMessage, "");
});

runtimeImportValidate.addEventListener("click", validateRuntimeImport);
runtimeImportActivate.addEventListener("click", activateRuntimeImport);

logoutButton.addEventListener("click", async () => {
  await fetch("/v1/auth/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  localStorage.removeItem("trainmeet.accessToken");
  localStorage.removeItem("trainmeet.panelID");
  state.token = null;
  state.snapshots.clear();
  clearTimeout(state.snapshotTimer);
  clearTimeout(state.adminTimer);
  setConnection("offline", "Ej ansluten");
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
  const button = adminAccessForm.querySelector("button");
  button.disabled = true;
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
    logoutButton.classList.toggle("hidden", state.authStatus?.access_mode !== "external");
    await refreshAdminAccess();
  } catch (error) {
    setMessage(adminAccessMessage, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

serverIdentityForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(serverIdentityMessage, "");
  const button = serverIdentityForm.querySelector("button");
  button.disabled = true;
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
  } catch (error) {
    setMessage(serverIdentityMessage, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#connection-badge-screens").addEventListener("change", saveConnectionBadgeSettings);
document.querySelector("#connection-badge-validity").addEventListener("change", saveConnectionBadgeSettings);

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
    action: "start",
    time: document.querySelector("#local-clock-time").value,
    speed,
  });
});

document.querySelector("#stop-local-clock").addEventListener("click", async () => {
  await controlLocalClock({
    action: "stop",
    reason: document.querySelector("#local-clock-reason").value.trim(),
  });
});

deviceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(deviceMessage, "");
  const button = deviceForm.querySelector("button");
  button.disabled = true;
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
  } catch (error) {
    setMessage(deviceMessage, error.message, "error");
  } finally {
    button.disabled = false;
  }
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
  const button = runtimeForm.querySelector("button");
  button.disabled = true;
  try {
    const response = await authorizedFetch("/v1/runtime/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        central_url: document.querySelector("#runtime-central-url").value,
        sync_code: syncCode,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Träffen kunde inte hämtas");
    setMessage(runtimeMessage, `3/3 · ${payload.message} Cloud-kopplingen är sparad på servern.`, payload.restart_required ? "notice" : "success");
    runtimeSyncCodeBoxes.reset();
    document.querySelector("#runtime-link-details").open = false;
    await Promise.all([refreshRuntime(), refreshInfo()]);
  } catch (error) {
    setMessage(runtimeMessage, error.message, "error");
    document.querySelector("#cloud-connection-state").textContent = "Kopplingen misslyckades";
  } finally {
    button.disabled = false;
  }
});

factoryResetConfirmation.addEventListener("input", () => {
  factoryResetButton.disabled = factoryResetConfirmation.value.trim().toUpperCase() !== "NOLLSTÄLL";
});

factoryResetButton.addEventListener("click", async () => {
  if (factoryResetConfirmation.value.trim().toUpperCase() !== "NOLLSTÄLL") return;
  const localFactoryReset = state.authStatus?.access_mode === "local";
  const question = localFactoryReset
    ? "All lokal TrainMeet-data och administratören tas bort. Vill du fabriksåterställa nu?"
    : "Träffdata och anslutningar tas bort. Din administratörsinloggning behålls. Vill du fortsätta?";
  if (!window.confirm(question)) return;
  factoryResetButton.disabled = true;
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
    await waitForServerReturn();
  } catch (error) {
    state.restarting = false;
    factoryResetButton.disabled = false;
    setMessage(factoryResetMessage, error.message, "error");
  }
});

const AUTO_SYNC_REASON =
  "Automatiska Cloud-uppdateringar är på — servern hämtar och aktiverar själv.";

/** Keep the manual staging controls from racing the automatic loop.

    With automatic updates on, the fifteen-second loop installs *and* activates,
    so downloading a version by hand can only duplicate what it has already
    done. The buttons stay visible rather than vanishing: an operator who just
    saw them should be told why they are inert, not left hunting for them. */
function applyCloudAutoSyncLock() {
  const automatic = runtimeAutoSync.checked;
  [runtimeDownloadUpdate, runtimeActivateUpdate].forEach((button) => {
    button.disabled = automatic;
    if (automatic) button.title = AUTO_SYNC_REASON;
    else button.removeAttribute("title");
  });
  // Only worth saying when there is a disabled button on screen to explain.
  const offering = [runtimeDownloadUpdate, runtimeActivateUpdate]
    .some((button) => !button.classList.contains("hidden"));
  runtimeAutoSyncHint.textContent = automatic && offering ? AUTO_SYNC_REASON : "";
  runtimeAutoSyncHint.classList.toggle("hidden", !(automatic && offering));
}

runtimeAutoSync.addEventListener("change", async () => {
  runtimeAutoSync.disabled = true;
  try {
    const response = await authorizedFetch("/v1/cloud/auto-sync", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: runtimeAutoSync.checked }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Inställningen kunde inte sparas");
    setMessage(runtimeMessage, payload.message, "success");
  } catch (error) {
    runtimeAutoSync.checked = !runtimeAutoSync.checked;
    setMessage(runtimeMessage, error.message, "error");
  } finally {
    runtimeAutoSync.disabled = false;
    applyCloudAutoSyncLock();
  }
});

runtimeCheckUpdate.addEventListener("click", async () => {
  setMessage(runtimeMessage, "Söker efter en ny publicerad version …");
  runtimeCheckUpdate.disabled = true;
  try {
    const response = await authorizedFetch("/v1/runtime/update");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Uppdateringen kunde inte kontrolleras");
    state.pendingPublicationID = payload.publication_id;
    runtimeDownloadUpdate.classList.toggle("hidden", !payload.update_available);
    runtimeActivateUpdate.classList.add("hidden");
    setMessage(runtimeMessage, payload.update_available
      ? "En ny version finns. Hämta den för att granska och aktivera lokalt."
      : "Servern har redan den senaste publicerade versionen.", payload.update_available ? "notice" : "success");
  } catch (error) {
    setMessage(runtimeMessage, error.message, "error");
  } finally {
    runtimeCheckUpdate.disabled = false;
    applyCloudAutoSyncLock();
  }
});

runtimeDownloadUpdate.addEventListener("click", async () => {
  setMessage(runtimeMessage, "Hämtar den nya versionen …");
  runtimeDownloadUpdate.disabled = true;
  try {
    const response = await authorizedFetch("/v1/runtime/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Versionen kunde inte hämtas");
    state.pendingPublicationID = payload.downloaded_publication_id;
    runtimeDownloadUpdate.classList.add("hidden");
    runtimeActivateUpdate.classList.remove("hidden");
    setMessage(runtimeMessage, payload.message, "notice");
    await refreshRuntime();
  } catch (error) {
    setMessage(runtimeMessage, error.message, "error");
  } finally {
    applyCloudAutoSyncLock();
  }
});

runtimeActivateUpdate.addEventListener("click", async () => {
  setMessage(runtimeMessage, "Aktiverar den hämtade versionen …");
  runtimeActivateUpdate.disabled = true;
  try {
    const response = await authorizedFetch("/v1/runtime/activate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ publication_id: state.pendingPublicationID }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Versionen kunde inte aktiveras");
    runtimeActivateUpdate.classList.add("hidden");
    state.pendingPublicationID = null;
    state.restartRequired = !!payload.restart_required;
    setRestartButtonsVisible(state.restartRequired);
    setMessage(runtimeMessage, payload.message, payload.restart_required ? "notice" : "success");
    await Promise.all([refreshRuntime(), refreshInfo()]);
  } catch (error) {
    setMessage(runtimeMessage, error.message, "error");
  } finally {
    applyCloudAutoSyncLock();
  }
});

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
  updateStepSubtitles(null);

  renderUpdateProgress(payload);
  renderVersionMove(payload);
  renderTechnicalDetails(payload);

  const failed = payload.status === "failed";
  const running = (payload.steps || []).some((step) => step.state === "active");
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

configForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveConfiguration(false);
});

document.querySelector("#activate-config").addEventListener("click", async () => {
  await saveConfiguration(true);
});

restartButtons.forEach((button) => button.addEventListener("click", restartServer));

configForm.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button || !state.config) return;
  event.preventDefault();
  syncConfigurationFromDOM();
  const action = button.dataset.action;
  const index = Number(button.dataset.index);

  if (action === "add-station") {
    state.config.stations.push({ id: uniqueID("station"), code: nextStationCode(), name: "Ny station" });
  } else if (action === "remove-station") {
    const removedID = state.config.stations[index]?.id;
    state.config.stations.splice(index, 1);
    state.config.connections = state.config.connections.filter(
      (item) => item.station_a_id !== removedID && item.station_b_id !== removedID,
    );
    state.config.panels = state.config.panels.filter((item) => item.station_id !== removedID);
  } else if (action === "station-up" && index > 0) {
    [state.config.stations[index - 1], state.config.stations[index]] = [
      state.config.stations[index],
      state.config.stations[index - 1],
    ];
  } else if (action === "station-down" && index < state.config.stations.length - 1) {
    [state.config.stations[index + 1], state.config.stations[index]] = [
      state.config.stations[index],
      state.config.stations[index + 1],
    ];
  } else if (action === "add-connection") {
    addConnection();
  } else if (action === "remove-connection") {
    const removedID = state.config.connections[index]?.id;
    state.config.connections.splice(index, 1);
    for (const panel of state.config.panels) {
      for (const key of slotKeys) if (panel.slots[key] === removedID) panel.slots[key] = null;
    }
  } else if (action === "add-panel") {
    addPanel();
  } else if (action === "remove-panel") {
    state.config.panels.splice(index, 1);
  } else if (action === "build-chain") {
    buildStationChain();
  }
  renderConfiguration();
});

panelEditor.addEventListener("change", (event) => {
  if (!event.target.matches("select[data-field='station_id']")) return;
  syncConfigurationFromDOM();
  const panel = state.config.panels[Number(event.target.closest(".panel-row").dataset.index)];
  panel.slots = { A: null, B: null, C: null, D: null };
  renderConfiguration();
});

async function openApplication() {
  setup.classList.add("hidden");
  login.classList.add("hidden");
  appView.classList.remove("hidden");
  logoutButton.classList.toggle("hidden", state.authStatus?.access_mode !== "external");
  setMode(localStorage.getItem("trainmeet.mode") === "bygg" ? "bygg" : "kor");
  try {
    await Promise.all([
      refreshInfo(),
      refreshAdminAccess(),
      loadLocalConfiguration(),
      refreshDevices(),
      refreshRuntime(),
      refreshLocalClock(),
    ]);
    setConnection(
      "online",
      state.authStatus?.access_mode === "external" ? "Externt ansluten" : "Lokalt ansluten",
    );
    scheduleAdminRefresh();
  } catch (error) {
    handleConnectionError(error);
  }
}


// ------------------------------------------------------------ TKL-terminalen
// Inbäddad enligt DEL 3.5. Iframen monteras först när fliken visas: en TKL som
// ligger och kör i bakgrunden håller en MQTT-anslutning i onödan.
/** Stationsväljaren ovanför den inbäddade terminalen (DEL 3.5). */
function fillTklStations(stations) {
  const select = document.querySelector("#tkl-station");
  if (!select) return;
  const signature = stations.map((station) => station.id).join("|");
  if (select.dataset.signature === signature) return;
  select.dataset.signature = signature;
  select.replaceChildren();
  for (const station of stations) {
    const option = document.createElement("option");
    option.value = station.id;
    option.textContent = station.name || station.id;
    select.append(option);
  }
  if (!select.dataset.bound) {
    select.dataset.bound = "1";
    select.addEventListener("change", mountTklFrame);
  }
}

function mountTklFrame() {
  const frame = document.querySelector("#tkl-frame");
  if (!frame) return;
  const station = document.querySelector("#tkl-station")?.value || "";
  const target = station ? `/tkl/?station=${encodeURIComponent(station)}` : "/tkl/";
  if (frame.getAttribute("src") !== target) frame.setAttribute("src", target);
  const link = document.querySelector("#tkl-open-tab");
  if (link) link.setAttribute("href", target);
}

function unmountTklFrame() {
  const frame = document.querySelector("#tkl-frame");
  if (frame && frame.getAttribute("src") !== "about:blank") frame.setAttribute("src", "about:blank");
}

// ------------------------------------------------------------------- Trafik
let trafficTimer = null;

function startTrafficView() {
  renderTrafficView();
  clearInterval(trafficTimer);
  trafficTimer = setInterval(renderTrafficView, 4000);
}

function stopTrafficView() {
  clearInterval(trafficTimer);
  trafficTimer = null;
}

function updateRuntimeNavigation(configured) {
  document.querySelectorAll("[data-requires-runtime]").forEach((element) => {
    element.classList.toggle("hidden", !configured);
  });
  if (!configured && ["tmbox", "skarmar", "trafik", "tkl"].includes(state.runTab)) selectRunTab("oversikt");
}


function scheduleAdminRefresh() {
  clearTimeout(state.adminTimer);
  state.adminTimer = setTimeout(async () => {
    if (!state.authStatus?.authenticated) return;
    await Promise.allSettled([refreshInfo(), refreshDevices(), refreshRuntime(), refreshAdminAccess(), refreshLocalClock()]);
    scheduleAdminRefresh();
  }, 5000);
}


async function refreshInfo() {
  const response = await authorizedFetch("/v1/info");
  if (!response.ok) return;
  const info = await response.json();
  document.querySelector("#server-name").textContent = info.gateway_id || "TrainMeet Server";
  document.querySelector("#server-detail").textContent =
    `Kör lokalt · aktiv trafiksession: ${info.traffic_session_name}`;
  document.querySelector("#system-server-name").textContent = info.runtime?.server_name || info.gateway_id || "TrainMeet Server";
  document.querySelector("#system-runtime-name").textContent = info.runtime?.configured ? info.runtime.meet_name : "Ingen aktiv träff";
  document.querySelector("#system-cloud-state").textContent = info.runtime?.linked ? "Kopplad" : "Inte kopplad";
  const serverNameInput = document.querySelector("#admin-server-name");
  if (document.activeElement !== serverNameInput) {
    serverNameInput.value = info.runtime?.server_name || info.gateway_id || "";
  }
  const pill = document.querySelector("#runtime-pill");
  if (info.runtime?.configured) {
    pill.textContent = `${info.runtime.meet_name} · ${info.runtime.active_day}`;
    pill.classList.add("active");
    document.querySelector("#overview-runtime-state").textContent = "Lokalt aktiv";
    document.querySelector("#sidebar-runtime-name").textContent = info.runtime.meet_name;
    document.querySelector("#sidebar-runtime-status").textContent = `${info.runtime.active_day} · ${info.runtime.linked ? "Cloud kopplad" : "lokal config"}`;
  } else {
    pill.textContent = info.runtime?.error ? "Konfigurationen behöver rättas" : "Ingen träff aktiverad";
    pill.classList.remove("active");
    document.querySelector("#overview-runtime-state").textContent = info.runtime?.error
      ? "Konfigurationsfel"
      : "Ej konfigurerad";
    document.querySelector("#sidebar-runtime-name").textContent = "Ingen aktiv träff";
    document.querySelector("#sidebar-runtime-status").textContent = info.runtime?.error
      ? "Importera en rättad version"
      : "Konfiguration krävs";
  }
  updateRuntimeNavigation(Boolean(info.runtime?.configured));
  updateRestartButton(Boolean(info.restart_required));
}

async function refreshAdminAccess() {
  const response = await authorizedFetch("/v1/admin/access");
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "Åtkomstinställningen kunde inte läsas");
  document.querySelector("#admin-username").value = payload.username || "";

  // Chippet svarar på "hur är jag inne just nu", vilket är vad paketets
  // "Lokal åtkomst" säger, och det kommer ur /v1/auth/status - inte ur en
  // gissning här. Om ett externt lösenord finns är en annan fråga, och den
  // står på egen rad i stället för att trängas in i samma chip.
  const external = state.authStatus?.access_mode === "external";
  const badge = document.querySelector("#access-mode");
  badge.textContent = external ? "Extern inloggning" : "Lokal åtkomst";
  badge.classList.toggle("active", external);

  const passwordState = document.querySelector("#access-password-state");
  passwordState.textContent = payload.password_configured
    ? "Ett lösenord är satt, så andra enheter kan logga in."
    : "Inget lösenord är satt än, så inloggning utifrån är avstängd.";
  passwordState.classList.toggle("is-missing", !payload.password_configured);
}

async function loadLocalConfiguration() {
  const response = await authorizedFetch("/v1/local-configuration");
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "Konfigurationen kunde inte läsas");
  state.configRevision = payload.revision || 0;
  state.config = payload.draft;
  renderConfiguration();
}

async function saveConfiguration(activate) {
  syncConfigurationFromDOM();
  setMessage(configMessage, activate ? "Sparar och aktiverar …" : "Sparar utkast …");
  const buttons = configForm.querySelectorAll("button");
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const saveResponse = await authorizedFetch("/v1/local-configuration", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: state.configRevision, draft: state.config }),
    });
    const saved = await saveResponse.json();
    if (!saveResponse.ok) throw new Error(saved.message || "Konfigurationen kunde inte sparas");
    state.configRevision = saved.revision;
    state.config = saved.draft;
    renderConfiguration();

    if (!activate) {
      setMessage(configMessage, "Utkastet är sparat lokalt på servern.", "success");
      return;
    }
    const activateResponse = await authorizedFetch("/v1/local-configuration/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: state.configRevision }),
    });
    const result = await activateResponse.json();
    if (!activateResponse.ok) throw new Error(result.message || "Konfigurationen kunde inte aktiveras");
    setMessage(configMessage, result.message, result.restart_required ? "notice" : "success");
    updateRestartButton(Boolean(result.restart_required));
    await Promise.all([refreshRuntime(), refreshInfo()]);
  } catch (error) {
    setMessage(configMessage, error.message, "error");
    if (/annan klient/i.test(error.message)) await loadLocalConfiguration();
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

async function restartServer() {
  if (state.restarting || !state.restartRequired) return;
  if (!window.confirm("Starta om TrainMeet Server och börja använda den aktiverade stationsplanen?")) return;
  state.restarting = true;
  setRestartButtonsDisabled(true);
  setMessage(configMessage, "Startar om TrainMeet Server …", "notice");
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
    setMessage(configMessage, payload.message, "notice");
    await waitForServerReturn();
  } catch (error) {
    state.restarting = false;
    setRestartButtonsDisabled(false);
    setMessage(configMessage, error.message, "error");
    scheduleAdminRefresh();
  }
}

async function waitForServerReturn() {
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
  setMessage(configMessage, "Servern har inte kommit tillbaka ännu. Kontrollera ström och nätverk.", "error");
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

function renderConfiguration() {
  const config = state.config;
  if (!config) return;
  document.querySelector("#meet-name").value = config.name || "";
  document.querySelector("#dispatch-mode").value = config.default_dispatch_mode || "clearance";
  document.querySelector("#clock-time").value = config.clock_time || "12:00";
  document.querySelector("#active-day").value = config.active_day || "Dagl";
  document.querySelector("#draft-revision").textContent = state.configRevision
    ? `Utkast ${state.configRevision}`
    : "Nytt utkast";

  stationEditor.innerHTML = config.stations.length
    ? config.stations.map((station, index) => `
      <div class="editor-row station-row" data-index="${index}">
        <span class="sequence-number">${index + 1}</span>
        <label>Kod<input data-field="code" maxlength="8" value="${escapeHTML(station.code)}"></label>
        <label class="grow">Stationsnamn<input data-field="name" maxlength="100" value="${escapeHTML(station.name)}"></label>
        <div class="row-actions">
          <button type="button" class="icon-button" data-action="station-up" data-index="${index}" title="Flytta upp" ${index === 0 ? "disabled" : ""}>↑</button>
          <button type="button" class="icon-button" data-action="station-down" data-index="${index}" title="Flytta ned" ${index === config.stations.length - 1 ? "disabled" : ""}>↓</button>
          <button type="button" class="icon-button danger" data-action="remove-station" data-index="${index}" title="Ta bort">×</button>
        </div>
      </div>`).join("")
    : emptyEditor("Inga stationer ännu", "Lägg till stationerna i den ordning de ligger på banan.");

  connectionEditor.innerHTML = config.connections.length
    ? config.connections.map((connection, index) => `
      <div class="editor-row connection-row" data-index="${index}">
        <span class="sequence-number">${index + 1}</span>
        <label>Från<select data-field="station_a_id">${stationOptions(connection.station_a_id)}</select></label>
        <label>Till<select data-field="station_b_id">${stationOptions(connection.station_b_id)}</select></label>
        <label>Spår<select data-field="track_type">
          <option value="single" ${connection.track_type === "single" ? "selected" : ""}>Enkelspår</option>
          <option value="double" ${connection.track_type === "double" ? "selected" : ""}>Dubbelspår</option>
        </select></label>
        <label>Trafikläge<select data-field="dispatch_mode_override">
          <option value="" ${!connection.dispatch_mode_override ? "selected" : ""}>Träffens läge</option>
          <option value="clearance" ${connection.dispatch_mode_override === "clearance" ? "selected" : ""}>Begär och bekräfta</option>
          <option value="direct" ${connection.dispatch_mode_override === "direct" ? "selected" : ""}>Direkt om ledigt</option>
        </select></label>
        <button type="button" class="icon-button danger" data-action="remove-connection" data-index="${index}" title="Ta bort">×</button>
      </div>`).join("")
    : emptyEditor("Inga sträckor ännu", "Bygg automatiskt från stationsordningen eller lägg till en sträcka manuellt.");

  panelEditor.innerHTML = config.panels.length
    ? config.panels.map((panel, index) => `
      <div class="editor-row panel-row" data-index="${index}">
        <span class="sequence-number">${index + 1}</span>
        <label>Station<select data-field="station_id">${stationOptions(panel.station_id)}</select></label>
        <label class="grow">Panelnamn<input data-field="name" maxlength="100" value="${escapeHTML(panel.name)}"></label>
        <div class="slot-grid">
          ${slotKeys.map((key) => `<label><b>${key}</b><select data-slot="${key}">${connectionOptions(panel.station_id, panel.slots[key])}</select></label>`).join("")}
        </div>
        <button type="button" class="icon-button danger" data-action="remove-panel" data-index="${index}" title="Ta bort">×</button>
      </div>`).join("")
    : emptyEditor("Inga paneler ännu", "Varje station som ska användas behöver minst en TMBox-panel.");
}

function syncConfigurationFromDOM() {
  if (!state.config) return;
  state.config.name = document.querySelector("#meet-name").value.trim();
  state.config.id = state.config.id || `local-${slugify(state.config.name) || uniqueID("meet")}`;
  state.config.default_dispatch_mode = document.querySelector("#dispatch-mode").value;
  state.config.clock_time = document.querySelector("#clock-time").value || "12:00";
  state.config.active_day = document.querySelector("#active-day").value.trim() || "Dagl";

  state.config.stations = [...stationEditor.querySelectorAll(".station-row")].map((row, index) => ({
    ...state.config.stations[index],
    code: row.querySelector("[data-field='code']").value.trim().toUpperCase(),
    name: row.querySelector("[data-field='name']").value.trim(),
  }));
  state.config.connections = [...connectionEditor.querySelectorAll(".connection-row")].map((row, index) => ({
    ...state.config.connections[index],
    station_a_id: row.querySelector("[data-field='station_a_id']").value,
    station_b_id: row.querySelector("[data-field='station_b_id']").value,
    track_type: row.querySelector("[data-field='track_type']").value,
    dispatch_mode_override: row.querySelector("[data-field='dispatch_mode_override']").value || null,
  }));
  state.config.panels = [...panelEditor.querySelectorAll(".panel-row")].map((row, index) => ({
    ...state.config.panels[index],
    station_id: row.querySelector("[data-field='station_id']").value,
    name: row.querySelector("[data-field='name']").value.trim(),
    slots: Object.fromEntries(slotKeys.map((key) => [key, row.querySelector(`[data-slot='${key}']`).value || null])),
  }));
}

function addConnection() {
  const stations = state.config.stations;
  if (stations.length < 2) {
    setMessage(configMessage, "Lägg till minst två stationer först.", "error");
    return;
  }
  state.config.connections.push({
    id: uniqueID("connection"),
    station_a_id: stations[0].id,
    station_b_id: stations[1].id,
    track_type: "single",
    dispatch_mode_override: null,
    display_side_a: "right",
    display_side_b: "left",
    display_order_a: 0,
    display_order_b: 0,
  });
}

function addPanel() {
  const station = state.config.stations[0];
  if (!station) {
    setMessage(configMessage, "Lägg till en station först.", "error");
    return;
  }
  state.config.panels.push({
    id: uniqueID("panel"),
    station_id: station.id,
    name: `${station.code} TMBox`,
    slots: { A: null, B: null, C: null, D: null },
  });
}

function buildStationChain() {
  const stations = state.config.stations;
  if (stations.length < 2) {
    setMessage(configMessage, "Lägg till minst två stationer först.", "error");
    return;
  }
  if ((state.config.connections.length || state.config.panels.length)
      && !window.confirm("Detta bygger om nuvarande sträckor och paneler från stationsordningen. Fortsätta?")) return;

  state.config.connections = stations.slice(0, -1).map((station, index) => ({
    id: uniqueID("connection"),
    station_a_id: station.id,
    station_b_id: stations[index + 1].id,
    track_type: "single",
    dispatch_mode_override: null,
    display_side_a: "right",
    display_side_b: "left",
    display_order_a: 0,
    display_order_b: 0,
  }));
  state.config.panels = stations.map((station) => {
    const incident = state.config.connections.filter(
      (connection) => connection.station_a_id === station.id || connection.station_b_id === station.id,
    );
    return {
      id: uniqueID("panel"),
      station_id: station.id,
      name: `${station.code} TMBox`,
      slots: Object.fromEntries(slotKeys.map((key, index) => [key, incident[index]?.id || null])),
    };
  });
  setMessage(configMessage, "Stationskedjan är byggd. Kontrollera A–D-valen och spara.", "success");
}

function stationOptions(selectedID) {
  return state.config.stations.map((station) =>
    `<option value="${escapeHTML(station.id)}" ${station.id === selectedID ? "selected" : ""}>${escapeHTML(station.code)} · ${escapeHTML(station.name)}</option>`
  ).join("");
}

function connectionOptions(stationID, selectedID) {
  const connections = state.config.connections.filter(
    (connection) => connection.station_a_id === stationID || connection.station_b_id === stationID,
  );
  return `<option value="">Inte använd</option>${connections.map((connection) => {
    const otherID = connection.station_a_id === stationID ? connection.station_b_id : connection.station_a_id;
    const other = state.config.stations.find((station) => station.id === otherID);
    const label = other ? `${other.code} · ${connection.track_type === "double" ? "dubbelspår" : "enkelspår"}` : "Okänd sträcka";
    return `<option value="${escapeHTML(connection.id)}" ${connection.id === selectedID ? "selected" : ""}>${escapeHTML(label)}</option>`;
  }).join("")}`;
}

function emptyEditor(title, detail) {
  return `<div class="empty-editor"><b>${escapeHTML(title)}</b><span>${escapeHTML(detail)}</span></div>`;
}

async function refreshDevices() {
  const response = await authorizedFetch("/v1/devices");
  if (!response.ok) return;
  const payload = await response.json();
  // Samma svar föder både listan, stegräckan och TKL:s stationsväljare, så
  // de kan inte visa olika många.
  state.devices = payload.devices || [];
  state.stations = payload.stations || [];
  updateStepSubtitles(null);
  fillTklStations(state.stations);
  const list = document.querySelector("#device-list");
  updateStationOptions(payload.stations || []);
  list.replaceChildren();
  if (!payload.devices.length) {
    list.innerHTML = '<div class="empty-status">Ingen fysisk TMBox har presenterat sig ännu.</div>';
    return;
  }
  for (const device of payload.devices) {
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
      : "Väntar på station";
    row.append(identity, assignment);
    list.append(row);
  }
}

function updateStationOptions(stations) {
  const signature = stations.map((station) => `${station.id}:${station.code}`).join("|");
  if (deviceStation.dataset.signature === signature) return;
  deviceStation.dataset.signature = signature;
  const previous = deviceStation.value;
  deviceStation.replaceChildren();
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
  const runtime = await response.json();
  // Stegräckans underrubriker läser samma svar. Ett andra anrop skulle bara
  // kunna ge ett annat tal än det vyn just visat.
  state.runtime = runtime;
  updateStepSubtitles(null);
  const status = document.querySelector("#runtime-status");
  status.replaceChildren();
  const row = document.createElement("div");
  row.className = "status-row";
  const identity = document.createElement("div");
  const title = document.createElement("b");
  const detail = document.createElement("small");
  if (runtime.configured) {
    title.textContent = runtime.meet_name;
    detail.textContent = `${runtime.station_count} stationer · ${runtime.train_count} tågrörelser · ${runtime.linked ? "Cloud kopplad" : "lokal konfiguration"}`;
  } else {
    title.textContent = runtime.error ? "Träffkonfigurationen kunde inte aktiveras" : "Ingen träff aktiverad";
    detail.textContent = runtime.error
      ? `${runtime.error}. Hämta eller aktivera en rättad version; den tidigare versionen är sparad.`
      : "Koppla en konfigurationsserver eller bygg en lokal träff";
  }
  runtimeCheckUpdate.classList.toggle("hidden", !runtime.linked);
  runtimeAutoSync.checked = !!runtime.cloud_auto_sync;
  runtimeAutoSync.disabled = !runtime.linked;
  if (runtime.central_url) document.querySelector("#runtime-central-url").value = runtime.central_url;
  if (runtime.available_publication_id) {
    state.pendingPublicationID = runtime.available_publication_id;
    runtimeActivateUpdate.classList.remove("hidden");
  }
  applyCloudAutoSyncLock();
  identity.append(title, detail);
  const day = document.createElement("span");
  day.textContent = runtime.active_day || "–";
  row.append(identity, day);
  status.append(row);

  const cloudMeet = document.querySelector("#cloud-connection-meet");
  const cloudMeta = document.querySelector("#cloud-connection-meta");
  const cloudState = document.querySelector("#cloud-connection-state");
  const cloudDetails = document.querySelector("#runtime-link-details");
  cloudMeet.textContent = runtime.linked ? runtime.meet_name : "Ingen Cloud-koppling";
  const publicationTime = runtime.published_at
    ? new Date(runtime.published_at).toLocaleString("sv-SE", { dateStyle: "medium", timeStyle: "short" })
    : null;
  cloudMeta.textContent = runtime.linked
    ? `${runtime.central_url || "TrainMeet Cloud"} · ${runtime.station_count} stationer · ${runtime.train_count} tågrörelser${publicationTime ? ` · publicerad ${publicationTime}` : ""}`
    : "Koppla en publicerad träff med en sexsiffrig kod.";
  cloudState.textContent = runtime.linked ? "Kopplad" : "Inte kopplad";
  cloudState.classList.toggle("active", runtime.linked);
  const cloudSteps = {
    server: document.querySelector("#cloud-step-server"),
    code: document.querySelector("#cloud-step-code"),
    version: document.querySelector("#cloud-step-version"),
    sync: document.querySelector("#cloud-step-sync"),
  };
  cloudSteps.server.classList.add("is-complete");
  cloudSteps.server.querySelector("small").textContent = runtime.central_url || "Config-server vald";
  cloudSteps.code.classList.toggle("is-complete", runtime.linked);
  cloudSteps.code.querySelector("small").textContent = runtime.linked ? "Koppling sparad" : "Väntar på sexsiffrig kod";
  cloudSteps.version.classList.toggle("is-complete", runtime.configured);
  cloudSteps.version.querySelector("small").textContent = runtime.configured ? `${runtime.meet_name} finns lokalt` : "Ingen lokal version";
  cloudSteps.sync.classList.toggle("is-complete", runtime.linked && runtime.cloud_auto_sync);
  cloudSteps.sync.querySelector("small").textContent = runtime.cloud_auto_sync ? "Automatisk var 15:e sekund" : (runtime.linked ? "Manuell uppdatering" : "Aktiveras efter koppling");
  if (!state.runtimeLinkInitialized) {
    cloudDetails.open = !runtime.linked;
    state.runtimeLinkInitialized = true;
  }
}

async function refreshLocalClock() {
  const response = await fetch("/v1/display", { cache: "no-store" });
  if (!response.ok) return;
  const payload = await response.json();
  state.overviewSnapshot = payload;
  renderOverview(payload);
  const clock = payload.clock || {};
  const timeInput = document.querySelector("#local-clock-time");
  if (document.activeElement !== timeInput) timeInput.value = clock.time || "12:00:00";
  const speedInput = document.querySelector("#local-clock-speed");
  if (document.activeElement !== speedInput) speedInput.value = Number(clock.speed || 1);
  const stateLabel = document.querySelector("#clock-state");
  stateLabel.textContent = clock.running ? `Går · ${Number(clock.speed || 1)}×` : "Stoppad";
  stateLabel.classList.toggle("clock-running", Boolean(clock.running));
  renderConnectionBadgeSettings(payload.connection || {});
}

function renderConnectionBadgeSettings(connection) {
  const container = document.querySelector("#connection-badge-screens");
  if (!container) return;
  const screens = connection.screens || [];
  for (const input of container.querySelectorAll("input[type=checkbox]")) {
    if (document.activeElement !== input) input.checked = screens.includes(input.value);
  }
  const validity = document.querySelector("#connection-badge-validity");
  if (validity && document.activeElement !== validity) validity.value = String(connection.validity_hours ?? 0);
  const badge = document.querySelector("#connection-badge-code");
  badge.textContent = connection.code
    ? `${connection.host}:${connection.port} · ${connection.code}`
    : "Ingen kod utfärdad";
}

async function saveConnectionBadgeSettings() {
  const container = document.querySelector("#connection-badge-screens");
  const message = document.querySelector("#connection-badge-message");
  const screens = [...container.querySelectorAll("input[type=checkbox]")]
    .filter((input) => input.checked)
    .map((input) => input.value);
  try {
    const response = await authorizedFetch("/v1/display/connection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        screens,
        validity_hours: Number(document.querySelector("#connection-badge-validity").value),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Inställningen kunde inte sparas");
    setMessage(
      message,
      payload.restart_required
        ? "Sparat. Starta om servern för att ge koden den nya giltighetstiden."
        : screens.length
          ? `Sparat. Koden visas på ${screens.length} av 4 skärmar.`
          : "Sparat. Koden visas inte på någon skärm.",
      "success",
    );
  } catch (error) {
    setMessage(message, error.message, "error");
  }
}

async function controlLocalClock(command) {
  const buttons = [...clockControlForm.querySelectorAll("button")];
  buttons.forEach((button) => { button.disabled = true; });
  setMessage(clockControlMessage, "Uppdaterar den lokala klockan …", "notice");
  try {
    const response = await authorizedFetch("/v1/clock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Klockan kunde inte uppdateras");
    setMessage(
      clockControlMessage,
      payload.running ? `Klockan går från ${payload.time.slice(0, 5)} i ${Number(payload.speed)}×.` : "Klockan är stoppad.",
      "success",
    );
    await refreshLocalClock();
  } catch (error) {
    setMessage(clockControlMessage, error.message, "error");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
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
    ? neighbors.map(({ station: neighbor, connection }) => `<span>${escapeHTML(neighbor.name)} · ${connection.track_type === "double" ? "dubbelspår" : "enkelspår"}</span>`).join("")
    : "<span>Fristående station</span>";
  document.querySelector("#station-inspector-trains").innerHTML = trains.length
    ? trains.slice(0, 5).map((train) => `<li><button type="button" data-train-number="${escapeHTML(train.trainNumber)}"><b>${escapeHTML(train.trainNumber)}</b><span>${escapeHTML(train.kind)} ${escapeHTML(train.time)}</span></button></li>`).join("")
    : "<li>Inga tåg i tidtabellen.</li>";
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

  document.querySelector("#admin-active-meet").textContent = snapshot.meet?.name || "Lokal träff";
  document.querySelector("#admin-active-detail").textContent = `${snapshot.meet?.default_dispatch_mode === "direct" ? "Direkttrafik" : "Tåganmälan"} · ${services.length} tågrutter från den aktiva tidtabellen`;
  document.querySelector("#admin-active-day").textContent = snapshot.active_day || "Dagl";
  document.querySelector("#admin-active-stations").textContent = stations.length;
  document.querySelector("#admin-active-connections").textContent = connections.length;
  document.querySelector("#admin-active-trains").textContent = services.length;
  document.querySelector("#admin-active-clock").textContent = clockTime;
  document.querySelector("#admin-active-station-list").innerHTML = orderedStations(snapshot).map((station) => `<span><b>${escapeHTML(station.code || "–")}</b>${escapeHTML(station.name)}</span>`).join("");
  renderActiveRuntimePlan(snapshot);

  document.querySelector("#display-card-topology").textContent = `${stations.length} stationer · ${connections.length} sträckor`;
  document.querySelector("#display-card-graph").textContent = `${services.length} tåg · ${snapshot.active_day || "Dagl"}`;
  document.querySelector("#display-card-clock").textContent = clockState;
  document.querySelector("#display-card-dashboard").textContent = `${activeTrains} aktiva tåg · ${activeConnections} upptagna sträckor`;
}

function renderActiveRuntimePlan(snapshot) {
  if (!snapshot) return;
  const stations = snapshot.stations || [];
  const connections = snapshot.connections || [];
  const stationByID = new Map(stations.map((station) => [station.id, station]));
  const connectionList = document.querySelector("#admin-active-connection-list");
  const panelList = document.querySelector("#admin-active-panel-list");

  connectionList.innerHTML = connections.length
    ? connections.map((connection) => {
      const stationA = stationByID.get(connection.station_a_id);
      const stationB = stationByID.get(connection.station_b_id);
      const endpointA = stationA?.code || stationA?.name || "?";
      const endpointB = stationB?.code || stationB?.name || "?";
      const keys = [connection.tambox_key_a, connection.tambox_key_b].filter(Boolean).join(" / ");
      const detail = `${connection.track_type === "double" ? "Dubbelspår" : "Enkelspår"}${keys ? ` · ${keys}` : ""}`;
      return `<div class="runtime-plan-row"><b>${escapeHTML(endpointA)} ↔ ${escapeHTML(endpointB)}</b><span>${escapeHTML(detail)}</span></div>`;
    }).join("")
    : '<div class="runtime-plan-empty">Inga aktiva sträckor.</div>';

  const stationIDs = new Set(stations.map((station) => station.id));
  const panels = [...state.snapshots.values()]
    .filter((panel) => stationIDs.has(panel.station_id))
    .sort((a, b) => String(a.panel_name).localeCompare(String(b.panel_name), "sv"));
  panelList.innerHTML = panels.length
    ? panels.map((panel) => {
      const assignments = slotKeys.flatMap((key) => {
        const slot = panel.slots?.[key];
        return slot?.connection_id ? [`${key}→${slot.station_code || "?"}`] : [];
      }).join(" · ");
      return `<div class="runtime-plan-row"><b>${escapeHTML(panel.panel_name)}</b><span>${escapeHTML(assignments || "Ingen A–D-koppling")}</span></div>`;
    }).join("")
    : '<div class="runtime-plan-empty">Panelerna läses in …</div>';

  document.querySelector("#admin-active-connection-label").textContent = `${connections.length} konfigurerade`;
  copyActiveRuntimeButton.disabled = stations.length === 0;
}

async function validateRuntimeImport() {
  const file = runtimeImportFile.files?.[0];
  if (!file) return;
  runtimeImportValidate.disabled = true;
  runtimeImportActivate.classList.add("hidden");
  runtimeImportReview.classList.add("hidden");
  setMessage(runtimeImportMessage, "Läser och validerar hela driftpaketet …", "notice");
  try {
    if (file.size > 3_500_000) throw new Error("Runtime-filen får vara högst 3,5 MB");
    const parsed = JSON.parse(await file.text());
    const packageValue = parsed?.package && typeof parsed.package === "object" ? parsed.package : parsed;
    const response = await authorizedFetch("/v1/runtime/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ package: packageValue }),
    });
    const validation = await response.json();
    if (!response.ok) throw new Error(validation.message || "Driftpaketet är inte giltigt");
    state.pendingImportPackage = packageValue;
    state.pendingImportValidation = validation;
    renderRuntimeImportReview(validation);
    runtimeImportActivate.classList.remove("hidden");
    document.querySelector("#runtime-import-state").textContent = "Validerad";
    setMessage(
      runtimeImportMessage,
      `${validation.meet.name} är validerad. Ingen körande data har ändrats ännu.`,
      validation.warnings.length ? "notice" : "success",
    );
  } catch (error) {
    state.pendingImportPackage = null;
    state.pendingImportValidation = null;
    document.querySelector("#runtime-import-state").textContent = "Kontrollera filen";
    setMessage(runtimeImportMessage, error instanceof SyntaxError ? "Filen innehåller inte giltig JSON." : error.message, "error");
  } finally {
    runtimeImportValidate.disabled = false;
  }
}

function renderRuntimeImportReview(validation) {
  const counts = validation.counts;
  document.querySelector("#runtime-import-facts").innerHTML = `
    <div><b>${counts.stations}</b><span>stationer</span></div>
    <div><b>${counts.operating_points}</b><span>driftplatser</span></div>
    <div><b>${counts.connections}</b><span>sträckor</span></div>
    <div><b>${counts.services}</b><span>tågrutter</span></div>
    <div><b>${counts.timetable_rows}</b><span>tågrörelser</span></div>`;
  const warningBox = document.querySelector("#runtime-import-warnings");
  warningBox.classList.toggle("hidden", validation.warnings.length === 0);
  warningBox.innerHTML = validation.warnings.length
    ? `<b>Kontrollera före aktivering</b><ul>${validation.warnings.map((warning) => `<li>${escapeHTML(warning)}</li>`).join("")}</ul>`
    : "";
  document.querySelector("#runtime-import-stations").innerHTML = validation.stations.map((station) => {
    const operatingPoints = station.operating_points?.length
      ? station.operating_points.map((point) => `${point.name}: ${point.tracks.join(", ") || "inga spår"} · ${point.timetable_rows} rader`).join(" · ")
      : "";
    return `<tr>
      <th><b>${escapeHTML(station.code)}</b><span>${escapeHTML(station.name)}</span>${operatingPoints ? `<small>${escapeHTML(operatingPoints)}</small>` : ""}</th>
      <td>${station.track_count}</td>
      <td>${station.connection_count}</td>
      <td>${station.panel_count}</td>
      <td>${station.timetable_rows}</td>
    </tr>`;
  }).join("");
  runtimeImportReview.classList.remove("hidden");
}

async function activateRuntimeImport() {
  if (!state.pendingImportPackage || !state.pendingImportValidation) return;
  const name = state.pendingImportValidation.meet.name;
  if (!window.confirm(`Importera och aktivera ${name}? Den nuvarande träffen ligger kvar i historiken men den nya blir aktiv.`)) return;
  runtimeImportActivate.disabled = true;
  setMessage(runtimeImportMessage, "Importerar och aktiverar den validerade träffen …", "notice");
  try {
    const response = await authorizedFetch("/v1/runtime/install", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ package: state.pendingImportPackage }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || "Driftpaketet kunde inte importeras");
    state.restartRequired = Boolean(result.restart_required);
    setRestartButtonsVisible(state.restartRequired);
    document.querySelector("#runtime-import-state").textContent = result.restart_required ? "Aktiverad · omstart krävs" : "Aktiverad";
    setMessage(runtimeImportMessage, result.message, result.restart_required ? "notice" : "success");
    await Promise.all([refreshRuntime(), refreshInfo()]);
  } catch (error) {
    setMessage(runtimeImportMessage, error.message, "error");
  } finally {
    runtimeImportActivate.disabled = false;
  }
}

function copyActiveRuntimeToDraft() {
  const snapshot = state.overviewSnapshot;
  if (!snapshot?.stations?.length) {
    setMessage(configMessage, "Det finns ingen aktiv stationsplan att kopiera.", "error");
    return;
  }
  const currentHasContent = Boolean(
    state.config?.stations?.length
    || state.config?.connections?.length
    || state.config?.panels?.length
  );
  if (currentHasContent && !window.confirm("Ersätt det lokala utkastet i formuläret med den aktiva träffen? Inget sparas förrän du väljer Spara utkast.")) return;

  const stationIDs = new Set(snapshot.stations.map((station) => station.id));
  const panels = [...state.snapshots.values()]
    .filter((panel) => stationIDs.has(panel.station_id))
    .map((panel) => ({
      id: panel.panel_id,
      station_id: panel.station_id,
      name: panel.panel_name,
      slots: Object.fromEntries(slotKeys.map((key) => [key, panel.slots?.[key]?.connection_id || null])),
    }));

  state.config = {
    schema_version: 1,
    id: `local-${snapshot.meet?.id || slugify(snapshot.meet?.name || "trainmeet")}`,
    name: snapshot.meet?.name || "Lokal träff",
    timezone: snapshot.meet?.timezone || "Europe/Stockholm",
    active_day: snapshot.active_day || snapshot.meet?.active_day || "Dagl",
    default_dispatch_mode: snapshot.meet?.default_dispatch_mode === "direct" ? "direct" : "clearance",
    clock_time: String(snapshot.meet?.clock_time || snapshot.clock?.time || "12:00").slice(0, 5),
    stations: orderedStations(snapshot).map((station) => ({
      id: station.id,
      code: station.code,
      name: station.name,
    })),
    connections: (snapshot.connections || []).map((connection) => ({
      id: connection.id,
      station_a_id: connection.station_a_id,
      station_b_id: connection.station_b_id,
      track_type: connection.track_type === "double" ? "double" : "single",
      dispatch_mode_override: connection.dispatch_mode_override || null,
      display_side_a: String(connection.display_side_a || "right").startsWith("left") ? "left" : "right",
      display_side_b: String(connection.display_side_b || "left").startsWith("right") ? "right" : "left",
      display_order_a: Number(connection.display_order_a || 0),
      display_order_b: Number(connection.display_order_b || 0),
    })),
    panels,
  };
  document.querySelector("#local-draft-title").textContent = "Lokalt utkast från aktiv träff";
  renderConfiguration();
  setMessage(configMessage, "Den aktiva stationsplanen är kopierad till formuläret. Granska den och välj Spara utkast när du är nöjd.", "success");
  configForm.scrollIntoView({ behavior: "smooth", block: "start" });
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
  renderOverviewGraph(snapshot);
}

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
        return `<button type="button" data-train-number="${escapeHTML(trainNumber)}" class="route-number${trainNumber === state.selectedTrainNumber ? " active" : ""}">${escapeHTML(trainNumber)}</button>`;
      }).join("")
    : '<p class="route-empty">Inga tåg hittades.</p>';

  const selected = services.find((service) => String(service.train_number) === state.selectedTrainNumber);
  const detail = document.querySelector("#overview-route-detail");
  if (!selected) {
    detail.innerHTML = services.length
      ? '<div class="route-detail-empty">Välj ett tåg i listan, banöversikten eller tågdiagrammet.</div>'
      : '<div class="route-detail-empty">Tidtabellen saknar tågrutter.</div>';
  } else {
    const stops = [...(selected.stops || [])].sort((a, b) => Number(a.stop_order) - Number(b.stop_order));
    detail.innerHTML = `
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
          return `<li class="route-stop${positionClass}${stop.station_id === state.selectedStationID ? " selected" : ""}">
            <i></i>
            <button type="button" data-station-id="${escapeHTML(stop.station_id)}"><b>${escapeHTML(station?.name || stop.station_name || "Okänd station")}</b><span>${escapeHTML(times)}</span></button>
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
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  return fetch(path, { ...options, headers, credentials: "same-origin" });
}

function handleConnectionError(error) {
  setConnection("waiting", "Återansluter");
  // v1-simuleringens meddelanderad är borta med den vyn; v2 har en egen.
  const target = document.querySelector("#tmbox-v2-message");
  if (target) setMessage(target, error.message, "error");
  if (!state.authStatus?.authenticated) showLogin();
}

async function refreshAuthStatus() {
  const response = await fetch("/v1/auth/status", { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) throw new Error("Serverns åtkomstläge kunde inte läsas");
  state.authStatus = await response.json();
  state.token = null;
  localStorage.removeItem("trainmeet.accessToken");
  // The username field is left alone. The application never fills it in -
  // the browser's own password manager may still offer a saved login, which
  // is the user's choice rather than ours.
  configureResetMode();
  return state.authStatus;
}

function configureResetMode() {
  const localFactoryReset = state.authStatus?.access_mode === "local";
  // Sammanfattningen är det enda som syns när blocket är hopfällt, så den ska
  // säga vilken av de två nollställningarna som gäller den här webbläsaren.
  document.querySelector("#reset-mode-summary").textContent = localFactoryReset
    ? "Fabriksåterställ servern"
    : "Nollställ träffdata";
  document.querySelector("#reset-mode-title").textContent = localFactoryReset
    ? "Börja om från en helt ren TrainMeet Server"
    : "Börja om utan att förlora administratörsåtkomsten";
  document.querySelector("#reset-mode-description").textContent = localFactoryReset
    ? "Tar bort administratör, träffkonfiguration, lokal trafikhistorik, Cloud-koppling och parkopplade enheter. Första installationen öppnas efter omstarten."
    : "Tar bort träffkonfiguration, lokal trafikhistorik, Cloud-koppling och parkopplade enheter. Administratören, servernamnet och din aktiva webbinloggning behålls.";
  factoryResetButton.textContent = localFactoryReset
    ? "Fabriksåterställ servern"
    : "Nollställ träffdata";
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
    document.querySelector("#setup-runtime-summary").innerHTML = `
      <b>${escapeHTML(installation.runtime.meet_name)}</b>
      <span>${Number(installation.runtime.station_count || 0)} stationer · ${Number(installation.runtime.train_count || 0)} tågrörelser</span>
    `;
  }
  setConnection("waiting", "Installation pågår");
}

async function showLogin() {
  clearTimeout(state.snapshotTimer);
  clearTimeout(state.adminTimer);
  state.authStatus = { ...(state.authStatus || {}), authenticated: false };
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
    showLogin();
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
  connectionStatus.querySelector("b").textContent = text;
}

function setMessage(element, text, kind = "") {
  element.textContent = text || "";
  element.className = `form-message ${kind}`.trim();
}



function nextStationCode() {
  const number = state.config.stations.length + 1;
  return `ST${number}`;
}

function uniqueID(prefix) {
  return `${prefix}-${crypto.randomUUID().slice(0, 8)}`;
}

function slugify(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48);
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

function appendLocomotive(target, point, trainNumber, direction = 1, options = {}) {
  const group = svgElement("g", {
    transform: `translate(${point.x},${point.y})`,
    class: `topology-train${options.selected ? " selected" : ""}${options.dimmed ? " dimmed" : ""}${options.clickable ? " clickable" : ""}`,
    role: options.clickable ? "button" : "img",
    tabindex: options.clickable ? "0" : "-1",
    "aria-label": `Tåg ${trainNumber}`,
  });
  if (options.selected) group.append(svgElement("circle", { r: 17, cy: -2, class: "topology-train-ring" }));
  group.append(svgElement("text", { y: -14, class: "train-number" }, trainNumber));
  const locomotive = svgElement("g", { transform: `scale(${direction},1)` });
  locomotive.append(svgElement("rect", { x: -6, y: -4, width: 12, height: 7, rx: 3, fill: "hsl(0 72% 51%)" }));
  locomotive.append(svgElement("rect", { x: -9, y: -6, width: 5, height: 9, rx: 1, fill: "hsl(0 72% 51%)", opacity: .9 }));
  locomotive.append(svgElement("rect", { x: 4, y: -8, width: 2.5, height: 4, rx: 1, fill: "hsl(0 72% 51%)", opacity: .85 }));
  locomotive.append(svgElement("circle", { cx: 5.25, cy: -10, r: 2, fill: "var(--display-muted)", opacity: .5 }));
  locomotive.append(svgElement("polygon", { points: "6,3 9,1 9,3", fill: "hsl(0 72% 51%)", opacity: .8 }));
  locomotive.append(svgElement("circle", { cx: -5, cy: 4, r: 2, fill: "var(--display-fg)", opacity: .7 }));
  locomotive.append(svgElement("circle", { cx: 0, cy: 4, r: 2, fill: "var(--display-fg)", opacity: .7 }));
  locomotive.append(svgElement("circle", { cx: 5, cy: 4, r: 1.5, fill: "var(--display-fg)", opacity: .7 }));
  group.append(locomotive);
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

function renderTopology(snapshot, target = document.querySelector("#topology-svg"), options = {}) {
  if (!target) return;
  const { positions, edges, viewBox } = topologyLayout(snapshot);
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
    group.append(svgElement("text", { x: point.x, y: point.y + (autonomous ? 16 : 20), class: "topology-name", "font-style": autonomous ? "italic" : "normal" }, station.name));
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
  for (const position of snapshot.train_positions || []) {
    let point = null;
    if (position.status === "station") point = positions.get(position.station_id);
    else {
      const from = positions.get(position.from_station_id), to = positions.get(position.to_station_id);
      if (from && to) point = { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
    }
    if (!point) continue;
    const direction = position.from_station_id && position.to_station_id
      ? ((positions.get(position.to_station_id)?.x || 0) >= (positions.get(position.from_station_id)?.x || 0) ? 1 : -1)
      : 1;
    appendLocomotive(target, { x: point.x, y: point.y - 17 }, position.train_number, direction, {
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
    panel.innerHTML = `<p>TÅG</p><b>${escapeHTML(service.train_number)}</b><span>${stops.length} stopp</span><small>${stops.map((stop) => escapeHTML((snapshot.stations || []).find((item) => item.id === stop.station_id)?.name || "?")).join(" → ")}</small>`;
  } else if (station) {
    const rows = stationTrafficRows(snapshot, station.id);
    const connected = (snapshot.connections || []).filter((connection) => connection.station_a_id === station.id || connection.station_b_id === station.id).length;
    panel.innerHTML = `<p>STATION</p><b>${escapeHTML(station.name)}</b><span>${escapeHTML(station.code || "–")} · ${rows.length} tåg · ${connected} sträckor</span><small>${rows.slice(0, 4).map((row) => `${escapeHTML(row.trainNumber)} ${escapeHTML(row.kind)} ${escapeHTML(row.time)}`).join(" · ") || "Inga tidtabellslag"}</small>`;
  }
}

function renderGraph(snapshot) {
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
    return `<line x1="100" y1="${start}" x2="100" y2="${start + length}" stroke="${markerColor}" stroke-width="${width}" transform="rotate(${index * 6} 100 100)"/>`;
  }).join("");
  const numbers = config.hasNumbers ? Array.from({ length: 12 }, (_, index) => {
    const value = index === 0 ? 12 : index;
    const angle = (index * 30 - 90) * Math.PI / 180;
    return `<text x="${100 + 68 * Math.cos(angle)}" y="${100 + 68 * Math.sin(angle)}" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="bold" fill="${numberColor}" font-family="sans-serif">${value}</text>`;
  }).join("") : "";
  const secondHand = showSeconds ? `<g data-clock-hand="second" transform="rotate(0 100 100)">
    <line x1="100" y1="118" x2="100" y2="${100 - config.secondHandLength}" stroke="${config.secondHandColor}" stroke-width="${config.secondHandWidth}" stroke-linecap="round"/>
    ${config.secondBallRadius > 0 ? `<circle cx="100" cy="${100 - config.secondBallOffset}" r="${config.secondBallRadius}" fill="${config.secondHandColor}"/>` : ""}
  </g>` : "";
  return `<svg class="clock-face${stopped ? " stopped" : ""}" viewBox="0 0 200 200">
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

function renderClock(snapshot) {
  const target = document.querySelector("#clock-view");
  const available = snapshot.clock?.available_styles?.length ? snapshot.clock.available_styles : ["swiss", "swedish", "digital"];
  let style = new URLSearchParams(location.search).get("style") || localStorage.getItem("trainmeet.clockStyle") || available[0];
  if (!available.includes(style)) style = available[0];
  const styleSelect = document.querySelector("#display-clock-style");
  const signature = available.join("|");
  if (styleSelect.dataset.signature !== signature) {
    styleSelect.dataset.signature = signature;
    styleSelect.innerHTML = available.map((value) => `<option value="${escapeHTML(value)}">${escapeHTML(clockStyleLabels[value] || value)}</option>`).join("");
  }
  styleSelect.value = style;
  const seconds = currentClockSeconds(snapshot);
  const time = formatClockTime(seconds);
  const localSeconds = localStorage.getItem("trainmeet.showSeconds");
  const showSeconds = localSeconds === null ? snapshot.clock?.show_seconds !== false : localSeconds === "true";
  const displayTime = showSeconds ? time : time.slice(0, 5);
  const darkBackground = !document.querySelector("#display-app").classList.contains("light");
  const stopped = !snapshot.clock?.running;
  const stopText = snapshot.clock?.running ? "" : `STOPPAD${snapshot.clock?.stopped_reason ? ` · ${snapshot.clock.stopped_reason}` : ""}`;
  const renderSignature = [style, darkBackground, showSeconds, stopped, stopText].join("|");
  if (target.dataset.clockSignature !== renderSignature) {
    target.dataset.clockSignature = renderSignature;
    const face = style === "digital"
      ? `<div class="clock-digital${stopped ? " stopped" : ""}"></div>`
      : clockSVG(style, darkBackground, showSeconds, stopped);
    const reason = stopText ? `<div class="clock-stopped">${escapeHTML(stopText)}</div>` : "";
    target.innerHTML = `<div class="clock-shell${style === "digital" ? " clock-shell--digital" : ""}">${face}${reason}</div>`;
  }
  if (style === "digital") {
    const digital = target.querySelector(".clock-digital");
    if (digital) digital.dataset.seconds = String(showSeconds);
    if (digital && digital.textContent !== displayTime) digital.textContent = displayTime;
  } else {
    updateAnalogClockHands(target, seconds, style, !stopped);
  }
}

function renderDashboard(snapshot) {
  const target = document.querySelector("#dashboard-view");
  const activeConnections = (snapshot.connection_states || []).filter((state) => state.state !== "free").length;
  const activeTrains = (snapshot.train_positions || []).length;
  target.innerHTML = `<div class="dashboard-column">
    <section class="display-card"><div class="dashboard-clock">${escapeHTML(currentClockTime(snapshot).slice(0, 5))}</div><p class="clock-meta">${Number(snapshot.clock?.speed || 1)}× · ${snapshot.clock?.running ? "Klockan går" : "Klockan är stoppad"}</p></section>
    <section class="display-card dashboard-stats">
      <div class="dashboard-stat"><b>${activeTrains}</b><span>aktiva tåg</span></div>
      <div class="dashboard-stat"><b>${activeConnections}</b><span>upptagna sträckor</span></div>
      <div class="dashboard-stat"><b>${snapshot.stations?.length || 0}</b><span>stationer</span></div>
      <div class="dashboard-stat"><b>${uniqueOverviewServices(snapshot).length}</b><span>tågturer idag</span></div>
    </section>
  </div><section class="display-card"><svg id="dashboard-topology" class="display-visual" role="img" aria-label="Banöversikt"></svg></section>`;
  renderTopology(snapshot, document.querySelector("#dashboard-topology"));
}

function renderDisplayTopology(snapshot) {
  renderTopology(snapshot, document.querySelector("#topology-svg"), {
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
  const visible = Boolean(connection.code) && Boolean(address) && screens.includes(displayKind);
  badge.classList.toggle("hidden", !visible);
  if (!visible) return;
  document.querySelector("#display-connection-address").textContent = `TMBox ${address}`;
  document.querySelector("#display-connection-code").textContent = connection.code;
}

function renderDisplay(snapshot) {
  displaySnapshot = snapshot;
  document.querySelector("#display-loading").classList.add("hidden");
  document.querySelector("#display-title").textContent = `${snapshot.meet?.name || "TrainMeet"} · ${({ topology: "Banöversikt", graph: "Tågdiagram", clock: "Träffklocka", dashboard: "Översikt" })[displayKind]}`;
  document.querySelector("#display-day").textContent = snapshot.active_day || "Dagl";
  const isClock = displayKind === "clock";
  document.querySelector("#display-speed").classList.toggle("hidden", !isClock);
  document.querySelector("#display-speed").textContent = `${Number(snapshot.clock?.speed || 1)}×`;
  document.querySelector("#display-clock-style").classList.toggle("hidden", !isClock || (snapshot.clock?.available_styles?.length || 0) < 2);
  document.querySelector("#display-seconds").classList.toggle("hidden", !isClock);
  const trainSelect = document.querySelector("#display-train-select");
  const trainSelectable = displayKind === "topology" || displayKind === "graph";
  const services = uniqueOverviewServices(snapshot);
  const trainSignature = services.map((service) => service.train_number).join("|");
  trainSelect.classList.toggle("hidden", !trainSelectable);
  if (trainSelect.dataset.signature !== trainSignature) {
    trainSelect.dataset.signature = trainSignature;
    trainSelect.innerHTML = `<option value="">Alla tåg</option>${services.map((service) => `<option value="${escapeHTML(service.train_number)}">Tåg ${escapeHTML(service.train_number)}</option>`).join("")}`;
  }
  if (!services.some((service) => String(service.train_number) === state.displaySelectedTrainNumber)) state.displaySelectedTrainNumber = null;
  trainSelect.value = state.displaySelectedTrainNumber || "";
  renderConnectionBadge(snapshot);
  const ids = { topology: "topology-svg", graph: "graph-scroll", clock: "clock-view", dashboard: "dashboard-view" };
  for (const id of Object.values(ids)) document.querySelector(`#${id}`).classList.toggle("hidden", id !== ids[displayKind]);
  if (displayKind === "topology") renderDisplayTopology(snapshot);
  if (displayKind === "graph") renderGraph(snapshot);
  if (displayKind === "clock") {
    document.querySelector("#display-selection").classList.add("hidden");
    renderClock(snapshot);
  }
  if (displayKind === "dashboard") {
    document.querySelector("#display-selection").classList.add("hidden");
    renderDashboard(snapshot);
  }
}

async function pollDisplay() {
  clearTimeout(displayPollTimer);
  const live = document.querySelector("#display-live");
  try {
    const response = await fetch("/v1/display", { cache: "no-store" });
    if (!response.ok) throw new Error("Servern svarade inte");
    const payload = await response.json();
    displaySnapshotReceivedAt = performance.now();
    syncDisplayClock(payload, displaySnapshotReceivedAt);
    live.classList.remove("offline");
    live.lastChild.textContent = " Ansluten";
    renderDisplay(payload);
  } catch {
    live.classList.add("offline");
    live.lastChild.textContent = " Återansluter";
  }
  displayPollTimer = setTimeout(pollDisplay, 1000);
}

async function initDisplay() {
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
  document.querySelector("#display-clock-style").addEventListener("change", (event) => {
    localStorage.setItem("trainmeet.clockStyle", event.target.value);
    if (displaySnapshot) renderClock(displaySnapshot);
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
  document.querySelector("#display-seconds").addEventListener("click", () => {
    const current = localStorage.getItem("trainmeet.showSeconds");
    const serverDefault = displaySnapshot?.clock?.show_seconds !== false;
    const next = current === null ? !serverDefault : current !== "true";
    localStorage.setItem("trainmeet.showSeconds", String(next));
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
  showToolbar();
  try { await navigator.wakeLock?.request("screen"); } catch {}
  const animateClock = () => {
    if (displaySnapshot && displayKind === "clock") renderClock(displaySnapshot);
    displayTickTimer = requestAnimationFrame(animateClock);
  };
  displayTickTimer = requestAnimationFrame(animateClock);
  pollDisplay();
}

if (displayKind && ["topology", "graph", "clock", "dashboard"].includes(displayKind)) initDisplay();
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
  return V2_GEOMETRIES[stored] ? stored : "16x2";
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

function bindV2Controls() {
  const device = v2El("device");
  const station = v2El("station");
  const geometry = v2El("geometry");
  device.value = localStorage.getItem("trainmeet.v2Device") || "";
  geometry.value = v2Geometry();
  device.addEventListener("change", () => {
    localStorage.setItem("trainmeet.v2Device", device.value.trim());
    refreshTMBoxV2();
  });
  station.addEventListener("change", () => {
    localStorage.setItem("trainmeet.v2Station", station.value);
    tmboxV2.configFor = null;
    refreshTMBoxV2();
  });
  geometry.addEventListener("change", () => {
    localStorage.setItem("trainmeet.v2Geometry", geometry.value);
    drawV2();
  });
}

async function loadV2Stations() {
  const station = v2El("station");
  try {
    const response = await authorizedFetch("/v1/tmbox-v2/stations");
    const body = await response.json().catch(() => ({}));
    // The server says why it will not run - no active meet, or a client
    // without admin rights - and dropping that left an empty station list
    // with no explanation, which looks exactly like a broken simulator.
    if (!response.ok) {
      station.innerHTML = "";
      throw new Error(body.message || "Stationerna kunde inte hämtas");
    }
    const remembered = localStorage.getItem("trainmeet.v2Station");
    station.innerHTML = "";
    for (const entry of body.stations || []) {
      const option = document.createElement("option");
      option.value = entry.id;
      option.textContent = entry.code ? `${entry.name} (${entry.code})` : entry.name;
      station.append(option);
    }
    if (remembered && [...station.options].some((o) => o.value === remembered)) {
      station.value = remembered;
    }
  } catch (error) {
    setMessage(v2El("message"), error.message, "error");
  }
}

async function refreshTMBoxV2() {
  const deviceID = v2El("device").value.trim();
  const stationID = v2El("station").value;
  const nav = tmboxV2.nav;
  if (!nav) return;

  if (deviceID) {
    try {
      const response = await authorizedFetch(
        `/v1/tmbox-v2/assignment?device_id=${encodeURIComponent(deviceID)}`);
      tmboxV2.assignment = response.ok ? await response.json() : null;
    } catch { tmboxV2.assignment = null; }
  } else {
    tmboxV2.assignment = null;
  }

  // A box that is not assigned shows KOPPLA BOXEN and nothing else - it has
  // no station to browse.
  if (!tmboxV2.assignment || tmboxV2.assignment.status !== "assigned") {
    nav.show(tmboxV2.assignment ? "AwaitingAssignment" : "Identity", v2Now());
    drawV2();
    return;
  }

  if (stationID && tmboxV2.configFor !== stationID) {
    try {
      const response = await authorizedFetch(
        `/v1/tmbox-v2/config?station_id=${encodeURIComponent(stationID)}`);
      if (response.ok) {
        tmboxV2.config = v2NormaliseConfig(await response.json());
        tmboxV2.configFor = stationID;
        tmboxV2.attention.forget();
      }
    } catch { /* the snapshot below reports the trouble */ }
  }

  try {
    const response = await authorizedFetch(
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
    tmboxV2.snapshot = await response.json();
    v2Signal(tmboxV2.attention.observe(tmboxV2.snapshot));
    if (["Identity", "AwaitingAssignment", "ServerGone", "SeekingServer"]
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
  const frame = TMBoxRender.render(geometry, nav.view, tmboxV2.config, tmboxV2.snapshot);
  frame.forEach((line, row) => { lcd.children[row].textContent = line; });
}

async function pressV2Key(key) {
  const nav = tmboxV2.nav;
  if (!nav || tmboxV2.busy) return;

  // A flash is a screen the operator is reading; let it finish before a key
  // is taken against whatever is behind it.
  if (tmboxV2.flashUntil > Date.now()) return;

  const result = nav.press(key, v2Now(), tmboxV2.config, tmboxV2.snapshot);
  if (result.outcome === "Ignored") return;
  if (result.outcome === "Redraw") { drawV2(); return; }

  const deviceID = v2El("device").value.trim();
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
    message_id: `sim-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    action: result.command.action,
    station_id: v2El("station").value,
    payload: v2Payload(result.command),
  };
  try {
    const response = await authorizedFetch("/v1/tmbox-v2/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: deviceID, command }),
    });
    const ack = await response.json();
    v2El("ack").textContent = JSON.stringify(ack, null, 2);

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

// ==================================================== KÖR › Trafik (DEL 3.3)
// Vyn som ersätter dagens "Aktiv träff". Den svarar på två frågor: var är
// tågen, och vem väntar på mig?
//
// All data kommer från /v1/display — samma källa som trafikmotorn och
// hallskärmarna. Ingenting här är exempeldata, och ingenting härleds på ett
// annat sätt än motorn gör det.

const trafficState = { station: "", onlyDeviations: false, snapshot: null };

document.querySelector("#traffic-station")?.addEventListener("change", (event) => {
  trafficState.station = event.target.value;
  renderTrafficView();
});
document.querySelector("#traffic-only-deviations")?.addEventListener("change", (event) => {
  trafficState.onlyDeviations = event.target.checked;
  renderTrafficView();
});

async function renderTrafficView() {
  try {
    const response = await authorizedFetch("/v1/display");
    if (!response.ok) return;
    trafficState.snapshot = await response.json();
  } catch {
    return; // nästa intervall försöker igen
  }
  const snapshot = trafficState.snapshot;
  if (!snapshot) return;

  fillTrafficStationFilter(snapshot);
  renderTrafficOnline(snapshot);
  renderTrafficStations(snapshot);
  renderTrafficTimeline(snapshot);
}

function fillTrafficStationFilter(snapshot) {
  const select = document.querySelector("#traffic-station");
  if (!select || select.options.length > 1) return;
  for (const station of snapshot.stations || []) {
    const option = document.createElement("option");
    option.value = station.id;
    option.textContent = station.name;
    select.append(option);
  }
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
      chip.textContent = late ? `${Math.abs(away)} min sen` : "I tid";

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
      status.textContent = away === null ? "" : away < 0 ? "Avgått" : away <= 2 ? "Strax" : `${away} min`;
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

// ================================================== BYGG › 1 Träffen (3.7)
// Källvalet är inte en etikett. Det är serverns driftläge, som redan finns
// sedan 1.2.0 och redan låser redigeringsvägarna:
//
//   TrainMeet Cloud  ↔  cloud-linked   Cloud är redaktör, redigering låst
//   Lokalt utkast    ↔  offline-meet   servern är redaktör, redigering öppen
//   Importerad fil                     en åtgärd, inte ett läge
//
// Att gå tillbaka till Cloud kastar de lokala revisionerna, och servern
// vägrar tills man sett vad som kastas och bekräftat. Den kontrollen ligger
// i API:t, inte här - UI:t visar bara vad servern svarar.

const SOURCE_MODES = { cloud: "cloud-linked", lokal: "offline-meet" };

async function refreshSourceChoice() {
  const message = document.querySelector("#source-message");
  try {
    const response = await authorizedFetch("/v1/operating-mode");
    if (!response.ok) return;
    const state = await response.json();
    applySourceChoice(state.mode === "cloud-linked" ? "cloud" : "lokal", state);
  } catch {
    if (message) setMessage(message, "Driftläget kunde inte läsas", "error");
  }
}

function applySourceChoice(source, modeState) {
  document.querySelectorAll(".source-card").forEach((card) => {
    const selected = card.dataset.source === source;
    card.classList.toggle("selected", selected);
    card.setAttribute("aria-checked", String(selected));
  });
  // locked = source === "cloud" (DEL 5). Härledd, aldrig satt: den kommer ur
  // serverns svar, så UI och server kan inte tycka olika.
  document.body.dataset.sourceLocked = String(!modeState.editing_open);
  updateStepSubtitles(modeState);
}

async function chooseSource(source) {
  const message = document.querySelector("#source-message");
  if (source === "fil") {
    // Importen är en åtgärd, inte ett läge. Den kräver att redigering är
    // öppen, precis som varje annan skrivväg.
    document.querySelector("#runtime-import")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return;
  }
  const mode = SOURCE_MODES[source];
  if (!mode) return;
  try {
    const response = await authorizedFetch("/v1/operating-mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    const payload = await response.json();
    if (response.status === 409 && payload.code === "confirm_discard") {
      // D4: aldrig tyst. Servern har räknat exakt vad som kastas.
      await confirmDiscardAndSwitch(mode, message);
      return;
    }
    if (!response.ok) throw new Error(payload.message || "Läget kunde inte bytas");
    applySourceChoice(source, payload);
    setMessage(message, "", "notice");
  } catch (error) {
    setMessage(message, error.message, "error");
  }
}

async function confirmDiscardAndSwitch(mode, message) {
  const state = await (await authorizedFetch("/v1/operating-mode")).json();
  const preview = state.discards_on_return || { revisions: 0, rows: [] };
  const lines = preview.rows.slice(0, 12).map((row) => `  ${row.train_number || row.id}: ${row.change}`);
  const more = preview.rows.length > 12 ? `\n  … och ${preview.rows.length - 12} till` : "";
  const question =
    `${preview.revisions} lokala revisioner kastas när Clouds version gäller igen.\n\n` +
    `${lines.join("\n")}${more}\n\nFortsätta?`;
  if (!confirm(question)) {
    setMessage(message, "Läget är oförändrat.", "notice");
    return;
  }
  const response = await authorizedFetch("/v1/operating-mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, discard_local_revisions: true }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "Läget kunde inte bytas");
  applySourceChoice("cloud", payload);
  setMessage(message, `${preview.revisions} lokala revisioner kastades.`, "notice");
}

/** Underrubrikerna i stegräckan, ur verklig data. */
function updateStepSubtitles(modeState) {
  const runtime = state.runtime || {};
  const set = (step, text) => {
    const host = document.querySelector(`[data-step-subtitle="${step}"]`);
    if (host) host.textContent = text || "";
  };
  if (modeState) {
    const source = modeState.editing_open ? "Lokalt utkast" : "Cloud";
    const revisions = (modeState.local_revisions || []).length;
    set("kalla", revisions ? `${source} · ${revisions} lokala revisioner` : source);
  }
  set("bana", runtime.station_count != null ? `${runtime.station_count} stationer` : "");
  set("tid", runtime.train_count != null ? `${runtime.train_count} rörelser` : "");
  set("boxar", state.devices?.length != null ? `${state.devices.length} kopplade` : "");
  const version = document.querySelector("#software-version")?.dataset;
  set("server", version?.version ? `${version.version}${version.build ? ` · build ${version.build}` : ""}` : "");
}

document.querySelectorAll(".source-card").forEach((card) => {
  card.addEventListener("click", () => chooseSource(card.dataset.source));
});
