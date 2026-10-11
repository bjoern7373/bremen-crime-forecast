// Minimal offline support for the Bremen Tatkarte PWA.
// Everything of ours is fetched from the network first -- the data changes
// daily and the pages change with every improvement, and showing the newest
// version matters more than an instant load. Whatever was fetched last is
// kept, so the app still opens without a connection, with the last-seen data.
var CACHE = "bt-shell-v3";
// fetched when the worker is installed, so the map also opens offline after a single visit
// (the two files with full texts that are only loaded on demand are kept from first use on)
var SHELL = ["map_local.html", "details_local.html", "message_local.html", "manifest.json", "icons/icon-192.png",
             "map_data.json", "forecast.json", "news_index.json"];

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
  // map tiles, the Leaflet library etc. are not ours to cache
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  event.respondWith(
    fetch(event.request).then(function (res) {
      if (res.ok) {
        var copy = res.clone();
        // message_local.html is only ever requested with ?pm=... / ?court=...: it is the same
        // page every time, so it is kept once, under its plain address, and stays current
        caches.open(CACHE).then(function (c) { c.put(url.origin + url.pathname, copy); });
      }
      return res;
    }).catch(function () {
      // offline: the same page regardless of its ?id=... / ?court=... parameters
      return caches.match(event.request).then(function (hit) {
        return hit || caches.match(event.request, { ignoreSearch: true });
      });
    })
  );
});
