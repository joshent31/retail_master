/* Retail Ops - service worker.
 *
 * Caches the POS shell (page + static assets) so the terminal can be re-opened
 * without a network. API calls are never cached: the POS handles sales offline
 * with its own IndexedDB queue, and Frappe data always has to be fresh.
 *
 * Served from /assets/retail_ops/js/sw.js with `Service-Worker-Allowed: /`
 * (set in retail_ops/retail_ops/utils.py) so it can control /retail-pos.
 */
// bump the version whenever the POS shell changes, so installed terminals
// replace the cached CSS/JS on their next visit
var CACHE_NAME = "retail-ops-v3";
var SHELL = [
	"/retail-pos",
	"/assets/retail_ops/manifest.json",
	"/assets/retail_ops/images/retail-pos-192.png",
	"/assets/retail_ops/images/retail-pos-512.png",
	"/assets/retail_ops/css/retail-pos.css",
	"/assets/retail_ops/js/offline-db.js",
	"/assets/retail_ops/js/sync.js",
	"/assets/retail_ops/js/pos.js",
];

self.addEventListener("install", function (event) {
	event.waitUntil(
		caches
			.open(CACHE_NAME)
			.then(function (cache) {
				return Promise.all(
					SHELL.map(function (url) {
						return cache.add(url).catch(function (error) {
							console.warn("SW: could not cache", url, error);
						});
					})
				);
			})
			.then(function () {
				return self.skipWaiting();
			})
	);
});

self.addEventListener("activate", function (event) {
	event.waitUntil(
		caches
			.keys()
			.then(function (keys) {
				return Promise.all(
					keys.map(function (key) {
						return key === CACHE_NAME ? null : caches.delete(key);
					})
				);
			})
			.then(function () {
				return self.clients.claim();
			})
	);
});

self.addEventListener("fetch", function (event) {
	var request = event.request;
	if (request.method !== "GET") {
		return;
	}

	var url;
	try {
		url = new URL(request.url);
	} catch (error) {
		return;
	}

	if (url.origin !== self.location.origin) {
		return;
	}
	if (url.pathname.indexOf("/api/") === 0 || url.pathname.indexOf("/assets/retail_ops/js/sw.js") === 0) {
		return;
	}

	// POS page: network first, cached shell as fallback.
	if (url.pathname === "/retail-pos" || url.pathname === "/retail-pos/") {
		event.respondWith(
			fetch(request)
				.then(function (response) {
					if (response && response.ok) {
						var copy = response.clone();
						caches.open(CACHE_NAME).then(function (cache) {
							cache.put(request, copy);
						});
					}
					return response;
				})
				.catch(function () {
					return caches.match("/retail-pos").then(function (cached) {
						return cached || new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } });
					});
				})
		);
		return;
	}

	// App assets and desk assets: cache first, refresh in background.
	if (url.pathname.indexOf("/assets/retail_ops/") === 0) {
		event.respondWith(
			caches.match(request).then(function (cached) {
				if (cached) {
					return cached;
				}
				return fetch(request).then(function (response) {
					if (response && response.ok) {
						var copy = response.clone();
						caches.open(CACHE_NAME).then(function (cache) {
							cache.put(request, copy);
						});
					}
					return response;
				});
			})
		);
	}
});
