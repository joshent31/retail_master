"""Goods sent back to a supplier (a debit note).

The mirror image of a purchase receipt: stock leaves the warehouse, and the
supplier payables report nets the value off what is owed. Linking the return to
the receipt it came from lets the app check that nobody returns more than was
received in the first place.
"""

import frappe
from frappe import _  # noqa: F401  (used in the messages thrown here)
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from retail_ops.stock import add_batch_qty, apply_stock_delta, consume_batches
from retail_ops.utils import assert_branch_access, get_branch, get_warehouse

RECEIPT = "Retail Purchase Receipt"


def returned_qty(receipt, product):
	"""How much of ``product`` has already gone back against ``receipt``."""
	parents = frappe.get_all(
		"Retail Purchase Return",
		filters={"against_receipt": receipt, "docstatus": 1},
		pluck="name",
	)
	if not parents:
		return 0
	rows = frappe.get_all(
		"Retail Purchase Return Item",
		filters={"parent": ["in", parents], "parenttype": "Retail Purchase Return", "product": product},
		fields=["qty"],
	)
	return flt(sum(flt(row.qty) for row in rows))


class RetailPurchaseReturn(Document):
	def validate(self):
		assert_branch_access(self.branch)
		branch = get_branch(self.branch)
		self.warehouse = get_warehouse(self.warehouse, branch=self.branch).name
		self.return_date = self.return_date or getdate()
		self.supplier = (self.supplier or "").strip()

		supplier = frappe.db.get_value(
			"Retail Supplier", self.supplier, ["supplier_name", "gstin", "is_active"], as_dict=True
		)
		if not supplier:
			frappe.throw(
				_("Supplier {0} does not exist").format(frappe.bold(self.supplier)),
				frappe.DoesNotExistError,
			)
		if not cint(supplier.is_active):
			frappe.throw(
				_("Supplier {0} is inactive").format(frappe.bold(supplier.supplier_name)),
				frappe.ValidationError,
			)
		self.supplier_gstin = self.supplier_gstin or supplier.gstin

		if not self.items:
			frappe.throw(_("A return needs at least one item"), frappe.ValidationError)

		received = self.received_quantities()
		seen = set()
		total_qty = 0
		total_amount = 0
		for row in self.items:
			row.qty = flt(row.qty, 6)
			row.rate = flt(row.rate, 4)
			if row.qty <= 0:
				frappe.throw(
					_("Qty for {0} must be more than zero").format(frappe.bold(row.product)),
					frappe.ValidationError,
				)
			if row.rate < 0:
				frappe.throw(
					_("Rate for {0} cannot be negative").format(frappe.bold(row.product)),
					frappe.ValidationError,
				)
			if row.product in seen:
				frappe.throw(
					_("{0} is listed twice, combine it into one line").format(frappe.bold(row.product)),
					frappe.ValidationError,
				)
			seen.add(row.product)

			if received is not None and row.product not in received:
				frappe.throw(
					_("{0} was not on receipt {1}").format(
						frappe.bold(row.product), frappe.bold(self.against_receipt)
					),
					frappe.ValidationError,
				)
			if received is not None and row.qty > flt(received[row.product]) + 0.000001:
				frappe.throw(
					_("Only {0} of {1} is left on receipt {2}").format(
						frappe.bold(flt(received[row.product])),
						frappe.bold(row.product),
						frappe.bold(self.against_receipt),
					),
					frappe.ValidationError,
				)

			product = frappe.db.get_value(
				"Retail Product", row.product, ["item_code", "product_name", "uom"], as_dict=True
			)
			if not product:
				frappe.throw(
					_("Product {0} does not exist").format(frappe.bold(row.product)),
					frappe.DoesNotExistError,
				)
			row.item_code = product.item_code
			row.item_name = product.product_name
			row.uom = product.uom
			row.batch_no = (row.batch_no or "").strip() or None
			row.expiry_date = row.expiry_date or None
			row.amount = flt(row.qty * row.rate, 2)
			total_qty += row.qty
			total_amount += row.amount

		self.total_qty = flt(total_qty, 3)
		self.total_amount = flt(total_amount, 2)

		if not cint(branch.get("allow_negative_stock")):
			for row in self.items:
				on_hand = flt(
					frappe.db.get_value(
						"Retail Stock Balance",
						{"product": row.product, "warehouse": self.warehouse},
						"qty",
					)
				)
				if row.qty > on_hand + 0.000001:
					frappe.throw(
						_("Only {0} of {1} is on hand in {2}, cannot return {3}").format(
							frappe.bold(flt(on_hand)),
							frappe.bold(row.product),
							frappe.bold(self.warehouse),
							frappe.bold(flt(row.qty)),
						),
						frappe.ValidationError,
					)

	def received_quantities(self):
		"""What the linked receipt brought in, minus what already went back.

		``None`` when no receipt is linked, which skips the check.
		"""
		if not self.against_receipt:
			return None

		receipt = frappe.db.get_value(
			RECEIPT,
			self.against_receipt,
			["name", "docstatus", "supplier", "branch"],
			as_dict=True,
		)
		if not receipt:
			frappe.throw(
				_("Receipt {0} not found").format(frappe.bold(self.against_receipt)),
				frappe.DoesNotExistError,
			)
		if receipt.docstatus != 1:
			frappe.throw(
				_("Only a submitted receipt can be returned against"), frappe.ValidationError
			)
		if receipt.supplier != self.supplier:
			frappe.throw(
				_("Receipt {0} belongs to another supplier").format(
					frappe.bold(self.against_receipt)
				),
				frappe.ValidationError,
			)
		if receipt.branch != self.branch:
			frappe.throw(
				_("Receipt {0} belongs to another branch").format(
					frappe.bold(self.against_receipt)
				),
				frappe.ValidationError,
			)

		rows = frappe.get_all(
			"Retail Purchase Receipt Item",
			filters={"parent": receipt.name, "parenttype": RECEIPT},
			fields=["product", "qty"],
		)
		available = {}
		for row in rows:
			available[row.product] = flt(available.get(row.product, 0)) + flt(row.qty)

		for product in list(available):
			available[product] = flt(available[product]) - returned_qty(receipt.name, product)
		return available

	def on_submit(self):
		branch = get_branch(self.branch)
		allow_negative = bool(cint(branch.get("allow_negative_stock")))
		for row in self.items:
			apply_stock_delta(
				row.product,
				self.warehouse,
				-flt(row.qty),
				allow_negative=allow_negative,
				remarks=_("Purchase return {0}").format(self.name),
			)
			if row.batch_no:
				add_batch_qty(
					row.product,
					self.warehouse,
					row.batch_no,
					row.expiry_date,
					-flt(row.qty),
					allow_negative=allow_negative,
				)
			else:
				# no batch named: take it off the earliest expiring one, the same
				# way a sale would
				consume_batches(
					row.product, self.warehouse, flt(row.qty), allow_negative=allow_negative
				)

	def on_cancel(self):
		for row in self.items:
			apply_stock_delta(
				row.product,
				self.warehouse,
				flt(row.qty),
				remarks=_("Purchase return {0} cancelled").format(self.name),
			)
			if row.batch_no:
				add_batch_qty(
					row.product,
					self.warehouse,
					row.batch_no,
					row.expiry_date,
					flt(row.qty),
				)
			else:
				add_batch_qty(row.product, self.warehouse, None, None, flt(row.qty))
