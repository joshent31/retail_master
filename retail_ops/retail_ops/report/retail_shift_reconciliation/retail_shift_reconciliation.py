"""Day sessions with their cash reconciliation, highlighting variances."""

import frappe
from frappe import _
from frappe.utils import add_months, getdate, nowdate

from retail_ops.utils import report_branch_filter


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or add_months(to_date, -1))

	columns = [
		{"fieldname": "session", "label": _("Session"), "fieldtype": "Link", "options": "Retail Day Session", "width": 150},
		{"fieldname": "session_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Retail Branch", "width": 150},
		{"fieldname": "warehouse", "label": _("Warehouse"), "fieldtype": "Link", "options": "Retail Warehouse", "width": 150},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "opened_by", "label": _("Opened By"), "fieldtype": "Link", "options": "User", "width": 170},
		{"fieldname": "closed_by", "label": _("Closed By"), "fieldtype": "Link", "options": "User", "width": 170},
		{"fieldname": "invoice_count", "label": _("Bills"), "fieldtype": "Int", "width": 70},
		{"fieldname": "opening_float", "label": _("Opening Float"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "sales_total", "label": _("Sales"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "cash_sales", "label": _("Cash"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "card_sales", "label": _("Card"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "upi_sales", "label": _("UPI"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "credit_sales", "label": _("Credit"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "expected_cash", "label": _("Expected Cash"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "closing_cash", "label": _("Counted Cash"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "variance", "label": _("Variance"), "fieldtype": "Currency", "width": 110},
	]

	branches = report_branch_filter(filters)
	if branches is not None and not branches:
		return columns, []

	conditions = ["s.session_date >= %(from_date)s", "s.session_date <= %(to_date)s"]
	values = {"from_date": from_date, "to_date": to_date}
	if branches is not None:
		conditions.append("s.branch in %(branches)s")
		values["branches"] = branches
	if filters.get("status"):
		conditions.append("s.status = %(status)s")
		values["status"] = filters.status
	if filters.get("only_variance"):
		conditions.append("abs(coalesce(s.variance, 0)) > 0.01")

	data = frappe.db.sql(
		f"""
		select
			s.name as session,
			s.session_date,
			s.branch,
			s.warehouse,
			s.status,
			s.opened_by,
			s.closed_by,
			s.invoice_count,
			s.opening_float,
			s.sales_total,
			s.cash_sales,
			s.card_sales,
			s.upi_sales,
			s.credit_sales,
			s.expected_cash,
			s.closing_cash,
			s.variance
		from `tabRetail Day Session` s
		where {" and ".join(conditions)}
		order by s.session_date desc, s.name desc
		""",
		values,
		as_dict=True,
	)
	return columns, data
