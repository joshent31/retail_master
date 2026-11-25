import frappe
from frappe.model.document import Document


class RetailStockBatch(Document):
	def validate(self):
		if not self.item_name and self.product:
			self.item_name = frappe.db.get_value("Retail Product", self.product, "product_name")
		if not self.branch and self.warehouse:
			self.branch = frappe.db.get_value("Retail Warehouse", self.warehouse, "branch")
