frappe.query_reports["Retail Shift Reconciliation"] = {
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
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Retail Branch",
		},
		{
			fieldname: "status",
			label: __("Session Status"),
			fieldtype: "Select",
			options: ["", "Open", "Closed", "Cancelled"],
		},
		{
			fieldname: "only_variance",
			label: __("Only Cash Differences"),
			fieldtype: "Check",
			default: 0,
		},
	],
};
