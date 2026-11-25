"""Retail billing.

* Totals (line, tax, discount, payment status) are computed on the server so a
  tampered offline payload cannot invent prices when price override is off.
* Every bill belongs to a day session and is stamped with an ``offline_uuid``
  idempotency key, so retried syncs never double-book a sale or its stock.
"""

import re

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now

from retail_ops.sessions import get_open_session, update_session_totals
from retail_ops.stock import add_batch_qty, apply_stock_delta, consume_batches
from retail_ops.utils import (
	as_dict,
	assert_branch_access,
	get_branch,
	get_warehouse,
	money,
)
from retail_ops.whatsapp import maybe_send_after_submit

# *Loyalty* settles with the customer's points instead of money; it is a
# payment mode rather than a discount so the tax on the bill stays untouched.
PAYMENT_MODES = ("Cash", "Card", "UPI", "Credit", "Loyalty")

# 2 digits state code + PAN + entity + Z + checksum
GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

# Returns are stored as a normal bill with negative quantities, so every
# report that sums qty/amount nets them out without special cases.


# ---------------------------------------------------------------------------
# Controller hook: validate
# ---------------------------------------------------------------------------
def validate_invoice(doc, method=None):
	if not doc.offline_uuid:
		doc.offline_uuid = frappe.generate_hash(length=20)

	assert_branch_access(doc.branch)
	branch = get_branch(doc.branch)
	warehouse = get_warehouse(doc.warehouse, branch=doc.branch)
	resolve_customer(doc)
	doc.customer_gstin = (doc.customer_gstin or "").strip().upper() or None
	if doc.customer_gstin and not GSTIN_PATTERN.match(doc.customer_gstin):
		frappe.throw(
			_("Customer GSTIN {0} does not look like a GST number").format(
				frappe.bold(doc.customer_gstin)
			),
			frappe.ValidationError,
		)
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

	validate_return(doc, branch)
	validate_session(doc, branch)

	if not doc.items:
		frappe.throw(_("A bill needs at least one item"), frappe.ValidationError)

	resolve_tax_type(doc, branch)
	for row in doc.items:
		validate_item(doc, row, branch)

	apply_bill_offer(doc)
	calculate_totals(doc)
	validate_payments(doc)
	validate_loyalty(doc, branch)


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------
def validate_return(doc, branch):
	if not cint(doc.is_return):
		doc.return_against = None
		return

	if not doc.return_against:
		frappe.throw(_("A return needs the bill it is returning"), frappe.ValidationError)

	against = frappe.db.get_value(
		"Retail Invoice",
		doc.return_against,
		[
			"name",
			"docstatus",
			"branch",
			"warehouse",
			"posting_datetime",
			"is_return",
			"customer",
			"customer_name",
			"customer_mobile",
			"customer_gstin",
		],
		as_dict=True,
	)
	if not against:
		frappe.throw(
			_("Bill {0} not found").format(frappe.bold(doc.return_against)), frappe.DoesNotExistError
		)
	if against.docstatus != 1:
		frappe.throw(_("Only a submitted bill can be returned"), frappe.ValidationError)
	if cint(against.is_return):
		frappe.throw(_("A credit note cannot be returned"), frappe.ValidationError)
	if against.branch != doc.branch:
		frappe.throw(
			_("Bill {0} belongs to another branch").format(frappe.bold(against.name)),
			frappe.ValidationError,
		)

	window = cint(branch.get("return_within_days"))
	if window and getdate(doc.posting_datetime) > add_days(getdate(against.posting_datetime), window):
		frappe.throw(
			_("Bill {0} is outside the {1} day return window").format(
				frappe.bold(against.name), window
			),
			frappe.ValidationError,
		)

	# keep the customer on the credit note, the cashier only picks the items
	doc.customer = doc.customer or against.customer
	doc.customer_name = doc.customer_name or against.customer_name
	doc.customer_mobile = doc.customer_mobile or against.customer_mobile
	doc.customer_gstin = doc.customer_gstin or against.customer_gstin
	# stock goes back where it left from, not to the branch default
	if against.warehouse:
		doc.warehouse = against.warehouse

	returned = returned_qty(against.name)
	original = frappe.db.get_all(
		"Retail Invoice Item",
		filters={"parent": against.name, "parenttype": "Retail Invoice"},
		fields=["product", "qty", "rate", "amount", "tax_rate"],
	)
	sold = {row.product: flt(row.qty) for row in original}
	original_lines = {row.product: row for row in original}
	for row in doc.items:
		line = original_lines.get(row.product)
		if not line:
			continue
		# refund what was actually charged: net of the offer/manual discount
		# taken on the bill, at the tax rate that was charged then
		if flt(line.qty):
			row.rate = flt(flt(line.amount) / flt(line.qty), 4)
		row.tax_rate = flt(line.tax_rate)

	for row in doc.items:
		if not row.product:
			continue
		available = flt(sold.get(row.product)) - flt(returned.get(row.product))
		if abs(flt(row.qty)) > available + 0.0005:
			frappe.throw(
				_("Only {0} of {1} can still be returned on {2}").format(
					frappe.bold(available), frappe.bold(row.item_name or row.product), frappe.bold(against.name)
				),
				frappe.ValidationError,
			)


