"""Tax collected per tax rate or per HSN code, for filing and reconciliation."""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, getdate, nowdate

from retail_ops.utils import report_branch_filter


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or add_months(to_date, -1))
	by_hsn = (filters.get("group_by") or "Tax Rate") == "HSN/SAC"

	columns = []
	if by_hsn:
		columns.append({"fieldname": "hsn_code", "label": _("HSN/SAC"), "fieldtype": "Data", "width": 110})
	columns += [
		{"fieldname": "tax_rate", "label": _("Tax Rate %"), "fieldtype": "Percent", "width": 110},
		{"fieldname": "invoices", "label": _("Bills"), "fieldtype": "Int", "width": 80},
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 100},
		{"fieldname": "taxable", "label": _("Taxable Amount"), "fieldtype": "Currency", "width": 150},
		{"fieldname": "cgst", "label": _("CGST"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "sgst", "label": _("SGST"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "igst", "label": _("IGST"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "tax", "label": _("Tax"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "total", "label": _("Total"), "fieldtype": "Currency", "width": 140},
	]

	branches = report_branch_filter(filters)
	if branches is not None and not branches:
		return columns, []

	conditions = [
		"i.docstatus = 1",
		"i.posting_datetime >= %(from_date)s",
		"i.posting_datetime < %(to_date)s",
	]
	values = {"from_date": from_date, "to_date": add_days(to_date, 1)}
	if branches is not None:
		conditions.append("i.branch in %(branches)s")
		values["branches"] = branches

	# Only group by HSN when it is asked for: the column is not in the group
	# by otherwise, and MySQL would reject the select.
	select_hsn = "coalesce(it.hsn_code, '') as hsn_code," if by_hsn else ""
	group_by = "coalesce(it.hsn_code, ''), coalesce(it.tax_rate, 0)" if by_hsn else "coalesce(it.tax_rate, 0)"
	order_by = "hsn_code asc, tax_rate asc" if by_hsn else "tax_rate asc"

	data = frappe.db.sql(
		f"""
		select
			{select_hsn}
			coalesce(it.tax_rate, 0) as tax_rate,
			count(distinct i.name) as invoices,
			sum(it.qty) as qty,
			sum(it.amount) as taxable,
			sum(coalesce(it.cgst_amount, 0)) as cgst,
			sum(coalesce(it.sgst_amount, 0)) as sgst,
			sum(coalesce(it.igst_amount, 0)) as igst,
			sum(it.tax_amount) as tax,
			sum(it.total_amount) as total
		from `tabRetail Invoice Item` it
		inner join `tabRetail Invoice` i on i.name = it.parent
		where {" and ".join(conditions)}
		group by {group_by}
		order by {order_by}
		""",
		values,
		as_dict=True,
	)
	return columns, data
