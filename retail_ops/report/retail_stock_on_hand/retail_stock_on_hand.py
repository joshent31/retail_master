"""Current stock per product and warehouse, valued at the product rate."""

import frappe
from frappe import _
from frappe.utils import cint

from retail_ops.utils import report_branch_filter


def execute(filters=None):
	filters = frappe._dict(filters or {})

	columns = [
		{"fieldname": "product", "label": _("Product"), "fieldtype": "Link", "options": "Retail Product", "width": 150},
		{"fieldname": "product_name", "label": _("Product Name"), "fieldtype": "Data", "width": 220},
		{"fieldname": "category", "label": _("Category"), "fieldtype": "Data", "width": 140},
		{"fieldname": "warehouse", "label": _("Warehouse"), "fieldtype": "Link", "options": "Retail Warehouse", "width": 170},
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Retail Branch", "width": 160},
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 100},
		{"fieldname": "rate", "label": _("Rate"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "value", "label": _("Stock Value"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "updated_at", "label": _("Updated On"), "fieldtype": "Datetime", "width": 160},
	]

	branches = report_branch_filter(filters)
	if branches is not None and not branches:
		return columns, []

	conditions = ["1 = 1"]
	values = {}
	if cint(filters.get("only_available", 1)):
		conditions.append("s.qty > 0")
	if branches is not None:
		conditions.append("s.branch in %(branches)s")
		values["branches"] = branches
	if filters.get("warehouse"):
		conditions.append("s.warehouse = %(warehouse)s")
		values["warehouse"] = filters.warehouse
	if filters.get("category"):
		conditions.append("p.category = %(category)s")
		values["category"] = filters.category

	data = frappe.db.sql(
		f"""
		select
			s.product,
			p.product_name,
			p.category,
			s.warehouse,
			coalesce(s.branch, w.branch) as branch,
			s.qty,
			p.rate,
			s.qty * p.rate as value,
			s.updated_at
		from `tabRetail Stock Balance` s
		inner join `tabRetail Product` p on p.name = s.product
		left join `tabRetail Warehouse` w on w.name = s.warehouse
		where {" and ".join(conditions)}
		order by value desc, p.product_name asc
		""",
		values,
		as_dict=True,
	)
	return columns, data
