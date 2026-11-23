/* Retail Ops - offline sync engine.
 *
 * Pushes the IndexedDB queue to /api/method/retail_ops.api.sync.push whenever
 * the browser reports connectivity (online event, interval, tab focus), then
 * refreshes the catalog. Failed items stay queued with their error so the
 * cashier can see what needs attention.
 */
(function (global) {
	"use strict";

	var API_ROOT = "/api/method/retail_ops.api.";
	var POLL_MS = 20000;
	var LAST_SYNC_KEY = "retail_ops.last_sync";
	var listeners = new Set();
	var timer = null;
	var state = {
		online: typeof navigator === "undefined" ? true : navigator.onLine,
		syncing: false,
		pending: 0,
		lastSync: readLastSync(),
		lastError: null,
	};

	/* Kept in localStorage so a reload or a re-opened tab can still tell the
	 * cashier when the queue last reached the server. */
	function readLastSync() {
		try {
			var stored = global.localStorage.getItem(LAST_SYNC_KEY);
			var value = parseInt(stored, 10);
			return isNaN(value) ? null : value;
		} catch (error) {
			return null;
		}
	}

	function markSynced() {
		state.lastSync = Date.now();
		try {
			global.localStorage.setItem(LAST_SYNC_KEY, String(state.lastSync));
		} catch (error) {
			/* storage blocked: the header keeps the in-memory value */
		}
		emit();
	}

	function emit() {
		var snapshot = Object.assign({}, state);
		listeners.forEach(function (listener) {
			try {
				listener(snapshot);
			} catch (error) {
				console.error("RetailSync listener failed", error);
			}
		});
	}

	function onState(listener) {
		listeners.add(listener);
		listener(Object.assign({}, state));
		return function () {
			listeners.delete(listener);
		};
	}

	function csrfToken() {
		var config = global.RP_CONFIG || {};
		return config.csrfToken || "";
	}

	function serverMessage(body) {
		if (!body) {
			return "";
		}
		if (body._server_messages) {
			try {
				var messages = JSON.parse(body._server_messages);
				var last = messages[messages.length - 1];
				var parsed = typeof last === "string" ? JSON.parse(last) : last;
				return parsed.message || JSON.stringify(parsed);
			} catch (error) {
				/* fall through */
			}
		}
		return body.message || body.exception || body.exc_type || "";
	}

	function api(method, payload) {
		var url = API_ROOT + method;
		return fetch(url, {
			method: "POST",
			credentials: "same-origin",
			headers: {
				"Content-Type": "application/json",
				Accept: "application/json",
				"X-Frappe-CSRF-Token": csrfToken(),
			},
			body: JSON.stringify(payload || {}),
		}).then(function (response) {
			return response.text().then(function (text) {
				var body = null;
				try {
					body = text ? JSON.parse(text) : null;
				} catch (error) {
					body = null;
				}
				if (!response.ok) {
					var message = serverMessage(body) || "Request failed (" + response.status + ")";
					var failure = new Error(String(message).replace(/<[^>]*>/g, " "));
					failure.status = response.status;
					throw failure;
				}
				if (body && Object.prototype.hasOwnProperty.call(body, "message")) {
					return body.message;
				}
				return body;
			});
		});
	}

	function broadcast(name, detail) {
		try {
			global.dispatchEvent(new CustomEvent(name, { detail: detail }));
		} catch (error) {
			/* CustomEvent unsupported: ignore */
		}
	}

	function refreshPending() {
		return global.RetailDB.queueLength().then(function (count) {
			state.pending = count;
			emit();
			return count;
		});
	}

	function flush() {
		if (state.syncing) {
			return Promise.resolve(null);
		}
		if (!navigator.onLine) {
			state.online = false;
			emit();
			return refreshPending().then(function () {
				return { ok: false, offline: true };
			});
		}

		return global.RetailDB.getQueue()
			.then(function (queue) {
				if (!queue.length) {
					state.online = true;
					return { ok: true, empty: true, results: null };
				}

				state.syncing = true;
				state.online = true;
				state.lastError = null;
				emit();

				var sessions = [];
				var invoices = [];
				queue.forEach(function (item) {
					if (item.type === "session") {
						sessions.push(item.payload);
					} else if (item.type === "invoice") {
						invoices.push(item.payload);
					}
				});

				return api("sync.push", {
					sessions: sessions,
					invoices: invoices,
					client_time: new Date().toISOString(),
				}).then(function (results) {
					results = results || { sessions: [], invoices: [] };
					var updates = [];

					(results.sessions || []).forEach(function (row) {
						updates.push(settle(row.offline_uuid, row, "session"));
					});
					(results.invoices || []).forEach(function (row) {
						updates.push(settle(row.offline_uuid, row, "invoice"));
					});

					return Promise.all(updates).then(function () {
						markSynced();
						return refreshPending().then(function () {
							return pullCatalog()
								.catch(function (error) {
									console.warn("RetailSync: catalog refresh failed", error);
									return null;
								})
								.then(function (catalog) {
									broadcast("retail:synced", { results: results, catalog: catalog });
									return { ok: true, results: results };
								});
						});
					});
				});
			})
			.catch(function (error) {
				state.lastError = error.message || String(error);
				state.online = navigator.onLine;
				broadcast("retail:sync-failed", { error: state.lastError });
				return { ok: false, error: state.lastError };
			})
			.then(function (outcome) {
				state.syncing = false;
				emit();
				return outcome;
			});
	}

	function settle(id, row, type) {
		if (!id) {
			return Promise.resolve();
		}
		if (row.status === "created" || row.status === "duplicate") {
			return global.RetailDB.removeQueueItem(id).then(function () {
				broadcast("retail:item-synced", { id: id, type: type, result: row });
			});
		}
		return global.RetailDB.getQueueItem(id).then(function (item) {
			var attempts = ((item && item.attempts) || 0) + 1;
			return global.RetailDB.updateQueueItem(id, {
				status: "error",
				attempts: attempts,
				last_error: row.error || "Unknown error",
			});
		});
	}

	function pullCatalog(since) {
		return api("sync.pull", { since: since || null }).then(function (catalog) {
			return global.RetailDB.getCatalog().then(function (cached) {
				var merged = mergeCatalog(cached, catalog, Boolean(since));
				return global.RetailDB.saveCatalog(merged).then(function () {
					return merged;
				});
			});
		});
	}

	function mergeCatalog(cached, incoming, incremental) {
		if (!cached || !incremental) {
			return incoming;
		}
		var products = new Map();
		(cached.products || []).forEach(function (product) {
			products.set(product.name, product);
		});
		(incoming.products || []).forEach(function (product) {
			products.set(product.name, product);
		});
		return Object.assign({}, cached, incoming, {
			products: Array.from(products.values()),
		});
	}

	function refreshCatalog() {
		return api("bootstrap.get_bootstrap", {}).then(function (catalog) {
			return global.RetailDB.saveCatalog(catalog).then(function () {
				markSynced();
				broadcast("retail:catalog", { catalog: catalog });
				return catalog;
			});
		});
	}

	function handleOnline() {
		state.online = true;
		emit();
		flush();
	}

	function handleOffline() {
		state.online = false;
		emit();
	}

	function start() {
		if (timer) {
			return;
		}
		global.addEventListener("online", handleOnline);
		global.addEventListener("offline", handleOffline);
		document.addEventListener("visibilitychange", function () {
			if (!document.hidden && navigator.onLine) {
				flush();
			}
		});
		timer = global.setInterval(function () {
			if (navigator.onLine) {
				flush();
			}
		}, POLL_MS);
		refreshPending();
		if (navigator.onLine) {
			flush();
		}
	}

	global.RetailSync = {
		api: api,
		flush: flush,
		pullCatalog: pullCatalog,
		refreshCatalog: refreshCatalog,
		refreshPending: refreshPending,
		onState: onState,
		getState: function () {
			return Object.assign({}, state);
		},
		start: start,
	};
})(window);
