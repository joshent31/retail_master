"""Customer master lookups for the counter.

The till finds a customer by mobile number instead of scrolling a list, so a
cashier can attach loyalty points without slowing the queue. These calls need a
connection: the points balance must be the server's, otherwise two terminals
could hand out the same points.
"""

import frappe
from frappe import _  # noqa: F401  (used in the messages thrown here)
from frappe.utils import cint, flt

CUSTOMER = "Retail Customer"
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


def _snapshot(row):
	return {
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
		return {"customer": _snapshot(exact), "matches": []}

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
	return {"customer": _snapshot(rows[0]) if rows else None, "matches": [_snapshot(r) for r in rows[1:]]}


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
		return {"customer": _snapshot(existing), "created": False}

	if mobile:
		existing = frappe.db.get_value(CUSTOMER, {"mobile": mobile}, FIELDS, as_dict=True)
		if existing:
			return {"customer": _snapshot(existing), "created": False}

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
	return {"customer": _snapshot(doc), "created": True}
