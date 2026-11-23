"""Product wise sales for a period, with each product's share of the total."""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, flt, getdate, nowdate

from retail_ops.utils import report_branch_filter


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or add_months(to_date, -1))

	columns = [
		{"fieldname": "product", "label": _("Product"), "fieldtype": "Link", "options": "Retail Product", "width": 150},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 200},
		{"fieldname": "category", "label": _("Category"), "fieldtype": "Data", "width": 130},
		{"fieldname": "invoices", "label": _("Bills"), "fieldtype": "Int", "width": 70},
		{"fieldname": "qty", "label": _("Qty Sold"), "fieldtype": "Float", "width": 100},
		{"fieldname": "amount", "label": _("Net Amount"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "discount", "label": _("Discount"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "tax", "label": _("Tax"), "fieldtype": "Currency", "width": 100},
		{"fieldname": "total", "label": _("Total"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "share", "label": _("Share %"), "fieldtype": "Percent", "width": 90},
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
	if filters.get("category"):
		conditions.append("p.category = %(category)s")
		values["category"] = filters.category

	data = frappe.db.sql(
		f"""
		select
			it.product,
			max(it.item_name) as item_name,
			max(p.category) as category,
			count(distinct i.name) as invoices,
			sum(it.qty) as qty,
			sum(it.amount) as amount,
			sum(it.discount_amount) as discount,
			sum(it.tax_amount) as tax,
			sum(it.total_amount) as total
		from `tabRetail Invoice Item` it
		inner join `tabRetail Invoice` i on i.name = it.parent
		left join `tabRetail Product` p on p.name = it.product
		where {" and ".join(conditions)}
		group by it.product
		order by total desc
		""",
		values,
		as_dict=True,
	)

	grand_total = sum(flt(row.total) for row in data)
	for row in data:
		row["share"] = flt(row.total) * 100.0 / grand_total if grand_total else 0
	return columns, data
