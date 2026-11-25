"""Stock corrections that no sale or receipt explains.

Milk that broke, a crate that crossed its expiry, or the difference a physical
count found: each line carries a signed adjustment, so the same document both
writes stock off and puts a counting error right. The batch breakdown is moved
along with the balance, otherwise the batches would stop adding up to the
warehouse.
"""

import frappe
from frappe import _  # noqa: F401  (used in the messages thrown here)
from frappe.model.document import Document
from frappe.utils import cint, flt, now

from retail_ops.stock import add_batch_qty, apply_stock_delta, consume_batches, get_stock
from retail_ops.utils import assert_branch_access, get_branch, get_warehouse


class RetailStockAdjustment(Document):
	def validate(self):
		assert_branch_access(self.branch)
		branch = get_branch(self.branch)
		self.warehouse = get_warehouse(self.warehouse, branch=self.branch).name
		self.posting_datetime = self.posting_datetime or now()

		if not self.items:
			frappe.throw(_("An adjustment needs at least one item"), frappe.ValidationError)

		seen = set()
		total_qty = 0
		for row in self.items:
			row.qty = flt(row.qty, 6)
			if not row.qty:
				frappe.throw(
					_("Adjustment for {0} cannot be zero").format(frappe.bold(row.product)),
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
			row.batch_no = (row.batch_no or "").strip() or None
			row.expiry_date = row.expiry_date or None

			# informational: what the shelf holds right now, and where it lands
			row.current_qty = flt(get_stock(row.product, self.warehouse))
			row.new_qty = flt(row.current_qty + row.qty)
			if row.new_qty < -0.000001 and not cint(branch.get("allow_negative_stock")):
				frappe.throw(
					_("{0} has {1} in {2}, so it cannot go down by {3}").format(
						frappe.bold(row.product),
						frappe.bold(flt(row.current_qty)),
						frappe.bold(self.warehouse),
						frappe.bold(abs(flt(row.qty))),
					),
					frappe.ValidationError,
				)

			if row.batch_no:
				batch = frappe.db.get_value(
					"Retail Stock Batch",
					{"product": row.product, "warehouse": self.warehouse, "batch_no": row.batch_no},
					["qty", "expiry_date"],
					as_dict=True,
				)
				batch_qty = flt(batch.qty) if batch else 0
				if flt(batch_qty + row.qty) < -0.000001 and not cint(
					branch.get("allow_negative_stock")
				):
					frappe.throw(
						_("Batch {0} of {1} only has {2} left").format(
							frappe.bold(row.batch_no),
							frappe.bold(row.product),
							frappe.bold(batch_qty),
						),
						frappe.ValidationError,
					)

			total_qty += row.qty

		self.total_qty = flt(total_qty, 3)

	def on_submit(self):
		branch = get_branch(self.branch)
		allow_negative = bool(cint(branch.get("allow_negative_stock")))
		self.move_stock(1, allow_negative)

	def on_cancel(self):
		self.move_stock(-1, True)

	def move_stock(self, direction, allow_negative):
		"""Apply the lines; ``direction`` is -1 to undo them."""
		for row in self.items:
			qty = flt(row.qty) * direction
			if not qty:
				continue
			apply_stock_delta(
				row.product,
				self.warehouse,
				qty,
				allow_negative=allow_negative,
				remarks=_("Stock adjustment {0} ({1})").format(self.name, self.reason),
			)
			if row.batch_no:
				add_batch_qty(
					row.product,
					self.warehouse,
					row.batch_no,
					row.expiry_date,
					qty,
					allow_negative=allow_negative,
				)
			elif qty > 0:
				add_batch_qty(
					row.product, self.warehouse, None, None, qty, allow_negative=allow_negative
				)
			else:
				consume_batches(row.product, self.warehouse, -qty, allow_negative=allow_negative)
