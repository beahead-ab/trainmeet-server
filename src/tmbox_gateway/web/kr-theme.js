/* Kontrollrummets tema, sätt innan sidan målas så att den inte blinkar till.
 * Mörkt är grundläget; valet i sidhuvudet sparas i den här webbläsaren. */
(() => {
  let theme = "dark";
  try { if (localStorage.getItem("trainmeet.theme") === "light") theme = "light"; } catch { /* privat läge: mörkt */ }
  document.documentElement.dataset.krTheme = theme;
})();
