/* Lokföraren: appens skal finns kvar i telefonen även utan nät.
 * API-svar (/v1/…) sparas aldrig här; senaste tjänsten håller appen själv i
 * högst tolv timmar. Scope: bara /lokforaren/, aldrig admin eller skärmar. */
const CACHE = "lokforaren-v1";
const SHELL = [
  "/lokforaren/",
  "/lokforaren/style.css",
  "/lokforaren/app.js",
  "/lokforaren/korplan.js",
  "/lokforaren/messages.js",
  "/lokforaren/manifest.webmanifest",
  "/assets/clock-sync.js",
  "/assets/fonts/fonts.css",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((key) => key.startsWith("lokforaren-") && key !== CACHE).map((key) => caches.delete(key))))
    .then(() => self.clients.claim()));
});

// Nätet först, så att en ny version alltid kommer fram; cachen bara när
// servern inte svarar.
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/v1/")) return;
  const shell = SHELL.includes(url.pathname) || url.pathname.startsWith("/assets/fonts/");
  if (!shell) return;
  event.respondWith(fetch(event.request).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      caches.open(CACHE).then((cache) => cache.put(event.request, copy));
    }
    return response;
  }).catch(() => caches.match(event.request).then((cached) => cached || caches.match("/lokforaren/"))));
});
