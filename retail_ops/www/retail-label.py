"""Printable shelf / barcode labels: /retail-label?products=A,B&copies=2

The barcode is drawn in the browser from the item code, so the page works on
any printer, including the cheap label printers that only accept an image or a
plain page. Labels are printed per product, ``copies`` times each.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from retail_ops.utils import assert_branch_access, get_branch, get_currency


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to print labels"), frappe.PermissionError)

	context.no_cache = 1
	context.title = _("Price Labels")

	names = [name.strip() for name in (frappe.form_dict.get("products") or "").split(",") if name.strip()]
	if not names:
		context.labels = []
		context.store = ""
		context.error = _("Open a product and use Print Label, or add ?products=<item> to the address.")
		return

	if len(names) > 60:
		names = names[:60]

	copies = max(1, min(cint(frappe.form_dict.get("copies")) or 1, 60))
	branch = frappe.form_dict.get("branch")
	if branch:
		assert_branch_access(branch)
		get_branch(branch)

	context.currency = get_currency(branch) if branch else get_currency()
	context.store = frappe.db.get_value("Retail Branch", branch, "branch_name") if branch else ""
	context.error = None
	context.labels = []

	products = frappe.get_all(
		"Retail Product",
		filters={"name": ["in", names]},
		fields=["name", "item_code", "barcode", "product_name", "mrp", "rate", "tax_rate", "uom"],
	)
	by_name = {row.name: row for row in products}

	for name in names:
		product = by_name.get(name)
		if not product:
			continue
		code = (product.barcode or product.item_code or product.name or "").strip()
		price = flt(product.rate) * (1 + flt(product.tax_rate) / 100)
		label = {
			"name": product.name,
			"item_name": product.product_name or product.name,
			"code": code,
			"uom": product.uom or "",
			"price_text": "{} {:,.2f}".format(context.currency, flt(price, 2)),
			"mrp_text": "{:,.2f}".format(flt(product.mrp, 2)) if flt(product.mrp) else "",
		}
		for _copy in range(copies):
			context.labels.append(label)
