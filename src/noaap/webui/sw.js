// App shell cache: the UI works offline-ish; /api/* always goes to the network.
// A fresh name is what makes an installed PWA drop the shell it had: `activate` deletes every
// cache that is not this one.
const CACHE = "noaap-v1";
const SHELL = ["/manifest.webmanifest", "/icon.svg"]; // app.js, logic.mjs and style.css are content-hashed
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL))));
self.addEventListener("activate", (e) =>
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))))
);
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request)));
});
