import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate


class RetailOffer(Document):
	def validate(self):
		if flt(self.value) <= 0:
			frappe.throw(_("The discount value must be greater than zero"), frappe.ValidationError)

		if self.discount_type == "Percent" and flt(self.value) > 100:
			frappe.throw(_("A percent discount cannot be more than 100"), frappe.ValidationError)

		if self.apply_on == "Product" and not self.product:
			frappe.throw(_("Pick the product this offer applies to"), frappe.ValidationError)
		if self.apply_on == "Category" and not (self.category or "").strip():
			frappe.throw(_("Type the category this offer applies to"), frappe.ValidationError)

		if self.valid_from and self.valid_to and getdate(self.valid_to) < getdate(self.valid_from):
			frappe.throw(_("Valid To cannot be before Valid From"), frappe.ValidationError)

		self.title = (self.title or "").strip()
		if self.apply_on == "Category":
			self.category = self.category.strip()
		else:
			self.category = None
		if self.apply_on == "Product":
			self.product = self.product
		else:
			self.product = None
		self.min_qty = flt(self.min_qty)
		self.enabled = cint(self.enabled)
