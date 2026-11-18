"""Retail billing.

* Totals (line, tax, discount, payment status) are computed on the server so a
  tampered offline payload cannot invent prices when price override is off.
* Every bill belongs to a day session and is stamped with an ``offline_uuid``
  idempotency key, so retried syncs never double-book a sale or its stock.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, now

from retail_ops.sessions import get_open_session, update_session_totals
from retail_ops.stock import apply_stock_delta
from retail_ops.utils import (
	as_dict,
	assert_branch_access,
	get_branch,
	get_warehouse,
	money,
)

PAYMENT_MODES = ("Cash", "Card", "UPI", "Credit")


# ---------------------------------------------------------------------------
# Controller hook: validate
# ---------------------------------------------------------------------------
def validate_invoice(doc, method=None):
	if not doc.offline_uuid:
		doc.offline_uuid = frappe.generate_hash(length=20)

	assert_branch_access(doc.branch)
	branch = get_branch(doc.branch)
	warehouse = get_warehouse(doc.warehouse, branch=doc.branch)
	doc.warehouse = warehouse.name
	doc.posting_datetime = doc.posting_datetime or now()
	doc.sync_source = doc.sync_source or "Online"

	if not doc.day_session:
		open_session = get_open_session(doc.branch, doc.warehouse)
		if not open_session:
			frappe.throw(
				_("Open the day for {0} before billing").format(frappe.bold(doc.warehouse)),
				frappe.ValidationError,
			)
		doc.day_session = open_session.name

	validate_session(doc, branch)

	if not doc.items:
		frappe.throw(_("A bill needs at least one item"), frappe.ValidationError)

	for row in doc.items:
		validate_item(row, branch)

	calculate_totals(doc)
	validate_payments(doc)


def validate_session(doc, branch):
	session = frappe.db.get_value(
		"Retail Day Session",
		doc.day_session,
		["branch", "warehouse", "status", "session_date", "opened_at", "closed_at"],
		as_dict=True,
	)
	if not session:
		frappe.throw(_("Day session {0} not found").format(doc.day_session), frappe.DoesNotExistError)

	if session.branch != doc.branch or session.warehouse != doc.warehouse:
		frappe.throw(
			_("Day session {0} belongs to another branch/warehouse").format(frappe.bold(doc.day_session)),
			frappe.ValidationError,
		)

	if session.status == "Open":
		return

	# Bills queued offline during a day that has since been closed must still
	# post, otherwise a terminal that was offline at closing time would lose
	# sales. They are only accepted for the session's own business date and
	# only when they were created before the day was closed.
	queued_offline = False
	if doc.sync_source == "Offline Sync" and getdate(session.session_date) == getdate(doc.posting_datetime):
		queued_offline = not session.closed_at or get_datetime(doc.posting_datetime) <= get_datetime(
			session.closed_at
		)
	if session.status == "Closed" and (queued_offline or cint(branch.get("allow_post_close_billing"))):
		return

	if session.status == "Closed":
		frappe.throw(
			_("Day session {0} is closed. Open a new day to bill.").format(frappe.bold(doc.day_session)),
			frappe.ValidationError,
		)
	frappe.throw(
		_("Day session {0} is cancelled").format(frappe.bold(doc.day_session)), frappe.ValidationError
	)


def validate_item(row, branch):
	if not row.product:
		frappe.throw(_("Every line needs a product"), frappe.ValidationError)

	product = frappe.db.get_value(
		"Retail Product",
		row.product,
		["item_code", "product_name", "uom", "rate", "tax_rate", "is_active"],
		as_dict=True,
	)
	if not product:
		frappe.throw(_("Product {0} not found").format(row.product), frappe.DoesNotExistError)
	if not cint(product.is_active):
		frappe.throw(_("Product {0} is inactive").format(frappe.bold(row.product)), frappe.ValidationError)

	row.qty = flt(row.qty, 3)
	if row.qty <= 0:
		frappe.throw(_("Quantity must be greater than zero for {0}").format(product.product_name))

	row.item_code = product.item_code
	row.item_name = product.product_name
	row.uom = product.uom
	row.tax_rate = flt(product.tax_rate)

	if cint(branch.get("allow_price_override")):
		row.rate = money(row.rate if row.rate is not None else product.rate)
	else:
		row.rate = money(product.rate)

	if row.rate < 0:
		frappe.throw(_("Rate cannot be negative for {0}").format(product.product_name))

	row.discount_amount = min(money(row.discount_amount), money(row.rate * row.qty))
	row.amount = money(row.rate * row.qty - row.discount_amount)
	row.tax_amount = money(row.amount * row.tax_rate / 100)
	row.total_amount = money(row.amount + row.tax_amount)


def calculate_totals(doc):
	doc.net_total = money(sum(flt(row.amount) for row in doc.items))
	doc.discount_total = money(sum(flt(row.discount_amount) for row in doc.items))
	doc.tax_total = money(sum(flt(row.tax_amount) for row in doc.items))
	doc.grand_total = money(doc.net_total + doc.tax_total)

	paid = sum(flt(payment.amount) for payment in doc.payments if payment.mode != "Credit")
	doc.paid_amount = money(paid)
	doc.change_amount = money(max(0, paid - doc.grand_total))
	doc.outstanding_amount = money(max(0, doc.grand_total - paid))

	if doc.outstanding_amount <= 0.005:
		doc.status = "Paid"
	elif doc.paid_amount > 0:
		doc.status = "Partially Paid"
	else:
		doc.status = "Unpaid"


def validate_payments(doc):
	for payment in doc.payments:
		if payment.mode not in PAYMENT_MODES:
			frappe.throw(_("Unknown payment mode {0}").format(payment.mode), frappe.ValidationError)
		payment.amount = money(payment.amount)
		if payment.amount < 0:
			frappe.throw(_("Payment amounts cannot be negative"), frappe.ValidationError)

	# A bill with no payment rows is a full credit sale.
	if not doc.payments and doc.grand_total > 0:
		doc.append("payments", {"mode": "Credit", "amount": doc.grand_total})
		calculate_totals(doc)


# ---------------------------------------------------------------------------
# Controller hooks: submit / cancel
# ---------------------------------------------------------------------------
def on_invoice_submit(doc, method=None):
	branch = get_branch(doc.branch)
	for row in doc.items:
		apply_stock_delta(
			row.product,
			doc.warehouse,
			-flt(row.qty),
			allow_negative=bool(cint(branch.get("allow_negative_stock"))),
		)

	doc.db_set("status", doc.status, update_modified=False)
	update_session_totals(doc.day_session)


def on_invoice_cancel(doc, method=None):
	for row in doc.items:
		apply_stock_delta(row.product, doc.warehouse, flt(row.qty), allow_negative=True)

	doc.db_set("status", "Cancelled", update_modified=False)
	if doc.day_session and frappe.db.exists("Retail Day Session", doc.day_session):
		update_session_totals(doc.day_session)


# ---------------------------------------------------------------------------
# Creation (online API + offline sync)
# ---------------------------------------------------------------------------
def find_by_offline_uuid(offline_uuid):
	if not offline_uuid:
		return None
	return frappe.db.get_value(
		"Retail Invoice",
		{"offline_uuid": offline_uuid},
		["name", "docstatus", "grand_total", "branch", "day_session", "posting_datetime"],
		as_dict=True,
	)


def create_invoice(payload, sync_source="Online"):
	"""Create + submit a bill. Idempotent on ``offline_uuid``.

	Returns a dict the POS can store as the sync result.
	"""
	data = as_dict(payload)
	offline_uuid = data.get("offline_uuid") or frappe.generate_hash(length=20)

	existing = find_by_offline_uuid(offline_uuid)
	if existing:
		return {
			"status": "duplicate",
			"offline_uuid": offline_uuid,
			"invoice": existing.name,
			"grand_total": flt(existing.grand_total),
		}

	doc = frappe.new_doc("Retail Invoice")
	doc.branch = data.get("branch")
	doc.warehouse = data.get("warehouse")

	# An offline terminal only knows its local session id, which is the
	# session's offline_uuid on the server. Accept either a document name or
	# an offline_uuid here.
	session_key = data.get("day_session")
	if session_key and not frappe.db.exists("Retail Day Session", session_key):
		session_key = (
			frappe.db.get_value("Retail Day Session", {"offline_uuid": session_key}, "name") or session_key
		)
	doc.day_session = session_key
	doc.customer_name = data.get("customer_name")
	doc.customer_mobile = data.get("customer_mobile")
	doc.posting_datetime = data.get("posting_datetime") or now()
	doc.sync_source = data.get("sync_source") or sync_source
	doc.offline_uuid = offline_uuid
	doc.remarks = data.get("remarks")

	for item in data.get("items") or []:
		doc.append(
			"items",
			{
				"product": item.get("product") or item.get("item_code"),
				"qty": item.get("qty"),
				"rate": item.get("rate"),
				"discount_amount": item.get("discount_amount") or 0,
			},
		)

	for payment in data.get("payments") or []:
		doc.append(
			"payments",
			{
				"mode": payment.get("mode") or "Cash",
				"amount": payment.get("amount"),
				"reference_no": payment.get("reference_no"),
			},
		)

	try:
		doc.insert()
		doc.submit()
	except frappe.DuplicateEntryError:
		existing = find_by_offline_uuid(offline_uuid)
		if existing:
			return {
				"status": "duplicate",
				"offline_uuid": offline_uuid,
				"invoice": existing.name,
				"grand_total": flt(existing.grand_total),
			}
		raise

	return {
		"status": "created",
		"offline_uuid": offline_uuid,
		"invoice": doc.name,
		"grand_total": flt(doc.grand_total),
		"status_detail": doc.status,
		"server_time": now(),
	}


def invoice_snapshot(doc):
	return {
		"name": doc.name,
		"branch": doc.branch,
		"warehouse": doc.warehouse,
		"day_session": doc.day_session,
		"customer_name": doc.customer_name,
		"customer_mobile": doc.customer_mobile,
		"posting_datetime": str(doc.posting_datetime or ""),
		"grand_total": flt(doc.grand_total),
		"paid_amount": flt(doc.paid_amount),
		"outstanding_amount": flt(doc.outstanding_amount),
		"change_amount": flt(doc.change_amount),
		"status": doc.status,
		"docstatus": doc.docstatus,
		"offline_uuid": doc.offline_uuid,
		"items": [
			{
				"product": row.product,
				"item_name": row.item_name,
				"qty": flt(row.qty),
				"rate": flt(row.rate),
				"amount": flt(row.amount),
				"tax_amount": flt(row.tax_amount),
				"total_amount": flt(row.total_amount),
			}
			for row in doc.items
		],
		"payments": [
			{"mode": row.mode, "amount": flt(row.amount), "reference_no": row.reference_no}
			for row in doc.payments
		],
	}
