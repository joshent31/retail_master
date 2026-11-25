"""Day wise opening / closing.

A *Retail Day Session* belongs to one branch + warehouse + business date. Bills
are always linked to the session that was open when they were created, so the
closing figures (cash, card, UPI, credit) are computed from the bills, not typed
in by the cashier.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now, today

from retail_ops.utils import (
	assert_branch_access,
	get_branch,
	get_warehouse,
	money,
	server_time,
)

OPEN = "Open"
CLOSED = "Closed"
CANCELLED = "Cancelled"


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
def get_open_session(branch, warehouse):
	name = frappe.db.get_value(
		"Retail Day Session",
		{"branch": branch, "warehouse": warehouse, "status": OPEN},
		"name",
		order_by="session_date desc, creation desc",
	)
	return frappe.get_doc("Retail Day Session", name) if name else None


def find_session_by_offline_uuid(offline_uuid):
	if not offline_uuid:
		return None
	return frappe.db.get_value("Retail Day Session", {"offline_uuid": offline_uuid}, "name")


def get_session_totals(session_name):
	invoices = frappe.get_all(
		"Retail Invoice",
		filters={"day_session": session_name, "docstatus": 1},
		fields=[
			"name",
			"grand_total",
			"tax_total",
			"discount_total",
			"change_amount",
			"paid_amount",
			"outstanding_amount",
		],
	)
	payments = frappe.db.sql(
		"""select p.mode, sum(p.amount) as amount
		from `tabRetail Invoice Payment` p
		inner join `tabRetail Invoice` i on i.name = p.parent
		where i.day_session = %(session)s and i.docstatus = 1
		group by p.mode""",
		{"session": session_name},
		as_dict=True,
	)
	by_mode = {row.mode: flt(row.amount) for row in payments}

	# Money a customer hands over for an older credit bill lands in the same
	# drawer, so it has to count towards the expected cash of the day.
	due_cash = 0
	due_total = 0
	if frappe.db.table_exists("Retail Customer Receipt"):
		dues = frappe.db.sql(
			"""select
				sum(case when r.mode = 'Cash' then r.amount else 0 end) as cash,
				sum(r.amount) as total
			from `tabRetail Customer Receipt` r
			where r.day_session = %(session)s and r.docstatus = 1""",
			{"session": session_name},
			as_dict=True,
		)
		due_cash = flt(dues[0].cash) if dues else 0
		due_total = flt(dues[0].total) if dues else 0

	return {
		"invoice_count": len(invoices),
		"sales_total": money(sum(flt(row.grand_total) for row in invoices)),
		"tax_total": money(sum(flt(row.tax_total) for row in invoices)),
		"discount_total": money(sum(flt(row.discount_total) for row in invoices)),
		"cash_sales": money(by_mode.get("Cash", 0)),
		"card_sales": money(by_mode.get("Card", 0)),
		"upi_sales": money(by_mode.get("UPI", 0)),
		"credit_sales": money(by_mode.get("Credit", 0)),
		"due_collected": money(due_total),
		"due_cash": money(due_cash),
		"change_total": money(sum(flt(row.change_amount) for row in invoices)),
	}


def update_session_totals(session_name):
	doc = frappe.get_doc("Retail Day Session", session_name)
	totals = get_session_totals(session_name)
	# cash from dues has no column of its own on the session, only in expected cash
	due_cash = flt(totals.pop("due_cash", 0))
	doc.update(totals)
	doc.expected_cash = money(
		flt(doc.opening_float)
		+ flt(totals["cash_sales"])
		+ due_cash
		- flt(totals["change_total"])
	)
	if cint(doc.status == CLOSED) or doc.closing_cash is not None:
		doc.variance = money(flt(doc.closing_cash) - flt(doc.expected_cash))
	doc.db_update()
	if doc.meta.has_field("modified"):
		frappe.db.set_value("Retail Day Session", doc.name, "modified", now(), update_modified=False)
	return doc


# ---------------------------------------------------------------------------
# Controller hook
# ---------------------------------------------------------------------------
def validate_session(doc, method=None):
	warehouse = get_warehouse(doc.warehouse, branch=doc.branch)
	doc.session_date = doc.session_date or today()
	if doc.warehouse != warehouse.name:
		doc.warehouse = warehouse.name

	# only one open session per warehouse at any time
	if doc.status == OPEN:
		other = frappe.db.get_value(
			"Retail Day Session",
			{
				"branch": doc.branch,
				"warehouse": doc.warehouse,
				"status": OPEN,
				"name": ["!=", doc.name or ""],
			},
			"name",
		)
		if other:
			frappe.throw(
				_("Session {0} is already open for {1}. Close it before opening a new day.").format(
					frappe.bold(other), frappe.bold(doc.warehouse)
				),
				frappe.ValidationError,
			)

	if doc.status == CLOSED:
		if doc.closing_cash is None:
			frappe.throw(_("Closing cash is required to close the day"), frappe.ValidationError)
		doc.closed_by = doc.closed_by or frappe.session.user
		doc.closed_at = doc.closed_at or now()

	# totals are always derived from submitted bills
	if doc.name:
		totals = get_session_totals(doc.name)
		doc.update(totals)
		doc.expected_cash = money(
			flt(doc.opening_float) + flt(totals["cash_sales"]) - flt(totals["change_total"])
		)
		if doc.status == CLOSED:
			doc.variance = money(flt(doc.closing_cash) - flt(doc.expected_cash))


# ---------------------------------------------------------------------------
# Operations (shared by the REST API and the offline sync)
# ---------------------------------------------------------------------------
def open_session(branch, warehouse, opening_float=0, session_date=None, offline_uuid=None, opened_at=None):
	if offline_uuid:
		existing = frappe.db.get_value("Retail Day Session", {"offline_uuid": offline_uuid}, "name")
		if existing:
			return frappe.get_doc("Retail Day Session", existing), True

	assert_branch_access(branch)
	get_branch(branch)  # validates active branch
	get_warehouse(warehouse, branch=branch)

	existing = get_open_session(branch, warehouse)
	if existing:
		return existing, True

	doc = frappe.new_doc("Retail Day Session")
	doc.branch = branch
	doc.warehouse = warehouse
	doc.session_date = getdate(session_date) if session_date else getdate(today())
	doc.opening_float = money(opening_float)
	doc.status = OPEN
	doc.opened_by = frappe.session.user
	doc.opened_at = opened_at or now()
	doc.offline_uuid = offline_uuid or frappe.generate_hash(length=20)
	doc.insert()
	return doc, False


def close_session(session, closing_cash, notes=None, closed_at=None):
	doc = frappe.get_doc("Retail Day Session", session) if isinstance(session, str) else session
	assert_branch_access(doc.branch)

	if doc.status == CLOSED:
		return doc, True
	if doc.status == CANCELLED:
		frappe.throw(_("Session {0} is cancelled").format(frappe.bold(doc.name)), frappe.ValidationError)

	doc.closing_cash = money(closing_cash)
	doc.notes = notes or doc.notes
	doc.closed_by = frappe.session.user
	doc.closed_at = closed_at or now()
	doc.status = CLOSED
	doc.save()
	return doc, False


def session_snapshot(doc):
	return {
		"name": doc.name,
		"branch": doc.branch,
		"warehouse": doc.warehouse,
		"session_date": str(doc.session_date or ""),
		"status": doc.status,
		"opening_float": flt(doc.opening_float),
		"opened_by": doc.opened_by,
		"opened_at": str(doc.opened_at or ""),
		"closed_by": doc.closed_by,
		"closed_at": str(doc.closed_at or ""),
		"closing_cash": flt(doc.closing_cash),
		"expected_cash": flt(doc.expected_cash),
		"variance": flt(doc.variance),
		"invoice_count": cint(doc.invoice_count),
		"sales_total": flt(doc.sales_total),
		"cash_sales": flt(doc.cash_sales),
		"card_sales": flt(doc.card_sales),
		"upi_sales": flt(doc.upi_sales),
		"credit_sales": flt(doc.credit_sales),
		"tax_total": flt(doc.tax_total),
		"discount_total": flt(doc.discount_total),
		"change_total": flt(doc.change_total),
		"notes": doc.notes,
		"offline_uuid": doc.offline_uuid,
		"server_time": server_time(),
	}