def returned_qty(invoice):
	"""How much of each product has already come back on credit notes."""
	rows = frappe.db.sql(
		"""
		select it.product, sum(it.qty) as qty
		from `tabRetail Invoice Item` it
		inner join `tabRetail Invoice` i on i.name = it.parent
		where i.return_against = %(invoice)s and i.docstatus = 1
		group by it.product
		""",
		{"invoice": invoice},
		as_dict=True,
	)
	# return rows carry negative qty, so flip the sign to get "returned so far"
	return {row.product: -flt(row.qty) for row in rows}


# ---------------------------------------------------------------------------
# GST
# ---------------------------------------------------------------------------
def resolve_tax_type(doc, branch):
	"""CGST+SGST inside the state, IGST across it (GSTIN prefix decides)."""
	state = str(branch.get("state_code") or "").strip()
	customer_state = str(doc.customer_gstin or "")[:2]
	if customer_state.isdigit() and state and customer_state != state:
		doc.tax_type = "Inter-State"
		doc.place_of_supply = customer_state
	else:
		doc.tax_type = "Intra-State"
		doc.place_of_supply = state or customer_state or ""


def split_gst(doc, row):
	tax = flt(row.tax_amount)
	if doc.tax_type == "Inter-State":
		row.cgst_amount = 0
		row.sgst_amount = 0
		row.igst_amount = tax
	else:
		row.cgst_amount = money(tax / 2)
		row.sgst_amount = money(tax - row.cgst_amount)
		row.igst_amount = 0


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


# ---------------------------------------------------------------------------
# Customer (loyalty points live on the customer, not on the bill)
# ---------------------------------------------------------------------------
def resolve_customer(doc):
	"""Link the bill to the customer master, matching on the mobile number."""
	doc.customer_mobile = (doc.customer_mobile or "").strip() or None
	if doc.customer:
		row = frappe.db.get_value(
			"Retail Customer",
			doc.customer,
			["customer_name", "mobile", "gstin", "is_active"],
			as_dict=True,
		)
		if not row:
			frappe.throw(
				_("Customer {0} not found").format(frappe.bold(doc.customer)), frappe.DoesNotExistError
			)
		if not cint(row.is_active):
			frappe.throw(
				_("Customer {0} is inactive").format(frappe.bold(row.customer_name)),
				frappe.ValidationError,
			)
		doc.customer_name = doc.customer_name or row.customer_name
		doc.customer_mobile = doc.customer_mobile or row.mobile
		doc.customer_gstin = doc.customer_gstin or row.gstin
		return

	if not doc.customer_mobile:
		return

	row = frappe.db.get_value(
		"Retail Customer",
		{"mobile": doc.customer_mobile},
		["name", "customer_name", "gstin"],
		as_dict=True,
	)
	if row:
		doc.customer = row.name
		doc.customer_name = doc.customer_name or row.customer_name
		doc.customer_gstin = doc.customer_gstin or row.gstin


