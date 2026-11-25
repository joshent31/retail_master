"""Create a supplier record for every name already typed on a receipt.

The receipt's supplier field became a link to the new supplier master. Because
the master is named after the supplier itself, the existing values keep
pointing at the right record once it exists.
"""

import frappe


def execute():
	if not frappe.db.table_exists("Retail Purchase Receipt"):
		return

	names = frappe.db.sql(
		"""select distinct supplier from `tabRetail Purchase Receipt`
		where ifnull(supplier, '') != ''""",
		pluck=True,
	)
	for name in names:
		name = (name or "").strip()
		if not name or frappe.db.exists("Retail Supplier", name):
			continue
		frappe.get_doc({"doctype": "Retail Supplier", "supplier_name": name}).insert(
			ignore_permissions=True
		)
	frappe.db.commit()
