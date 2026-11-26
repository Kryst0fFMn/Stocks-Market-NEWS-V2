// Market Pulse – service worker: web se dá nainstalovat na plochu a otevře se i bez připojení.
// Ceny, grafy a synchronizace (Worker, Yahoo, fonty) jdou vždy přímo ze sítě a nikdy se neukládají.

const CACHE = "market-pulse-v1";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;   // Worker, fonty, Yahoo: ne přes mezipaměť

  // klíč bez ?t=..., aby se pod novou adresou neukládal každý dotaz zvlášť
  const key = url.origin + url.pathname;

  const networkFirst = request.mode === "navigate" || /\/(index\.html|market\.json)$/.test(url.pathname);

  if (networkFirst) {
    // stránka a data: nejdřív aktuální ze sítě, při výpadku poslední uložená verze
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(key, copy));
          }
          return response;
        })
        .catch(() => caches.match(key).then((cached) => cached || caches.match("./")))
    );
    return;
  }

  // ikony a manifest: z mezipaměti, a když tam nejsou, ze sítě
  event.respondWith(
    caches.match(key).then(
      (cached) =>
        cached ||
        fetch(request).then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(key, copy));
          }
          return response;
        })
    )
  );
});
