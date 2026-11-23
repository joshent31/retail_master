frappe.query_reports["Retail Credit Outstanding"] = {
	filters: [
		{
			fieldname: "as_on",
			label: __("As On Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Retail Branch",
		},
		{
			fieldname: "min_days",
			label: __("Older Than (Days)"),
			fieldtype: "Int",
			default: 0,
		},
	],
};
