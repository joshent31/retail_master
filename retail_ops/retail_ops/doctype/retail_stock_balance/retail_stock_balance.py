from frappe.model.document import Document


class RetailStockBalance(Document):
	def validate(self):
		if not self.branch:
			from retail_ops.utils import get_warehouse

			self.branch = get_warehouse(self.warehouse).branch
