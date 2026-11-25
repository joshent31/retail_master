"""Money paid to a supplier.

Kept as its own submitted document instead of a field on the receipt, so a
payment can settle several receipts, and so cancelling one puts the payable
back where it was.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from retail_ops.utils import assert_branch_access, get_branch, money


class RetailSupplierPayment(Document):
	def validate(self):
		self.payment_date = self.payment_date or getdate()
		self.amount = money(self.amount)
		self.reference_no = (self.reference_no or "").strip() or None

		supplier = frappe.db.get_value(
			"Retail Supplier", self.supplier, ["supplier_name", "is_active"], as_dict=True
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

		if flt(self.amount) <= 0:
			frappe.throw(_("A payment must be more than zero"), frappe.ValidationError)

		if self.branch:
			get_branch(self.branch)
			assert_branch_access(self.branch)
