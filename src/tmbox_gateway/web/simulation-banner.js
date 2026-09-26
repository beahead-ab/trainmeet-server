// The run label is shared by admin, traffic displays, TKL and the live TMBox.
// The isolated per-browser lab deliberately does not load this script.
(() => {
  if (location.pathname.startsWith("/tkl/") && new URLSearchParams(location.search).get("mode") === "demo") return;
  const banner = document.createElement("div");
  banner.setAttribute("role", "status");
  banner.hidden = true;
  banner.style.cssText = "position:sticky;top:0;z-index:100;background:#6e3c10;color:#fff;padding:10px 16px;text-align:center;font:600 15px system-ui";
  document.body.prepend(banner);
  let active = false;
  async function refresh() {
    try {
      const response = await fetch("/v1/display", {cache: "no-store", credentials: "same-origin"});
      if (!response.ok) throw new Error("offline");
      const data = await response.json();
      active = Boolean(data.clock?.simulation);
      banner.hidden = !active;
      banner.textContent = active ? `SIMULERING · ${data.clock.running ? "Pågår" : "Pausad"} · Vanlig driftdata påverkas inte` : "";
    } catch {
      // Never silently remove the warning on a dropped connection.
      if (active) banner.textContent = "SIMULERING · Kontakt med servern saknas";
    } finally { setTimeout(refresh, 2000); }
  }
  refresh();
})();
