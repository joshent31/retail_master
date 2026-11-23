"""Everything the POS needs to render itself, in one call (cached offline)."""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_fullname

from retail_ops.offers import get_offers_for_branches
from retail_ops.permissions import get_my_branches
from retail_ops.sessions import get_open_session, session_snapshot
from retail_ops.stock import get_stock_map
from retail_ops.utils import get_branch, is_retail_admin, server_time
from retail_ops.whatsapp import get_status as get_whatsapp_status

PRODUCT_FIELDS = [
	"name",
	"item_code",
	"barcode",
	"product_name",
	"category",
	"uom",
	"rate",
	"mrp",
	"hsn_code",
	"tax_rate",
	"is_active",
	"image",
]


def get_products(since=None):
	filters = {"is_active": 1}
	if since:
		filters["modified"] = [">", since]
	return frappe.get_all(
		"Retail Product",
		filters=filters,
		fields=PRODUCT_FIELDS,
		order_by="product_name asc",
	)


def get_catalog_version():
	row = frappe.db.sql(
		"""select greatest(
			coalesce((select max(modified) from `tabRetail Product`), '1970-01-01'),
			coalesce((select max(modified) from `tabRetail Stock Balance`), '1970-01-01'),
			coalesce((select max(modified) from `tabRetail Branch`), '1970-01-01'),
			coalesce((select max(modified) from `tabRetail Warehouse`), '1970-01-01')
		)"""
	)
	return str(row[0][0]) if row and row[0][0] else ""


def get_branch_payload(branches):
	payload = []
	for row in branches:
		settings = get_branch(row.name)
		payload.append(
			{
				"name": row.name,
				"branch_name": row.branch_name,
				"branch_code": row.branch_code,
				"currency": settings.get("currency") or row.currency,
				"allow_offline_billing": cint(settings.get("allow_offline_billing")),
				"allow_price_override": cint(settings.get("allow_price_override")),
				"allow_negative_stock": cint(settings.get("allow_negative_stock")),
				"allow_post_close_billing": cint(settings.get("allow_post_close_billing")),
				"return_within_days": cint(settings.get("return_within_days")),
				"gstin": settings.get("gstin") or "",
				"state_code": settings.get("state_code") or "",
				"receipt_footer": settings.get("receipt_footer") or "",
				"warehouses": row.warehouses,
			}
		)
	return payload


def get_open_sessions(branch_names):
	if not branch_names:
		return []
	names = frappe.get_all(
		"Retail Day Session",
		filters={"status": "Open", "branch": ["in", branch_names]},
		pluck="name",
	)
	return [session_snapshot(frappe.get_doc("Retail Day Session", name)) for name in names]


def get_stock_for_branches(branch_names):
	if not branch_names:
		return []
	allowed = set(branch_names)
	return [row for row in get_stock_map() if row.branch in allowed]


def build_catalog(since=None):
	branches = get_my_branches()
	branch_names = [row.name for row in branches]
	return {
		"branches": get_branch_payload(branches),
		"products": get_products(since),
		"offers": get_offers_for_branches(branch_names),
		"stock": get_stock_for_branches(branch_names),
		"open_sessions": get_open_sessions(branch_names),
		"catalog_version": get_catalog_version(),
		"server_time": server_time(),
	}


@frappe.whitelist()
def get_bootstrap():
	"""Initial payload for the POS (also cached in IndexedDB for offline use)."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to use the POS"), frappe.PermissionError)

	catalog = build_catalog()
	roles = frappe.get_roles()

	catalog.update(
		{
			"user": frappe.session.user,
			"full_name": get_fullname(frappe.session.user),
			"roles": [role for role in roles if role.startswith("Retail ") or role == "System Manager"],
			"is_admin": is_retail_admin(),
			"whatsapp": get_whatsapp_status(),
			"can": {
				"create_invoice": bool(frappe.has_permission("Retail Invoice", "create")),
				"submit_invoice": bool(frappe.has_permission("Retail Invoice", "submit")),
				"cancel_invoice": bool(frappe.has_permission("Retail Invoice", "cancel")),
				"create_return": bool(frappe.has_permission("Retail Invoice", "create")),
				"open_day": bool(frappe.has_permission("Retail Day Session", "create")),
				"close_day": bool(frappe.has_permission("Retail Day Session", "write")),
				"edit_catalog": bool(frappe.has_permission("Retail Product", "write")),
				"manage_branches": bool(frappe.has_permission("Retail Branch", "write")),
			},
		}
	)
	return catalog


@frappe.whitelist()
def get_open_day(branch, warehouse):
	"""Current open session for a branch + warehouse (or ``None``)."""
	from retail_ops.utils import assert_branch_access

	assert_branch_access(branch)
	doc = get_open_session(branch, warehouse)
	return session_snapshot(doc) if doc else None


@frappe.whitelist()
def get_receipt(invoice):
	if not frappe.db.exists("Retail Invoice", invoice):
		frappe.throw(_("Invoice {0} not found").format(invoice), frappe.DoesNotExistError)
	doc = frappe.get_doc("Retail Invoice", invoice)
	if not frappe.has_permission("Retail Invoice", "read", doc):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	branch = get_branch(doc.branch)
	return {
		"invoice": doc.name,
		"branch": branch.get("branch_name"),
		"branch_code": branch.get("branch_code"),
		"address": frappe.db.get_value("Retail Branch", doc.branch, "address_line"),
		"phone": frappe.db.get_value("Retail Branch", doc.branch, "phone"),
		"receipt_footer": branch.get("receipt_footer"),
		"currency": branch.get("currency"),
		"customer_name": doc.customer_name,
		"posting_datetime": str(doc.posting_datetime),
		"items": [
			{
				"item_name": row.item_name,
				"qty": flt(row.qty),
				"rate": flt(row.rate),
				"amount": flt(row.total_amount),
			}
			for row in doc.items
		],
		"payments": [{"mode": row.mode, "amount": flt(row.amount)} for row in doc.payments],
		"net_total": flt(doc.net_total),
		"discount_total": flt(doc.discount_total),
		"tax_total": flt(doc.tax_total),
		"grand_total": flt(doc.grand_total),
		"paid_amount": flt(doc.paid_amount),
		"change_amount": flt(doc.change_amount),
		"status": doc.status,
		"cashier": doc.owner,
	}
