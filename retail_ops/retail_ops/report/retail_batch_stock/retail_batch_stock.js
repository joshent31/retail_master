frappe.query_reports["Retail Batch Stock"] = {
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
			fieldname: "product",
			label: __("Product"),
			fieldtype: "Link",
			options: "Retail Product",
		},
		{
			fieldname: "batch_no",
			label: __("Batch No"),
			fieldtype: "Data",
		},
		{
			fieldname: "expiry_within_days",
			label: __("Expiring Within (Days)"),
			fieldtype: "Int",
			default: 0,
			description: __("0 shows every batch. 30 is the usual near-expiry list."),
		},
		{
			fieldname: "hide_zero",
			label: __("Hide Empty Batches"),
			fieldtype: "Check",
			default: 1,
		},
	],
};
