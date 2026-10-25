/* Host Wi-Fi only. No passwords in localStorage, URLs, meet state or logging. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const t = (s, values) => globalThis.TrainMeetI18n.t(s, values);
  const app = () => globalThis.TrainMeetApp;
  const panel = $("pi-network-panel");
  if (!panel) return;
  let data = null, loading = false, busy = false, editing = null, action = null, rendered = "";
  const message = (id, text, kind = "error") => app().setMessage($(id), text, kind);
  const visible = () => document.body.dataset.mode === "installningar" && document.body.dataset.signedIn === "yes" && location.hash === "#server";
  async function request(verb, payload) {
    // Do not transmit a secret over LAN HTTP and wait for the server to reject
    // it: the server advertises permission before any edit form can be opened.
    if (verb && !data?.can_manage) throw new Error(t("Ändra nätverk i webbläsaren på Pi:n eller via en konfigurerad HTTPS-anslutning."));
    // Use a cookie explicitly, not a remembered paired-device bearer token.
    const response = await fetch("/v1/host/wifi" + (verb ? "/" + verb : ""), {
      method: verb ? "POST" : "GET", credentials: "same-origin", cache: "no-store",
      headers: verb ? { "Content-Type": "application/json", "X-TrainMeet-Network": "1" } : {},
      ...(verb ? { body: JSON.stringify(payload) } : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || t("Nätverksåtgärden misslyckades."));
    return result;
  }
  function button(label, callback, disabled = false) {
    const el = document.createElement("button"); el.type = "button"; el.className = "kr-btn sm";
    el.textContent = t(label); el.disabled = disabled || busy || Boolean(data.trial) || data.recovering || !data.can_manage; el.addEventListener("click", callback); return el;
  }
  function render() {
    panel.hidden = !data?.supported;
    if (panel.hidden) return;
    const selector = $("pi-network-interface"), old = selector.value;
    const signature = JSON.stringify([data, busy, old, globalThis.TrainMeetI18n.getLanguage()]);
    if (signature === rendered) return; // Keep keyboard focus during polling.
    rendered = signature;
    selector.replaceChildren(...data.devices.map(d => new Option(d.interface, d.interface)));
    if (data.devices.some(d => d.interface === old)) selector.value = old;
    const device = data.devices.find(d => d.interface === selector.value);
    const active = data.profiles.find(p => p.uuid === device?.uuid);
    $("pi-network-state").textContent = !data.radio_enabled ? t("Wi-Fi är avstängt i operativsystemet.") : device?.state === 100 ? t("Ansluten till {ssid}", { ssid: active?.ssid || "—" }) : t("Inte ansluten");
    $("pi-network-address").textContent = device?.addresses.join(" · ") || "—";
    const host = $("pi-network-profiles"); host.replaceChildren();
    const editable = data.profiles.filter(p => p.editable);
    if (!data.profiles.length) { const p = document.createElement("p"); p.textContent = t("Inga sparade Wi-Fi-nätverk."); host.append(p); }
    for (const profile of data.profiles) {
      const row = document.createElement("div"); row.className = "pi-network-profile";
      const label = document.createElement("strong"); label.textContent = profile.ssid; label.dataset.noI18n = "";
      const status = document.createElement("span");
      status.textContent = [profile.active ? t("Ansluten") : "", profile.security === "open" ? t("Öppet nätverk") : profile.security === "unsupported" ? t("Skrivskyddad systemprofil") : profile.security.toUpperCase(),
        !profile.autoconnect ? t("Automatisk anslutning avstängd") : ""].filter(Boolean).join(" · ");
      const actions = document.createElement("div"); actions.className = "pi-network-buttons";
      const index = editable.indexOf(profile);
      actions.append(button("Flytta upp", () => reorder(index, -1), index <= 0), button("Flytta ned", () => reorder(index, 1), index < 0 || index === editable.length - 1),
        button("Redigera", () => edit(profile), !profile.editable || profile.active),
        button("Anslut nu", () => prepare("connect", profile), !profile.editable || profile.active || !device || !data.radio_enabled),
        button("Ta bort", () => prepare("delete", profile), !profile.editable || profile.active));
      row.append(label, status, actions); host.append(row);
    }
    $("pi-network-add").disabled = busy || Boolean(data.trial) || data.recovering || !data.can_manage;
    $("pi-network-scan").disabled = busy || Boolean(data.trial) || data.recovering || !data.can_manage || !device || !data.radio_enabled;
    $("pi-network-recovering").hidden = !data.recovering;
    $("pi-network-trial").hidden = !data.trial;
    if (data.trial) {
      $("pi-network-trial-text").textContent = t("Provanslutning: {seconds} sekunder kvar. Utan bekräftelse återställs tidigare anslutning.", { seconds: data.trial.remaining });
      $("pi-network-confirm").disabled = busy || !data.can_manage || !data.trial.token || !data.devices.some(d => d.interface === data.trial.interface && d.uuid === data.trial.uuid && d.state === 100 && d.addresses.length);
      $("pi-network-rollback").disabled = busy || !data.can_manage || !data.trial.token;
    }
  }
  async function refresh() {
    if (loading || busy || !visible()) return;
    loading = true;
    try {
      const next = await request();
      // Do not hide an active trial on a transient helper failure.
      if (data?.supported && !next.supported) throw new Error(t("Pi:ns nätverkstjänst svarar inte."));
      data = next; render();
    } catch (error) {
      if (data?.supported) message("pi-network-message", t("Kontakt saknas. Återställningstimern fortsätter på Pi:n."));
    } finally { loading = false; }
  }
  async function mutate(verb, payload, output = "pi-network-message") {
    if (busy) return false;
    busy = true; render();
    const dialog = document.querySelector("dialog[open]");
    if (dialog) dialog.dataset.busy = "true";
    try {
      data = await request(verb, payload); message(output, ""); return true;
    } catch (error) { message(output, error.message); return false; }
    finally { busy = false; if (dialog) dialog.dataset.busy = "false"; render(); }
  }
  function securityChanged() {
    const open = $("pi-network-security").value === "open";
    $("pi-network-password").disabled = open;
    if (open) $("pi-network-password").value = "";
    $("pi-network-open-warning").hidden = !open;
  }
  function edit(profile = null) {
    if (!data?.can_manage) return;
    editing = profile;
    $("pi-network-form").reset();
    $("pi-network-ssid").value = profile?.ssid || "";
    $("pi-network-security").value = profile?.security || "wpa2";
    $("pi-network-hidden").checked = profile?.hidden || false;
    const nearby = $("pi-network-nearby"); nearby.replaceChildren(new Option(t("Skriv SSID manuellt"), ""));
    const seen = new Set();
    data.nearby.filter(n => n.interface === $("pi-network-interface").value && n.security.length).forEach(n => {
      if (seen.has(n.ssid)) return; seen.add(n.ssid);
      const option = new Option(`${n.ssid} (${n.signal}%)`, n.ssid); option.dataset.security = n.security.includes("wpa2") ? "wpa2" : n.security[0]; nearby.append(option);
    });
    securityChanged(); app().openModal("pi-network-edit");
  }
  function prepare(verb, profile) {
    action = { verb, payload: { uuid: profile.uuid, ...(verb === "connect" ? { interface: $("pi-network-interface").value } : {}) } };
    $("pi-network-action-name").textContent = profile.ssid;
    $("pi-network-action-warning").textContent = t(verb === "connect"
      ? "Webbläsaren och TMBoxarna kan tappa kontakten. Återanslut till Pi:n och välj Behåll anslutningen inom 90 sekunder, annars återställs tidigare anslutning."
      : "Ta bort det sparade nätverket från Pi:n? Lösenordet måste anges igen om nätverket läggs tillbaka.");
    app().openModal("pi-network-action");
  }
  async function reorder(index, direction) {
    const ids = data.profiles.filter(p => p.editable).map(p => p.uuid);
    [ids[index], ids[index + direction]] = [ids[index + direction], ids[index]];
    await mutate("order", { uuids: ids });
  }
  $("pi-network-add").addEventListener("click", () => edit());
  $("pi-network-refresh").addEventListener("click", refresh);
  $("pi-network-interface").addEventListener("change", render);
  $("pi-network-security").addEventListener("change", securityChanged);
  $("pi-network-nearby").addEventListener("change", () => {
    const choice = $("pi-network-nearby").selectedOptions[0];
    if (choice.value) { $("pi-network-ssid").value = choice.value; $("pi-network-security").value = choice.dataset.security; securityChanged(); }
  });
  $("pi-network-form").addEventListener("submit", async event => {
    event.preventDefault();
    const payload = { ...(editing ? { uuid: editing.uuid } : {}), ssid: $("pi-network-ssid").value,
      security: $("pi-network-security").value, password: $("pi-network-password").value, hidden: $("pi-network-hidden").checked };
    if (await mutate("save", payload, "pi-network-edit-message")) {
      $("pi-network-password").value = "";
      $("pi-network-edit").close(); message("pi-network-message", t("Nätverket är sparat. Aktiv anslutning är oförändrad."), "success");
    }
    payload.password = "";
  });
  $("pi-network-edit").addEventListener("close", () => { $("pi-network-password").value = ""; });
  $("pi-network-action-submit").addEventListener("click", async () => {
    if (action && await mutate(action.verb, action.payload, "pi-network-action-message")) $("pi-network-action").close();
  });
  $("pi-network-confirm").addEventListener("click", () => mutate("confirm", { token: data.trial?.token }));
  $("pi-network-rollback").addEventListener("click", () => mutate("rollback", { token: data.trial?.token }));
  $("pi-network-scan").addEventListener("click", async () => {
    if (await mutate("scan", { interface: $("pi-network-interface").value })) {
      message("pi-network-message", t("Sökningen är startad. Välj Lägg till nätverk för att se resultaten."), "success");
      setTimeout(refresh, 2500);
    }
  });
  addEventListener("hashchange", refresh);
  globalThis.TrainMeetI18n.subscribe(() => { if (data) render(); });
  new MutationObserver(refresh).observe(document.body, { attributes: true, attributeFilter: ["data-mode", "data-signed-in"] });
  setInterval(() => { if (!document.hidden && data?.supported) refresh(); }, 5000);
  refresh();
})();
