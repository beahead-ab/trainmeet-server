const { t, html } = globalThis.TrainMeetI18n;

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
    sessionStorage.removeItem("trainmeet.workspace");
    history.replaceState(null, "", location.pathname + "#workspaces");
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
    await waitForServerReturn();
  } catch (error) {
    state.restarting = false;
    setMessage(message, error.message, "error");
    button.disabled = false;
  }
});


// Server workspaces select an interface, never a different meet or engine.
const SETTINGS_SECTIONS = ["meet", "identity", "access", "users", "devices", "software", "cloud", "system"];
const WORKSPACE_PANELS = {
  kor: "#overview-view", installningar: "#admin-view",
  skarmar: "#displays-view", tmbox: "#tmbox-v2-view",
};
const MODES = ["workspaces", ...Object.keys(WORKSPACE_PANELS)];
const WORKSPACE_KEY = "trainmeet.workspace";
const WORKSPACES = {
  administration: { title: "Drift och administration", detail: "Trafikläge, klocka och serverinställningar", path: "/#overview" },
  tkl: { title: "TKL", detail: "Stationsarbetet i en egen arbetsyta", path: "/tkl/" },
  tmbox: { title: "TMBox v2", detail: "Testa display, knappsats och boxens flöden", path: "/#tmbox" },
  dispatcher: { title: "Dispatcher", detail: "Trafikledning för träffens territorier", path: "/us/dispatcher" },
  conductor: { title: "Conductor", detail: "Tåguppdrag och körtillstånd", path: "/us/conductor" },
};

// Local vector illustrations: no fonts, remote assets or meet data in markup.
const WORKSPACE_ICONS = {
  administration: '<rect x="12" y="18" width="72" height="54" rx="9"/><path d="M12 34h72M30 34v38M21 26h1m7 0h1m7 0h1M39 62V50m11 12V43m11 19v-8"/><circle class="workspace-icon-fill" cx="72" cy="66" r="15"/><path d="M72 58v8l5 3"/>',
  tkl: '<path d="M17 65h62M25 74h46M30 65l-8 17m44-17 8 17"/><rect class="workspace-icon-fill" x="28" y="14" width="40" height="51" rx="10"/><path d="M28 38h40M48 22v16M37 51h1m20 0h1M38 22h20"/><path d="M15 28v22m66-22v22M11 28h8m58 0h8"/>',
  tmbox: '<rect class="workspace-icon-fill" x="24" y="9" width="48" height="78" rx="10"/><rect x="31" y="18" width="34" height="22" rx="3"/><path d="M37 25h17m-17 7h23M34 50h3m10 0h3m10 0h3M34 60h3m10 0h3m10 0h3M34 70h3m10 0h3m10 0h3M34 79h3m10 0h3m10 0h3"/>',
  dispatcher: '<path d="M13 69h22l27-41h21M13 28h22l27 41h21M48 49v-30"/><circle class="workspace-icon-fill" cx="13" cy="28" r="6"/><circle class="workspace-icon-fill" cx="83" cy="28" r="6"/><circle class="workspace-icon-fill" cx="13" cy="69" r="6"/><circle class="workspace-icon-fill" cx="83" cy="69" r="6"/><circle class="workspace-icon-fill" cx="48" cy="49" r="8"/><path d="M42 15h12"/>',
  conductor: '<rect class="workspace-icon-fill" x="22" y="17" width="52" height="66" rx="8"/><rect x="36" y="10" width="24" height="14" rx="4"/><path d="m33 41 4 4 7-8m-11 24 4 4 7-8M51 41h12M51 61h12"/>',
};

function workspaceIcon(key) {
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("viewBox", "0 0 96 96");
  icon.setAttribute("aria-hidden", "true");
  icon.setAttribute("focusable", "false");
  icon.innerHTML = WORKSPACE_ICONS[key];
  return icon;
}

function currentMode() { return document.body.dataset.mode || "workspaces"; }
function storedMode() { return sessionStorage.getItem(WORKSPACE_KEY) ? "kor" : "workspaces"; }

