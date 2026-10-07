// Minimal offline shell for the Bremen Tatkarte PWA.
// map_data.json is always network-first (daily updates matter more than
// instant load); the static page shell is cached so the app still opens
// without a connection, just with the last-seen data.
var CACHE = "bt-shell-v2";
var SHELL = ["map_local.html", "details_local.html", "message_local.html", "manifest.json"];

self.addEventListener("install", function (event) {
  event.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); }));
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
    })
  );
  self.clients.claim();
});

self.addEventListener("fetch", function (event) {
  var url = new URL(event.request.url);
  if (url.pathname.endsWith("map_data.json")) {
    event.respondWith(
      fetch(event.request).then(function (res) {
        caches.open(CACHE).then(function (c) { c.put(event.request, res.clone()); });
        return res;
      }).catch(function () { return caches.match(event.request); })
    );
    return;
  }
  event.respondWith(caches.match(event.request).then(function (cached) { return cached || fetch(event.request); }));
});
