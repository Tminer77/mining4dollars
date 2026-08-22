/* M4D iPad console — keep the shell and the log on the device. */
const CACHE = "m4d-ipad-v6";
const SHELL = [
  "./",
  "./index.html",
  "./console.html",
  "./notes.html",
  "./inner.html",
  "./template.html",
  "./apps.json",
  "./manifest.webmanifest",
  "./app.css",
  "./app.js",
  "./home.js",
  "./notes.js",
  "./inner.js",
  "./store.js",
  "./icons/icon-180.png",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/favicon.svg",
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
    url.pathname.endsWith("/healthz") ||
    url.pathname.endsWith("/readyz") ||
    url.pathname.includes("/v1/") ||
    url.pathname.endsWith("/docs") ||
    url.pathname.endsWith("/openapi.json")
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
        .catch(() => cached || caches.match(new URL("./", self.location)));
      return cached || fetched;
    }),
  );
});
