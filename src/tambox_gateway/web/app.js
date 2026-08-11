const keypadKeys = ["1", "2", "3", "A", "4", "5", "6", "B", "7", "8", "9", "C", "*", "0", "#", "D"];
const slotKeys = ["A", "B", "C", "D"];

const state = {
  token: localStorage.getItem("tambox.accessToken"),
  clientID: localStorage.getItem("tambox.clientID") || `web-${crypto.randomUUID()}`,
  selectedView: localStorage.getItem("trainmeet.view") || "server",
  snapshots: new Map(),
  selectedPanelID: localStorage.getItem("tambox.panelID"),
  snapshotTimer: null,
  adminTimer: null,
  sending: false,
  config: null,
  configRevision: 0,
  restartRequired: false,
  restarting: false,
  authStatus: null,
};

const login = document.querySelector("#login");
const appView = document.querySelector("#app-view");
const loginForm = document.querySelector("#login-form");
const loginError = document.querySelector("#login-error");
const panelSelect = document.querySelector("#panel-select");
const connectionStatus = document.querySelector("#connection");
const commandMessage = document.querySelector("#command-message");
const keypad = document.querySelector("#keypad");
const deviceForm = document.querySelector("#device-form");
const deviceMessage = document.querySelector("#device-message");
const devicePanel = document.querySelector("#device-panel");
const runtimeForm = document.querySelector("#runtime-sync-form");
const runtimeMessage = document.querySelector("#runtime-message");
const configForm = document.querySelector("#config-form");
const configMessage = document.querySelector("#config-message");
const stationEditor = document.querySelector("#station-editor");
const connectionEditor = document.querySelector("#connection-editor");
const panelEditor = document.querySelector("#panel-editor");
const restartButton = document.querySelector("#restart-server");
const logoutButton = document.querySelector("#logout");
const adminAccessForm = document.querySelector("#admin-access-form");
const adminAccessMessage = document.querySelector("#admin-access-message");