function workspaceHome() {
  return WORKSPACES[sessionStorage.getItem(WORKSPACE_KEY)]?.path || "/#workspaces";
}

function availableWorkspaces() {
  // Fail closed: a stale browser choice must not grant a role or select EU/US.
  return (state.serverContext?.available_workspaces || []).filter((key) => WORKSPACES[key]);
}

async function refreshServerContext() {
  const response = await authorizedFetch("/v1/server-context", { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "Serverns träff kunde inte läsas.");
  state.serverContext = payload;
  const warning = document.querySelector("#server-context-warning");
  setMessage(warning, payload.error?.message || (typeof payload.error === "string" ? payload.error : "") || (payload.transition_pending ? "Byte av träff pågår. Trafikkommandon är tillfälligt spärrade." : ""), "error");
  warning.classList.toggle("hidden", !warning.textContent);
  const meet = payload.selected_meet;
  const name = meet?.name || t("Ingen träff vald");
  document.querySelector("#app-meet-name").textContent = name;
  renderWorkspaceMeetLabel();
  const selected = sessionStorage.getItem(WORKSPACE_KEY);
  if (selected && !availableWorkspaces().includes(selected)) {
    sessionStorage.removeItem(WORKSPACE_KEY);
    setMode("workspaces");
  }
  renderWorkspacePicker();
  renderCloudStatus();
  const us = payload.operating_region === "us";
  document.querySelector("#us-runtime-summary").classList.toggle("hidden", !us);
  document.querySelectorAll('.display-launch-card[href="/display/topology"], .display-launch-card[href="/display/graph"], .display-launch-card[href="/display/dashboard"], .connection-badge-card')
    .forEach((node) => node.classList.toggle("hidden", us));
  document.querySelectorAll("#overview-view .topology-overview-card, #overview-traffic, #overview-timetable")
    .forEach((node) => node.classList.toggle("hidden", us));
  document.querySelector("#workspace-home").href = workspaceHome();
  return payload;
}

function renderWorkspacePicker() {
  const host = document.querySelector("#workspace-options");
  const signature = availableWorkspaces().join(",") + ":" + document.documentElement.lang;
  if (host.dataset.workspaces === signature) return;
  host.dataset.workspaces = signature;
  host.replaceChildren();
  for (const key of availableWorkspaces()) {
    const entry = WORKSPACES[key];
    const button = document.createElement("button");
    button.type = "button";
    button.className = "workspace-option";
    button.dataset.workspace = key;
    const illustration = document.createElement("span");
    illustration.className = "workspace-illustration";
    illustration.append(workspaceIcon(key));
    const title = document.createElement("strong");
    title.id = `workspace-title-${key}`;
    title.textContent = t(entry.title);
    const detail = document.createElement("span");
    detail.id = `workspace-detail-${key}`;
    detail.className = "workspace-detail";
    detail.textContent = t(entry.detail);
    button.setAttribute("aria-labelledby", title.id);
    button.setAttribute("aria-describedby", detail.id);
    const action = document.createElement("span");
    action.className = "workspace-enter";
    action.setAttribute("aria-hidden", "true");
    action.textContent = t("Öppna arbetsyta") + " →";
    button.append(illustration, title, detail, action);
    button.addEventListener("click", () => {
      sessionStorage.setItem(WORKSPACE_KEY, key);
      if (key === "administration" || key === "tmbox") {
        const hash = key === "tmbox" ? "#tmbox" : "#overview";
        if (location.hash === hash) applyWorkspaceRoute();
        else location.hash = hash;
      } else location.assign(entry.path);
    });
    host.append(button);
  }
}

function renderWorkspaceMeetLabel() {
  const meet = state.serverContext?.selected_meet;
  document.querySelector("#workspace-meet").textContent = meet
    ? t("{name} · alla arbetsytor använder samma träff.", { name: meet.name })
    : t("Koppla servern till en publicerad träff i Cloud via Inställningar.");
}

globalThis.TrainMeetI18n.subscribe(() => {
  renderWorkspacePicker();
  renderWorkspaceMeetLabel();
  renderCloudStatus();
  renderUsers();
  configureResetMode();
});

function setMode(mode) {
  const next = MODES.includes(mode) ? mode : "workspaces";
  document.body.dataset.mode = next;
  document.querySelector("#workspace-home").href = workspaceHome();
  document.querySelector("#workspace-picker").classList.toggle("hidden", next !== "workspaces");
  document.querySelector(".server-admin-shell").classList.toggle("hidden", next === "workspaces");
  for (const [name, selector] of Object.entries(WORKSPACE_PANELS)) {
    document.querySelector(selector).classList.toggle("hidden", name !== next);
  }
  document.querySelector("#application-menu").open = false;
  document.querySelector("#settings-heading").classList.toggle("hidden", next !== "installningar");
  if (next !== "tmbox") stopTMBoxV2();
  if (next === "workspaces") {
    renderWorkspacePicker();
  } else if (next === "tmbox") {
    startTMBoxV2();
  } else if (next === "installningar") showSettings();
  else if (next === "kor" && state.serverContext?.operating_region === "eu") renderOverview(state.overviewSnapshot);
  window.scrollTo({ top: 0, behavior: "auto" });
}

function showSettings() {
  document.querySelectorAll(".admin-section-panel").forEach((panel) => {
    panel.classList.toggle("hidden", !SETTINGS_SECTIONS.includes(panel.dataset.adminSection));
  });
  Promise.allSettled([checkSoftwareUpdate(), refreshUsers(), refreshBackups(), refreshRuntime(), refreshDevices()]);
}

function applyWorkspaceRoute() {
  if (!state.authStatus?.authenticated) return;
  const route = location.hash.slice(1);
  document.querySelector("#application-menu").open = false;
  if (route === "settings") setMode("installningar");
  else if (route === "screens") setMode("skarmar");
  else if (route === "tmbox") {
    if (!availableWorkspaces().includes("tmbox")) { setMode("workspaces"); return; }
    sessionStorage.setItem(WORKSPACE_KEY, "tmbox");
    setMode("tmbox");
  }
  else if (route === "traffic" && availableWorkspaces().includes("administration")) {
    // Deprecated separate Traffic route: keep bookmarks, not the old view.
    sessionStorage.setItem(WORKSPACE_KEY, "administration");
    history.replaceState(null, "", "/#overview");
    setMode("kor");
    if (state.serverContext?.operating_region === "eu") document.querySelector("#overview-traffic").scrollIntoView();
  }
  else if (route === "workspaces" || !sessionStorage.getItem(WORKSPACE_KEY)) setMode("workspaces");
  else if (sessionStorage.getItem(WORKSPACE_KEY) === "tmbox") {
    history.replaceState(null, "", "/#tmbox");
    setMode("tmbox");
  }
  else if (sessionStorage.getItem(WORKSPACE_KEY) !== "administration") location.assign(workspaceHome());
  else setMode("kor");
}

document.body.dataset.mode = storedMode();
window.addEventListener("hashchange", applyWorkspaceRoute);
document.querySelector("#workspace-home").addEventListener("click", (event) => {
  event.preventDefault();
  const destination = workspaceHome();
  if (destination.startsWith("/#")) {
    const hash = destination.slice(1);
    if (location.hash === hash) applyWorkspaceRoute();
    else location.hash = hash;
  } else location.assign(destination);
});
bindUsersSection();
bindRestore();

// Native dialog supplies focus trapping; every editor shares cancellation,
// dirty-state protection and focus restoration. Background refresh never
// rewrites fields in an open editor.
const modalOrigins = new WeakMap();
const modalValues = new WeakMap();
function openModal(id) {
  const dialog = document.getElementById(id);
  if (!dialog || dialog.open) return;
  modalOrigins.set(dialog, document.activeElement);
  modalValues.set(dialog, [...dialog.querySelectorAll("input, select, textarea")].map((input) => [input, input.value, input.checked]));
  dialog.dataset.dirty = "false";
  dialog.querySelectorAll(".form-message").forEach((message) => setMessage(message, ""));
  document.body.append(dialog);
  dialog.showModal();
}
function cancelModal(dialog) {
  if (dialog.querySelector('[type="submit"]:disabled') || dialog.dataset.busy === "true") return;
  if (dialog.dataset.dirty === "true" && !window.confirm(t("Stäng utan att spara ändringarna?"))) return;
  for (const [input, value, checked] of modalValues.get(dialog) || []) { input.value = value; input.checked = checked; }
  dialog.close();
}
function finishModal(form) {
  const dialog = form.closest("dialog");
  if (dialog) { dialog.dataset.dirty = "false"; dialog.close(); }
}
function bindAdminModals() {
  document.querySelectorAll("dialog.admin-modal").forEach((dialog) => {
    dialog.addEventListener("input", () => { dialog.dataset.dirty = "true"; });
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); cancelModal(dialog); });
    dialog.addEventListener("close", () => modalOrigins.get(dialog)?.focus());
    dialog.querySelectorAll("[data-close-modal]").forEach((button) => button.addEventListener("click", () => cancelModal(dialog)));
  });
  document.querySelectorAll("[data-open-modal]").forEach((button) => button.addEventListener("click", () => openModal(button.dataset.openModal)));
}
bindAdminModals();
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
    logoutButton.classList.toggle("hidden", !state.authStatus?.authenticated);
    await refreshAdminAccess();
    finishModal(adminAccessForm);
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
    finishModal(serverIdentityForm);
  } catch (error) {
    setMessage(serverIdentityMessage, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#connection-badge-form").addEventListener("submit", (event) => {
  event.preventDefault();
  saveConnectionBadgeSettings();
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
  });
});

