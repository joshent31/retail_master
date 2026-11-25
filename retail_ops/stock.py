"""Warehouse level stock for the retail POS.

Stock is a simple running balance per (product, warehouse), updated when a
Retail Invoice is submitted/cancelled. The balance is intentionally
independent from ERPNext Stock Ledger Entries so the POS works without
ERPNext installed; a custom integration can mirror it later.

Batches ride along as a breakdown of the same thing: every receipt records the
batch and expiry it brought in, a sale takes the earliest expiring batch first
(FEFO) and remembers which batch each line came from, and a transfer carries the
batching across with the goods. The warehouse balance stays the authority; the
batches explain where that quantity came from.
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


STOCK_BATCH = "Retail Stock Batch"
DEFAULT_BATCH = "NO BATCH"


def get_batch(product, warehouse, batch_no):
	return frappe.db.get_value(
		STOCK_BATCH,
		{"product": product, "warehouse": warehouse, "batch_no": batch_no},
		["name", "qty", "expiry_date"],
		as_dict=True,
	)


def add_batch_qty(product, warehouse, batch_no, expiry_date=None, qty=0, allow_negative=True):
	"""Add (or take) quantity on one batch of a product in a warehouse."""
	qty = flt(qty, 6)
	if not qty:
		return
	batch_no = (batch_no or "").strip() or DEFAULT_BATCH
	row = get_batch(product, warehouse, batch_no)

	if not row:
		if qty < 0 and not allow_negative:
			frappe.throw(
				_("Batch {0} of {1} has no stock in {2}").format(
					frappe.bold(batch_no), frappe.bold(product), frappe.bold(warehouse)
				),
				frappe.ValidationError,
			)
		warehouse_doc = get_warehouse(warehouse)
		frappe.get_doc(
			{
				"doctype": STOCK_BATCH,
				"product": product,
				"item_name": frappe.db.get_value("Retail Product", product, "product_name"),
				"warehouse": warehouse,
				"branch": warehouse_doc.branch,
				"batch_no": batch_no,
				"expiry_date": expiry_date or None,
				"qty": qty,
			}
		).insert(ignore_permissions=True)
		return

	new_qty = flt(row.qty) + qty
	if new_qty < -0.000001 and not allow_negative:
		frappe.throw(
			_("Batch {0} of {1} in {2} has only {3} left").format(
				frappe.bold(batch_no), frappe.bold(product), frappe.bold(warehouse), flt(row.qty)
			),
			frappe.ValidationError,
		)

	values = {"qty": new_qty}
	if expiry_date and not row.expiry_date:
		values["expiry_date"] = expiry_date
	frappe.db.set_value(STOCK_BATCH, row.name, values, update_modified=False)


def consume_batches(product, warehouse, qty, allow_negative=True):
	"""Take ``qty`` off the batches, earliest expiry first.

	Returns the allocation as a list of ``{batch_no, expiry_date, qty}``, which
	the bill keeps on its line so a return can put the goods back on the same
	batch.
	"""
	qty = flt(qty)
	if qty <= 0:
		return []

	rows = frappe.get_all(
		STOCK_BATCH,
		filters={"product": product, "warehouse": warehouse, "qty": [">", 0]},
		fields=["name", "batch_no", "expiry_date", "qty"],
		order_by="ifnull(expiry_date, '9999-12-31') asc, creation asc",
	)

	remaining = qty
	taken = []
	for row in rows:
		if remaining <= 0.000001:
			break
		take = min(flt(row.qty), remaining)
		frappe.db.set_value(STOCK_BATCH, row.name, "qty", flt(row.qty) - take, update_modified=False)
		taken.append({"batch_no": row.batch_no, "expiry_date": row.expiry_date, "qty": take})
		remaining = flt(remaining - take)

	if remaining > 0.000001:
		# stock entered before batches were tracked, or a branch that sells
		# into negative: keep the batch totals adding up to the warehouse
		add_batch_qty(
			product, warehouse, DEFAULT_BATCH, None, -remaining, allow_negative=allow_negative
		)
		taken.append({"batch_no": DEFAULT_BATCH, "expiry_date": None, "qty": remaining})
	return taken


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
	"""Recompute balances from submitted bills, purchase receipts and transfers.

	Retail Admin only.
	"""
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

	# a transfer changes two warehouses, so either end can be the one being
	# rebuilt; both sides are then filtered down to the requested scope
	transfer_scope = []
	if branch:
		transfer_scope += [["branch", "=", branch], ["to_branch", "=", branch]]
	if warehouse:
		transfer_scope += [["from_warehouse", "=", warehouse], ["to_warehouse", "=", warehouse]]

	transfers = frappe.get_all(
		"Retail Stock Transfer",
		filters={"docstatus": 1},
		or_filters=transfer_scope or None,
		fields=["name", "branch", "to_branch", "from_warehouse", "to_warehouse"],
	)
	transfers = [
		transfer
		for transfer in transfers
		if (not branch or branch in (transfer.branch, transfer.to_branch))
		and (not warehouse or warehouse in (transfer.from_warehouse, transfer.to_warehouse))
	]
	for transfer in transfers:
		items = frappe.get_all(
			"Retail Stock Transfer Item",
			filters={"parent": transfer.name, "parenttype": "Retail Stock Transfer"},
			fields=["product", "qty"],
		)
		# only the side that belongs to the rebuild scope is recomputed; the
		# other side's balance would otherwise be overwritten with a number
		# that ignores its own bills and receipts
		from_scope = (not branch or transfer.branch == branch) and (
			not warehouse or transfer.from_warehouse == warehouse
		)
		to_scope = (not branch or transfer.to_branch == branch) and (
			not warehouse or transfer.to_warehouse == warehouse
		)
		for item in items:
			if from_scope:
				out_key = (item.product, transfer.from_warehouse)
				balances[out_key] = balances.get(out_key, 0) - flt(item.qty)
			if to_scope:
				in_key = (item.product, transfer.to_warehouse)
				balances[in_key] = balances.get(in_key, 0) + flt(item.qty)

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

	return {
		"invoices": len(invoices),
		"receipts": len(receipts),
		"transfers": len(transfers),
		"balances": len(balances),
	}
