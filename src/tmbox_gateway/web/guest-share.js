/* Guest-only navigation and QR sharing. Read-only, powered by the public display
 * snapshot; no admin API, no private Raspberry Pi Wi-Fi profiles. */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const t = (message) => globalThis.TrainMeetI18n.t(message);
  const screens = {
    clock: "Träffklocka",
    dashboard: "Översikt",
    topology: "Banöversikt",
    graph: "Tågdiagram",
    territories: "Områdestavla",
  };
  let snapshot = null;
  let bound = false;

  function renderScreens() {
    const host = $("#pv-screens-list");
    if (!host || !snapshot) return;
    const kinds = snapshot.meet?.operating_region === "us"
      ? ["clock", "territories"] : ["clock", "dashboard", "topology", "graph"];
    const signature = kinds.join("|") + "|" + globalThis.TrainMeetI18n.getLanguage();
    if (host.dataset.signature === signature) return;
    host.dataset.signature = signature;
    host.replaceChildren(...kinds.map((kind) => {
      const link = document.createElement("a");
      link.href = "/display/" + kind;
      link.textContent = t(screens[kind]);
      return link;
    }));
  }

  function qrCard(payload, heading, detail) {
    const card = document.createElement("div");
    card.className = "pv-share-qr";
    const title = document.createElement("strong");
    title.textContent = heading;
    const graphic = document.createElement("div");
    graphic.className = "pv-share-qr__graphic";
    // The existing QR renderer emits a local SVG. No QR service or request.
    graphic.innerHTML = globalThis.TrainMeetServerUI.qrSVG(payload);
    graphic.setAttribute("role", "img");
    graphic.setAttribute("aria-label", heading);
    const caption = document.createElement("span");
    caption.className = "pv-share-qr__detail";
    caption.textContent = detail;
    card.append(title, graphic, caption);
    return card;
  }

  function renderShare() {
    const host = $("#pv-share-codes");
    if (!host || !snapshot) return;
    const link = new URL("/", location.href).href;
    const wifi = snapshot.connection?.wifi || {};
    // A network with a withheld password is not a scan-ready QR payload.
    const canShareWifi = Boolean(wifi.name) && (!wifi.has_password || Boolean(wifi.password));
    const network = canShareWifi ? globalThis.TrainMeetServerUI?.wifiQR(wifi) : "";
    const signature = [network, wifi.name || "", wifi.has_password || false, link, globalThis.TrainMeetI18n.getLanguage()].join("|");
    if (host.dataset.signature === signature) return;
    host.dataset.signature = signature;
    host.replaceChildren();
    const lib = globalThis.TrainMeetServerUI;
    if (!lib?.qrSVG || typeof globalThis.qrcode !== "function") {
      const notice = document.createElement("p");
      notice.className = "pv-note";
      notice.textContent = t("QR-koderna kunde inte visas. Dela länken nedan.");
      host.append(notice);
    } else {
      if (network) {
        host.append(qrCard(network, "1 · " + t("Wi-Fi"), wifi.name));
      } else {
        const info = document.createElement("div");
        info.className = "pv-share-empty";
        const title = document.createElement("strong");
        title.textContent = "1 · " + t("Wi-Fi");
        const detail = document.createElement("p");
        detail.textContent = wifi.name
          ? t("Wi-Fi-lösenordet delas inte. Fråga trafikledningen.")
          : t("Träffens Wi-Fi har inte lagts in ännu.");
        info.append(title, detail);
        host.append(info);
      }
      host.append(qrCard(link, "2 · " + t("Träffen"), link));
    }
    const input = $("#pv-share-link");
    if (input) input.value = link;
    const status = $("#pv-share-copy-status");
    if (status) status.textContent = "";
  }

  async function copyLink() {
    const input = $("#pv-share-link");
    const status = $("#pv-share-copy-status");
    if (!input || !status) return;
    input.focus();
    input.select();
    let copied = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(input.value);
        copied = true;
      } else if (typeof document.execCommand === "function") {
        copied = document.execCommand("copy");
      }
    } catch { /* Some local HTTP browsers disallow clipboard writes. */ }
    status.textContent = copied ? t("Länken är kopierad.") : t("Markera och kopiera länken ovan.");
  }

  function bind() {
    if (bound) return;
    bound = true;
    const dialog = $("#pv-share-card");
    $("#pv-share-open")?.addEventListener("click", () => {
      renderShare();
      if (typeof dialog?.showModal === "function") dialog.showModal();
      else dialog?.setAttribute("open", "");
    });
    $("#pv-share-close")?.addEventListener("click", () => dialog.close());
    dialog?.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    $("#pv-share-copy")?.addEventListener("click", copyLink);
    // Keep the selected view in this document: a page navigation would discard
    // the user's gesture and leave native fullscreen before the view opens.
    $("#participant-view")?.addEventListener("click", (event) => {
      const link = event.target.closest("a");
      if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const url = new URL(link.href, location.href);
      if (url.origin !== location.origin || !url.pathname.startsWith("/display/")) return;
      if (globalThis.TrainMeetDisplays?.openGuest(url.pathname)) event.preventDefault();
    });
  }

  globalThis.TrainMeetGuest = {
    bind,
    render(value) {
      snapshot = value;
      renderScreens();
      renderShare();
    },
  };
})();
