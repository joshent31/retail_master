"""POS endpoints for sending bills on WhatsApp."""

import frappe
from frappe import _

from retail_ops.utils import assert_branch_access
from retail_ops.whatsapp import send_bill


@frappe.whitelist()
def send_invoice(invoice, phone=None, force=False):
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to use the POS"), frappe.PermissionError)
	if not frappe.has_permission("Retail Invoice", "read", invoice):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	doc = frappe.get_doc("Retail Invoice", invoice)
	assert_branch_access(doc.branch)
	return send_bill(doc, number=phone, force=cint_bool(force))


def cint_bool(value):
	return value in (True, 1, "1", "true", "True", "yes")
