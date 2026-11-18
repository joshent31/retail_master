from frappe.model.document import Document


class RetailDaySession(Document):
	"""Validation lives in ``retail_ops.sessions`` (hooked via doc_events) so the
	same rules apply to the desk form, the REST API and the offline sync."""

	def open_session(self, opening_float=0):
		from retail_ops.sessions import open_session

		return open_session(self.branch, self.warehouse, opening_float)

	def close_session(self, closing_cash, notes=None):
		from retail_ops.sessions import close_session

		return close_session(self.name, closing_cash, notes)
