frappe.ui.form.on("Retail Product", {
	refresh: function (frm) {
		if (frm.is_new()) {
			return;
		}
		frm.add_custom_button(__("Print Label"), function () {
			frappe.prompt(
				[
					{
						fieldname: "copies",
						fieldtype: "Int",
						label: __("Copies"),
						default: 1,
						reqd: 1,
					},
				],
				function (values) {
					var copies = values.copies || 1;
					var url =
						"/retail-label?products=" +
						encodeURIComponent(frm.doc.name) +
						"&copies=" +
						encodeURIComponent(copies);
					window.open(url, "_blank");
				},
				__("Print price label")
			);
		});
	},
});
