/* Kontrollrummets tema, sätt innan sidan målas så att den inte blinkar till.
 * Mörkt är grundläget; valet i sidhuvudet sparas i den här webbläsaren.
 * Bara deltagarvyn utan sparat val följer enhetens inställning. */
(() => {
  let theme = "dark";
  try {
    // En skärm (/display/…) har ett eget val, så att TV:n kan vara ljus när driften är mörk.
    const own = location.pathname.startsWith("/display/") ? localStorage.getItem("trainmeet.displayTheme") : null;
    const stored = own || localStorage.getItem("trainmeet.theme");
    if (stored === "light") theme = "light";
    // Deltagarvyn (/) har ingen väljare: utan eget val följer den telefonens ljus eller mörker.
    else if (!stored && location.pathname === "/" && matchMedia("(prefers-color-scheme: light)").matches) theme = "light";
  } catch { /* privat läge: mörkt */ }
  document.documentElement.dataset.krTheme = theme;
  // Adminsidorna har kontrollrummets botten redan i första bilden, innan app.js
  // vet vilken vy som ska visas. Annars syntes en ljus sida en stund.
  if (/^\/(drift|installningar|tidtabell|hjalp|login)\/?$/.test(location.pathname)) document.documentElement.dataset.krPage = "admin";
})();
