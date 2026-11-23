"""Tax collected per tax rate, for filing and reconciliation."""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, getdate, nowdate

from retail_ops.utils import report_branch_filter


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or add_months(to_date, -1))

	columns = [
		{"fieldname": "tax_rate", "label": _("Tax Rate %"), "fieldtype": "Percent", "width": 110},
		{"fieldname": "invoices", "label": _("Bills"), "fieldtype": "Int", "width": 80},
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 100},
		{"fieldname": "taxable", "label": _("Taxable Amount"), "fieldtype": "Currency", "width": 150},
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

	data = frappe.db.sql(
		f"""
		select
			coalesce(it.tax_rate, 0) as tax_rate,
			count(distinct i.name) as invoices,
			sum(it.qty) as qty,
			sum(it.amount) as taxable,
			sum(it.tax_amount) as tax,
			sum(it.total_amount) as total
		from `tabRetail Invoice Item` it
		inner join `tabRetail Invoice` i on i.name = it.parent
		where {" and ".join(conditions)}
		group by coalesce(it.tax_rate, 0)
		order by tax_rate asc
		""",
		values,
		as_dict=True,
	)
	return columns, data
