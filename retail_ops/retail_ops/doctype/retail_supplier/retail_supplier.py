import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class RetailSupplier(Document):
	def validate(self):
		self.supplier_name = (self.supplier_name or "").strip()
		self.gstin = (self.gstin or "").strip().upper() or None
		self.mobile = (self.mobile or "").strip() or None

		if not self.supplier_name:
			frappe.throw(_("A supplier needs a name"), frappe.ValidationError)
		if cint(self.payment_terms_days) < 0:
			frappe.throw(_("Payment terms cannot be negative"), frappe.ValidationError)

		other = frappe.db.get_value(
			"Retail Supplier",
			{"supplier_name": self.supplier_name, "name": ["!=", self.name or ""]},
			"name",
		)
		if other:
			frappe.throw(
				_("Supplier {0} already exists").format(frappe.bold(self.supplier_name)),
				frappe.ValidationError,
			)
