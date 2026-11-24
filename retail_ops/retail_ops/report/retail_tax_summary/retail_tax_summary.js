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
};
