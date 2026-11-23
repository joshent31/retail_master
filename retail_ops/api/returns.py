"""Counter returns: look up a bill and post a credit note against it.

A return is an ordinary Retail Invoice with negative quantities and
``is_return = 1``, so stock, day-session totals, reports and the WhatsApp
document all work without special cases. Returns are created online: the
original bill lives on the server, and a credit note needs the authoritative
sold quantities to stop the same item being returned twice.
"""

import frappe
from frappe import _  # noqa: F401  (kept for error messages in callers)
from frappe.utils import cint, flt

from retail_ops.invoices import create_invoice, returned_qty
from retail_ops.utils import assert_branch_access


@frappe.whitelist()
def get_returnable(invoice):
	"""Bill header plus how much of each line can still come back."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to use the POS"), frappe.PermissionError)

	doc = frappe.get_doc("Retail Invoice", invoice)
	if not frappe.has_permission("Retail Invoice", "read", doc):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	assert_branch_access(doc.branch)

	if doc.docstatus != 1:
		frappe.throw(_("Only a submitted bill can be returned"), frappe.ValidationError)
	if cint(doc.is_return):
		frappe.throw(_("This document is already a credit note"), frappe.ValidationError)

	returned = returned_qty(doc.name)
	items = []
	for row in doc.items:
		already = flt(returned.get(row.product))
		available = flt(row.qty) - already
		# the refund price is what was charged after any offer or manual
		# discount, not the rate on the product today
		charged = flt(row.amount) / flt(row.qty) if flt(row.qty) else flt(row.rate)
		items.append(
			{
				"product": row.product,
				"item_name": row.item_name,
				"item_code": row.item_code,
				"uom": row.uom,
				"qty": flt(row.qty),
				"returned_qty": already,
				"returnable_qty": available if available > 0 else 0,
				"rate": flt(charged, 4),
				"list_rate": flt(row.rate),
				"tax_rate": flt(row.tax_rate),
				"total_amount": flt(row.total_amount),
			}
		)

	return {
		"invoice": doc.name,
		"branch": doc.branch,
		"warehouse": doc.warehouse,
		"customer_name": doc.customer_name,
		"customer_mobile": doc.customer_mobile,
		"customer_gstin": doc.customer_gstin,
		"posting_datetime": str(doc.posting_datetime or ""),
		"grand_total": flt(doc.grand_total),
		"items": items,
		"can_return": any(item["returnable_qty"] > 0 for item in items),
	}


@frappe.whitelist()
def create_return(invoice, items, refund_mode="Cash", reason=None):
	"""Post a credit note. ``items`` is a JSON list of {product, qty}."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to use the POS"), frappe.PermissionError)

	rows = frappe.parse_json(items) if isinstance(items, str) else items
	if not rows:
		frappe.throw(_("Pick at least one item to return"), frappe.ValidationError)

	detail = get_returnable(invoice)
	branch_doc = frappe.db.get_value(
		"Retail Invoice",
		invoice,
		["branch", "warehouse", "customer_name", "customer_mobile", "customer_gstin"],
		as_dict=True,
	)

	payload = {
		"branch": branch_doc.branch,
		"warehouse": branch_doc.warehouse,
		"customer_name": branch_doc.customer_name,
		"customer_mobile": branch_doc.customer_mobile,
		"customer_gstin": branch_doc.customer_gstin,
		"is_return": 1,
		"return_against": invoice,
		"refund_mode": refund_mode or "Cash",
		"return_reason": reason or None,
		"remarks": _("Return against {0}").format(invoice),
		"items": [
			{
				"product": row.get("product"),
				"qty": abs(flt(row.get("qty"))),
				"rate": row.get("rate"),
			}
			for row in rows
			if flt(row.get("qty"))
		],
	}
	if not payload["items"]:
		frappe.throw(_("Pick at least one item to return"), frappe.ValidationError)
	if not detail["can_return"]:
		frappe.throw(_("Nothing is left to return on {0}").format(frappe.bold(invoice)))

	result = create_invoice(payload, sync_source="Online")
	result["refund_mode"] = refund_mode or "Cash"
	result["return_against"] = invoice
	return result
