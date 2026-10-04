// The run label is shared by admin, traffic displays, TKL and the live TMBox.
// The isolated per-browser lab deliberately does not load this script.
(() => {
  if (location.pathname.startsWith("/tkl/") && new URLSearchParams(location.search).get("mode") === "demo") return;
  const banner = document.createElement("div");
  banner.setAttribute("role", "status");
  banner.hidden = true;
  banner.style.cssText = "position:sticky;top:0;z-index:100;background:#6e3c10;color:#fff;padding:10px 16px;text-align:center;font:600 15px Inter, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif";
  document.body.prepend(banner);
  // Pages without the localization runtime (the per-browser lab) show the Swedish source.
  const t = (source) => globalThis.TrainMeetI18n?.t ? globalThis.TrainMeetI18n.t(source) : source;
  let active = false, timer = null, message = "";
  const show = (source) => { message = source; banner.textContent = source ? t(source) : ""; };
  globalThis.TrainMeetI18n?.subscribe?.(() => show(message));
  async function refresh() {
    clearTimeout(timer);
    try {
      const response = await fetch("/v1/display", {cache: "no-store", credentials: "same-origin"});
      if (!response.ok) throw new Error("offline");
      const data = await response.json();
      active = Boolean(data.clock?.simulation);
      banner.hidden = !active;
      show(!active ? "" : data.clock.running ? "SIMULERING · Pågår · Vanlig driftdata påverkas inte" : "SIMULERING · Pausad · Vanlig driftdata påverkas inte");
    } catch {
      // Never silently remove the warning on a dropped connection.
      if (active) show("SIMULERING · Kontakt med servern saknas");
    } finally { clearTimeout(timer); timer = setTimeout(refresh, globalThis.TrainMeetLive?.connected ? 10000 : 2000); }
  }
  // Listens when the page has a stream open anyway; opens none of its own, so
  // a virtual TMBox keeps its two-second check.
  globalThis.TrainMeetLive?.subscribe((topics) => {
    if (["simulation", "runtime", "clock"].some((name) => topics.has(name))) refresh();
  }, {passive: true});
  globalThis.TrainMeetLive?.onStatus?.((connected) => {
    clearTimeout(timer);
    timer = setTimeout(refresh, connected ? 10000 : 2000);
  });
  refresh();
})();
