"""Warehouse level stock for the retail POS.

Stock is a simple running balance per (product, warehouse), updated when a
Retail Invoice is submitted/cancelled. The balance is intentionally
independent from ERPNext Stock Ledger Entries so the POS works without
ERPNext installed; a custom integration can mirror it later.
"""

import frappe
from frappe import _
from frappe.utils import flt, now

from retail_ops.utils import get_warehouse


def get_stock(product, warehouse):
	return flt(
		frappe.db.get_value(
			"Retail Stock Balance", {"product": product, "warehouse": warehouse}, "qty"
		)
	)


def get_stock_map(warehouse=None, branch=None):
	filters = {}
	if warehouse:
		filters["warehouse"] = warehouse
	if branch:
		filters["branch"] = branch
	rows = frappe.get_all(
		"Retail Stock Balance",
		filters=filters,
		fields=["product", "warehouse", "branch", "qty"],
	)
	return rows


def apply_stock_delta(product, warehouse, qty_delta, allow_negative=True, remarks=None):
	"""Atomically add ``qty_delta`` to the product/warehouse balance."""
	qty_delta = flt(qty_delta, 6)
	if not qty_delta:
		return
	if not product or not warehouse:
		frappe.throw(_("Product and warehouse are required for a stock movement"))

	warehouse_doc = get_warehouse(warehouse)
	row = frappe.db.sql(
		"""select name, qty from `tabRetail Stock Balance`
		where product = %s and warehouse = %s for update""",
		(product, warehouse),
		as_dict=True,
	)

	if not row:
		doc = frappe.get_doc(
			{
				"doctype": "Retail Stock Balance",
				"product": product,
				"warehouse": warehouse,
				"branch": warehouse_doc.branch,
				"qty": qty_delta,
				"updated_at": now(),
			}
		)
		doc.insert(ignore_permissions=True)
		return

	current = flt(row[0].qty)
	new_qty = current + qty_delta
	if new_qty < -0.000001 and not allow_negative:
		frappe.throw(
			_("Insufficient stock for {0} in {1}: available {2}, required {3}").format(
				frappe.bold(product), frappe.bold(warehouse), current, abs(qty_delta)
			),
			frappe.ValidationError,
		)

	frappe.db.set_value(
		"Retail Stock Balance",
		row[0].name,
		{"qty": new_qty, "updated_at": now(), "branch": warehouse_doc.branch},
		update_modified=False,
	)


@frappe.whitelist()
def get_stock_levels(branch=None, warehouse=None):
	"""Used by the POS to refresh on-hand quantities."""
	from retail_ops.utils import accessible_branch_names, assert_branch_access

	if branch:
		assert_branch_access(branch)
		return get_stock_map(warehouse=warehouse, branch=branch)

	allowed = set(accessible_branch_names())
	return [row for row in get_stock_map(warehouse=warehouse) if row.branch in allowed]


@frappe.whitelist()
def rebuild_stock_balance(branch=None, warehouse=None):
	"""Recompute balances from submitted invoices and purchase receipts. Retail Admin only."""
	if not ({"System Manager", "Retail Admin"} & set(frappe.get_roles())):
		frappe.throw(_("Only a Retail Admin can rebuild stock"), frappe.PermissionError)

	invoice_filters = {"docstatus": 1}
	if branch:
		invoice_filters["branch"] = branch
	if warehouse:
		invoice_filters["warehouse"] = warehouse

	invoices = frappe.get_all("Retail Invoice", filters=invoice_filters, fields=["name", "warehouse"])
	balances = {}
	for invoice in invoices:
		items = frappe.get_all(
			"Retail Invoice Item",
			filters={"parent": invoice.name, "parenttype": "Retail Invoice"},
			fields=["product", "qty"],
		)
		for item in items:
			key = (item.product, invoice.warehouse)
			balances[key] = balances.get(key, 0) - flt(item.qty)

	receipt_filters = {"docstatus": 1}
	if branch:
		receipt_filters["branch"] = branch
	if warehouse:
		receipt_filters["warehouse"] = warehouse

	receipts = frappe.get_all(
		"Retail Purchase Receipt", filters=receipt_filters, fields=["name", "warehouse"]
	)
	for receipt in receipts:
		items = frappe.get_all(
			"Retail Purchase Receipt Item",
			filters={"parent": receipt.name, "parenttype": "Retail Purchase Receipt"},
			fields=["product", "qty"],
		)
		for item in items:
			key = (item.product, receipt.warehouse)
			balances[key] = balances.get(key, 0) + flt(item.qty)

	for (product, warehouse_name), qty in balances.items():
		existing = frappe.db.get_value(
			"Retail Stock Balance", {"product": product, "warehouse": warehouse_name}, "name"
		)
		if existing:
			frappe.db.set_value("Retail Stock Balance", existing, "qty", qty)
		else:
			frappe.get_doc(
				{
					"doctype": "Retail Stock Balance",
					"product": product,
					"warehouse": warehouse_name,
					"branch": get_warehouse(warehouse_name).branch,
					"qty": qty,
					"updated_at": now(),
				}
			).insert(ignore_permissions=True)

	return {"invoices": len(invoices), "receipts": len(receipts), "balances": len(balances)}