# ---------------------------------------------------------------------------
# Bill level offers
# ---------------------------------------------------------------------------
def apply_bill_offer(doc):
	"""Spread one cart level offer across the lines, before tax.

	The discount has to land on the taxable value, not only on the amount
	payable: a bill offer is a trade discount, so the tax and the GSTR-1 taxable
	value both have to come down with it. It is shared by line value, which is
	the fairest split and keeps the bill's own numbers adding up.
	"""
	if cint(doc.is_return):
		doc.bill_discount = 0
		doc.bill_offer = None
		return

	# ``validate`` runs again on submit, and by then the rows already carry
	# their share of the offer; applying it a second time would stack it.
	if getattr(doc, "_bill_offer_applied", False):
		return
	doc._bill_offer_applied = True  # type: ignore[attr-defined]

	from retail_ops.offers import best_bill_offer

	net = money(sum(flt(row.amount) for row in doc.items))
	offer = best_bill_offer(doc.branch, net, on_date=getdate(doc.posting_datetime or now()))
	doc.bill_discount = money(offer["discount"])
	doc.bill_offer = offer["title"] or None
	if not doc.bill_discount or net <= 0:
		return

	remaining = doc.bill_discount
	last = len(doc.items) - 1
	for position, row in enumerate(doc.items):
		if position == last:
			share = remaining
		else:
			share = money(doc.bill_discount * flt(row.amount) / net)
			share = min(share, flt(row.amount), remaining)
		remaining = money(remaining - share)

		row.discount_amount = money(flt(row.discount_amount) + share)
		row.amount = money(flt(row.amount) - share)
		row.tax_amount = money(flt(row.amount) * flt(row.tax_rate) / 100)
		row.total_amount = money(flt(row.amount) + flt(row.tax_amount))
		split_gst(doc, row)


# ---------------------------------------------------------------------------
# Loyalty points
# ---------------------------------------------------------------------------
def earned_points(branch, net_total):
	per = flt(branch.get("loyalty_earn_per_amount"))
	if per <= 0:
		return 0
	return float(int(flt(net_total) / per))


def validate_loyalty(doc, branch):
	"""Points settled at the counter (payment mode *Loyalty*).

	Points pay part of the bill like cash does, so they never change the tax
	on the tax invoice: the bill total stays what the goods cost and the points
	settle it. A return refunds points back with a negative loyalty payment.
	"""
	doc.loyalty_points_used = 0
	doc.loyalty_points_earned = 0

	is_return = bool(cint(doc.is_return))
	enabled = bool(cint(branch.get("loyalty_enabled")))
	point_value = flt(branch.get("loyalty_point_value")) or 1

	paid_with_points = sum(flt(row.amount) for row in doc.payments if row.mode == "Loyalty")
	if paid_with_points:
		if not enabled:
			frappe.throw(
				_("Loyalty points are not enabled for {0}").format(frappe.bold(doc.branch)),
				frappe.ValidationError,
			)
		if not doc.customer:
			frappe.throw(
				_("Pick the customer before paying with points"), frappe.ValidationError
			)

		points = abs(paid_with_points) / point_value
		if abs(points - round(points)) > 0.005:
			frappe.throw(
				_("Points are redeemed in whole numbers — one point is {0}").format(
					frappe.bold(money(point_value))
				),
				frappe.ValidationError,
			)
		points = round(points)

		if is_return:
			doc.loyalty_points_used = -points
		else:
			cap = money(flt(doc.grand_total) * flt(branch.get("loyalty_max_percent")) / 100)
			if flt(paid_with_points) > cap + 0.005:
				frappe.throw(
					_("At most {0} of this bill can be paid with points").format(
						frappe.bold(cap)
					),
					frappe.ValidationError,
				)
			doc.loyalty_points_used = points

	if enabled and doc.customer and not is_return:
		doc.loyalty_points_earned = earned_points(branch, doc.net_total)



