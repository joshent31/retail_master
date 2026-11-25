"""Customer master lookups for the counter.

The till finds a customer by mobile number instead of scrolling a list, so a
cashier can attach loyalty points without slowing the queue. These calls need a
connection: the points balance must be the server's, otherwise two terminals
could hand out the same points.
"""

import frappe
from frappe import _  # noqa: F401  (used in the messages thrown here)
from frappe.utils import cint, flt, now

from retail_ops.retail_ops.doctype.retail_customer_receipt.retail_customer_receipt import (
	RECEIPT_MODES,
	due_total,
	open_dues,
)

CUSTOMER = "Retail Customer"
RECEIPT = "Retail Customer Receipt"
FIELDS = [
	"name",
	"customer_name",
	"mobile",
	"email",
	"gstin",
	"loyalty_points",
	"total_spent",
	"bills",
	"last_visit",
	"is_active",
]


def _snapshot(row, due=None):
	return {
		"due": flt(due or 0),
		"name": row.name,
		"customer_name": row.customer_name,
		"mobile": row.mobile or "",
		"email": row.email or "",
		"gstin": row.gstin or "",
		"loyalty_points": flt(row.loyalty_points),
		"total_spent": flt(row.total_spent),
		"bills": cint(row.bills),
		"last_visit": str(row.last_visit or ""),
		"is_active": cint(row.is_active),
	}


@frappe.whitelist()
def find_customer(query=None):
	"""Find a customer by mobile number, then by name.

	Returns the best single match (an exact mobile wins) plus any other
	candidates, so the till can show "did you mean" without a second call.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to use the POS"), frappe.PermissionError)

	query = (query or "").strip()
	if not query:
		return {"customer": None, "matches": []}

	if not frappe.has_permission(CUSTOMER, "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	exact = frappe.db.get_value(CUSTOMER, {"mobile": query}, FIELDS, as_dict=True)
	if exact:
		return {"customer": _snapshot(exact, due_total(exact.name)), "matches": []}

	rows = frappe.get_all(
		CUSTOMER,
		filters={"is_active": 1},
		or_filters=[
			["mobile", "like", "%" + query + "%"],
			["customer_name", "like", "%" + query + "%"],
		],
		fields=FIELDS,
		order_by="modified desc",
		limit=10,
	)
	return {
		"customer": _snapshot(rows[0], due_total(rows[0].name)) if rows else None,
		"matches": [_snapshot(r) for r in rows[1:]],
	}


@frappe.whitelist()
def save_customer(customer=None, customer_name=None, mobile=None, gstin=None):
	"""Attach a bill to a customer, creating the record on first visit.

	An existing mobile is never overwritten: the record already on the server
	owns the points, and a typo at the counter must not move them.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to use the POS"), frappe.PermissionError)

	mobile = (mobile or "").strip() or None
	customer_name = (customer_name or "").strip()
	gstin = (gstin or "").strip().upper() or None

	if customer:
		existing = frappe.db.get_value(CUSTOMER, customer, FIELDS, as_dict=True)
		if not existing:
			frappe.throw(_("Customer {0} not found").format(frappe.bold(customer)), frappe.DoesNotExistError)
		return {"customer": _snapshot(existing, due_total(existing.name)), "created": False}

	if mobile:
		existing = frappe.db.get_value(CUSTOMER, {"mobile": mobile}, FIELDS, as_dict=True)
		if existing:
			return {"customer": _snapshot(existing, due_total(existing.name)), "created": False}

	if not mobile or not customer_name:
		# nothing to enrol: the bill simply stays a walk-in sale
		return {"customer": None, "created": False}

	if not frappe.has_permission(CUSTOMER, "create"):
		frappe.throw(_("You are not allowed to add customers"), frappe.PermissionError)

	doc = frappe.new_doc(CUSTOMER)
	doc.customer_name = customer_name
	doc.mobile = mobile
	doc.gstin = gstin
	doc.insert()
	return {"customer": _snapshot(doc, 0), "created": True}


@frappe.whitelist()
def get_dues(customer=None, branch=None):
	"""The unpaid bills a customer can settle at the counter, oldest first."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to use the POS"), frappe.PermissionError)
	if not customer:
		frappe.throw(_("Pick a customer first"), frappe.ValidationError)
	if not frappe.has_permission("Retail Invoice", "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	bills = open_dues(customer, branch or None)
	return {
		"bills": [
			{
				"name": bill.name,
				"branch": bill.branch,
				"posting_datetime": str(bill.posting_datetime or ""),
				"grand_total": flt(bill.grand_total),
				"outstanding": flt(bill.outstanding_amount),
			}
			for bill in bills
		],
		"total": due_total(customer, branch or None),
	}


@frappe.whitelist()
def collect_due(customer=None, amount=None, mode="Cash", reference_no=None, branch=None, warehouse=None):
	"""Take money against a customer's credit bills and settle them.

	The server decides which bills the money goes to (oldest first), so the till
	only has to say who paid and how much.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to use the POS"), frappe.PermissionError)
	if not customer:
		frappe.throw(_("Pick the customer who is paying"), frappe.ValidationError)
	if not branch:
		frappe.throw(_("Pick a branch first"), frappe.ValidationError)
	if not frappe.has_permission(RECEIPT, "create"):
		frappe.throw(_("You are not allowed to collect a due"), frappe.PermissionError)

	mode = mode if mode in RECEIPT_MODES else "Cash"
	doc = frappe.new_doc(RECEIPT)
	doc.customer = customer
	doc.amount = flt(amount)
	doc.mode = mode
	doc.reference_no = reference_no
	doc.branch = branch
	doc.warehouse = warehouse or None
	doc.posting_datetime = now()
	doc.insert()
	doc.submit()

	return {
		"receipt": doc.name,
		"amount": flt(doc.amount),
		"settled": flt(doc.total_allocated),
		"bills": [row.invoice for row in doc.allocations],
		"due": due_total(customer, branch),
	}
