/* Lokföraren: appens skal finns kvar i telefonen även utan nät.
 * API-svar (/v1/…) sparas aldrig här; senaste tjänsten håller appen själv i
 * högst tolv timmar. Scope: bara /lokforaren/, aldrig admin eller skärmar.
 *
 * Cachebust: servern skriver in appens byggstämpel där det står __LF_BUILD__,
 * både här och i varje fil-adress i index.html (?v=…). En ny version ger
 * alltså en ny sw.js (som webbläsaren ser som en uppdatering), en ny cache
 * och nya adresser, och den gamla cachen tas bort när den nya tar över. */
const BUILD = "__LF_BUILD__";
const CACHE = `lokforaren-${BUILD}`;
const PAGE = "/lokforaren/";
const SHELL = [
  "/lokforaren/style.css",
  "/lokforaren/app.js",
  "/lokforaren/korplan.js",
  "/lokforaren/messages.js",
  "/lokforaren/manifest.webmanifest",
  "/lokforaren/ikon.svg",
  "/lokforaren/ikon-32.png",
  "/lokforaren/ikon-180.png",
  "/assets/clock-sync.js",
  "/assets/fonts/fonts.css",
].map((path) => `${path}?v=${BUILD}`);

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE)
    .then((cache) => cache.addAll([PAGE, ...SHELL]))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((key) => key.startsWith("lokforaren-") && key !== CACHE).map((key) => caches.delete(key))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/v1/")) return;

  // Själva sidan: nätet först, så att en ny version alltid kommer fram;
  // cachen bara när servern inte svarar.
  if (request.mode === "navigate" || url.pathname === PAGE || url.pathname === "/lokforaren/index.html") {
    event.respondWith(fetch(request).then((response) => {
      if (response.ok && url.pathname === PAGE && !url.search) {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(PAGE, copy));
      }
      return response;
    }).catch(() => caches.match(PAGE, { cacheName: CACHE }).then((cached) => cached || caches.match(PAGE))));
    return;
  }

  // Appens filer bär versionen i adressen och ändras aldrig under samma
  // adress: cachen först, nätet om den saknas. Typsnitten likaså.
  const versioned = SHELL.includes(url.pathname + url.search);
  const font = url.pathname.startsWith("/assets/fonts/") && url.pathname.endsWith(".woff2");
  if (!versioned && !font) return;
  event.respondWith(caches.open(CACHE).then((cache) => cache.match(request).then((cached) => cached || fetch(request).then((response) => {
    if (response.ok) cache.put(request, response.clone());
    return response;
  }))));
});
