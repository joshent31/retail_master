import frappe
from frappe import _
from frappe.model.document import Document


class RetailWarehouse(Document):
	def validate(self):
		self.warehouse_code = (self.warehouse_code or "").strip()
		self.warehouse_name = (self.warehouse_name or "").strip()
		if not frappe.db.exists("Retail Branch", self.branch):
			frappe.throw(_("Branch {0} does not exist").format(self.branch), frappe.DoesNotExistError)

		if self.is_default:
			other = frappe.db.get_value(
				"Retail Warehouse",
				{"branch": self.branch, "is_default": 1, "name": ["!=", self.name or ""]},
				"name",
			)
			if other:
				frappe.throw(
					_("Warehouse {0} is already the default for {1}").format(
						frappe.bold(other), frappe.bold(self.branch)
					),
					frappe.ValidationError,
				)
