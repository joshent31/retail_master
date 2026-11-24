"""Move stock between warehouses.

A bill takes stock out and a purchase receipt brings stock in; a transfer is
how goods get from one shelf to another without either. Both warehouse
balances are kept in step: the quantity leaves the source on submit and
arrives at the destination, and cancelling a transfer puts it back where it
came from.
"""

import frappe
from frappe import _  # noqa: F401  (kept for error messages in callers)
from frappe.model.document import Document
from frappe.utils import flt, getdate

from retail_ops.stock import apply_stock_delta, get_stock
from retail_ops.utils import assert_branch_access, get_branch, get_warehouse


class RetailStockTransfer(Document):
	def validate(self):
		# both ends have to be reachable, otherwise stock could be pushed into a
		# branch the user cannot see again
		get_branch(self.branch)
		get_branch(self.to_branch)
		assert_branch_access(self.branch)
		assert_branch_access(self.to_branch)

		self.from_warehouse = get_warehouse(self.from_warehouse, branch=self.branch).name
		self.to_warehouse = get_warehouse(self.to_warehouse, branch=self.to_branch).name
		self.transfer_date = self.transfer_date or getdate()

		if self.from_warehouse == self.to_warehouse:
			frappe.throw(
				_("A transfer needs two different warehouses"), frappe.ValidationError
			)
		if not self.items:
			frappe.throw(_("A transfer needs at least one item"), frappe.ValidationError)

		seen = set()
		total_qty = 0
		for row in self.items:
			row.qty = flt(row.qty, 6)
			if row.qty <= 0:
				frappe.throw(
					_("Qty for {0} must be more than zero").format(frappe.bold(row.product)),
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
			total_qty += row.qty

		self.total_qty = flt(total_qty, 3)

	def _assert_stock(self, warehouse):
		"""Check every line before moving any of them.

		Otherwise a transfer of several products could half-apply when the last
		line is short, leaving the two warehouses out of step.
		"""
		for row in self.items:
			available = get_stock(row.product, warehouse)
			if flt(row.qty) > available + 0.000001:
				frappe.throw(
					_("Insufficient stock for {0} in {1}: available {2}, required {3}").format(
						frappe.bold(row.product),
						frappe.bold(warehouse),
						available,
						flt(row.qty),
					),
					frappe.ValidationError,
				)

	def on_submit(self):
		self._assert_stock(self.from_warehouse)
		for row in self.items:
			apply_stock_delta(
				row.product,
				self.from_warehouse,
				-flt(row.qty),
				allow_negative=False,
				remarks=_("Stock transfer {0}").format(self.name),
			)
			apply_stock_delta(
				row.product,
				self.to_warehouse,
				flt(row.qty),
				remarks=_("Stock transfer {0}").format(self.name),
			)

	def on_cancel(self):
		# the goods may already have been sold at the destination, so the whole
		# transfer is refused rather than moved back in part
		self._assert_stock(self.to_warehouse)
		for row in self.items:
			apply_stock_delta(
				row.product,
				self.to_warehouse,
				-flt(row.qty),
				allow_negative=False,
				remarks=_("Stock transfer {0} cancelled").format(self.name),
			)
			apply_stock_delta(
				row.product,
				self.from_warehouse,
				flt(row.qty),
				remarks=_("Stock transfer {0} cancelled").format(self.name),
			)
