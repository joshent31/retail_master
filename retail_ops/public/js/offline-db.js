/* Retail Ops - offline storage (IndexedDB with in-memory fallback).
 *
 * Stores:
 *   catalog : cached bootstrap payload (products, stock, branches, sessions)
 *   queue   : pending offline operations, keyPath "id" (the offline_uuid)
 *   meta    : selected branch/warehouse, local session, cart draft
 */
(function (global) {
	"use strict";

	var DB_NAME = "retail_ops";
	var DB_VERSION = 1;
	var STORES = ["catalog", "queue", "meta"];
	var memory = { catalog: new Map(), queue: new Map(), meta: new Map() };
	var hasIDB = typeof global.indexedDB !== "undefined";
	var dbPromise = null;

	function open() {
		if (!hasIDB) {
			return Promise.resolve(null);
		}
		if (dbPromise) {
			return dbPromise;
		}
		dbPromise = new Promise(function (resolve, reject) {
			var request = global.indexedDB.open(DB_NAME, DB_VERSION);
			request.onupgradeneeded = function () {
				var db = request.result;
				if (!db.objectStoreNames.contains("queue")) {
					db.createObjectStore("queue", { keyPath: "id" });
				}
				if (!db.objectStoreNames.contains("catalog")) {
					db.createObjectStore("catalog");
				}
				if (!db.objectStoreNames.contains("meta")) {
					db.createObjectStore("meta");
				}
			};
			request.onsuccess = function () {
				resolve(request.result);
			};
			request.onerror = function () {
				reject(request.error);
			};
		}).catch(function (error) {
			console.warn("RetailDB: IndexedDB unavailable, falling back to memory", error);
			return null;
		});
		return dbPromise;
	}

	function requestResult(request) {
		return new Promise(function (resolve, reject) {
			request.onsuccess = function () {
				resolve(request.result);
			};
			request.onerror = function () {
				reject(request.error);
			};
		});
	}

	function run(storeName, mode, action) {
		return open().then(function (db) {
			if (!db) {
				return action(null);
			}
			var transaction = db.transaction(storeName, mode);
			var store = transaction.objectStore(storeName);
			var result = action(store);
			return Promise.resolve(result).then(function (value) {
				return new Promise(function (resolve, reject) {
					transaction.oncomplete = function () {
						resolve(value);
					};
					transaction.onerror = function () {
						reject(transaction.error || new Error("IndexedDB transaction failed"));
					};
					transaction.onabort = function () {
						reject(transaction.error || new Error("IndexedDB transaction aborted"));
					};
				});
			});
		});
	}

	function get(storeName, key) {
		return open().then(function (db) {
			if (!db) {
				return memory[storeName].get(key);
			}
			return requestResult(db.transaction(storeName, "readonly").objectStore(storeName).get(key));
		});
	}

	function put(storeName, value, key) {
		return open().then(function (db) {
			if (!db) {
				memory[storeName].set(key === undefined ? value.id : key, value);
				return value;
			}
			return run(storeName, "readwrite", function (store) {
				return key === undefined ? requestResult(store.put(value)) : requestResult(store.put(value, key));
			}).then(function () {
				return value;
			});
		});
	}

	function remove(storeName, key) {
		return open().then(function (db) {
			if (!db) {
				memory[storeName].delete(key);
				return true;
			}
			return run(storeName, "readwrite", function (store) {
				return requestResult(store.delete(key));
			}).then(function () {
				return true;
			});
		});
	}

	function getAll(storeName) {
		return open().then(function (db) {
			if (!db) {
				return Array.from(memory[storeName].values());
			}
			return requestResult(db.transaction(storeName, "readonly").objectStore(storeName).getAll());
		});
	}

	function uuid() {
		if (global.crypto && typeof global.crypto.randomUUID === "function") {
			return global.crypto.randomUUID().replace(/-/g, "");
		}
		var bytes = new Uint8Array(16);
		if (global.crypto && global.crypto.getRandomValues) {
			global.crypto.getRandomValues(bytes);
		} else {
			for (var i = 0; i < 16; i += 1) {
				bytes[i] = Math.floor(Math.random() * 256);
			}
		}
		return Array.prototype.map
			.call(bytes, function (byte) {
				return ("0" + byte.toString(16)).slice(-2);
			})
			.join("");
	}

	var RetailDB = {
		STORES: STORES,
		uuid: uuid,

		/* catalog cache */
		getCatalog: function () {
			return get("catalog", "bootstrap");
		},
		saveCatalog: function (payload) {
			return put("catalog", payload, "bootstrap");
		},

		/* small key/value settings */
		getMeta: function (key) {
			return get("meta", key);
		},
		setMeta: function (key, value) {
			return put("meta", value, key);
		},
		removeMeta: function (key) {
			return remove("meta", key);
		},

		/* offline queue */
		enqueue: function (item) {
			var entry = {
				id: item.id || uuid(),
				type: item.type,
				payload: item.payload,
				created_at: item.created_at || Date.now(),
				attempts: 0,
				status: "pending",
				last_error: null,
			};
			return put("queue", entry).then(function () {
				return entry;
			});
		},
		queueLength: function () {
			return getAll("queue").then(function (rows) {
				return rows.length;
			});
		},
		getQueueItem: function (id) {
			return get("queue", id);
		},
		getQueue: function () {
			return getAll("queue").then(function (rows) {
				return rows.sort(function (a, b) {
					return a.created_at - b.created_at;
				});
			});
		},
		updateQueueItem: function (id, patch) {
			return get("queue", id).then(function (row) {
				if (!row) {
					return null;
				}
				var updated = Object.assign({}, row, patch);
				return put("queue", updated);
			});
		},
		removeQueueItem: function (id) {
			return remove("queue", id);
		},
		clearQueue: function () {
			return getAll("queue").then(function (rows) {
				return Promise.all(
					rows.map(function (row) {
						return remove("queue", row.id);
					})
				);
			});
		},
	};

	global.RetailDB = RetailDB;
})(window);
