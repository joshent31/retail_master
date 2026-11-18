import frappe
from frappe.model.document import Document


class RetailInvoice(Document):
	"""All business rules are implemented in ``retail_ops.invoices`` and wired
	through ``doc_events`` so they also apply to sync/API-created documents."""

	def before_insert(self):
		# offline_uuid is the sync idempotency key; make sure online bills get one
		# too so the column is never empty and the unique index stays reliable.
		if not self.offline_uuid:
			self.offline_uuid = frappe.generate_hash(length=20)