def validate_item(doc, row, branch):
	if not row.product:
		frappe.throw(_("Every line needs a product"), frappe.ValidationError)

	product = frappe.db.get_value(
		"Retail Product",
		row.product,
		[
			"item_code",
			"product_name",
			"uom",
			"rate",
			"tax_rate",
			"is_active",
			"hsn_code",
			"category",
			"mrp",
		],
		as_dict=True,
	)
	if not product:
		frappe.throw(_("Product {0} not found").format(row.product), frappe.DoesNotExistError)
	if not cint(product.is_active):
		frappe.throw(_("Product {0} is inactive").format(frappe.bold(row.product)), frappe.ValidationError)

	row.qty = flt(row.qty, 3)
	if cint(doc.is_return):
		row.qty = -abs(row.qty)
		if row.qty >= 0:
			frappe.throw(_("Quantity must be greater than zero for {0}").format(product.product_name))
	elif row.qty <= 0:
		frappe.throw(_("Quantity must be greater than zero for {0}").format(product.product_name))

	row.item_code = product.item_code
	row.item_name = product.product_name
	row.uom = product.uom
	row.hsn_code = product.hsn_code or ""

	if cint(doc.is_return):
		# keep the tax rate (and the net rate) that the original bill charged
		row.tax_rate = flt(row.tax_rate) or flt(product.tax_rate)
		row.rate = flt(row.rate, 4)
	else:
		row.tax_rate = flt(product.tax_rate)
		if cint(branch.get("allow_price_override")):
			row.rate = money(row.rate if row.rate is not None else product.rate)
		else:
			row.rate = money(product.rate)

	if row.rate < 0:
		frappe.throw(_("Rate cannot be negative for {0}").format(product.product_name))

	if not cint(doc.is_return) and flt(product.mrp) and row.rate > flt(product.mrp):
		frappe.throw(
			_("{0} is priced at {1}, above its MRP of {2}").format(
				frappe.bold(product.product_name), frappe.bold(row.rate), frappe.bold(product.mrp)
			),
			frappe.ValidationError,
		)

	line_value = money(row.rate * row.qty)
	if cint(doc.is_return):
		# offers and manual discounts never apply to money going back
		row.discount_amount = 0
		row.amount = line_value
	else:
		from retail_ops.offers import offer_discount

		offer = offer_discount(doc.branch, product, flt(row.qty), line_value)
		manual = min(money(row.discount_amount), max(line_value, 0))
		row.discount_amount = max(manual, money(offer))
		row.amount = money(line_value - row.discount_amount)

	row.tax_amount = money(row.amount * row.tax_rate / 100)
	row.total_amount = money(row.amount + row.tax_amount)
	split_gst(doc, row)


