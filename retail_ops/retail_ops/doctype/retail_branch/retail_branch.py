import frappe
from frappe import _
from frappe.model.document import Document


class RetailBranch(Document):
	def validate(self):
		self.branch_code = (self.branch_code or "").strip()
		self.branch_name = (self.branch_name or "").strip()
		default_currency = frappe.db.get_single_value("System Settings", "currency") or "INR"
		self.currency = (self.currency or default_currency).strip().upper()
		if self.branch_code and " " in self.branch_code:
			frappe.throw(_("Branch Code cannot contain spaces"), frappe.ValidationError)
