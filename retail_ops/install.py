import frappe

# Role -> description used when the app is installed.
ROLES = {
	"Retail Admin": "Full access to every branch, warehouse, product and report.",
	"Retail Manager": "Branch manager: billing, day open/close, catalog and staff branch access.",
	"Retail Cashier": "POS operator: bill customers and open/close the day for their own branch.",
}


def after_install():
	create_roles()
	after_migrate()


def after_migrate():
	ensure_unique_indexes()


def create_roles():
	for role, description in ROLES.items():
		if frappe.db.exists("Role", role):
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role,
				"desk_access": 1,
			}
		)
		if doc.meta.has_field("description"):
			doc.description = description
		doc.insert(ignore_permissions=True)
	frappe.db.commit()


def ensure_unique_indexes():
	"""Create the indexes a DocType JSON cannot express.

	``offline_uuid`` is already declared ``unique: 1`` in the DocType JSON, so
	Frappe creates its unique index during migrate. Only the composite stock
	unique and the session lookup index are added here.
	"""
	_add_index("tabRetail Stock Balance", "uidx_stock_product_warehouse", "product, warehouse", unique=True)
	_add_index(
		"tabRetail Stock Batch",
		"uidx_batch_product_warehouse_batch",
		"product, warehouse, batch_no",
		unique=True,
	)
	_add_index("tabRetail Invoice", "idx_invoice_day_session", "day_session")
	_add_index("tabRetail Supplier Payment", "idx_supplier_payment_supplier", "supplier")


def _add_index(table, index_name, columns, unique=False):
	try:
		if not frappe.db.table_exists(table.replace("tab", "", 1)):
			return
		if _index_exists(table, index_name):
			return
		kind = "UNIQUE INDEX" if unique else "INDEX"
		frappe.db.sql(f"ALTER TABLE `{table}` ADD {kind} `{index_name}` ({columns})")
	except Exception:
		frappe.log_error(title=f"retail_ops: could not create index {index_name} on {table}")
		return
	frappe.logger("retail_ops").info(f"Created index {index_name} on {table}")


def _index_exists(table, index_name):
	try:
		return bool(frappe.db.has_index(table, index_name))
	except AttributeError:
		pass
	rows = frappe.db.sql(
		"""select 1 from information_schema.statistics
		where table_schema = database() and table_name = %s and index_name = %s""",
		(table, index_name),
	)
	return bool(rows)
