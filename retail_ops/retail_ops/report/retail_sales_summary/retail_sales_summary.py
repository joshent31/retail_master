"""Daily sales totals per branch, split by payment mode."""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, getdate, nowdate

from retail_ops.utils import report_branch_filter


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or add_months(to_date, -1))

	columns = [
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 110},
		{"fieldname": "branch", "label": _("Branch"), "fieldtype": "Link", "options": "Retail Branch", "width": 170},
		{"fieldname": "invoices", "label": _("Bills"), "fieldtype": "Int", "width": 80},
		{"fieldname": "net_total", "label": _("Net Total"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "discount_total", "label": _("Discount"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "tax_total", "label": _("Tax"), "fieldtype": "Currency", "width": 110},
		{"fieldname": "grand_total", "label": _("Billed"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "cash", "label": _("Cash"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "card", "label": _("Card"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "upi", "label": _("UPI"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "credit", "label": _("Credit"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "outstanding", "label": _("Credit Due"), "fieldtype": "Currency", "width": 120},
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
	if filters.get("warehouse"):
		conditions.append("i.warehouse = %(warehouse)s")
		values["warehouse"] = filters.warehouse

	data = frappe.db.sql(
		f"""
		select
			cast(i.posting_datetime as date) as posting_date,
			i.branch,
			count(i.name) as invoices,
			sum(i.net_total) as net_total,
			sum(i.discount_total) as discount_total,
			sum(i.tax_total) as tax_total,
			sum(i.grand_total) as grand_total,
			sum(i.outstanding_amount) as outstanding,
			sum(coalesce(p.cash, 0)) as cash,
			sum(coalesce(p.card, 0)) as card,
			sum(coalesce(p.upi, 0)) as upi,
			sum(coalesce(p.credit, 0)) as credit
		from `tabRetail Invoice` i
		left join (
			select
				parent,
				sum(case when mode = 'Cash' then amount else 0 end) as cash,
				sum(case when mode = 'Card' then amount else 0 end) as card,
				sum(case when mode = 'UPI' then amount else 0 end) as upi,
				sum(case when mode = 'Credit' then amount else 0 end) as credit
			from `tabRetail Invoice Payment`
			group by parent
		) p on p.parent = i.name
		where {" and ".join(conditions)}
		group by cast(i.posting_datetime as date), i.branch
		order by posting_date desc, i.branch asc
		""",
		values,
		as_dict=True,
	)
	return columns, data
