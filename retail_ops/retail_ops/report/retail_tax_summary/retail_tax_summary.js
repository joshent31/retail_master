frappe.query_reports["Retail Tax Summary"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Tax Rate", "HSN/SAC"],
			default: "Tax Rate",
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Retail Branch",
		},
	],
	onload: function (report) {
		report.page.add_inner_button(__("Download GSTR-1"), function () {
			var filters = report.get_values() || {};
			frappe.call({
				method: "retail_ops.gstr1.get_gstr1",
				args: {
					from_date: filters.from_date,
					to_date: filters.to_date,
					branch: filters.branch,
				},
				freeze: true,
				freeze_message: __("Building the return…"),
				callback: function (response) {
					var document = response && response.message;
					if (!document) {
						return;
					}
					var name = document.filename || "GSTR1.json";
					delete document.filename;
					var blob = new Blob([JSON.stringify(document, null, 1)], {
						type: "application/json",
					});
					var link = document.createElement("a");
					link.href = URL.createObjectURL(blob);
					link.download = name;
					document.body.appendChild(link);
					link.click();
					setTimeout(function () {
						URL.revokeObjectURL(link.href);
						link.remove();
					}, 1000);
					frappe.show_alert({
						message: __("GSTR-1 written — check it in the GST offline tool before uploading"),
						indicator: "green",
					});
				},
			});
		});
	},
};