document.querySelector("#stop-local-clock").addEventListener("click", async () => {
  await controlLocalClock({
    action: "stop",
    reason: document.querySelector("#local-clock-reason").value.trim(),
  });
});

document.querySelector("#clock-appearance-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const message = document.querySelector("#clock-appearance-message");
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
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
  finally { button.disabled = false; }
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
    finishModal(deviceForm);
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
    button.disabled = false;
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
    if (!window.confirm(
      `Servern återställs och startar om. Allt som hänt efter kopian försvinner, ${restore.overwrites} inkluderat.`,
    )) return;
    restoreEl("start").disabled = true;
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
      await waitForServerReturn();
    } catch {
      setMessage(message, "Återställningen gick inte att starta", "error");
      updateRestoreButton();
    }
  });
}

factoryResetConfirmation.addEventListener("input", () => {
  factoryResetButton.disabled = factoryResetConfirmation.value.trim().toUpperCase() !== "NOLLSTÄLL";
});

factoryResetButton.addEventListener("click", async () => {
  if (factoryResetConfirmation.value.trim().toUpperCase() !== "NOLLSTÄLL") return;
  const localFactoryReset = state.authStatus?.at_the_machine === true;
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
  document.body.dataset.signedIn = "yes";
  setup.classList.add("hidden");
  login.classList.add("hidden");
  appView.classList.remove("hidden");
  logoutButton.classList.toggle("hidden", !state.authStatus?.authenticated);
  try {
    await refreshServerContext();
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
  document.querySelector("#server-name").textContent = info.gateway_id || "TrainMeet Server";
  document.querySelector("#server-detail").textContent =
    `Kör lokalt · aktiv trafiksession: ${info.traffic_session_name}`;
  document.querySelector("#system-server-name").textContent = info.runtime?.server_name || info.gateway_id || "TrainMeet Server";
  document.querySelector("#system-runtime-name").textContent = state.serverContext?.selected_meet?.name || t("Ingen aktiv träff");
  document.querySelector("#system-cloud-state").textContent = state.serverContext?.cloud_update?.linked ? t("Kopplad") : t("Inte kopplad");
  const serverNameInput = document.querySelector("#admin-server-name");
  if (!serverIdentityForm.closest("dialog").open) {
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


async function refreshDevices() {
  const response = await authorizedFetch("/v1/devices");
  if (!response.ok) return;
  const payload = await response.json();
  // Samma svar föder både listan, stegräckan och TKL:s stationsväljare, så
  // de kan inte visa olika många.
  state.devices = payload.devices || [];
  state.stations = payload.stations || [];
  document.querySelector("#app-devices").textContent = `${state.devices.length} TMBox${state.devices.length === 1 ? "" : "ar"}`;
  const list = document.querySelector("#device-list");
  updateStationOptions(payload.stations || []);
  list.replaceChildren();
  if (!payload.devices.length) {
    list.innerHTML = html`<div class="empty-status">Ingen fysisk TMBox har presenterat sig ännu.</div>`;
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
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "secondary";
    edit.dataset.tmText = "Ändra station";
    edit.textContent = t("Ändra station");
    edit.addEventListener("click", () => {
      document.querySelector("#device-code").value = device.device_code;
      deviceStation.value = device.station_id || "";
      openModal("device-form-modal");
    });
    row.append(edit);
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
  state.runtime = await response.json();
  const input = document.querySelector("#runtime-central-url");
  if (state.runtime.central_url && !input.closest("dialog")?.open) input.value = state.runtime.central_url;
  renderCloudStatus();
}

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
      renderConnectionBadgeSettings(payload.connection || {});
    }
  } else {
    const meet = state.serverContext.selected_meet;
    document.querySelector("#overview-meet-name").textContent = meet?.name || "TrainMeet Server";
    document.querySelector("#overview-runtime-meta").textContent = t("Publicerad config · {version}", { version: meet?.publication_id || "–" });
    document.querySelector("#overview-station-meta").textContent = t("Territorier och tåguppdrag från Cloud");
    document.querySelector("#overview-day").textContent = "";
    document.querySelector("#us-runtime-detail").textContent = t(clock.configured ? "Körningen finns lokalt på servern." : "Träffen är hämtad. Starta klockan när körningen ska börja.");
  }
  const timeInput = document.querySelector("#local-clock-time");
  if (!clockControlForm.closest("dialog").open) timeInput.value = clock.time || "12:00:00";
  const speedInput = document.querySelector("#local-clock-speed");
  if (!clockControlForm.closest("dialog").open) speedInput.value = Number(clock.speed || 1);
  const stateLabel = document.querySelector("#clock-state");
  if (!document.querySelector("#clock-appearance-modal").open) {
    const styleSelect = document.querySelector("#meet-clock-style");
    const styles = clock.available_styles || Object.keys(clockStyleLabels);
    styleSelect.replaceChildren(...styles.map(value => new Option(t(clockStyleLabels[value] || value), value)));
    styleSelect.value = clock.style || styles[0];
    document.querySelector("#meet-clock-seconds").checked = clock.show_seconds !== false;
  }
  stateLabel.textContent = clock.running ? t("Går · {speed}×", { speed: Number(clock.speed || 1) }) : t("Stoppad");
  stateLabel.classList.toggle("clock-running", Boolean(clock.running));
  document.querySelector("#overview-clock").textContent = String(clock.time || "--:--").slice(0, 5);
  document.querySelector("#app-clock").textContent = String(clock.time || "--:--").slice(0, 5);
  document.querySelector("#overview-clock-start").disabled = Boolean(clock.running) || !state.serverContext?.selected_meet;
  document.querySelector("#overview-clock-stop").disabled = !clock.running;
}

function renderConnectionBadgeSettings(connection) {
  const container = document.querySelector("#connection-badge-screens");
  if (!container) return;
  if (container.closest("dialog")?.open) return;
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
  const form = document.querySelector("#connection-badge-form");
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
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
    finishModal(form);
  } catch (error) {
    setMessage(message, error.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function controlLocalClock(command) {
  const buttons = [...clockControlForm.querySelectorAll("button"), document.querySelector("#overview-clock-start"), document.querySelector("#overview-clock-stop")];
  buttons.forEach((button) => { button.disabled = true; });
  const message = clockControlForm.closest("dialog").open ? clockControlMessage : document.querySelector("#overview-clock-message");
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
    if (clockControlForm.closest("dialog").open) finishModal(clockControlForm);
    await refreshLocalClock();
  } catch (error) {
    setMessage(message, error.message, "error");
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
    document.querySelector("#overview-clock-start").disabled = Boolean(state.clock?.running) || !state.serverContext?.selected_meet;
    document.querySelector("#overview-clock-stop").disabled = !state.clock?.running;
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
  if (!state.authStatus?.authenticated) showLogin();
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
    panel.innerHTML = html`<p>TÅG</p><b>${escapeHTML(service.train_number)}</b><span>${stops.length} stopp</span><small>${stops.map((stop) => escapeHTML((snapshot.stations || []).find((item) => item.id === stop.station_id)?.name || "?")).join(" → ")}</small>`;
  } else if (station) {
    const rows = stationTrafficRows(snapshot, station.id);
    const connected = (snapshot.connections || []).filter((connection) => connection.station_a_id === station.id || connection.station_b_id === station.id).length;
    panel.innerHTML = html`<p>STATION</p><b>${escapeHTML(station.name)}</b><span>${escapeHTML(station.code || "–")} · ${rows.length} tåg · ${connected} sträckor</span><small>${rows.slice(0, 4).map((row) => `${escapeHTML(row.trainNumber)} ${escapeHTML(row.kind)} ${escapeHTML(row.time)}`).join(" · ") || "Inga tidtabellslag"}</small>`;
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
    return html`<line x1="100" y1="${start}" x2="100" y2="${start + length}" stroke="${markerColor}" stroke-width="${width}" transform="rotate(${index * 6} 100 100)"/>`;
  }).join("");
  const numbers = config.hasNumbers ? Array.from({ length: 12 }, (_, index) => {
    const value = index === 0 ? 12 : index;
    const angle = (index * 30 - 90) * Math.PI / 180;
    return html`<text x="${100 + 68 * Math.cos(angle)}" y="${100 + 68 * Math.sin(angle)}" text-anchor="middle" dominant-baseline="central" font-size="12" font-weight="bold" fill="${numberColor}" font-family="sans-serif">${value}</text>`;
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
  // The meeting server owns presentation too. Retired localStorage/URL choices
  // must not silently override an administrator's change on another computer.
  let style = snapshot.clock?.style || available[0];
  if (!available.includes(style)) style = available[0];
  const seconds = currentClockSeconds(snapshot);
  const time = formatClockTime(seconds);
  const showSeconds = snapshot.clock?.show_seconds !== false;
  const displayTime = showSeconds ? time : time.slice(0, 5);
  const darkBackground = !document.querySelector("#display-app").classList.contains("light");
  const stopped = !snapshot.clock?.running;
  const stopText = snapshot.clock?.running ? "" : `STOPPAD${snapshot.clock?.stopped_reason ? ` · ${snapshot.clock.stopped_reason}` : ""}`;
  const renderSignature = [style, darkBackground, showSeconds, stopped, stopText].join("|");
  if (target.dataset.clockSignature !== renderSignature) {
    target.dataset.clockSignature = renderSignature;
    const face = style === "digital"
      ? html`<div class="clock-digital${stopped ? " stopped" : ""}"></div>`
      : clockSVG(style, darkBackground, showSeconds, stopped);
    const reason = stopText ? html`<div class="clock-stopped">${escapeHTML(stopText)}</div>` : "";
    target.innerHTML = html`<div class="clock-shell${style === "digital" ? " clock-shell--digital" : ""}">${face}${reason}</div>`;
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
  target.innerHTML = html`<div class="dashboard-column">
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
  document.querySelector("#display-title").textContent = `${snapshot.meet?.name || "TrainMeet"} · ${({ topology: t("Banöversikt"), graph: "Tågdiagram", clock: t("Träffklocka"), dashboard: t("Översikt") })[displayKind]}`;
  document.querySelector("#display-day").textContent = snapshot.active_day || "Dagl";
  const isClock = displayKind === "clock";
  document.querySelector("#display-speed").classList.toggle("hidden", !isClock);
  document.querySelector("#display-speed").textContent = `${Number(snapshot.clock?.speed || 1)}×`;
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
  return V2_GEOMETRIES[v2Geometry()];
}

//: Skärmkatalogen: varje fall i fixturerna, renderat i vald geometri.
function buildScreenCatalog() {
  const host = document.querySelector("#tmbox-screen-catalog");
  if (!host || typeof TMBoxFixtures === "undefined") return;
  const geometry = tmboxDocGeometry();
  const { config, snapshot, CASES, viewFor } = TMBoxFixtures;

  host.replaceChildren(...CASES.map(([name, screen, movement]) => {
    const card = document.createElement("article");
    card.className = "tmbox-screen-card card";

    const heading = document.createElement("h4");
    heading.textContent = name;
    card.append(heading);

    const value = document.createElement("p");
    value.className = "tmbox-screen-value";
    value.textContent = `Screen::${screen}`;
    card.append(value);

    const lcd = document.createElement("div");
    lcd.className = "lcd tmbox-mini-lcd";
    lcd.setAttribute("aria-label", `Skärmen ${name}`);
    paintFrame(lcd, geometry, TMBoxRender.render(geometry, viewFor(screen, movement), config, snapshot));
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
  const trace = TMBoxFixtures.TRACES.find((item) => item.name === name);
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
  source.textContent = `${trace.name} · tangenter ${trace.keys.split("").join(" ")} · ${trace.pace} ms mellan tryck`;
  parts.push(source);

  const note = document.createElement("p");
  note.className = "tmbox-flow-note";
  note.textContent = trace.note;
  parts.push(note);

  const list = document.createElement("ol");
  list.className = "tmbox-step-list";
  for (const step of replayTrace(trace).steps) {
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
    outcome.textContent = { Send: "Skickar", Redraw: "Ritar om", Ignored: "Ignoreras" }[step.outcome] || step.outcome;
    head.append(outcome);

    const screen = document.createElement("span");
    screen.className = "tmbox-step-screen";
    screen.textContent = `Screen::${step.screen}`;
    head.append(screen);
    item.append(head);

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
  host.replaceChildren(...TMBoxFixtures.TRACES.map((trace) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tmbox-flow-item";
    button.dataset.tmboxFlow = trace.name;
    button.setAttribute("aria-pressed", "false");
    button.textContent = trace.title;
    return button;
  }));
  const remembered = localStorage.getItem("trainmeet.tmboxFlow");
  const known = TMBoxFixtures.TRACES.some((trace) => trace.name === remembered);
  showFlow(known ? remembered : TMBoxFixtures.TRACES[0].name);
}

//: Dokumentationsvyerna ritas om när geometrin byts, annars visar de rutor i
//: en bredd som inte längre är vald.
function redrawTMBoxDocs() {
  if (typeof TMBoxFixtures === "undefined") return;
  buildScreenCatalog();
  const active = localStorage.getItem("trainmeet.tmboxFlow");
  if (document.querySelector("#tmbox-flow-list")?.children.length) {
    showFlow(TMBoxFixtures.TRACES.some((t) => t.name === active) ? active : TMBoxFixtures.TRACES[0].name);
  }
}

function selectTMBoxPane(pane) {
  const selected = TMBOX_PANES.includes(pane) ? pane : "klient";
  localStorage.setItem("trainmeet.tmboxPane", selected);
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
  if (modal?.dataset.busy === "true") return null;
  if (modal) modal.dataset.busy = "true";
  const buttons = [...(modal?.querySelectorAll("button") || [])];
  const previousDisabled = buttons.map((button) => button.disabled);
  buttons.forEach((button) => { button.disabled = true; });
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
    if (modal) modal.dataset.busy = "false";
    buttons.forEach((button, index) => { button.disabled = previousDisabled[index]; });
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
  if (result) finishModal(document.querySelector("#user-edit-form"));
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
    redrawTMBoxDocs();
  });
  bindTMBoxPanes();
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
    // Use the snapshot the operator acted on, not a newer polled context.
    meet_generation: tmboxV2.snapshot?.meet_generation,
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

// ==================================================== Unified live overview
// Uses the overview's snapshot and refresh cycle, never a second polling loop.
//
// All data kommer från /v1/display — samma källa som trafikmotorn och
// hallskärmarna. Ingenting här är exempeldata, och ingenting härleds på ett
// annat sätt än motorn gör det.

const trafficState = { station: "", onlyDeviations: false };

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
