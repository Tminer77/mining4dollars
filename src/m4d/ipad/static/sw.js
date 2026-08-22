/* M4D iPad console — keep the shell and the log on the device. */
const CACHE = "m4d-ipad-v2";
const SHELL = [
  "/",
  "/manifest.webmanifest",
  "/apple-touch-icon.png",
  "/ipad/app.css",
  "/ipad/app.js",
  "/ipad/store.js",
  "/ipad/icons/icon-180.png",
  "/ipad/icons/icon-192.png",
  "/ipad/icons/icon-512.png",
  "/ipad/icons/favicon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }

  // Live API and probes always go to the network.
  if (
    url.pathname.startsWith("/v1/") ||
    url.pathname === "/healthz" ||
    url.pathname === "/readyz" ||
    url.pathname === "/docs" ||
    url.pathname === "/openapi.json"
  ) {
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      const fetched = fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached || caches.match("/"));
      return cached || fetched;
    }),
  );
});
