/* Retail Ops - POS terminal.
 *
 * Everything happens locally first: the catalog is cached in IndexedDB, bills
 * and day open/close operations go into an offline queue and are pushed by
 * RetailSync as soon as the network is available.
 */
(function (global) {
	"use strict";

	var state = {
		catalog: null,
		branches: [],
		products: [],
		productIndex: new Map(),
		stock: new Map(),
		offers: [],
		openSessions: [],
		can: {},
		branch: null,
		warehouse: null,
		session: null,
		cart: [],
		customer: { id: null, name: "", mobile: "", gstin: "", points: 0, due: 0 },
		loyaltyPoints: 0,
		query: "",
		category: "",
		tab: "products",
		tenderedTouched: false,
		syncing: false,
		online: navigator.onLine,
	};

	var dom = {};
	var moneyFormatter = null;

	/* ------------------------------------------------------------------ utils */
	function esc(value) {
		return String(value == null ? "" : value)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;");
	}

	function num(value) {
		var parsed = parseFloat(value);
		return isNaN(parsed) ? 0 : parsed;
	}

	function round(value) {
		return Math.round((num(value) + Number.EPSILON) * 100) / 100;
	}

	function currency() {
		return (state.branch && state.branch.currency) || "INR";
	}

	function money(value) {
		var amount = round(value);
		if (!moneyFormatter) {
			try {
				moneyFormatter = new Intl.NumberFormat(undefined, {
					style: "currency",
					currency: currency(),
					minimumFractionDigits: 2,
				});
			} catch (error) {
				moneyFormatter = null;
			}
		}
		return moneyFormatter ? moneyFormatter.format(amount) : amount.toFixed(2);
	}

	function resetMoneyFormatter() {
		moneyFormatter = null;
	}

	function pad(value) {
		return ("0" + value).slice(-2);
	}

	function today() {
		var now = new Date();
		return now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate());
	}

	function serverNow() {
		var now = new Date();
		return (
			now.getFullYear() +
			"-" +
			pad(now.getMonth() + 1) +
			"-" +
			pad(now.getDate()) +
			" " +
			pad(now.getHours()) +
			":" +
			pad(now.getMinutes()) +
			":" +
			pad(now.getSeconds())
		);
	}

	function timeOf(value) {
		if (!value) {
			return "";
		}
		var parts = String(value).split(" ");
		return parts.length > 1 ? parts[1].slice(0, 5) : String(value).slice(0, 5);
	}

	function toast(message, kind) {
		var node = document.createElement("div");
		node.className = "rp-toast" + (kind ? " is-" + kind : "");
		node.textContent = message;
		dom.toasts.appendChild(node);
		global.setTimeout(function () {
			node.remove();
		}, kind === "error" ? 6000 : 3200);
	}

	function sessionStoreKey() {
		if (!state.branch || !state.warehouse) {
			return null;
		}
		return "session::" + state.branch.name + "::" + state.warehouse.name;
	}

	function cartStoreKey() {
		return "cart";
	}

	/* -------------------------------------------------------------------- boot */
	function cacheDom() {
		[
			"rp-app",
			"rp-tabbar",
			"rp-tab-count",
			"rp-tab-total",
			"rp-install",
			"rp-branch",
			"rp-warehouse",
			"rp-day-pill",
			"rp-net-pill",
			"rp-open-day",
			"rp-close-day",
			"rp-sync",
			"rp-queue",
			"rp-sync-time",
			"rp-user",
			"rp-store-label",
			"rp-offline-warning",
			"rp-search",
			"rp-search-clear",
			"rp-scan",
			"rp-categories",
			"rp-products",
			"rp-products-empty",
			"rp-cart",
			"rp-clear",
			"rp-customer-name",
			"rp-customer-mobile",
			"rp-customer-gstin",
			"rp-customer-points",
			"rp-customer-due",
			"rp-loyalty-wrap",
			"rp-loyalty-points",
			"rp-total-offer",
			"rp-return",
			"rp-total-net",
			"rp-total-discount",
			"rp-total-tax",
			"rp-total-grand",
			"rp-cart-count",
			"rp-pay-mode",
			"rp-tendered",
			"rp-checkout",
			"rp-day-hint",
			"rp-modal",
			"rp-modal-title",
			"rp-modal-body",
			"rp-modal-cancel",
			"rp-modal-ok",
			"rp-scanner",
			"rp-scanner-video",
			"rp-scanner-hint",
			"rp-scanner-close",
			"rp-toasts",
		].forEach(function (id) {
			dom[id] = document.getElementById(id);
		});
	}

	function bindEvents() {
		dom["rp-branch"].addEventListener("change", function () {
			selectBranch(this.value);
		});
		dom["rp-warehouse"].addEventListener("change", function () {
			selectWarehouse(this.value);
		});
		dom["rp-open-day"].addEventListener("click", openDay);
		dom["rp-close-day"].addEventListener("click", closeDay);
		dom["rp-sync"].addEventListener("click", function () {
			RetailSync.flush().then(function (outcome) {
				if (outcome && outcome.ok) {
					toast(outcome.empty ? "Everything is in sync" : "Queue synced", "success");
				} else if (outcome && outcome.offline) {
					toast("Still offline — bills stay queued", "error");
				} else if (outcome && outcome.error) {
					toast(outcome.error, "error");
				}
			});
		});
		dom["rp-search"].addEventListener("input", function () {
			state.query = this.value.trim();
			renderProducts();
		});
		dom["rp-search"].addEventListener("keydown", function (event) {
			if (event.key === "Enter") {
				event.preventDefault();
				handleScan(this.value.trim());
			}
		});
		dom["rp-search-clear"].addEventListener("click", function () {
			dom["rp-search"].value = "";
			state.query = "";
			dom["rp-search"].focus();
			renderProducts();
		});
		dom["rp-products"].addEventListener("click", function (event) {
			var card = event.target.closest("[data-product]");
			if (card) {
				var product = state.productIndex.get(card.getAttribute("data-product"));
				if (product) {
					addToCart(product);
				}
			}
		});
		dom["rp-categories"].addEventListener("click", function (event) {
			var chip = event.target.closest("[data-category]");
			if (chip) {
				state.category = chip.getAttribute("data-category");
				renderCategories();
				renderProducts();
			}
		});
		dom["rp-cart"].addEventListener("click", function (event) {
			var button = event.target.closest("[data-action]");
			if (!button) {
				return;
			}
			var index = parseInt(button.getAttribute("data-index"), 10);
			var action = button.getAttribute("data-action");
			changeLine(index, action);
		});
		dom["rp-cart"].addEventListener("change", function (event) {
			var input = event.target.closest("[data-rate-index]");
			if (!input) {
				return;
			}
			var index = parseInt(input.getAttribute("data-rate-index"), 10);
			var line = state.cart[index];
			if (!line) {
				return;
			}
			line.rate = Math.max(0, num(input.value));
			applyLineOffers();
			saveCart();
			renderCart();
		});
		dom["rp-clear"].addEventListener("click", function () {
			if (!state.cart.length) {
				return;
			}
			state.cart = [];
			state.tenderedTouched = false;
			saveCart();
			renderCart();
		});
		dom["rp-customer-name"].addEventListener("input", function () {
			state.customer.name = this.value;
			saveCart();
		});
		dom["rp-customer-mobile"].addEventListener("input", function () {
			state.customer.mobile = this.value;
			saveCart();
		});
		dom["rp-customer-mobile"].addEventListener("change", function () {
			lookupCustomer(this.value);
		});
		dom["rp-loyalty-points"].addEventListener("input", function () {
			state.loyaltyPoints = Math.max(0, Math.floor(num(this.value)));
			state.tenderedTouched = false;
			renderCart();
		});
		dom["rp-customer-gstin"].addEventListener("input", function () {
			state.customer.gstin = this.value.trim().toUpperCase();
			saveCart();
		});
		dom["rp-customer-due"].addEventListener("click", openCollectDue);
		dom["rp-return"].addEventListener("click", openReturn);
		dom["rp-tendered"].addEventListener("input", function () {
			state.tenderedTouched = true;
		});
		dom["rp-day-pill"].addEventListener("click", function () {
			// on a phone this is the quickest way to reach Open / Close day
			setTab("menu");
		});
		dom["rp-install"].addEventListener("click", installApp);
		dom["rp-scan"].addEventListener("click", startScanner);
		dom["rp-scanner-close"].addEventListener("click", stopScanner);
		document.addEventListener("visibilitychange", function () {
			if (document.hidden) {
				stopScanner();
			}
		});
		dom["rp-tabbar"].addEventListener("click", function (event) {
			var button = event.target.closest("[data-tab]");
			if (button) {
				setTab(button.getAttribute("data-tab"));
			}
		});
		dom["rp-checkout"].addEventListener("click", checkout);
		dom["rp-modal-cancel"].addEventListener("click", closeModal);
		document.querySelector(".rp-modal-backdrop").addEventListener("click", closeModal);
		document.addEventListener("keydown", function (event) {
			if (event.key === "F2") {
				event.preventDefault();
				dom["rp-search"].focus();
				dom["rp-search"].select();
			}
			if (event.key === "Escape") {
				stopScanner();
				closeModal();
			}
		});

		global.addEventListener("retail:synced", function (event) {
			var detail = event.detail || {};
			if (detail.catalog) {
				applyBootstrap(detail.catalog);
			}
			RetailSync.refreshPending();
		});
		global.addEventListener("retail:item-synced", function (event) {
			var detail = event.detail || {};
			if (detail.type === "session" && detail.result && detail.result.session) {
				if (state.session && state.session.offline_uuid === detail.id) {
					state.session.name = detail.result.session;
					if (detail.result.session_data) {
						state.session = Object.assign({}, state.session, detail.result.session_data, {
							offline_uuid: detail.id,
						});
					}
					persistSession();
					renderHeader();
				}
			}
		});
	}

	function registerServiceWorker() {
		if (!("serviceWorker" in navigator)) {
			return;
		}
		navigator.serviceWorker
			.register("/assets/retail_ops/js/sw.js", { scope: "/" })
			.then(function () {
				/* offline shell ready */
			})
			.catch(function (error) {
				console.warn("Retail POS: service worker registration failed", error);
			});
	}

	function boot() {
		cacheDom();
		bindEvents();
		setTab(state.tab);
		setupInstall();
		// keep "3m ago" honest even when nothing else changes
		global.setInterval(function () {
			renderSync(RetailSync.getState ? RetailSync.getState() : {});
		}, 30000);
		registerServiceWorker();

		RetailSync.onState(function (syncState) {
			state.syncing = syncState.syncing;
			state.online = syncState.online;
			renderSync(syncState);
		});

		loadMeta()
			.then(function () {
				return RetailDB.getCatalog();
			})
			.then(function (cached) {
				if (cached) {
					applyBootstrap(cached);
				}
				RetailSync.start();
				if (navigator.onLine) {
					return RetailSync.refreshCatalog()
						.then(function (catalog) {
							applyBootstrap(catalog);
						})
						.catch(function (error) {
							console.warn("Retail POS: could not refresh catalog", error);
							toast("Working from the offline catalog", "error");
						});
				}
				return null;
			})
			.then(function () {
				return restoreLocal();
			})
			.then(function () {
				renderAll();
			});
	}

	/* ----------------------------------------------------------- catalog state */
	function applyBootstrap(catalog) {
		if (!catalog) {
			return;
		}
		var previousCatalog = state.catalog;
		state.catalog = catalog;
		state.branches = catalog.branches || [];
		if (catalog.can) {
			state.can = catalog.can;
		} else if (previousCatalog && previousCatalog.can) {
			// sync.pull does not repeat the capability block
			state.catalog.can = previousCatalog.can;
			state.catalog.user = catalog.user || previousCatalog.user;
			state.catalog.full_name = catalog.full_name || previousCatalog.full_name;
		}
		state.products = (catalog.products || []).filter(function (product) {
			return product.is_active === undefined || product.is_active;
		});
		state.productIndex = new Map();
		state.products.forEach(function (product) {
			state.productIndex.set(product.name, product);
		});
		state.stock = new Map();
		(catalog.stock || []).forEach(function (row) {
			state.stock.set(row.product + "::" + row.warehouse, num(row.qty));
		});
		if (catalog.offers) {
			state.offers = catalog.offers;
		}
		state.openSessions = catalog.open_sessions || [];

		var branchNames = state.branches.map(function (branch) {
			return branch.name;
		});
		if (!state.branch || branchNames.indexOf(state.branch.name) === -1) {
			var preferred = readMetaCached("branch");
			var match = state.branches.filter(function (branch) {
				return branch.name === preferred;
			})[0];
			state.branch = match || state.branches[0] || null;
		} else {
			state.branch = state.branches.filter(function (branch) {
				return branch.name === state.branch.name;
			})[0];
		}
		resetMoneyFormatter();

		if (state.branch) {
			var warehouses = state.branch.warehouses || [];
			var current = state.warehouse && state.warehouse.name;
			var preferredWarehouse = readMetaCached("warehouse");
			var chosen =
				warehouses.filter(function (warehouse) {
					return warehouse.name === current;
				})[0] ||
				warehouses.filter(function (warehouse) {
					return warehouse.name === preferredWarehouse;
				})[0] ||
				warehouses.filter(function (warehouse) {
					return warehouse.is_default;
				})[0] ||
				warehouses[0] ||
				null;
			state.warehouse = chosen;
		} else {
			state.warehouse = null;
		}

		syncSessionFromServer();
		applyLineOffers();
		renderAll();
	}

	var metaCache = { branch: null, warehouse: null, cart: null };

	function readMetaCached(key) {
		return metaCache[key] || null;
	}

	function loadMeta() {
		return Promise.all([RetailDB.getMeta("branch"), RetailDB.getMeta("warehouse"), RetailDB.getMeta("cart")]).then(
			function (values) {
				metaCache.branch = values[0] || null;
				metaCache.warehouse = values[1] || null;
				metaCache.cart = values[2] || null;
			}
		);
	}

	function restoreLocal() {
		return loadMeta()
			.then(function () {
				if (!state.branch || !state.warehouse) {
					return null;
				}
				var cart = metaCache.cart;
				if (cart && cart.branch === state.branch.name && cart.warehouse === state.warehouse.name) {
					state.cart = Array.isArray(cart.lines) ? cart.lines : [];
					state.customer = Object.assign(
						{ id: null, name: "", mobile: "", gstin: "", points: 0, due: 0 },
						cart.customer || {}
					);
					// points and dues are the server's: never trust a balance cached
					// on the device
					state.customer.id = null;
					state.customer.points = 0;
					state.customer.due = 0;
				}
				var key = sessionStoreKey();
				if (!key) {
					return null;
				}
				return RetailDB.getMeta(key).then(function (stored) {
					if (stored && stored.status === "Open") {
						var server = findOpenSession(state.branch.name, state.warehouse.name);
						if (server && server.offline_uuid === stored.offline_uuid) {
							state.session = server;
						} else if (!server) {
							state.session = stored;
						}
					}
				});
			})
			.then(function () {
				// a server session always wins if nothing local is open
				if (!state.session) {
					syncSessionFromServer();
				}
				dom["rp-customer-name"].value = state.customer.name || "";
				dom["rp-customer-mobile"].value = state.customer.mobile || "";
				dom["rp-customer-gstin"].value = state.customer.gstin || "";
				applyLineOffers();
			});
	}

	function findOpenSession(branch, warehouse) {
		return (
			state.openSessions.filter(function (session) {
				return session.branch === branch && session.warehouse === warehouse && session.status === "Open";
			})[0] || null
		);
	}

	function syncSessionFromServer() {
		if (!state.branch || !state.warehouse) {
			state.session = null;
			return;
		}
		var server = findOpenSession(state.branch.name, state.warehouse.name);
		if (state.session && state.session.offline_uuid && server && server.offline_uuid === state.session.offline_uuid) {
			state.session = Object.assign({}, state.session, server, { offline_uuid: state.session.offline_uuid });
			return;
		}
		state.session = server || state.session || null;
	}

	function persistSession() {
		var key = sessionStoreKey();
		if (!key) {
			return Promise.resolve();
		}
		if (!state.session) {
			return RetailDB.removeMeta(key);
		}
		return RetailDB.setMeta(key, state.session);
	}

	function clearStoredSession() {
		var key = sessionStoreKey();
		if (key) {
			RetailDB.removeMeta(key);
		}
	}

	function selectBranch(name) {
		state.branch =
			state.branches.filter(function (branch) {
				return branch.name === name;
			})[0] || null;
		state.warehouse = state.branch
			? (state.branch.warehouses || []).filter(function (warehouse) {
					return warehouse.is_default;
			  })[0] ||
			  (state.branch.warehouses || [])[0] ||
			  null
			: null;
		state.session = null;
		state.cart = [];
		resetMoneyFormatter();
		RetailDB.setMeta("branch", name);
		if (state.warehouse) {
			RetailDB.setMeta("warehouse", state.warehouse.name);
		}
		syncSessionFromServer();
		restoreLocal().then(renderAll);
	}

	function selectWarehouse(name) {
		if (!state.branch) {
			return;
		}
		state.warehouse =
			(state.branch.warehouses || []).filter(function (warehouse) {
				return warehouse.name === name;
			})[0] || null;
		state.session = null;
		RetailDB.setMeta("warehouse", name);
		syncSessionFromServer();
		restoreLocal().then(renderAll);
	}

	function saveCart() {
		RetailDB.setMeta(cartStoreKey(), {
			branch: state.branch ? state.branch.name : null,
			warehouse: state.warehouse ? state.warehouse.name : null,
			lines: state.cart,
			// the due is re-read from the server, never restored from here
			customer: Object.assign({}, state.customer, { due: 0 }),
		});
	}

	function stockFor(product, warehouse) {
		if (!warehouse) {
			return 0;
		}
		var value = state.stock.get(product + "::" + warehouse);
		return value === undefined ? 0 : value;
	}

	/* -------------------------------------------------------------------- tabs */
	/* The tab bar only exists on phones; on wider screens the CSS ignores the
	 * classes and shows the catalog and bill side by side. */
	function setTab(tab) {
		if (["products", "cart", "menu"].indexOf(tab) === -1) {
			tab = "products";
		}
		state.tab = tab;
		["products", "cart", "menu"].forEach(function (name) {
			dom["rp-app"].classList.toggle("is-tab-" + name, name === tab);
		});
		Array.prototype.forEach.call(dom["rp-tabbar"].querySelectorAll("[data-tab]"), function (button) {
			var active = button.getAttribute("data-tab") === tab;
			button.classList.toggle("is-active", active);
			button.setAttribute("aria-selected", active ? "true" : "false");
		});
	}

	/* ------------------------------------------------------------------ render */
	function renderAll() {
		renderSync(RetailSync.getState ? RetailSync.getState() : {});
		renderHeader();
		renderCategories();
		renderProducts();
		renderCart();
	}

	function renderHeader() {
		dom["rp-branch"].innerHTML = state.branches
			.map(function (branch) {
				var selected = state.branch && state.branch.name === branch.name ? " selected" : "";
				return '<option value="' + esc(branch.name) + '"' + selected + ">" + esc(branch.branch_name || branch.name) + "</option>";
			})
			.join("");
		dom["rp-branch"].disabled = !state.branches.length;

		var warehouses = (state.branch && state.branch.warehouses) || [];
		dom["rp-warehouse"].innerHTML = warehouses
			.map(function (warehouse) {
				var selected = state.warehouse && state.warehouse.name === warehouse.name ? " selected" : "";
				return (
					'<option value="' +
					esc(warehouse.name) +
					'"' +
					selected +
					">" +
					esc(warehouse.warehouse_name || warehouse.name) +
					"</option>"
				);
			})
			.join("");
		dom["rp-warehouse"].disabled = !warehouses.length;

		dom["rp-store-label"].textContent = state.branch
			? (state.branch.branch_name || state.branch.name) +
			  (state.warehouse ? " · " + (state.warehouse.warehouse_name || state.warehouse.name) : "")
			: "No branch assigned";

		var pill = dom["rp-day-pill"];
		if (state.session) {
			pill.textContent = "Day open" + (state.session.opened_at ? " · " + timeOf(state.session.opened_at) : "");
			pill.className = "rp-pill is-open";
		} else {
			pill.textContent = "Day closed";
			pill.className = "rp-pill";
		}

		dom["rp-open-day"].hidden = !(state.can.open_day && !state.session && state.branch);
		dom["rp-close-day"].hidden = !(state.can.close_day && state.session);

		var hint = dom["rp-day-hint"];
		if (!state.branch) {
			hint.textContent = "Ask an administrator to grant you access to a branch.";
			hint.className = "rp-hint is-warn";
		} else if (!state.session) {
			hint.textContent = "Open the day to start billing.";
			hint.className = "rp-hint is-warn";
		} else {
			var label = state.session.name || "offline session";
			hint.textContent =
				"Bills post to " +
				label +
				" · opening float " +
				money(state.session.opening_float || 0) +
				(state.session.name ? "" : " (will sync)");
			hint.className = "rp-hint";
		}
	}

	function timeAgo(timestamp) {
		var seconds = Math.round((Date.now() - timestamp) / 1000);
		if (seconds < 45) {
			return "just now";
		}
		var minutes = Math.round(seconds / 60);
		if (minutes < 60) {
			return minutes + "m ago";
		}
		var hours = Math.round(minutes / 60);
		if (hours < 24) {
			return hours + "h ago";
		}
		return Math.round(hours / 24) + "d ago";
	}

	function renderSync(syncState) {
		syncState = syncState || {};
		var pill = dom["rp-net-pill"];
		var pending = syncState.pending || 0;

		if (syncState.syncing) {
			pill.textContent = "Syncing…";
			pill.className = "rp-pill";
		} else if (!navigator.onLine || syncState.online === false) {
			pill.textContent = "Offline";
			pill.className = "rp-pill is-offline";
		} else if (syncState.lastError) {
			pill.textContent = "Sync issue";
			pill.className = "rp-pill is-error";
		} else {
			pill.textContent = "Online";
			pill.className = "rp-pill is-open";
		}

		dom["rp-queue"].hidden = !pending;
		dom["rp-queue"].textContent = pending + " queued";

		// how much is waiting, and when it last reached the server
		var lastSync = syncState.lastSync || 0;
		var when = lastSync ? timeAgo(lastSync) : "not yet";
		if (syncState.syncing) {
			dom["rp-sync-time"].textContent = "Syncing…";
		} else if (pending) {
			dom["rp-sync-time"].textContent = "Last sync " + when;
		} else {
			dom["rp-sync-time"].textContent = "Synced " + when;
		}
		dom["rp-sync-time"].title = lastSync
			? "Last successful sync " + new Date(lastSync).toLocaleString()
			: "No successful sync on this device yet";
		dom["rp-sync-time"].className =
			"rp-sync-time" + (pending && !syncState.syncing ? " is-pending" : "");
		dom["rp-sync"].disabled = !!syncState.syncing;
		dom["rp-offline-warning"].hidden = !!navigator.onLine;

		if (state.catalog && state.catalog.user) {
			dom["rp-user"].textContent = state.catalog.full_name || state.catalog.user;
		}
	}

	function renderCategories() {
		var categories = new Map();
		state.products.forEach(function (product) {
			var category = product.category || "Uncategorised";
			categories.set(category, (categories.get(category) || 0) + 1);
		});
		var html = ['<button class="rp-chip" data-category="">All (' + state.products.length + ")</button>"];
		Array.from(categories.keys())
			.sort()
			.forEach(function (category) {
				html.push(
					'<button class="rp-chip' +
						(state.category === category ? " is-active" : "") +
						'" data-category="' +
						esc(category) +
						'">' +
						esc(category) +
						" (" +
						categories.get(category) +
						")</button>"
				);
			});
		if (state.category === "") {
			html[0] = html[0].replace('class="rp-chip"', 'class="rp-chip is-active"');
		}
		dom["rp-categories"].innerHTML = html.join("");
	}

	function filteredProducts() {
		var query = state.query.toLowerCase();
		return state.products.filter(function (product) {
			if (state.category && (product.category || "Uncategorised") !== state.category) {
				return false;
			}
			if (!query) {
				return true;
			}
			return [product.product_name, product.item_code, product.barcode, product.category]
				.filter(Boolean)
				.join(" ")
				.toLowerCase()
				.indexOf(query) !== -1;
		});
	}

	/* ------------------------------------------------------------------ offers */
	/* Same rules as retail_ops/offers.py: the best matching offer wins, capped
	 * at the line value. The server recalculates on sync, so a stale cached
	 * offer can never change what a bill finally costs. */
	var GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$/;

	function validGstin(value) {
		return GSTIN_RE.test(String(value || "").trim().toUpperCase());
	}

	function todayIso() {
		var nowDate = new Date();
		var month = String(nowDate.getMonth() + 1);
		var day = String(nowDate.getDate());
		return (
			nowDate.getFullYear() +
			"-" +
			(month.length < 2 ? "0" + month : month) +
			"-" +
			(day.length < 2 ? "0" + day : day)
		);
	}

	function usableOffers() {
		var today = todayIso();
		var branch = state.branch ? state.branch.name : null;
		return (state.offers || []).filter(function (offer) {
			if (offer.enabled === false) {
				return false;
			}
			if (offer.branch && offer.branch !== branch) {
				return false;
			}
			if (offer.valid_from && String(offer.valid_from) > today) {
				return false;
			}
			if (offer.valid_to && String(offer.valid_to) < today) {
				return false;
			}
			return true;
		});
	}

	function offerMatches(offer, product, qty) {
		if (num(offer.min_qty) && num(qty) < num(offer.min_qty)) {
			return false;
		}
		if (offer.apply_on === "Product") {
			return offer.product === product.name;
		}
		if (offer.apply_on === "Category") {
			var offered = String(offer.category || "")
				.trim()
				.toLowerCase();
			var actual = String(product.category || "")
				.trim()
				.toLowerCase();
			return Boolean(offered) && offered === actual;
		}
		return false;
	}

	function bestOffer(product, qty, lineValue) {
		var value = lineValue === undefined ? round(num(product.rate) * num(qty)) : round(lineValue);
		var best = null;
		var bestAmount = 0;
		usableOffers().forEach(function (offer) {
			if (!offerMatches(offer, product, qty)) {
				return;
			}
			var amount =
				offer.discount_type === "Percent"
					? (value * num(offer.value)) / 100
					: num(offer.value) * Math.abs(num(qty));
			amount = Math.min(round(amount), Math.max(value, 0));
			if (amount > bestAmount + 0.0005) {
				bestAmount = amount;
				best = offer;
			}
		});
		return best ? { offer: best, discount: bestAmount, label: best.label || "Offer" } : null;
	}

	function applyLineOffers() {
		state.cart.forEach(function (line) {
			var product = state.productIndex.get(line.product) || { name: line.product, category: "" };
			var lineValue = round(num(line.qty) * num(line.rate));
			var offer = bestOffer(product, line.qty, lineValue);
			line.discount = offer ? offer.discount : 0;
			line.offer_label = offer ? offer.label : "";
		});
	}

	function renderProducts() {
		var products = filteredProducts();
		dom["rp-products-empty"].hidden = products.length > 0;
		var visible = products.slice(0, 240);
		dom["rp-products"].innerHTML = visible
			.map(function (product) {
				var qty = stockFor(product.name, state.warehouse && state.warehouse.name);
				var stockClass = qty <= 0 ? "is-out" : qty < 5 ? "is-low" : "";
				var offer = bestOffer(product, 1);
				var mrp = num(product.mrp);
				return (
					'<button class="rp-product" data-product="' +
					esc(product.name) +
					'">' +
					'<span class="rp-product-name">' +
					esc(product.product_name || product.name) +
					"</span>" +
					'<span class="rp-muted">' +
					esc(product.item_code || "") +
					"</span>" +
					(offer ? '<span class="rp-offer">' + esc(offer.label) + "</span>" : "") +
					(mrp > num(product.rate) ? '<span class="rp-mrp">' + esc(money(mrp)) + "</span>" : "") +
					'<span class="rp-product-meta">' +
					'<span class="rp-product-rate">' +
					esc(money(product.rate)) +
					"</span>" +
					'<span class="rp-stock ' +
					stockClass +
					'">' +
					esc(qty) +
					"</span>" +
					"</span>" +
					"</button>"
				);
			})
			.join("");
	}

	function lineAmount(line) {
		return Math.max(0, round(line.qty * line.rate - (line.discount || 0)));
	}

	function lineTax(line) {
		return round((lineAmount(line) * num(line.tax_rate)) / 100);
	}

	function bestBillOffer(netTotal) {
		var total = Math.max(0, round(num(netTotal)));
		var best = null;
		usableOffers().forEach(function (offer) {
			if (offer.apply_on !== "Bill") {
				return;
			}
			if (total < num(offer.min_amount)) {
				return;
			}
			var amount =
				offer.discount_type === "Percent"
					? (total * num(offer.value)) / 100
					: num(offer.value);
			amount = Math.min(round(amount), total);
			if (!best || amount > best.discount + 0.0005) {
				best = { discount: amount, label: offer.title || offer.label || "Bill offer" };
			}
		});
		return best && best.discount > 0 ? best : null;
	}

	function loyaltySettings() {
		var branch = state.branch || {};
		return {
			enabled: Boolean(num(branch.loyalty_enabled)),
			value: num(branch.loyalty_point_value) || 1,
			maxPercent: num(branch.loyalty_max_percent),
		};
	}

	function totals() {
		var gross = 0;
		var discount = 0;
		state.cart.forEach(function (line) {
			gross += lineAmount(line);
			discount += num(line.discount);
		});
		gross = round(gross);

		// A bill offer is a trade discount, so it reduces the taxable value too:
		// the lines are scaled down together and the tax follows them.
		var offer = bestBillOffer(gross);
		var billDiscount = offer ? Math.min(round(offer.discount), gross) : 0;
		var factor = gross > 0 ? (gross - billDiscount) / gross : 1;
		var net = round(gross - billDiscount);
		var tax = 0;
		state.cart.forEach(function (line) {
			tax += round((lineAmount(line) * factor * num(line.tax_rate)) / 100);
		});
		tax = round(tax);
		var grand = round(net + tax);

		// points settle part of the bill, they never change what the goods cost
		var settings = loyaltySettings();
		var loyaltyPoints = 0;
		if (settings.enabled && state.customer.id && state.loyaltyPoints > 0 && grand > 0) {
			var cap = Math.floor((grand * settings.maxPercent) / 100 / settings.value);
			loyaltyPoints = Math.max(
				0,
				Math.min(Math.floor(state.loyaltyPoints), num(state.customer.points), cap)
			);
		}
		var loyaltyAmount = round(loyaltyPoints * settings.value);

		return {
			gross: gross,
			net: net,
			discount: round(discount + billDiscount),
			billDiscount: billDiscount,
			billOffer: offer ? offer.label : "",
			factor: factor,
			tax: tax,
			grand: grand,
			loyaltyPoints: loyaltyPoints,
			loyaltyAmount: loyaltyAmount,
			due: round(grand - loyaltyAmount),
			count: state.cart.reduce(function (sum, line) {
				return sum + num(line.qty);
			}, 0),
		};
	}

	function renderCart() {
		var summary = totals();
		if (!state.cart.length) {
			dom["rp-cart"].innerHTML = '<div class="rp-empty">Scan a barcode or tap a product to start a bill.</div>';
		} else {
			dom["rp-cart"].innerHTML = state.cart
				.map(function (line, index) {
					var rateCell = state.branch && state.branch.allow_price_override
						? '<input class="rp-line-rate" type="number" min="0" step="0.01" data-rate-index="' +
						  index +
						  '" value="' +
						  esc(line.rate) +
						  '">'
						: '<span class="rp-muted">' + esc(money(line.rate)) + " × " + esc(line.qty) + "</span>";
					return (
						'<div class="rp-line">' +
						'<div><div class="rp-line-title">' +
						esc(line.item_name) +
						'</div><div class="rp-line-sub">' +
						esc(money(line.rate)) +
						" · tax " +
						esc(num(line.tax_rate)) +
						"%" +
						(num(line.discount)
							? ' · <span class="rp-line-offer">' +
							  esc(line.offer_label || "Offer") +
							  " −" +
							  esc(money(line.discount)) +
							  "</span>"
							: "") +
						"</div></div>" +
						'<div class="rp-line-amount">' +
						esc(money((lineAmount(line) + lineTax(line)) * (summary.factor || 1))) +
						"</div>" +
						'<div class="rp-line-actions">' +
						'<button class="rp-qty-btn" data-action="dec" data-index="' +
						index +
						'">−</button>' +
						'<span class="rp-qty">' +
						esc(line.qty) +
						"</span>" +
						'<button class="rp-qty-btn" data-action="inc" data-index="' +
						index +
						'">+</button>' +
						'<button class="rp-link" data-action="remove" data-index="' +
						index +
						'">Remove</button>' +
						"</div>" +
						'<div class="rp-line-sub">' +
						rateCell +
						"</div>" +
						"</div>"
					);
				})
				.join("");
		}

		var summary = totals();
		dom["rp-total-net"].textContent = money(summary.net);
		dom["rp-total-discount"].textContent = money(summary.discount);
		dom["rp-total-tax"].textContent = money(summary.tax);
		dom["rp-total-grand"].textContent = money(summary.grand);
		dom["rp-cart-count"].textContent = summary.count;

		if (dom["rp-total-offer"]) {
			dom["rp-total-offer"].hidden = !summary.billDiscount;
			if (summary.billDiscount) {
				dom["rp-total-offer"].firstChild.textContent = summary.billOffer || "Bill offer";
				dom["rp-total-offer"].lastChild.textContent = "−" + money(summary.billDiscount);
			}
		}			renderLoyalty(summary);
			renderDue();

		if (!state.tenderedTouched) {
			dom["rp-tendered"].value = summary.due ? summary.due.toFixed(2) : "";
		}
		dom["rp-checkout"].disabled = !state.cart.length || (state.can.create_invoice === false);

		dom["rp-tab-count"].hidden = !summary.count;
		dom["rp-tab-count"].textContent = summary.count;
		dom["rp-tab-total"].textContent = summary.grand ? money(summary.grand) : "";
	}

	function renderLoyalty(summary) {
		var points = dom["rp-customer-points"];
		var wrap = dom["rp-loyalty-wrap"];
		if (!points || !wrap) {
			return;
		}
		var settings = loyaltySettings();
		var hasCustomer = Boolean(state.customer.id);
		points.hidden = !(settings.enabled && (hasCustomer || num(state.customer.points)));
		if (!points.hidden) {
			points.textContent =
				(state.customer.name || "Customer") + " · " + num(state.customer.points) + " loyalty points";
		}

		var usable = settings.enabled && hasCustomer && num(state.customer.points) > 0 && summary.grand > 0;
		wrap.hidden = !usable;
		if (!usable) {
			state.loyaltyPoints = 0;
			dom["rp-loyalty-points"].value = "0";
			return;
		}

		var maxPoints = Math.min(
			Math.floor(num(state.customer.points)),
			Math.floor((summary.grand * settings.maxPercent) / 100 / settings.value)
		);
		dom["rp-loyalty-points"].max = maxPoints;
		if (num(dom["rp-loyalty-points"].value) !== summary.loyaltyPoints) {
			dom["rp-loyalty-points"].value = summary.loyaltyPoints;
			state.loyaltyPoints = summary.loyaltyPoints;
		}
		var hint = document.getElementById("rp-loyalty-hint");
		if (hint) {
			hint.textContent = "(up to " + maxPoints + ", 1 = " + money(settings.value) + ")";
		}
	}

	/* --------------------------------------------------------------------- dues */
	function renderDue() {
		var button = dom["rp-customer-due"];
		if (!button) {
			return;
		}
		var due = state.customer.id ? num(state.customer.due) : 0;
		var show = due > 0.005 && state.online;
		button.hidden = !show;
		if (show) {
			button.textContent = "Due " + money(due) + " \u00b7 Collect";
		}
	}

	function openCollectDue() {
		if (!state.branch) {
			toast("Select a branch first", "error");
			return;
		}
		if (!navigator.onLine) {
			toast("Collecting a due needs a connection", "error");
			return;
		}
		var customer = state.customer.id;
		var due = num(state.customer.due);
		if (!customer || due <= 0.005) {
			return;
		}

		RetailSync.api("customers.get_dues", { customer: customer, branch: state.branch.name })
			.then(function (outcome) {
				var total = num(outcome && outcome.total);
				if (total <= 0.005) {
					state.customer.due = 0;
					renderCart();
					toast("Nothing left to collect");
					return;
				}
				state.customer.due = total;
				renderCart();
				formModal({
					title: "Collect due \u00b7 " + money(total),
					html:
						modalFields([
							{ name: "amount", label: "Amount received", type: "number", value: total.toFixed(2) },
							{ name: "mode", label: "Received by (Cash / Card / UPI / Bank)", value: "Cash" },
							{ name: "reference_no", label: "Reference no (optional)", value: "" },
						]),
					okText: "Collect",
					fields: [{ name: "amount" }, { name: "mode" }, { name: "reference_no" }],
				}).then(function (values) {
					if (!values) {
						return;
					}
					collectDue(customer, num(values.amount), values.mode, values.reference_no);
				});
			})
			.catch(function (error) {
				toast((error && error.message) || "Could not read the dues", "error");
			});
	}

	function collectDue(customer, amount, mode, referenceNo) {
		if (!(amount > 0)) {
			toast("Enter the amount received", "error");
			return;
		}
		RetailSync.api("customers.collect_due", {
			customer: customer,
			amount: amount,
			mode: mode || "Cash",
			reference_no: referenceNo || null,
			branch: state.branch.name,
			warehouse: state.warehouse ? state.warehouse.name : null,
		})
			.then(function (result) {
				state.customer.due = num(result && result.due);
				renderCart();
				toast(
					"Collected " + money(result.amount) + " against " + (result.bills || []).length + " bill(s)",
					"success"
				);
			})
			.catch(function (error) {
				toast((error && error.message) || "Could not collect that due", "error");
			});
	}

	/* ----------------------------------------------------------------- customer */
	function lookupCustomer(mobile) {
		var value = String(mobile || "").trim();
		if (value.length < 6 || !navigator.onLine) {
			return;
		}
		RetailSync.api("customers.find_customer", { query: value })
			.then(function (outcome) {
				var customer = outcome && outcome.customer;
				if (!customer || String(customer.mobile || "") !== value) {
					// not a known number: the bill stays walk-in unless a name is typed
					state.customer.id = null;
					state.customer.points = 0;
					state.customer.due = 0;
					renderCart();
					return;
				}
				state.customer.id = customer.name;
				state.customer.points = num(customer.loyalty_points);
				state.customer.due = num(customer.due);
				if (!state.customer.name) {
					state.customer.name = customer.customer_name;
					dom["rp-customer-name"].value = customer.customer_name;
				}
				if (!state.customer.gstin && customer.gstin) {
					state.customer.gstin = customer.gstin;
					dom["rp-customer-gstin"].value = customer.gstin;
				}
				toast(customer.customer_name + " · " + num(customer.loyalty_points) + " points");
				saveCart();
				renderCart();
			})
			.catch(function () {});
	}

	/* -------------------------------------------------------------- cart logic */
	function handleScan(value) {
		if (!value) {
			return;
		}
		var lowered = value.toLowerCase();
		var exact = state.products.filter(function (product) {
			return (
				(product.barcode && String(product.barcode).toLowerCase() === lowered) ||
				String(product.item_code || "").toLowerCase() === lowered
			);
		})[0];
		if (exact) {
			addToCart(exact);
			dom["rp-search"].value = "";
			state.query = "";
			renderProducts();
			toast(exact.product_name + " added");
			return;
		}
		var matches = filteredProducts();
		if (matches.length === 1) {
			addToCart(matches[0]);
			dom["rp-search"].value = "";
			state.query = "";
			renderProducts();
			return;
		}
		toast("No exact barcode match", "error");
	}

	function addToCart(product, qty) {
		if (!state.session) {
			toast("Open the day before billing", "error");
			return;
		}
		var quantity = num(qty) || 1;
		var line = state.cart.filter(function (item) {
			return item.product === product.name;
		})[0];
		if (line) {
			line.qty = round(line.qty + quantity);
		} else {
			state.cart.push({
				product: product.name,
				item_name: product.product_name || product.name,
				item_code: product.item_code,
				uom: product.uom,
				rate: num(product.rate),
				tax_rate: num(product.tax_rate),
				qty: quantity,
				discount: 0,
			});
		}
		state.tenderedTouched = false;
		applyLineOffers();
		saveCart();
		renderCart();
	}

	function changeLine(index, action) {
		var line = state.cart[index];
		if (!line) {
			return;
		}
		if (action === "inc") {
			line.qty = round(line.qty + 1);
		} else if (action === "dec") {
			line.qty = round(line.qty - 1);
			if (line.qty <= 0) {
				state.cart.splice(index, 1);
			}
		} else if (action === "remove") {
			state.cart.splice(index, 1);
		}
		state.tenderedTouched = false;
		applyLineOffers();
		saveCart();
		renderCart();
	}

	/* ------------------------------------------------------------------ returns */
	function openReturn() {
		if (state.can.create_return === false) {
			toast("You are not allowed to take returns", "error");
			return;
		}
		if (!navigator.onLine) {
			toast("Returns need a connection to look up the bill", "error");
			return;
		}
		openModal({
			title: "Return a bill",
			html:
				'<label class="rp-field"><span>Bill number or customer mobile</span>' +
				'<input id="rp-return-query" type="search" autocomplete="off" placeholder="RI-2026-00042 or 9876543210"></label>' +
				'<p class="rp-muted">A mobile number lists that customer\u2019s recent bills, so the paper slip is not needed.</p>',
			okText: "Find bill",
			cancelText: "Cancel",
		});
		dom["rp-modal-ok"].onclick = function () {
			var input = document.getElementById("rp-return-query");
			var query = input ? input.value.trim() : "";
			if (!query) {
				toast("Type a bill number or mobile", "error");
				return;
			}
			dom["rp-modal-ok"].disabled = true;
			dom["rp-modal-ok"].textContent = "Looking…";
			RetailSync.api("returns.find_bills", { query: query })
				.then(function (outcome) {
					dom["rp-modal-ok"].disabled = false;
					dom["rp-modal-ok"].textContent = "Find bill";
					var bills = (outcome && outcome.bills) || [];
					if (!bills.length) {
						toast("No bill matches " + query, "error");
						return;
					}
					if (bills.length === 1) {
						loadReturnable(bills[0].name);
						return;
					}
					renderBillPicker(bills, query);
				})
				.catch(function (error) {
					dom["rp-modal-ok"].disabled = false;
					dom["rp-modal-ok"].textContent = "Find bill";
					toast((error && error.message) || "Could not look up that bill", "error");
				});
		};
		global.setTimeout(function () {
			var input = document.getElementById("rp-return-query");
			if (input) {
				input.focus();
			}
		}, 40);
	}

	function renderBillPicker(bills, query) {
		dom["rp-modal-title"].textContent = "Pick the bill";
		dom["rp-modal-body"].innerHTML =
			'<p class="rp-muted">' +
			esc(bills.length + " bills match \u201c" + query + "\u201d") +
			"</p>" +
			'<div class="rp-return-list">' +
			bills
				.map(function (bill, index) {
					return (
						'<button class="rp-pick' +
						(bill.can_return ? "" : " is-done") +
						'" type="button" data-bill-index="' +
						index +
						'">' +
						'<span class="rp-return-name">' +
						esc(bill.name) +
						"</span>" +
						'<span class="rp-return-meta">' +
						esc(String(bill.posting_datetime || "").slice(0, 16)) +
						" · " +
						esc(money(bill.grand_total)) +
						(bill.customer_mobile ? " · " + esc(bill.customer_mobile) : "") +
						(bill.can_return ? "" : " · fully returned") +
						"</span></button>"
					);
				})
				.join("") +
			"</div>";

		dom["rp-modal-ok"].hidden = true;
		dom["rp-modal-ok"].disabled = false;
		dom["rp-modal-ok"].textContent = "Find bill";
		Array.prototype.forEach.call(document.querySelectorAll("[data-bill-index]"), function (button) {
			button.addEventListener("click", function () {
				var bill = bills[parseInt(button.getAttribute("data-bill-index"), 10)];
				if (!bill) {
					return;
				}
				dom["rp-modal-ok"].hidden = false;
				loadReturnable(bill.name);
			});
		});
	}

	function loadReturnable(invoice) {
		dom["rp-modal-ok"].hidden = false;
		dom["rp-modal-ok"].disabled = true;
		dom["rp-modal-ok"].textContent = "Loading…";
		RetailSync.api("returns.get_returnable", { invoice: invoice })
			.then(function (detail) {
				dom["rp-modal-ok"].disabled = false;
				renderReturnForm(detail);
			})
			.catch(function (error) {
				dom["rp-modal-ok"].disabled = false;
				dom["rp-modal-ok"].textContent = "Find bill";
				toast((error && error.message) || "Could not open that bill", "error");
			});
	}

	function renderReturnForm(detail) {
		var rows = (detail.items || [])
			.map(function (item, index) {
				var available = num(item.returnable_qty);
				return (
					'<div class="rp-return-row' +
					(available ? "" : " is-done") +
					'">' +
					'<div class="rp-return-name">' +
					esc(item.item_name) +
					"</div>" +
					'<div class="rp-return-meta">' +
					esc(money(item.rate)) +
					" · sold " +
					esc(item.qty) +
					(available ? " · can return " + esc(available) : " · fully returned") +
					"</div>" +
					(available
						? '<input class="rp-line-rate" type="number" min="0" max="' +
						  available +
						  '" step="1" value="0" data-return-index="' +
						  index +
						  '" aria-label="Quantity to return">'
						: "") +
					"</div>"
				);
			})
			.join("");

		dom["rp-modal-title"].textContent = "Return " + detail.invoice;
		dom["rp-modal-body"].innerHTML =
			'<p class="rp-muted">' +
			esc(detail.customer_name || "Walk-in customer") +
			" · bill " +
			esc(money(detail.grand_total)) +
			"</p>" +
			'<div class="rp-return-list">' +
			rows +
			"</div>" +
			'<label class="rp-field"><span>Refund by</span><select id="rp-return-mode">' +
			'<option value="Cash">Cash refund</option>' +
			'<option value="Card">Card refund</option>' +
			'<option value="UPI">UPI refund</option>' +
			'<option value="Credit">Store credit (no money out)</option>' +
			"</select></label>" +
			'<label class="rp-field"><span>Reason (optional)</span><input id="rp-return-reason" type="text" maxlength="140"></label>';

		dom["rp-modal-ok"].disabled = false;
		dom["rp-modal-ok"].textContent = "Refund";
		dom["rp-modal-ok"].onclick = function () {
			var lines = [];
			Array.prototype.forEach.call(document.querySelectorAll("[data-return-index]"), function (input) {
				var item = (detail.items || [])[parseInt(input.getAttribute("data-return-index"), 10)];
				var qty = Math.min(num(input.value), item ? num(item.returnable_qty) : 0);
				if (!item || qty <= 0) {
					return;
				}
				lines.push({ product: item.product, qty: qty, rate: num(item.rate), item: item });
			});
			if (!lines.length) {
				toast("Enter how many of each item come back", "error");
				return;
			}
			var modeInput = document.getElementById("rp-return-mode");
			var reasonInput = document.getElementById("rp-return-reason");
			submitReturn(detail, lines, (modeInput && modeInput.value) || "Cash", (reasonInput && reasonInput.value) || "");
		};
	}

	function submitReturn(detail, lines, mode, reason) {
		dom["rp-modal-ok"].disabled = true;
		dom["rp-modal-ok"].textContent = "Refunding…";
		RetailSync.api("returns.create_return", {
			invoice: detail.invoice,
			items: JSON.stringify(
				lines.map(function (line) {
					return { product: line.product, qty: line.qty, rate: line.rate };
				})
			),
			refund_mode: mode,
			reason: reason,
		})
			.then(function (result) {
				dom["rp-modal-ok"].disabled = false;
				dom["rp-modal-ok"].textContent = "Refund";
				closeModal();
				if (!result || result.status === "failed") {
					toast((result && result.error) || "Could not record the return", "error");
					return;
				}
				toast("Credit note " + (result.invoice || "") + " recorded", "success");
				showReturnReceipt(detail, lines, mode, result);
			})
			.catch(function (error) {
				dom["rp-modal-ok"].disabled = false;
				dom["rp-modal-ok"].textContent = "Refund";
				toast((error && error.message) || "Could not record the return", "error");
			});
	}

	function showReturnReceipt(detail, lines, mode, result) {
		var net = 0;
		var tax = 0;
		var receiptLines;
		if (result && result.items && result.items.length) {
			// the server is the authority on what was refunded
			receiptLines = result.items.map(function (item) {
				return {
					item_name: item.item_name,
					qty: num(item.qty),
					rate: num(item.rate),
					total: num(item.total_amount),
				};
			});
			net = -num(result.net_total);
			tax = -num(result.tax_total);
		} else {
			receiptLines = lines.map(function (line) {
				var taxRate = num(line.item && line.item.tax_rate);
				var value = round(num(line.rate) * num(line.qty));
				net += value;
				tax += round((value * taxRate) / 100);
				return {
					item_name: line.item.item_name,
					qty: -Math.abs(num(line.qty)),
					rate: num(line.rate),
					total: -round(value + (value * taxRate) / 100),
				};
			});
		}
		var grand = result && result.grand_total !== undefined ? round(result.grand_total) : -round(net + tax);
		var receipt = {
			lines: receiptLines,
			totals: { net: -round(net), discount: 0, tax: -round(tax), grand: grand },
			mode: mode === "Credit" ? "Store credit" : mode + " refund",
			tendered: grand,
			change: 0,
			customer: detail.customer_name || "",
			gstin: detail.customer_gstin || "",
			posting_datetime: new Date().toISOString().slice(0, 16).replace("T", " "),
			cashier: (state.catalog && state.catalog.full_name) || "",
			is_return: true,
		};
		showReceipt(receipt, { status: (result && result.status) || "created", invoice: result && result.invoice });
	}

	/* --------------------------------------------------------------- install app */
	var installPrompt = null;

	function setupInstall() {
		var standalone =
			(window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
			navigator.standalone === true;
		if (standalone) {
			dom["rp-install"].hidden = true;
			return;
		}

		global.addEventListener("beforeinstallprompt", function (event) {
			event.preventDefault();
			installPrompt = event;
			dom["rp-install"].hidden = false;
		});
		global.addEventListener("appinstalled", function () {
			installPrompt = null;
			dom["rp-install"].hidden = true;
			toast("Retail POS added to your home screen", "success");
		});

		if (/iPad|iPhone|iPod/.test(navigator.userAgent)) {
			// Safari never fires beforeinstallprompt, so offer the manual steps
			dom["rp-install"].hidden = false;
		}
	}

	function installApp() {
		if (!installPrompt) {
			var ios = /iPad|iPhone|iPod/.test(navigator.userAgent);
			openModal({
				title: "Add Retail POS to your home screen",
				html:
					"<p>" +
					(ios
						? "Tap <strong>Share</strong> in Safari, then <strong>Add to Home Screen</strong>."
						: "Open the browser menu and choose <strong>Install app</strong> or <strong>Add to Home screen</strong>.") +
					"</p><p>It then opens full screen, like an app, and still works offline.</p>",
				okText: "Got it",
				hideCancel: true,
			});
			return;
		}
		var prompt = installPrompt;
		installPrompt = null;
		prompt.prompt();
		prompt.userChoice
			.then(function (choice) {
				if (choice && choice.outcome === "accepted") {
					dom["rp-install"].hidden = true;
					toast("Installing Retail POS…", "success");
				}
			})
			.catch(function () {});
	}

	/* ----------------------------------------------------------- camera scanning */
	var scanStream = null;
	var scanTimer = null;
	var scanDetector = null;
	var scanBusy = false;

	function startScanner() {
		if (scanStream) {
			return;
		}
		if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
			toast("The camera needs a secure (https) connection", "error");
			return;
		}
		if (!("BarcodeDetector" in global)) {
			toast("This browser cannot scan barcodes — type the code in the search box", "error");
			return;
		}

		dom["rp-scanner"].hidden = false;
		dom["rp-scanner-hint"].textContent = "Starting the camera…";
		navigator.mediaDevices
			.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false })
			.then(function (stream) {
				scanStream = stream;
				var video = dom["rp-scanner-video"];
				video.srcObject = stream;
				video.play().catch(function () {});
				dom["rp-scanner-hint"].textContent = "Point the camera at the barcode";
				return scanDetectorFor();
			})
			.then(function (detector) {
				if (!detector || !scanStream) {
					return;
				}
				scanDetector = detector;
				scanTimer = global.setInterval(scanFrame, 250);
			})
			.catch(function (error) {
				stopScanner();
				toast(cameraError(error), "error");
			});
	}

	function scanDetectorFor() {
		var wanted = [
			"ean_13",
			"ean_8",
			"upc_a",
			"upc_e",
			"code_128",
			"code_39",
			"itf",
			"codabar",
			"qr_code",
		];
		if (!global.BarcodeDetector.getSupportedFormats) {
			return Promise.resolve(new global.BarcodeDetector());
		}
		return global.BarcodeDetector.getSupportedFormats().then(function (formats) {
			var usable = wanted.filter(function (format) {
				return formats.indexOf(format) !== -1;
			});
			return usable.length ? new global.BarcodeDetector({ formats: usable }) : null;
		});
	}

	function scanFrame() {
		var video = dom["rp-scanner-video"];
		if (scanBusy || !scanDetector || !scanStream || video.readyState < 2) {
			return;
		}
		scanBusy = true;
		scanDetector
			.detect(video)
			.then(function (codes) {
				scanBusy = false;
				if (!codes || !codes.length || !codes[0].rawValue) {
					return;
				}
				var value = codes[0].rawValue;
				stopScanner();
				handleScan(value);
			})
			.catch(function () {
				scanBusy = false;
			});
	}

	function stopScanner() {
		if (scanTimer) {
			global.clearInterval(scanTimer);
			scanTimer = null;
		}
		scanDetector = null;
		scanBusy = false;
		if (scanStream) {
			scanStream.getTracks().forEach(function (track) {
				track.stop();
			});
			scanStream = null;
		}
		if (dom["rp-scanner-video"]) {
			dom["rp-scanner-video"].srcObject = null;
			dom["rp-scanner"].hidden = true;
		}
	}

	function cameraError(error) {
		var name = (error && error.name) || "";
		if (name === "NotAllowedError" || name === "SecurityError") {
			return "Camera permission was denied";
		}
		if (name === "NotFoundError" || name === "OverconstrainedError") {
			return "No camera found on this device";
		}
		if (name === "NotReadableError") {
			return "The camera is in use by another app";
		}
		return "Could not start the camera";
	}

	/* ------------------------------------------------------------------ modals */
	var modalResolve = null;

	function openModal(options) {
		dom["rp-modal-title"].textContent = options.title || "";
		dom["rp-modal-body"].innerHTML = options.html || "";
		dom["rp-modal-ok"].textContent = options.okText || "Confirm";
		dom["rp-modal-ok"].hidden = options.hideOk === true;
		dom["rp-modal-cancel"].textContent = options.cancelText || "Cancel";
		dom["rp-modal-cancel"].hidden = options.hideCancel === true;
		dom["rp-modal"].hidden = false;

		dom["rp-modal-ok"].onclick = function () {
			if (options.onConfirm) {
				options.onConfirm();
			}
			closeModal();
		};
		dom["rp-modal-cancel"].onclick = function () {
			closeModal();
		};
	}

	function closeModal() {
		dom["rp-modal"].hidden = true;
		if (!state.cart.length && state.tab === "cart") {
			// back to the catalog after a bill, the way a phone user expects
			setTab("products");
		}
		if (modalResolve) {
			var resolve = modalResolve;
			modalResolve = null;
			resolve(null);
		}
	}

	function formModal(options) {
		openModal(
			Object.assign({}, options, {
				onConfirm: function () {
					var result = {};
					(options.fields || []).forEach(function (field) {
						var input = document.getElementById("rp-modal-field-" + field.name);
						result[field.name] = input ? input.value : null;
					});
					if (modalResolve) {
						var resolve = modalResolve;
						modalResolve = null;
						resolve(result);
					}
				},
			})
		);
		return new Promise(function (resolve) {
			// cancelled/closed modals resolve to null
			modalResolve = resolve;
		});
	}

	function modalFields(fields) {
		return fields
			.map(function (field) {
				var input;
				if (field.type === "textarea") {
					input = '<textarea id="rp-modal-field-' + field.name + '" rows="2">' + esc(field.value || "") + "</textarea>";
				} else {
					input =
						'<input id="rp-modal-field-' +
						field.name +
						'" type="' +
						(field.type || "text") +
						'" value="' +
						esc(field.value == null ? "" : field.value) +
						'" step="0.01" min="0">';
				}
				return (
					'<label class="rp-field" style="margin-bottom:10px">' +
					"<span>" +
					esc(field.label) +
					"</span>" +
					input +
					"</label>"
				);
			})
			.join("");
	}

	/* ------------------------------------------------------------------- day */
	function openDay() {
		if (!state.branch || !state.warehouse) {
			toast("Select a branch and warehouse first", "error");
			return;
		}
		formModal({
			title: "Open day",
			html: modalFields([{ name: "opening_float", label: "Opening cash float", type: "number", value: "0" }]),
			okText: "Open day",
			fields: [{ name: "opening_float" }],
		}).then(function (values) {
			if (!values) {
				return;
			}
			startDay(num(values.opening_float));
		});
	}

	function startDay(openingFloat) {
		var offlineUuid = RetailDB.uuid();
		var localSession = {
			offline_uuid: offlineUuid,
			name: null,
			branch: state.branch.name,
			warehouse: state.warehouse.name,
			session_date: today(),
			status: "Open",
			opening_float: round(openingFloat),
			opened_at: serverNow(),
			invoice_count: 0,
			sales_total: 0,
			cash_sales: 0,
			card_sales: 0,
			upi_sales: 0,
			credit_sales: 0,
			tax_total: 0,
			discount_total: 0,
		};
		state.session = localSession;
		persistSession();
		renderHeader();
		setTab("products");
		dom["rp-day-hint"].textContent = "Opening day…";

		RetailDB.enqueue({
			id: offlineUuid,
			type: "session",
			payload: {
				type: "open",
				offline_uuid: offlineUuid,
				branch: localSession.branch,
				warehouse: localSession.warehouse,
				opening_float: localSession.opening_float,
				session_date: localSession.session_date,
				opened_at: localSession.opened_at,
			},
		}).then(function () {
			toast("Day opened", "success");
			return RetailSync.flush();
		});
	}

	function closeDay() {
		if (!state.session) {
			return;
		}
		var session = state.session;
		var summary = totals();
		var html =
			'<div class="rp-summary">' +
			'<span>Session</span><span class="rp-strong">' +
			esc(session.name || "offline session") +
			"</span>" +
			'<span>Opening float</span><span>' +
			esc(money(session.opening_float || 0)) +
			"</span>" +
			'<span>Bills synced</span><span>' +
			esc(session.invoice_count || 0) +
			"</span>" +
			'<span>Sales (synced)</span><span>' +
			esc(money(session.sales_total || 0)) +
			"</span>" +
			'<span>Cash sales</span><span>' +
			esc(money(session.cash_sales || 0)) +
			"</span>" +
			'<span>Expected cash</span><span class="rp-strong">' +
			esc(money(session.expected_cash || session.opening_float || 0)) +
			"</span>" +
			"</div>" +
			'<p class="rp-muted">Bills still queued on this device are synced before the close is pushed.</p>' +
			modalFields([
				{ name: "closing_cash", label: "Cash counted in drawer", type: "number", value: "" },
				{ name: "notes", label: "Notes (optional)", type: "textarea", value: "" },
			]);

		formModal({
			title: "Close day",
			html: html,
			okText: "Close day",
			fields: [{ name: "closing_cash" }, { name: "notes" }],
		}).then(function (values) {
			if (!values) {
				return;
			}
			finalizeDay(session, num(values.closing_cash), values.notes, summary);
		});
	}

	function finalizeDay(session, closingCash, notes, summary) {
		var closeUuid = RetailDB.uuid();
		RetailDB.enqueue({
			id: closeUuid,
			type: "session",
			payload: {
				type: "close",
				offline_uuid: closeUuid,
				session: session.name || null,
				session_offline_uuid: session.offline_uuid,
				closing_cash: round(closingCash),
				notes: notes || "",
				closed_at: serverNow(),
			},
		})
			.then(function () {
				state.session = null;
				clearStoredSession();
				renderHeader();
				renderCart();
				toast("Day closed" + (navigator.onLine ? "" : " (will sync)"), "success");
				return RetailSync.flush();
			})
			.then(function (outcome) {
				var snapshot = null;
				if (outcome && outcome.results) {
					(outcome.results.sessions || []).forEach(function (row) {
						if (row.offline_uuid === closeUuid && row.session_data) {
							snapshot = row.session_data;
						}
					});
				}
				showCloseSummary(snapshot, summary, closingCash);
			});
	}

	function showCloseSummary(snapshot, localSummary, closingCash) {
		var sales = snapshot ? snapshot.sales_total : localSummary.grand;
		var expected = snapshot ? snapshot.expected_cash : null;
		var variance = snapshot ? snapshot.variance : expected == null ? null : round(closingCash - expected);
		var html =
			'<div class="rp-summary">' +
			'<span>Sales</span><span class="rp-strong">' +
			esc(money(sales)) +
			"</span>" +
			'<span>Cash sales</span><span>' +
			esc(money(snapshot ? snapshot.cash_sales : 0)) +
			"</span>" +
			'<span>Card / UPI</span><span>' +
			esc(money(snapshot ? (snapshot.card_sales || 0) + (snapshot.upi_sales || 0) : 0)) +
			"</span>" +
			'<span>Credit</span><span>' +
			esc(money(snapshot ? snapshot.credit_sales : 0)) +
			"</span>" +
			'<span>Closing cash</span><span>' +
			esc(money(closingCash)) +
			"</span>" +
			'<span>Variance</span><span class="rp-strong">' +
			esc(variance == null ? "—" : money(variance)) +
			"</span>" +
			"</div>" +
			'<p class="rp-muted">' +
			(snapshot
				? "Saved on the server as " + esc(snapshot.name) + "."
				: "The close is queued and will be pushed with the next sync.") +
			"</p>";
		openModal({ title: "Day closed", html: html, hideOk: true, cancelText: "Done" });
	}

	/* --------------------------------------------------------------- checkout */
	function checkout() {
		if (!state.session) {
			toast("Open the day before billing", "error");
			return;
		}
		if (!state.cart.length) {
			return;
		}
		if (state.can.create_invoice === false) {
			toast("You are not allowed to create bills", "error");
			return;
		}

		if (state.customer.gstin && !validGstin(state.customer.gstin)) {
			toast("That GSTIN is not valid — 15 characters, e.g. 29ABCDE1234F1Z5", "error");
			return;
		}

		var summary = totals();
		if (summary.grand <= 0) {
			toast("Bill total must be greater than zero", "error");
			return;
		}

		var mode = dom["rp-pay-mode"].value;
		var tendered = num(dom["rp-tendered"].value);
		var loyaltyAmount = round(summary.loyaltyAmount || 0);
		var cashDue = round(summary.grand - loyaltyAmount);
		var payments = [];
		if (loyaltyAmount > 0) {
			payments.push({ mode: "Loyalty", amount: loyaltyAmount });
		}
		if (cashDue > 0.005) {
			if (mode === "Credit") {
				payments.push({ mode: "Credit", amount: cashDue });
			} else {
				payments.push({ mode: mode, amount: tendered > 0 ? round(tendered) : cashDue });
			}
		}

		var offlineUuid = RetailDB.uuid();
		var payload = {
			offline_uuid: offlineUuid,
			branch: state.branch.name,
			warehouse: state.warehouse.name,
			day_session: state.session.name || state.session.offline_uuid,
			posting_datetime: serverNow(),
			customer: state.customer.id || null,
			customer_name: state.customer.name || null,
			customer_mobile: state.customer.mobile || null,
			customer_gstin: (state.customer.gstin || "").trim().toUpperCase() || null,
			items: state.cart.map(function (line) {
				return {
					product: line.product,
					qty: line.qty,
					rate: line.rate,
					discount_amount: line.discount || 0,
				};
			}),
			payments: payments,
		};
		var receipt = {
			lines: state.cart.map(function (line) {
				// net of the line discount and of its share of any bill offer, so the
				// printed lines add up to the subtotal below them
				var total = round(lineAmount(line) * (summary.factor || 1));
				var qty = num(line.qty) || 1;
				return {
					item_name: line.item_name,
					qty: line.qty,
					rate: round(total / qty),
					total: total,
				};
			}),
			totals: summary,
			mode: loyaltyAmount > 0 ? mode + " + points" : mode,
			tendered: tendered,
			change: mode === "Credit" ? 0 : round(Math.max(0, tendered - cashDue)),
			customer: state.customer.name,
			gstin: state.customer.gstin,
			loyaltyPoints: summary.loyaltyPoints || 0,
			loyaltyAmount: loyaltyAmount,
			posting_datetime: payload.posting_datetime,
			cashier: (state.catalog && state.catalog.full_name) || "",
		};

		deductLocalStock(state.cart);

		var newCustomer = !state.customer.id && state.customer.name && state.customer.mobile && navigator.onLine;
		state.cart = [];
		state.customer = { id: null, name: "", mobile: "", gstin: "", points: 0 };
		state.loyaltyPoints = 0;
		state.tenderedTouched = false;
		dom["rp-customer-name"].value = "";
		dom["rp-customer-mobile"].value = "";
		dom["rp-customer-gstin"].value = "";
		saveCart();
		renderCart();

		dom["rp-checkout"].disabled = true;

		var prepare = Promise.resolve();
		if (newCustomer) {
			// enrol the walk-in first, so the bill is linked to the new record
			prepare = RetailSync.api("customers.save_customer", {
				customer_name: payload.customer_name,
				mobile: payload.customer_mobile,
				gstin: payload.customer_gstin,
			})
				.then(function (outcome) {
					if (outcome && outcome.customer) {
						payload.customer = outcome.customer.name;
					}
				})
				.catch(function () {});
		}

		prepare
			.then(function () {
				return RetailDB.enqueue({ id: offlineUuid, type: "invoice", payload: payload });
			})
			.then(function () {
				toast(navigator.onLine ? "Bill saved, syncing…" : "Bill stored offline", "success");
				return RetailSync.flush();
			})
			.then(function (outcome) {
				var result = null;
				if (outcome && outcome.results) {
					(outcome.results.invoices || []).forEach(function (row) {
						if (row.offline_uuid === offlineUuid) {
							result = row;
						}
					});
				}
				showReceipt(receipt, result);
				renderHeader();
			})
			.catch(function (error) {
				showReceipt(receipt, { status: "error", error: error.message });
			})
			.then(function () {
				renderCart();
			});
	}

	function deductLocalStock(lines) {
		lines.forEach(function (line) {
			var key = line.product + "::" + state.warehouse.name;
			var current = state.stock.get(key);
			if (current !== undefined) {
				state.stock.set(key, round(current - line.qty));
			}
		});
		renderProducts();
	}

	function showReceipt(receipt, result) {
		var status = result ? result.status : "queued";
		var badge = 'class="rp-badge is-pending">Pending sync';
		if (status === "created") {
			badge = 'class="rp-badge is-synced">Synced' + (result.invoice ? " · " + esc(result.invoice) : "") + "";
		} else if (status === "duplicate") {
			badge = 'class="rp-badge is-synced">Already synced' + (result.invoice ? " · " + esc(result.invoice) : "");
		} else if (status === "error") {
			badge = 'class="rp-badge is-error">Sync failed';
		}

		var canWhatsapp = !!(
			result &&
			result.invoice &&
			state.catalog &&
			state.catalog.whatsapp &&
			state.catalog.whatsapp.enabled
		);
		var body =
			'<p><span ' + badge + "</span></p>" +
			'<div class="rp-receipt">' +
			esc(renderReceiptText(receipt)) +
			"</div>" +
			(status === "error" ? '<p class="rp-muted">' + esc(result.error || "") + "</p>" : "") +
			'<div class="rp-modal-actions" style="justify-content:flex-start;flex-wrap:wrap;margin-top:10px">' +
			'<button class="rp-btn" id="rp-receipt-share">Share image</button>' +
			'<button class="rp-btn" id="rp-receipt-print">Print</button>' +
			(canWhatsapp ? '<button class="rp-btn" id="rp-receipt-whatsapp">WhatsApp bill</button>' : "") +
			"</div>";

		openModal({
			title: receipt.is_return
				? "Credit note saved"
				: status === "created" || status === "duplicate"
				? "Bill complete"
				: "Bill saved",
			html: body,
			hideOk: true,
			cancelText: "New bill",
		});

		// Rendered up front so the share sheet opens straight from the tap (some
		// browsers refuse navigator.share once the gesture has been awaited).
		var receiptFile = null;
		var imageReady = buildReceiptImage(receipt, (result && result.invoice) || "").then(
			function (file) {
				receiptFile = file;
				return file;
			},
			function (error) {
				console.warn("Retail POS: could not render the receipt image", error);
				return null;
			}
		);

		var shareButton = document.getElementById("rp-receipt-share");
		if (shareButton) {
			shareButton.addEventListener("click", function () {
				var label = shareButton.textContent;
				shareButton.disabled = true;
				shareButton.textContent = "Preparing…";
				var note = result && result.invoice ? "Bill " + result.invoice : "Bill";
				(receiptFile ? Promise.resolve(receiptFile) : imageReady)
					.then(function (file) {
						if (!file) {
							toast("Could not prepare the receipt image", "error");
							return null;
						}
						return shareReceiptImage(file, note).then(function (outcome) {
							if (outcome === "shared") {
								toast("Receipt image shared", "success");
							} else {
								toast("Receipt image saved — attach it in WhatsApp");
							}
						});
					})
					.catch(function (error) {
						// dismissing the share sheet is not an error
						if (error && error.name === "AbortError") {
							return;
						}
						toast("Could not share the receipt image", "error");
					})
					.then(function () {
						shareButton.disabled = false;
						shareButton.textContent = label;
					});
			});
		}

		var printButton = document.getElementById("rp-receipt-print");
		if (printButton) {
			printButton.addEventListener("click", function () {
				printReceipt(renderReceiptText(receipt));
			});
		}

		var whatsappButton = document.getElementById("rp-receipt-whatsapp");
		if (whatsappButton) {
			whatsappButton.addEventListener("click", function () {
				sendBillOnWhatsApp(result.invoice, whatsappButton);
			});
		}
	}

	function sendBillOnWhatsApp(invoice, button) {
		if (!navigator.onLine) {
			toast("Connect to the internet to send on WhatsApp", "error");
			return;
		}

		var label = button.textContent;
		button.disabled = true;
		button.textContent = "Sending…";

		RetailSync.api("whatsapp.send_invoice", { invoice: invoice })
			.then(function (outcome) {
				if (outcome && outcome.status === "failed") {
					toast(outcome.error || "Could not send on WhatsApp", "error");
				} else if (outcome && outcome.status === "already_sent") {
					toast("Bill was already sent on WhatsApp", "success");
				} else {
					toast(
						"Bill sent on WhatsApp" + (outcome && outcome.phone ? " to " + outcome.phone : ""),
						"success"
					);
				}
			})
			.catch(function (error) {
				toast((error && error.message) || "Could not send on WhatsApp", "error");
			})
			.then(function () {
				button.disabled = false;
				button.textContent = label;
			});
	}

	/* ------------------------------------------------------------ receipt image */
	/* Draws the bill on a canvas so it can leave the phone as a picture. Canvas
	 * text is drawn by the browser, so no extra library ships with the POS and
	 * the image can be produced offline, straight after Charge. */
	function buildReceiptImage(receipt, invoiceNo) {
		return new Promise(function (resolve, reject) {
			try {
				var canvas = document.createElement("canvas");
				var ctx = canvas.getContext("2d");
				if (!ctx) {
					throw new Error("Canvas is not supported");
				}

				var WIDTH = 720;
				var PAD = 40;
				var INNER = WIDTH - PAD * 2;
				var fonts = {
					title: "bold 34px 'Segoe UI', Arial, sans-serif",
					big: "bold 24px 'Segoe UI', Arial, sans-serif",
					body: "20px ui-monospace, Menlo, Consolas, monospace",
					bodyBold: "bold 20px ui-monospace, Menlo, Consolas, monospace",
					small: "18px ui-monospace, Menlo, Consolas, monospace",
				};
				var rows = [];

				function widthOf(value, cssFont) {
					ctx.font = cssFont;
					return ctx.measureText(value).width;
				}

				function wrap(value, cssFont, max) {
					var words = String(value).split(/\s+/);
					var lines = [];
					var current = "";
					words.forEach(function (word) {
						var next = current ? current + " " + word : word;
						if (current && widthOf(next, cssFont) > max) {
							lines.push(current);
							current = word;
						} else {
							current = next;
						}
					});
					if (current) {
						lines.push(current);
					}
					return lines;
				}

				function line(value, options) {
					options = options || {};
					rows.push({
						text: String(value),
						font: options.font || fonts.body,
						align: options.align || "left",
						height: options.height || 28,
						color: options.color || "#1f2430",
					});
				}

				function pair(left, right, options) {
					options = options || {};
					rows.push({
						left: String(left),
						right: String(right),
						font: options.font || fonts.small,
						height: options.height || 26,
						color: options.color || "#1f2430",
					});
				}

				function rule() {
					rows.push({ rule: true, height: 24 });
				}

				function gap(size) {
					rows.push({ height: size || 12 });
				}

				function amount(value) {
					return round(value).toFixed(2);
				}

				var branch = state.branch || {};
				var warehouse = state.warehouse || {};

				line(branch.branch_name || branch.name || "Retail store", {
					font: fonts.title,
					align: "center",
					height: 46,
				});
				if (warehouse.warehouse_name || warehouse.name) {
					line(warehouse.warehouse_name || warehouse.name, {
						align: "center",
						height: 26,
						color: "#6b7280",
					});
				}
				if (receipt.is_return) {
					line("CREDIT NOTE", {
						font: fonts.big,
						align: "center",
						height: 30,
						color: "#b42318",
					});
				} else if (receipt.gstin) {
					line("TAX INVOICE", { font: fonts.big, align: "center", height: 30 });
				}
				if (branch.gstin) {
					line("GSTIN: " + branch.gstin, { align: "center", height: 22, color: "#6b7280" });
				}
				if (receipt.posting_datetime) {
					line(receipt.posting_datetime, { align: "center", height: 24, color: "#6b7280" });
				}
				if (invoiceNo) {
					line("Bill " + invoiceNo, { font: fonts.big, align: "center", height: 32 });
				}
				rule();

				receipt.lines.forEach(function (item) {
					wrap(item.item_name || "", fonts.body, INNER).forEach(function (part, index) {
						line(part, { font: index ? fonts.body : fonts.bodyBold, height: 26 });
					});
					pair(item.qty + " × " + amount(item.rate), amount(item.total));
					gap(8);
				});
				rule();

				pair("Subtotal", amount(receipt.totals.net));
				if (round(receipt.totals.discount)) {
					pair("Discount", "-" + amount(receipt.totals.discount));
				}
				pair("Tax", amount(receipt.totals.tax));
				pair("Total", amount(receipt.totals.grand), {
					font: "bold 24px ui-monospace, Menlo, Consolas, monospace",
					height: 38,
				});
				pair(receipt.mode || "Paid", amount(receipt.tendered || receipt.totals.grand));
				if (receipt.change) {
					pair("Change", amount(receipt.change));
				}
				if (receipt.customer) {
					gap(10);
					pair("Customer", receipt.customer, { font: fonts.body, height: 30 });
				}
				if (receipt.gstin) {
					pair("GSTIN", receipt.gstin);
				}
				if (receipt.cashier) {
					pair("Cashier", receipt.cashier);
				}
				if (branch.receipt_footer) {
					gap(16);
					wrap(branch.receipt_footer, fonts.small, INNER).forEach(function (part) {
						line(part, { align: "center", height: 24, color: "#6b7280" });
					});
				}
				gap(18);
				line("Thank you!", { font: fonts.big, align: "center", height: 32 });
				gap(6);

				var height =
					rows.reduce(function (total, row) {
						return total + row.height;
					}, PAD * 2) + 1;
				canvas.width = WIDTH;
				canvas.height = height;
				ctx.fillStyle = "#ffffff";
				ctx.fillRect(0, 0, WIDTH, height);
				ctx.textBaseline = "alphabetic";

				var y = PAD;
				rows.forEach(function (row) {
					if (row.rule) {
						ctx.strokeStyle = "#d4d8de";
						ctx.lineWidth = 2;
						ctx.setLineDash([8, 8]);
						ctx.beginPath();
						ctx.moveTo(PAD, y + row.height / 2);
						ctx.lineTo(WIDTH - PAD, y + row.height / 2);
						ctx.stroke();
						ctx.setLineDash([]);
					} else if (row.left !== undefined) {
						ctx.font = row.font;
						ctx.fillStyle = row.color;
						ctx.textAlign = "left";
						ctx.fillText(row.left, PAD, y + row.height - 6);
						ctx.textAlign = "right";
						ctx.fillText(row.right, WIDTH - PAD, y + row.height - 6);
					} else if (row.text !== undefined) {
						ctx.font = row.font;
						ctx.fillStyle = row.color;
						ctx.textAlign = row.align || "left";
						ctx.fillText(row.text, row.align === "center" ? WIDTH / 2 : PAD, y + row.height - 6);
					}
					// rows with only a height are spacers between sections
					y += row.height;
				});
				ctx.textAlign = "left";

				var filename = "receipt-" + String(invoiceNo || "bill").replace(/[^A-Za-z0-9._-]/g, "-") + ".png";
				canvas.toBlob(function (blob) {
					if (!blob) {
						reject(new Error("Could not render the receipt image"));
						return;
					}
					resolve(new File([blob], filename, { type: "image/png" }));
				}, "image/png");
			} catch (error) {
				reject(error);
			}
		});
	}

	function shareReceiptImage(file, note) {
		if (navigator.canShare && navigator.share && navigator.canShare({ files: [file] })) {
			return navigator.share({ files: [file], title: "Receipt", text: note || "" }).then(function () {
				return "shared";
			});
		}
		downloadFile(file);
		return Promise.resolve("downloaded");
	}

	function downloadFile(file) {
		var url = URL.createObjectURL(file);
		var link = document.createElement("a");
		link.href = url;
		link.download = file.name;
		document.body.appendChild(link);
		link.click();
		link.remove();
		global.setTimeout(function () {
			URL.revokeObjectURL(url);
		}, 5000);
	}

	function renderReceiptText(receipt) {
		var branch = state.branch || {};
		var lines = [];
		lines.push(branch.branch_name || branch.name || "Retail store");
		if (receipt.is_return) {
			lines.push("CREDIT NOTE");
		} else if (receipt.gstin) {
			lines.push("TAX INVOICE");
		}
		if (branch.gstin) {
			lines.push("GSTIN: " + branch.gstin);
		}
		if (state.warehouse) {
			lines.push(state.warehouse.warehouse_name || state.warehouse.name);
		}
		lines.push(receipt.posting_datetime || "");
		lines.push("");
		receipt.lines.forEach(function (line) {
			lines.push(line.item_name);
			lines.push("  " + line.qty + " x " + round(line.rate).toFixed(2) + "   " + round(line.total).toFixed(2));
		});
		lines.push("");
		lines.push("Subtotal      " + round(receipt.totals.net).toFixed(2));
		lines.push("Tax           " + round(receipt.totals.tax).toFixed(2));
		lines.push("TOTAL         " + round(receipt.totals.grand).toFixed(2));
		if (receipt.totals.discount) {
			// line and bill discounts are already taken off the lines above
			lines.push("You saved     " + round(receipt.totals.discount).toFixed(2));
		}
		lines.push(receipt.mode + "          " + round(receipt.tendered || receipt.totals.grand).toFixed(2));
		if (receipt.change) {
			lines.push("Change        " + round(receipt.change).toFixed(2));
		}
		if (receipt.customer) {
			lines.push("");
			lines.push("Customer: " + receipt.customer);
		}
		if (receipt.gstin) {
			lines.push("GSTIN: " + receipt.gstin);
		}
		if (receipt.cashier) {
			lines.push("Cashier: " + receipt.cashier);
		}
		if (branch.receipt_footer) {
			lines.push("");
			lines.push(branch.receipt_footer);
		}
		lines.push("");
		lines.push("Thank you!");
		return lines.join("\n");
	}

	function printReceipt(text) {
		var win = global.open("", "_blank", "width=380,height=640");
		if (!win) {
			toast("Allow pop-ups to print a receipt", "error");
			return;
		}
		win.document.write(
			"<html><head><title>Receipt</title><style>body{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;white-space:pre-wrap;padding:12px}</style></head><body>" +
				esc(text) +
				"</body></html>"
		);
		win.document.close();
		win.focus();
		win.print();
	}

	document.addEventListener("DOMContentLoaded", boot);
})(window);