def calculate_totals(doc):
	doc.net_total = money(sum(flt(row.amount) for row in doc.items))
	# the bill offer was already spread into the row discounts
	doc.discount_total = money(sum(flt(row.discount_amount) for row in doc.items))
	doc.tax_total = money(sum(flt(row.tax_amount) for row in doc.items))
	doc.cgst_total = money(sum(flt(row.cgst_amount) for row in doc.items))
	doc.sgst_total = money(sum(flt(row.sgst_amount) for row in doc.items))
	doc.igst_total = money(sum(flt(row.igst_amount) for row in doc.items))
	doc.grand_total = money(doc.net_total + doc.tax_total)

	paid = sum(flt(payment.amount) for payment in doc.payments if payment.mode != "Credit")
	doc.paid_amount = money(paid)

	if cint(doc.is_return):
		# a refund leaves the drawer; nothing changes hands back to the customer
		doc.change_amount = 0
		if doc.refund_mode == "Credit" and not paid:
			# store credit settles in full: no cash is owed either way
			doc.outstanding_amount = 0
			doc.status = "Paid"
			return
		doc.outstanding_amount = money(doc.grand_total - paid)
		if abs(flt(doc.outstanding_amount)) <= 0.005:
			doc.status = "Paid"
		elif flt(doc.paid_amount):
			doc.status = "Partially Paid"
		else:
			doc.status = "Unpaid"
		return

	doc.change_amount = money(max(0, paid - doc.grand_total))
	doc.outstanding_amount = money(max(0, doc.grand_total - paid))

	if doc.outstanding_amount <= 0.005:
		doc.status = "Paid"
	elif doc.paid_amount > 0:
		doc.status = "Partially Paid"
	else:
		doc.status = "Unpaid"


def validate_payments(doc):
	is_return = bool(cint(doc.is_return))
	for payment in doc.payments:
		if payment.mode not in PAYMENT_MODES:
			frappe.throw(_("Unknown payment mode {0}").format(payment.mode), frappe.ValidationError)
		payment.amount = money(payment.amount)
		if not is_return and payment.amount < 0:
			frappe.throw(_("Payment amounts cannot be negative"), frappe.ValidationError)
		if is_return and payment.amount > 0:
			frappe.throw(
				_("A refund is paid out, so its amount is negative"), frappe.ValidationError
			)

	if is_return:
		if not doc.payments and flt(doc.grand_total):
			# whatever the cashier did not hand back stays as store credit
			doc.append(
				"payments",
				{"mode": doc.refund_mode or "Cash", "amount": doc.grand_total},
			)
			calculate_totals(doc)
		return

	# A bill with no payment rows is a full credit sale.
	if not doc.payments and doc.grand_total > 0:
		doc.append("payments", {"mode": "Credit", "amount": doc.grand_total})
		calculate_totals(doc)


# ---------------------------------------------------------------------------
# Controller hooks: submit / cancel
# ---------------------------------------------------------------------------
def on_invoice_submit(doc, method=None):
	branch = get_branch(doc.branch)
	allow_negative = bool(cint(branch.get("allow_negative_stock")))
	move_customer_points(doc, 1)
	for row in doc.items:
		apply_stock_delta(
			row.product,
			doc.warehouse,
			-flt(row.qty),
			allow_negative=allow_negative,
		)
		move_line_batches(doc, row, allow_negative=allow_negative)

	doc.db_set("status", doc.status, update_modified=False)
	update_session_totals(doc.day_session)
	maybe_send_after_submit(doc)


def on_invoice_cancel(doc, method=None):
	move_customer_points(doc, -1)
	for row in doc.items:
		apply_stock_delta(row.product, doc.warehouse, flt(row.qty), allow_negative=True)
		move_line_batches(doc, row, reverse=True)

	doc.db_set("status", "Cancelled", update_modified=False)
	if doc.day_session and frappe.db.exists("Retail Day Session", doc.day_session):
		update_session_totals(doc.day_session)


def move_line_batches(doc, row, allow_negative=True, reverse=False):
	"""Keep the batch breakdown of a line in step with the warehouse balance.

	A sale takes the earliest expiring batch first and stamps the batch on the
	line, so a credit note can put the goods back where they came from. The
	warehouse balance stays authoritative: a line that was billed before batches
	were tracked simply lands on the *no batch* bucket.
	"""
	qty = flt(row.qty)
	if not qty:
		return
	if reverse:
		qty = -qty

	if qty > 0:
		taken = consume_batches(row.product, doc.warehouse, qty, allow_negative=allow_negative)
		if taken and not reverse:
			first = taken[0]
			frappe.db.set_value(
				"Retail Invoice Item",
				row.name,
				{"batch_no": first["batch_no"], "expiry_date": first["expiry_date"]},
				update_modified=False,
			)
	else:
		add_batch_qty(
			row.product, doc.warehouse, row.batch_no, row.expiry_date, -qty
		)


