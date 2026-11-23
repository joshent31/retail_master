"""Customers who still owe money, oldest dues first."""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from retail_ops.utils import report_branch_filter


def execute(filters=None):
	filters = frappe._dict(filters or {})
	as_on = getdate(filters.get("as_on") or nowdate())

	columns = [
		{"fieldname": "customer_name", "label": _("Customer"), "fieldtype": "Data", "width": 200},
		{"fieldname": "customer_mobile", "label": _("Mobile"), "fieldtype": "Data", "width": 130},
		{"fieldname": "bills", "label": _("Open Bills"), "fieldtype": "Int", "width": 100},
		{"fieldname": "billed", "label": _("Billed"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "outstanding", "label": _("Outstanding"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "oldest_bill", "label": _("Oldest Bill"), "fieldtype": "Date", "width": 110},
		{"fieldname": "oldest_days", "label": _("Age (Days)"), "fieldtype": "Int", "width": 100},
		{"fieldname": "last_bill", "label": _("Last Bill"), "fieldtype": "Datetime", "width": 160},
	]

	branches = report_branch_filter(filters)
	if branches is not None and not branches:
		return columns, []

	conditions = ["i.docstatus = 1", "i.outstanding_amount > 0", "i.posting_datetime <= %(as_on)s"]
	values = {"as_on": as_on, "min_days": filters.get("min_days") or 0}
	if branches is not None:
		conditions.append("i.branch in %(branches)s")
		values["branches"] = branches

	data = frappe.db.sql(
		f"""
		select
			coalesce(nullif(i.customer_name, ''), '(no name)') as customer_name,
			coalesce(nullif(i.customer_mobile, ''), '-') as customer_mobile,
			count(i.name) as bills,
			sum(i.grand_total) as billed,
			sum(i.outstanding_amount) as outstanding,
			min(cast(i.posting_datetime as date)) as oldest_bill,
			datediff(%(as_on)s, min(cast(i.posting_datetime as date))) as oldest_days,
			max(i.posting_datetime) as last_bill
		from `tabRetail Invoice` i
		where {" and ".join(conditions)}
		group by i.customer_mobile, i.customer_name
		having oldest_days >= %(min_days)s
		order by outstanding desc, oldest_days desc
		""",
		values,
		as_dict=True,
	)
	return columns, data
