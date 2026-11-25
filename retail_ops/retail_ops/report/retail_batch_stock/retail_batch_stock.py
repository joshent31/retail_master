"""On hand per batch, with what expires next at the top.

The batch rows are maintained by receiving, billing and transfers, so this is
also the quickest check that they still add up to the warehouse balance: sum the
quantity here and compare with the stock on hand report.
"""

import frappe
from frappe import _
from frappe.utils import add_days, date_diff, flt, getdate, nowdate

from retail_ops.utils import report_branch_filter


def execute(filters=None):
	filters = frappe._dict(filters or {})
	branches = report_branch_filter(filters)

	columns = [
		{"fieldname": "product", "label": _("Product"), "fieldtype": "Link", "options": "Retail Product", "width": 180},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 220},
		{"fieldname": "batch_no", "label": _("Batch"), "fieldtype": "Data", "width": 130},
		{"fieldname": "warehouse", "label": _("Warehouse"), "fieldtype": "Link", "options": "Retail Warehouse", "width": 160},
		{"fieldname": "expiry_date", "label": _("Expiry"), "fieldtype": "Date", "width": 110},
		{"fieldname": "days_to_expiry", "label": _("Days Left"), "fieldtype": "Int", "width": 100},
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 100},
	]

	if branches is not None and not branches:
		return columns, []

	conditions = []
	values = {}
	if branches is not None:
		conditions.append("b.branch in %(branches)s")
		values["branches"] = branches
	if filters.get("warehouse"):
		conditions.append("b.warehouse = %(warehouse)s")
		values["warehouse"] = filters.warehouse
	if filters.get("product"):
		conditions.append("b.product = %(product)s")
		values["product"] = filters.product
	if filters.get("batch_no"):
		conditions.append("b.batch_no like %(batch_no)s")
		values["batch_no"] = "%" + filters.batch_no + "%"

	days = int(filters.get("expiry_within_days") or 0)
	if days > 0:
		# items with no expiry date still need looking at, so they stay in
		conditions.append(
			"(b.expiry_date is null or b.expiry_date <= %(cutoff)s)"
		)
		values["cutoff"] = add_days(getdate(nowdate()), days)
	if filters.get("hide_zero", 1):
		conditions.append("b.qty > 0.000001")

	where = " and ".join(conditions) or "1=1"
	rows = frappe.db.sql(
		f"""
		select
			b.product,
			coalesce(p.product_name, '') as item_name,
			b.batch_no,
			b.warehouse,
			b.expiry_date,
			b.qty
		from `tabRetail Stock Batch` b
		left join `tabRetail Product` p on p.name = b.product
		where {where}
		order by b.expiry_date is null, b.expiry_date asc, p.product_name asc
		""",
		values,
		as_dict=True,
	)

	today = getdate(nowdate())
	for row in rows:
		row["qty"] = flt(row.qty)
		row["days_to_expiry"] = date_diff(row.expiry_date, today) if row.expiry_date else None
	return columns, rows
