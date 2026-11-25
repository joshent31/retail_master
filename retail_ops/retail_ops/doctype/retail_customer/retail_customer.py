import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import validate_email_address


class RetailCustomer(Document):
	def validate(self):
		self.customer_name = (self.customer_name or "").strip()
		self.mobile = (self.mobile or "").strip() or None
		self.gstin = (self.gstin or "").strip().upper() or None
		self.email = (self.email or "").strip() or None

		if not self.customer_name:
			frappe.throw(_("A customer needs a name"), frappe.ValidationError)
		if self.email:
			validate_email_address(self.email, throw=True)
		if self.branch:
			from retail_ops.utils import assert_branch_access, get_branch

			get_branch(self.branch)
			assert_branch_access(self.branch)
