frappe.query_reports["Retail Supplier Payables"] = {
	filters: [
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Retail Branch",
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Retail Supplier",
		},
		{
			fieldname: "only_outstanding",
			label: __("Only Outstanding"),
			fieldtype: "Check",
			default: 1,
		},
	],
};
