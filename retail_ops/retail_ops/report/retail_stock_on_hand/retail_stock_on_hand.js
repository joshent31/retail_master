frappe.query_reports["Retail Stock On Hand"] = {
	filters: [
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Retail Branch",
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Retail Warehouse",
		},
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Data",
		},
		{
			fieldname: "only_available",
			label: __("Only Products In Stock"),
			fieldtype: "Check",
			default: 1,
		},
	],
};
