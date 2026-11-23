"""Numbers behind the Retail Dashboard desk page.

Every query is limited to the branches the signed-in user may access, the same
rule the list views and reports use, and a branch filter outside that scope is
refused instead of silently returning nothing.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_first_day, getdate, nowdate

from retail_ops.utils import get_currency, user_branches

PERIOD_CHOICES = (7, 30, 90)


@frappe.whitelist()
def get_dashboard(branch=None, days=30):
	if not frappe.has_permission("Retail Invoice", "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	days = cint(days) or 30
	if days not in PERIOD_CHOICES:
		days = 30
	branch = branch or None

	values = {
		"today": nowdate(),
		"tomorrow": add_days(nowdate(), 1),
		"yesterday": add_days(nowdate(), -1),
		"month_start": get_first_day(nowdate()),
		"from_date": add_days(getdate(nowdate()), -(days - 1)),
	}

	invoice_scope, invoice_values = branch_scope(branch)
	stock_scope, stock_values = branch_scope(branch, column="s.branch")
	session_scope, session_values = branch_scope(branch, column="s.branch")
	values.update(invoice_values)
	values.update(stock_values)
	values.update(session_values)

	return {
		"currency": get_currency(branch),
		"days": days,
		"branch": branch,
		"cards": get_cards(invoice_scope, stock_scope, session_scope, values),
		"charts": get_charts(invoice_scope, stock_scope, values),
	}


def branch_scope(branch=None, column="i.branch"):
	"""SQL fragment plus params limiting a query to the user's branches."""
	allowed = user_branches()
	if branch:
		if allowed is not None and branch not in allowed:
			frappe.throw(
				_("You do not have access to branch {0}").format(frappe.bold(branch)),
				frappe.PermissionError,
			)
		return "and {0} = %(branch)s".format(column), {"branch": branch}
	if allowed is None:
		return "", {}
	if not allowed:
		return "and 1 = 0", {}
	return "and {0} in %(branches)s".format(column), {"branches": tuple(allowed)}


def get_cards(invoice_scope, stock_scope, session_scope, values):
	# Today and yesterday in one pass so the sales card can show a comparison.
	days = frappe.db.sql(
		"""
		select date(i.posting_datetime) as day,
			sum(i.grand_total) as total,
			count(*) as bills
		from `tabRetail Invoice` i
		where i.docstatus = 1
			and i.posting_datetime >= %(yesterday)s
			and i.posting_datetime < %(tomorrow)s
			{scope}
		group by day
		""".format(scope=invoice_scope),
		values,
		as_dict=True,
	)
	by_day = {str(row.day): row for row in days}
	today = by_day.get(str(values["today"]))
	yesterday = by_day.get(str(values["yesterday"]))
	today_total = flt(today.total) if today else 0.0
	today_bills = int(today.bills) if today else 0
	yesterday_total = flt(yesterday.total) if yesterday else 0.0

	month = frappe.db.sql(
		"""
		select coalesce(sum(i.grand_total), 0) as total, count(*) as bills
		from `tabRetail Invoice` i
		where i.docstatus = 1 and i.posting_datetime >= %(month_start)s
			{scope}
		""".format(scope=invoice_scope),
		values,
		as_dict=True,
	)[0]

	cash = frappe.db.sql(
		"""
		select coalesce(sum(p.amount), 0) as total
		from `tabRetail Invoice Payment` p
		join `tabRetail Invoice` i on i.name = p.parent
		where i.docstatus = 1
			and p.mode = 'Cash'
			and i.posting_datetime >= %(today)s
			and i.posting_datetime < %(tomorrow)s
			{scope}
		""".format(scope=invoice_scope),
		values,
		as_dict=True,
	)[0]

	credit = frappe.db.sql(
		"""
		select coalesce(sum(i.outstanding_amount), 0) as total,
			count(distinct coalesce(nullif(i.customer_mobile, ''),
				nullif(i.customer_name, ''), i.name)) as customers
		from `tabRetail Invoice` i
		where i.docstatus = 1 and i.outstanding_amount > 0
			{scope}
		""".format(scope=invoice_scope),
		values,
		as_dict=True,
	)[0]

	open_sessions = frappe.db.sql(
		"""
		select count(*) as total
		from `tabRetail Day Session` s
		where s.status = 'Open' {scope}
		""".format(scope=session_scope),
		values,
		as_dict=True,
	)[0]

	stock = frappe.db.sql(
		"""
		select coalesce(sum(s.qty * p.rate), 0) as value
		from `tabRetail Stock Balance` s
		join `tabRetail Product` p on p.name = s.product
		where 1 = 1 {scope}
		""".format(scope=stock_scope),
		values,
		as_dict=True,
	)[0]

	diff = today_total - yesterday_total
	return [
		{
			"label": _("Sales Today"),
			"value": today_total,
			"is_currency": 1,
			"indicator": "blue",
			"subtext": _("Yesterday {0}").format(_money(yesterday_total)),
		},
		{
			"label": _("Bills Today"),
			"value": today_bills,
			"subtext": _("Average {0}").format(
				_money(today_total / today_bills if today_bills else 0)
			),
		},
		{
			"label": _("Sales Change"),
			"value": diff,
			"is_currency": 1,
			"indicator": "green" if diff >= 0 else "red",
			"subtext": _("Compared to yesterday"),
		},
		{
			"label": _("This Month"),
			"value": flt(month.total),
			"is_currency": 1,
			"indicator": "blue",
			"subtext": _("{0} bills").format(int(month.bills)),
		},
		{
			"label": _("Cash Today"),
			"value": flt(cash.total),
			"is_currency": 1,
			"indicator": "green",
			"subtext": _("Cash payments received"),
		},
		{
			"label": _("Credit Outstanding"),
			"value": flt(credit.total),
			"is_currency": 1,
			"indicator": "orange",
			"subtext": _("{0} customers").format(int(credit.customers)),
		},
		{
			"label": _("Open Sessions"),
			"value": int(open_sessions.total),
			"indicator": "purple",
			"subtext": _("Days still open for billing"),
		},
		{
			"label": _("Stock Value"),
			"value": flt(stock.value),
			"is_currency": 1,
			"indicator": "gray",
			"subtext": _("At selling rate"),
		},
	]