def move_customer_points(doc, direction=1):
	"""Apply (or undo) the loyalty movement of a bill.

	``direction`` is 1 on submit and -1 on cancel. A return carries negative
	``loyalty_points_used``, so the same arithmetic hands the points back
	instead of taking them.
	"""
	if not doc.customer:
		return
	if not frappe.db.exists("Retail Customer", doc.customer):
		return

	row = frappe.db.get_value(
		"Retail Customer", doc.customer, ["loyalty_points", "total_spent", "bills"], as_dict=True
	)
	if not row:
		return

	delta_points = flt(doc.loyalty_points_earned) - flt(doc.loyalty_points_used)
	points = flt(row.loyalty_points) + direction * delta_points
	if direction > 0 and points < -0.0005:
		frappe.throw(
			_("{0} has {1} points, but this bill needs {2}").format(
				frappe.bold(doc.customer), frappe.bold(flt(row.loyalty_points)), frappe.bold(-delta_points)
			),
			frappe.ValidationError,
		)

	bills = cint(row.bills)
	if not cint(doc.is_return):
		bills += direction

	values = {
		"loyalty_points": points,
		"total_spent": flt(row.total_spent) + direction * flt(doc.grand_total),
		"bills": max(bills, 0),
	}
	if direction > 0 and doc.posting_datetime:
		values["last_visit"] = doc.posting_datetime
	frappe.db.set_value("Retail Customer", doc.customer, values, update_modified=False)


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
	doc.customer = data.get("customer")
	doc.customer_name = data.get("customer_name")
	doc.customer_mobile = data.get("customer_mobile")
	doc.customer_gstin = data.get("customer_gstin")
	doc.posting_datetime = data.get("posting_datetime") or now()
	doc.sync_source = data.get("sync_source") or sync_source
	doc.offline_uuid = offline_uuid
	doc.remarks = data.get("remarks")
	doc.is_return = cint(data.get("is_return"))
	# GST expects credit notes to run in their own series, not mixed in with
	# the tax invoices they reverse.
	doc.naming_series = "CN-.YYYY.-" if doc.is_return else "RI-.YYYY.-"
	doc.return_against = data.get("return_against") if doc.is_return else None
	doc.return_reason = data.get("return_reason") if doc.is_return else None
	doc.refund_mode = data.get("refund_mode") if doc.is_return else None

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
		"is_return": cint(doc.is_return),
		"return_against": doc.return_against,
		"refund_mode": doc.refund_mode,
		"server_time": now(),
	}


def invoice_snapshot(doc):
	return {
		"name": doc.name,
		"branch": doc.branch,
		"warehouse": doc.warehouse,
		"day_session": doc.day_session,
		"customer": doc.customer,
		"customer_name": doc.customer_name,
		"customer_mobile": doc.customer_mobile,
		"posting_datetime": str(doc.posting_datetime or ""),
		"grand_total": flt(doc.grand_total),
		"bill_discount": flt(doc.bill_discount),
		"bill_offer": doc.bill_offer,
		"loyalty_points_used": flt(doc.loyalty_points_used),
		"loyalty_points_earned": flt(doc.loyalty_points_earned),
		"paid_amount": flt(doc.paid_amount),
		"outstanding_amount": flt(doc.outstanding_amount),
		"change_amount": flt(doc.change_amount),
		"status": doc.status,
		"docstatus": doc.docstatus,
		"offline_uuid": doc.offline_uuid,
		"is_return": cint(doc.is_return),
		"return_against": doc.return_against,
		"customer_gstin": doc.customer_gstin,
		"tax_type": doc.tax_type,
		"cgst_total": flt(doc.cgst_total),
		"sgst_total": flt(doc.sgst_total),
		"igst_total": flt(doc.igst_total),
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