for (const key of keypadKeys) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `key ${/[A-D]/.test(key) ? "letter" : ""}`;
  button.textContent = key;
  button.dataset.key = key;
  button.addEventListener("click", () => pressKey(key));
  keypad.append(button);
}

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
    await openApplication();
  } catch (error) {
    setMessage(loginError, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

document.querySelectorAll(".view-tab").forEach((button) => {
  button.addEventListener("click", () => selectView(button.dataset.view));
});

logoutButton.addEventListener("click", async () => {
  await fetch("/v1/auth/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  localStorage.removeItem("tambox.accessToken");
  localStorage.removeItem("tambox.panelID");
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

panelSelect.addEventListener("change", () => {
  state.selectedPanelID = panelSelect.value;
  localStorage.setItem("tambox.panelID", state.selectedPanelID);
  renderTambox();
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
        panel_id: devicePanel.value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Tamboxen kunde inte kopplas");
    setMessage(deviceMessage, "Tamboxen är kopplad och får sin panel vid nästa kontakt.", "success");
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
  setMessage(runtimeMessage, "Hämtar träffen …");
  const button = runtimeForm.querySelector("button");
  button.disabled = true;
  try {
    const response = await authorizedFetch("/v1/runtime/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sync_code: document.querySelector("#runtime-sync-code").value }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Träffen kunde inte hämtas");
    setMessage(runtimeMessage, payload.message, payload.restart_required ? "notice" : "success");
    document.querySelector("#runtime-sync-code").value = "";
    await Promise.all([refreshRuntime(), refreshInfo()]);
  } catch (error) {
    setMessage(runtimeMessage, error.message, "error");
  } finally {
    button.disabled = false;
  }
});

configForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveConfiguration(false);
});

document.querySelector("#activate-config").addEventListener("click", async () => {
  await saveConfiguration(true);
});

restartButton.addEventListener("click", restartServer);

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
  login.classList.add("hidden");
  appView.classList.remove("hidden");
  logoutButton.classList.toggle("hidden", state.authStatus?.access_mode !== "external");
  selectView(state.selectedView);
  try {
    await Promise.all([
      refreshInfo(),
      refreshAdminAccess(),
      loadLocalConfiguration(),
      refreshDevices(),
      refreshRuntime(),
      refreshSnapshots(),
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

function selectView(view) {
  const selected = view === "simulator" ? "simulator" : "server";
  state.selectedView = selected;
  localStorage.setItem("trainmeet.view", selected);
  document.querySelector("#server-view").classList.toggle("hidden", selected !== "server");
  document.querySelector("#simulator-view").classList.toggle("hidden", selected !== "simulator");
  document.querySelectorAll(".view-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === selected);
  });
}

async function refreshSnapshots() {
  clearTimeout(state.snapshotTimer);
  try {
    const response = await authorizedFetch("/v1/snapshots");
    if (response.status === 401) {
      localStorage.removeItem("tambox.accessToken");
      state.token = null;
      await showLogin();
      throw new Error("Inloggningen gäller inte längre");
    }
    if (!response.ok) throw new Error("Servern svarade inte korrekt");
    const payload = await response.json();
    state.snapshots = new Map(payload.snapshots.map((snapshot) => [snapshot.panel_id, snapshot]));
    if (!state.snapshots.has(state.selectedPanelID)) {
      state.selectedPanelID = payload.snapshots[0]?.panel_id || null;
    }
    updatePanelOptions();
    renderTambox();
    setConnection(
      "online",
      state.authStatus?.access_mode === "external" ? "Externt ansluten" : "Lokalt ansluten",
    );
  } catch (error) {
    handleConnectionError(error);
    if (!state.authStatus?.authenticated) return;
  }
  state.snapshotTimer = setTimeout(refreshSnapshots, 750);
}

function scheduleAdminRefresh() {
  clearTimeout(state.adminTimer);
  state.adminTimer = setTimeout(async () => {
    if (!state.authStatus?.authenticated) return;
    await Promise.allSettled([refreshInfo(), refreshDevices(), refreshRuntime(), refreshAdminAccess()]);
    scheduleAdminRefresh();
  }, 5000);
}

async function pressKey(key) {
  const snapshot = state.snapshots.get(state.selectedPanelID);
  if (!snapshot || state.sending || !snapshot.interaction.allowed_keys.includes(key)) return;
  state.sending = true;
  setMessage(commandMessage, "");
  renderTambox();
  try {
    const response = await authorizedFetch("/v1/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        command_id: crypto.randomUUID(),
        panel_id: snapshot.panel_id,
        expected_revision: snapshot.revision,
        key,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.message || "Kommandot kunde inte skickas");
    for (const next of Object.values(payload.snapshots || {})) {
      state.snapshots.set(next.panel_id, next);
    }
    if (payload.status === "rejected") setMessage(commandMessage, reasonText(payload.reason), "error");
    renderTambox();
  } catch (error) {
    setMessage(commandMessage, error.message, "error");
  } finally {
    state.sending = false;
    renderTambox();
  }
}

async function refreshInfo() {
  const response = await authorizedFetch("/v1/info");
  if (!response.ok) return;
  const info = await response.json();
  document.querySelector("#server-name").textContent = info.gateway_id || "TrainMeet Server";
  document.querySelector("#server-detail").textContent =
    `Kör lokalt · aktiv trafiksession: ${info.traffic_session_name}`;
  const pill = document.querySelector("#runtime-pill");
  if (info.runtime?.configured) {
    pill.textContent = `${info.runtime.meet_name} · ${info.runtime.active_day}`;
    pill.classList.add("active");
  } else {
    pill.textContent = "Demoläge – ingen träff aktiverad";
    pill.classList.remove("active");
  }
  updateRestartButton(Boolean(info.restart_required));
}

async function refreshAdminAccess() {
  const response = await authorizedFetch("/v1/admin/access");
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || "Åtkomstinställningen kunde inte läsas");
  document.querySelector("#admin-username").value = payload.username || "admin";
  const badge = document.querySelector("#access-mode");
  badge.textContent = payload.password_configured ? "Extern inloggning klar" : "Lösenord saknas";
  badge.classList.toggle("active", payload.password_configured);
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
  restartButton.disabled = true;
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
    restartButton.disabled = false;
    setMessage(configMessage, error.message, "error");
    scheduleAdminRefresh();
    refreshSnapshots();
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
  restartButton.disabled = false;
  setConnection("waiting", "Kontrollera servern");
  setMessage(configMessage, "Servern har inte kommit tillbaka ännu. Kontrollera ström och nätverk.", "error");
}

function updateRestartButton(required) {
  state.restartRequired = required;
  restartButton.classList.toggle("hidden", !required);
  restartButton.disabled = state.restarting;
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
    : emptyEditor("Inga paneler ännu", "Varje station som ska användas behöver minst en Tambox-panel.");
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
    name: `${station.code} Tambox`,
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
      name: `${station.code} Tambox`,
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
  const list = document.querySelector("#device-list");
  list.replaceChildren();
  if (!payload.devices.length) {
    list.innerHTML = '<div class="empty-status">Ingen fysisk Tambox har presenterat sig ännu.</div>';
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
    const assignment = document.createElement("span");
    assignment.textContent = device.assigned_panel_ids.length
      ? device.assigned_panel_ids.join(", ")
      : "Väntar på panel";
    row.append(identity, assignment);
    list.append(row);
  }
}

async function refreshRuntime() {
  const response = await authorizedFetch("/v1/runtime");
  if (!response.ok) return;
  const runtime = await response.json();
  const status = document.querySelector("#runtime-status");
  status.replaceChildren();
  const row = document.createElement("div");
  row.className = "status-row";
  const identity = document.createElement("div");
  const title = document.createElement("b");
  const detail = document.createElement("small");
  if (runtime.configured) {
    title.textContent = runtime.meet_name;
    detail.textContent = `${runtime.station_count} stationer · ${runtime.train_count} tågrörelser`;
  } else {
    title.textContent = "Ingen träff aktiverad";
    detail.textContent = "Servern kör den inbyggda demonstrationen";
  }
  identity.append(title, detail);
  const day = document.createElement("span");
  day.textContent = runtime.active_day || "–";
  row.append(identity, day);
  status.append(row);
}

function updatePanelOptions() {
  const signature = [...state.snapshots.values()]
    .map((snapshot) => `${snapshot.panel_id}:${snapshot.panel_name}`)
    .sort()
    .join("|");
  if (panelSelect.dataset.signature !== signature) {
    panelSelect.dataset.signature = signature;
    panelSelect.replaceChildren();
    for (const snapshot of [...state.snapshots.values()].sort((a, b) => a.panel_name.localeCompare(b.panel_name))) {
      const option = document.createElement("option");
      option.value = snapshot.panel_id;
      option.textContent = snapshot.panel_name;
      panelSelect.append(option);
    }
  }
  panelSelect.value = state.selectedPanelID || "";
  devicePanel.replaceChildren(...[...panelSelect.options].map((option) => option.cloneNode(true)));
}

function renderTambox() {
  const snapshot = state.snapshots.get(state.selectedPanelID);
  if (!snapshot) return;
  panelSelect.value = snapshot.panel_id;
  document.querySelector("#lcd-line-1").textContent = padDisplay(snapshot.display.line1);
  document.querySelector("#lcd-line-2").textContent = padDisplay(snapshot.display.line2);
  const allowed = new Set(snapshot.interaction.allowed_keys);
  for (const button of keypad.querySelectorAll("button")) {
    button.disabled = state.sending || !allowed.has(button.dataset.key);
  }
}

function authorizedFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  return fetch(path, { ...options, headers, credentials: "same-origin" });
}

function handleConnectionError(error) {
  setConnection("waiting", "Återansluter");
  setMessage(commandMessage, error.message, "error");
  if (!state.authStatus?.authenticated) showLogin();
}

async function refreshAuthStatus() {
  const response = await fetch("/v1/auth/status", { cache: "no-store", credentials: "same-origin" });
  if (!response.ok) throw new Error("Serverns åtkomstläge kunde inte läsas");
  state.authStatus = await response.json();
  state.token = null;
  localStorage.removeItem("tambox.accessToken");
  document.querySelector("#login-username").value = state.authStatus.username || "admin";
  return state.authStatus;
}

async function showLogin() {
  clearTimeout(state.snapshotTimer);
  clearTimeout(state.adminTimer);
  state.authStatus = { ...(state.authStatus || {}), authenticated: false };
  appView.classList.add("hidden");
  login.classList.remove("hidden");
  setConnection("offline", "Inloggning krävs");
}

async function bootstrap() {
  try {
    const status = await refreshAuthStatus();
    if (status.authenticated) {
      await openApplication();
      return;
    }
    showLogin();
    if (!status.password_configured) {
      setMessage(
        loginError,
        "Extern inloggning är inte konfigurerad. Öppna servern från det lokala nätverket och välj ett lösenord först.",
        "notice",
      );
    }
  } catch (error) {
    setConnection("waiting", "Servern svarar inte");
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

function padDisplay(value) {
  return String(value || "").slice(0, 16).padEnd(16, " ");
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

function reasonText(reason) {
  return ({
    connection_busy: "Sträckan är upptagen",
    stale_revision: "Läget ändrades – försök igen",
    interaction_owned: "En annan enhet använder panelen",
    expired_command: "Kommandot hann bli för gammalt",
    unused_slot: "Platsen är inte konfigurerad",
  })[reason] || "Kommandot nekades av TrainMeet Server";
}

bootstrap();
