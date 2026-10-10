// /v1/display för andra delar av sidan (day-change.js: toasten vid
// dygnsskiftet), utan att var och en hämtar själv. Bilden skickas som
// händelsen "trainmeet:display". Var tionde sekund när sidan har en öppen
// ström (/v1/events), annars varannan, och genast när trafiken, klockan eller
// träffen ändras. Simuleringens banderoll låg här tills simuleringen togs
// bort (Server 4.0).
(() => {
  let timer = null;
  async function refresh() {
    clearTimeout(timer);
    try {
      const response = await fetch("/v1/display", {cache: "no-store", credentials: "same-origin"});
      if (!response.ok) throw new Error("offline");
      const data = await response.json();
      try { document.dispatchEvent(new CustomEvent("trainmeet:display", {detail: data})); } catch {}
    } catch {
      // Ingen kontakt: nästa försök kommer av sig självt.
    } finally { clearTimeout(timer); timer = setTimeout(refresh, globalThis.TrainMeetLive?.connected ? 10000 : 2000); }
  }
  // Lyssnar när sidan ändå har en ström öppen; öppnar ingen egen, så en
  // virtuell TMBox behåller sin kontroll varannan sekund.
  globalThis.TrainMeetLive?.subscribe((topics) => {
    if (["automatic", "runtime", "clock"].some((name) => topics.has(name))) refresh();
  }, {passive: true});
  globalThis.TrainMeetLive?.onStatus?.((connected) => {
    clearTimeout(timer);
    timer = setTimeout(refresh, connected ? 10000 : 2000);
  });
  refresh();
})();
