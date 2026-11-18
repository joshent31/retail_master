import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class RetailProduct(Document):
	def validate(self):
		self.item_code = (self.item_code or "").strip()
		self.barcode = (self.barcode or "").strip() or None
		self.rate = flt(self.rate)
		self.tax_rate = flt(self.tax_rate)
		if self.rate < 0:
			frappe.throw(_("Rate cannot be negative"), frappe.ValidationError)
		if self.tax_rate < 0 or self.tax_rate > 100:
			frappe.throw(_("Tax rate must be between 0 and 100"), frappe.ValidationError)
