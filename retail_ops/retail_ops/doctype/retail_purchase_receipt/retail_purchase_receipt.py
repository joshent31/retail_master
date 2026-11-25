"""Purchase receiving (GRN).

Bills only take stock out of a warehouse. This is how stock comes back in:
each receipt adds its quantities to the warehouse balance on submit, so a
warehouse that was received into can be sold from without the balances having
to be corrected by hand.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from retail_ops.stock import add_batch_qty, apply_stock_delta
from retail_ops.utils import assert_branch_access, get_branch, get_warehouse


class RetailPurchaseReceipt(Document):
	def validate(self):
		assert_branch_access(self.branch)
		get_branch(self.branch)
		self.warehouse = get_warehouse(self.warehouse, branch=self.branch).name
		self.receipt_date = self.receipt_date or getdate()
		self.supplier = (self.supplier or "").strip()
		self.supplier_gstin = (self.supplier_gstin or "").strip().upper() or None

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
		self.supplier = supplier.supplier_name
		self.supplier_gstin = self.supplier_gstin or supplier.gstin

		if not self.items:
			frappe.throw(_("A receipt needs at least one item"), frappe.ValidationError)

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
					_("Cost rate for {0} cannot be negative").format(frappe.bold(row.product)),
					frappe.ValidationError,
				)
			if row.product in seen:
				frappe.throw(
					_("{0} is listed twice, combine it into one line").format(frappe.bold(row.product)),
					frappe.ValidationError,
				)
			seen.add(row.product)

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
			row.mrp = flt(row.mrp) or None
			row.batch_no = (row.batch_no or "").strip() or None
			row.expiry_date = row.expiry_date or None
			row.amount = flt(row.qty * row.rate, 2)
			total_qty += row.qty
			total_amount += row.amount

		self.total_qty = flt(total_qty, 3)
		self.total_amount = flt(total_amount, 2)

	def on_submit(self):
		for row in self.items:
			apply_stock_delta(
				row.product,
				self.warehouse,
				flt(row.qty),
				remarks=_("Purchase receipt {0}").format(self.name),
			)
			add_batch_qty(
				row.product,
				self.warehouse,
				row.batch_no,
				row.expiry_date,
				flt(row.qty),
			)

		if cint(self.update_mrp):
			for row in self.items:
				if row.mrp:
					frappe.db.set_value("Retail Product", row.product, "mrp", flt(row.mrp))

	def on_cancel(self):
		# Goods that have already been sold cannot be taken back out of the
		# warehouse, so a receipt can only be cancelled while what it brought in
		# is still on the shelf.
		for row in self.items:
			apply_stock_delta(
				row.product,
				self.warehouse,
				-flt(row.qty),
				allow_negative=False,
				remarks=_("Purchase receipt {0} cancelled").format(self.name),
			)
			add_batch_qty(
				row.product,
				self.warehouse,
				row.batch_no,
				row.expiry_date,
				-flt(row.qty),
				allow_negative=False,
			)
