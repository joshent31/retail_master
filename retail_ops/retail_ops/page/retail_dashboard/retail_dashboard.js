frappe.pages["retail-dashboard"] = frappe.pages["retail-dashboard"] || {};

frappe.pages["retail-dashboard"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Retail Dashboard"),
		single_column: true,
	});

	var state = { branch: null, days: 30, currency: null, loading: false };

	var $wrap = $('<div class="rd-wrap"></div>').appendTo(page.main);
	var $cards = $('<div class="rd-cards"></div>').appendTo($wrap);
	var $charts = $('<div class="rd-charts"></div>').appendTo($wrap);

	var branch_field = page.add_field({
		fieldname: "branch",
		label: __("Branch"),
		fieldtype: "Link",
		options: "Retail Branch",
		change: function () {
			state.branch = read_branch();
			refresh();
		},
	});

	var days_field = page.add_field({
		fieldname: "rd_days",
		label: __("Period"),
		fieldtype: "Select",
		options: ["Last 7 days", "Last 30 days", "Last 90 days"].join("\n"),
		default: "Last 30 days",
		change: function () {
			state.days = read_days();
			refresh();
		},
	});

	page.set_primary_action(__("Refresh"), refresh, "refresh");

	function field_value(control, fieldname) {
		if (control && control.get_value) {
			return control.get_value();
		}
		var field = page.fields_dict && page.fields_dict[fieldname];
		return field && field.get_value ? field.get_value() : null;
	}

	function read_branch() {
		return field_value(branch_field, "branch") || null;
	}

	function read_days() {
		var value = field_value(days_field, "rd_days");
		if (value === "Last 7 days") {
			return 7;
		}
		if (value === "Last 90 days") {
			return 90;
		}
		return 30;
	}

	function refresh() {
		if (state.loading) {
			return;
		}
		state.loading = true;
		page.set_indicator(__("Loading"), "orange");

		function done() {
			state.loading = false;
			page.clear_indicator();
		}

		frappe.call({
			method: "retail_ops.api.dashboard.get_dashboard",
			type: "GET",
			args: { branch: state.branch, days: state.days },
			callback: function (r) {
				render(r.message || {});
				done();
			},
			error: function () {
				frappe.show_alert({
					message: __("Could not load the dashboard"),
					indicator: "red",
				});
				done();
			},
		});
	}

	function render(data) {
		state.currency = data.currency || null;
		$cards.empty();
		(data.cards || []).forEach(function (card) {
			$cards.append(card_html(card));
		});
		render_charts(data.charts || {});
	}

	function card_html(card) {
		var value = card.is_currency ? money(card.value) : count(card.value);
		var $card = $('<div class="rd-card"></div>').addClass("rd-" + (card.indicator || "gray"));
		$('<div class="rd-card-label"></div>').text(card.label).appendTo($card);
		$('<div class="rd-card-value"></div>').text(value).appendTo($card);
		if (card.subtext) {
			$('<div class="rd-card-sub"></div>').text(card.subtext).appendTo($card);
		}
		return $card;
	}

	function render_charts(charts) {
		$charts.empty();
		var definitions = [
			{ key: "sales_trend", title: __("Sales trend"), height: 320 },
			{ key: "branch_sales", title: __("Sales by branch"), height: 300 },
			{ key: "payment_split", title: __("Payment modes"), height: 300 },
			{ key: "top_products", title: __("Top products by quantity"), height: 300 },
			{ key: "stock_by_category", title: __("Stock value by category"), height: 300 },
		];

		definitions.forEach(function (definition) {
			var spec = charts[definition.key];
			if (!spec) {
				return;
			}
			var $box = $('<div class="rd-chart"></div>')
				.addClass(definition.key === "sales_trend" ? "rd-wide" : "")
				.appendTo($charts);
			$('<div class="rd-chart-title"></div>').text(definition.title).appendTo($box);
			var $canvas = $('<div class="rd-chart-body"></div>').appendTo($box);

			if (!spec.labels || !spec.labels.length) {
				$('<div class="rd-empty"></div>')
					.text(__("No data for this period"))
					.appendTo($canvas);
				return;
			}

			new frappe.Chart($canvas.get(0), {
				type: spec.type || "bar",
				height: definition.height,
				data: {
					labels: spec.labels,
					datasets: spec.datasets,
				},
				colors: ["#2490ef", "#7c3aed", "#22c55e", "#f59e0b", "#ef4444", "#64748b"],
				axisOptions: { xIsSeries: !!spec.x_is_series },
			});
		});
	}

	function money(value) {
		var amount = Number(value || 0);
		var formatted = amount.toLocaleString(undefined, {
			minimumFractionDigits: 2,
			maximumFractionDigits: 2,
		});
		return state.currency ? state.currency + " " + formatted : formatted;
	}

	function count(value) {
		return Number(value || 0).toLocaleString();
	}

	wrapper.rd_refresh = refresh;
};

frappe.pages["retail-dashboard"].on_page_show = function (wrapper) {
	if (wrapper.rd_refresh) {
		wrapper.rd_refresh();
	}
};
