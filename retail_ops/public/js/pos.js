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
		openSessions: [],
		can: {},
		branch: null,
		warehouse: null,
		session: null,
		cart: [],
		customer: { name: "", mobile: "" },
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
					state.customer = cart.customer || { name: "", mobile: "" };
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
			customer: state.customer,
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

	function renderProducts() {
		var products = filteredProducts();
		dom["rp-products-empty"].hidden = products.length > 0;
		var visible = products.slice(0, 240);
		dom["rp-products"].innerHTML = visible
			.map(function (product) {
				var qty = stockFor(product.name, state.warehouse && state.warehouse.name);
				var stockClass = qty <= 0 ? "is-out" : qty < 5 ? "is-low" : "";
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

	function totals() {
		var net = 0;
		var discount = 0;
		var tax = 0;
		state.cart.forEach(function (line) {
			net += lineAmount(line);
			discount += num(line.discount);
			tax += lineTax(line);
		});
		net = round(net);
		return {
			net: net,
			discount: round(discount),
			tax: round(tax),
			grand: round(net + tax),
			count: state.cart.reduce(function (sum, line) {
				return sum + num(line.qty);
			}, 0),
		};
	}

	function renderCart() {
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
						"%</div></div>" +
						'<div class="rp-line-amount">' +
						esc(money(lineAmount(line) + lineTax(line))) +
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

		if (!state.tenderedTouched) {
			dom["rp-tendered"].value = summary.grand ? summary.grand.toFixed(2) : "";
		}
		dom["rp-checkout"].disabled = !state.cart.length || (state.can.create_invoice === false);

		dom["rp-tab-count"].hidden = !summary.count;
		dom["rp-tab-count"].textContent = summary.count;
		dom["rp-tab-total"].textContent = summary.grand ? money(summary.grand) : "";
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
		saveCart();
		renderCart();
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

		var summary = totals();
		if (summary.grand <= 0) {
			toast("Bill total must be greater than zero", "error");
			return;
		}

		var mode = dom["rp-pay-mode"].value;
		var tendered = num(dom["rp-tendered"].value);
		var payments = [];
		if (mode === "Credit") {
			payments.push({ mode: "Credit", amount: summary.grand });
		} else {
			payments.push({ mode: mode, amount: tendered > 0 ? round(tendered) : summary.grand });
		}

		var offlineUuid = RetailDB.uuid();
		var payload = {
			offline_uuid: offlineUuid,
			branch: state.branch.name,
			warehouse: state.warehouse.name,
			day_session: state.session.name || state.session.offline_uuid,
			posting_datetime: serverNow(),
			customer_name: state.customer.name || null,
			customer_mobile: state.customer.mobile || null,
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
				return {
					item_name: line.item_name,
					qty: line.qty,
					rate: line.rate,
					total: round(lineAmount(line) + lineTax(line)),
				};
			}),
			totals: summary,
			mode: mode,
			tendered: tendered,
			change: mode === "Credit" ? 0 : round(Math.max(0, tendered - summary.grand)),
			customer: state.customer.name,
			posting_datetime: payload.posting_datetime,
			cashier: (state.catalog && state.catalog.full_name) || "",
		};

		deductLocalStock(state.cart);

		state.cart = [];
		state.customer = { name: "", mobile: "" };
		state.tenderedTouched = false;
		dom["rp-customer-name"].value = "";
		dom["rp-customer-mobile"].value = "";
		saveCart();
		renderCart();

		dom["rp-checkout"].disabled = true;

		RetailDB.enqueue({ id: offlineUuid, type: "invoice", payload: payload })
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
			title: status === "created" || status === "duplicate" ? "Bill complete" : "Bill saved",
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
		if (receipt.totals.discount) {
			lines.push("Discount     -" + round(receipt.totals.discount).toFixed(2));
		}
		lines.push("Tax           " + round(receipt.totals.tax).toFixed(2));
		lines.push("TOTAL         " + round(receipt.totals.grand).toFixed(2));
		lines.push(receipt.mode + "          " + round(receipt.tendered || receipt.totals.grand).toFixed(2));
		if (receipt.change) {
			lines.push("Change        " + round(receipt.change).toFixed(2));
		}
		if (receipt.customer) {
			lines.push("");
			lines.push("Customer: " + receipt.customer);
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