def get_charts(invoice_scope, stock_scope, values):
	return {
		"sales_trend": sales_trend(invoice_scope, values),
		"branch_sales": branch_sales(invoice_scope, values),
		"payment_split": payment_split(invoice_scope, values),
		"top_products": top_products(invoice_scope, values),
		"stock_by_category": stock_by_category(stock_scope, values),
	}


def sales_trend(invoice_scope, values):
	rows = frappe.db.sql(
		"""
		select date(i.posting_datetime) as day, sum(i.grand_total) as total
		from `tabRetail Invoice` i
		where i.docstatus = 1 and i.posting_datetime >= %(from_date)s
			{scope}
		group by day
		order by day
		""".format(scope=invoice_scope),
		values,
		as_dict=True,
	)
	totals = {str(row.day): flt(row.total) for row in rows}

	labels, points = [], []
	day = getdate(values["from_date"])
	end = getdate(values["today"])
	while day <= end:
		labels.append(frappe.utils.format_date(day, "dd MMM"))
		points.append(totals.get(str(day), 0))
		day = add_days(day, 1)

	return {
		"type": "line",
		"labels": labels,
		"datasets": [{"name": _("Sales"), "values": points}],
		"x_is_series": 1,
	}


def branch_sales(invoice_scope, values):
	rows = frappe.db.sql(
		"""
		select i.branch as label, sum(i.grand_total) as total
		from `tabRetail Invoice` i
		where i.docstatus = 1 and i.posting_datetime >= %(from_date)s
			{scope}
		group by i.branch
		order by total desc
		""".format(scope=invoice_scope),
		values,
		as_dict=True,
	)
	return {
		"type": "bar",
		"labels": [row.label for row in rows],
		"datasets": [{"name": _("Sales"), "values": [flt(row.total) for row in rows]}],
	}


def payment_split(invoice_scope, values):
	rows = frappe.db.sql(
		"""
		select p.mode as label, sum(p.amount) as total
		from `tabRetail Invoice Payment` p
		join `tabRetail Invoice` i on i.name = p.parent
		where i.docstatus = 1 and i.posting_datetime >= %(from_date)s
			{scope}
		group by p.mode
		order by total desc
		""".format(scope=invoice_scope),
		values,
		as_dict=True,
	)
	return {
		"type": "donut",
		"labels": [row.label for row in rows],
		"datasets": [{"name": _("Collected"), "values": [flt(row.total) for row in rows]}],
	}


def top_products(invoice_scope, values):
	rows = frappe.db.sql(
		"""
		select it.product, max(it.item_name) as item_name,
			sum(it.qty) as qty, sum(it.total_amount) as total
		from `tabRetail Invoice Item` it
		join `tabRetail Invoice` i on i.name = it.parent
		where i.docstatus = 1 and i.posting_datetime >= %(from_date)s
			{scope}
		group by it.product
		order by qty desc
		limit 8
		""".format(scope=invoice_scope),
		values,
		as_dict=True,
	)
	return {
		"type": "bar",
		"labels": [row.item_name or row.product for row in rows],
		"datasets": [{"name": _("Quantity sold"), "values": [flt(row.qty) for row in rows]}],
	}


def stock_by_category(stock_scope, values):
	rows = frappe.db.sql(
		"""
		select coalesce(nullif(p.category, ''), 'Uncategorised') as label,
			sum(s.qty * p.rate) as total
		from `tabRetail Stock Balance` s
		join `tabRetail Product` p on p.name = s.product
		where 1 = 1 {scope}
		group by coalesce(nullif(p.category, ''), 'Uncategorised')
		order by total desc
		limit 8
		""".format(scope=stock_scope),
		values,
		as_dict=True,
	)
	return {
		"type": "bar",
		"labels": [row.label for row in rows],
		"datasets": [{"name": _("Stock value"), "values": [flt(row.total) for row in rows]}],
	}


def _money(value):
	return frappe.utils.fmt_money(flt(value), currency="")
