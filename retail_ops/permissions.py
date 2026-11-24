"""Role based + user based access control.

* Role based: the DocType permissions (see the doctype JSON files).
* User based: a standard **User Permission** on *Retail Branch* restricts a
  user to one or more branches. Every retail document is then filtered by its
  ``branch`` field.

Retail Admin / System Manager bypass the branch restriction (see ``utils.py``).
"""

import frappe

from retail_ops.utils import (
	assert_branch_access,
	clear_user_branch_cache,
	get_branch,
	has_branch_access,
	user_branches,
)


# ---------------------------------------------------------------------------
# Query conditions (list / report level filtering)
# ---------------------------------------------------------------------------
def _branch_condition(user, doctype, field="branch"):
	branches = user_branches(user)
	if branches is None:
		return ""
	if not branches:
		return "1=0"
	values = ", ".join(frappe.db.escape(branch) for branch in branches)
	return f"`tab{doctype}`.`{field}` in ({values})"


def branch_query_conditions(user):
	return _branch_condition(user, "Retail Branch", field="name")


def warehouse_query_conditions(user):
	return _branch_condition(user, "Retail Warehouse")


def session_query_conditions(user):
	return _branch_condition(user, "Retail Day Session")


def invoice_query_conditions(user):
	return _branch_condition(user, "Retail Invoice")


def stock_query_conditions(user):
	return _branch_condition(user, "Retail Stock Balance")


def purchase_receipt_query_conditions(user):
	return _branch_condition(user, "Retail Purchase Receipt")


def stock_transfer_query_conditions(user):
	return _branch_condition(user, "Retail Stock Transfer")


# ---------------------------------------------------------------------------
# has_permission (single document level)
# ---------------------------------------------------------------------------
def branch_has_permission(doc, ptype, user):
	if not doc or not doc.get("name"):
		# new document -> let the role permission decide
		return None
	return True if has_branch_access(doc.name, user) else False


def warehouse_has_permission(doc, ptype, user):
	branch = doc.get("branch") if doc else None
	if not branch:
		return None
	return True if has_branch_access(branch, user) else False


def document_has_permission(doc, ptype, user):
	branch = doc.get("branch") if doc else None
	if not branch:
		return None
	return True if has_branch_access(branch, user) else False


def clear_cache_after_user_permission_change(doc, method=None):
	if doc.allow == "Retail Branch":
		clear_user_branch_cache(doc.user)
		clear_user_branch_cache()


# ---------------------------------------------------------------------------
# Public API used by the POS front end
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_my_branches():
	"""List the branches the current user may sell from."""
	branches = user_branches()
	filters = {"is_active": 1}
	if branches is not None:
		filters["name"] = ["in", branches or [""]]

	rows = frappe.get_all(
		"Retail Branch",
		filters=filters,
		fields=["name", "branch_name", "branch_code", "currency"],
		order_by="branch_name asc",
	)
	for row in rows:
		row["warehouses"] = frappe.get_all(
			"Retail Warehouse",
			filters={"branch": row.name, "is_active": 1},
			fields=["name", "warehouse_name", "warehouse_code", "is_default"],
			order_by="is_default desc, warehouse_name asc",
		)
	return rows


@frappe.whitelist()
def grant_branch_access(user, branch):
	"""Give a user access to a branch (Retail Manager / Retail Admin only).

	Implemented with a standard *User Permission*, so it also applies to desk
	lists, reports and the REST API.
	"""
	roles = set(frappe.get_roles())
	if not ({"System Manager", "Retail Admin"} & roles):
		frappe.throw(frappe._("Only a Retail Admin can grant branch access"), frappe.PermissionError)

	if not frappe.db.exists("User", user):
		frappe.throw(frappe._("User {0} not found").format(user), frappe.DoesNotExistError)
	if not frappe.db.exists("Retail Branch", branch):
		frappe.throw(frappe._("Branch {0} not found").format(branch), frappe.DoesNotExistError)

	existing = frappe.db.exists("User Permission", {"user": user, "allow": "Retail Branch", "for_value": branch})
	if existing:
		return {"created": False, "user_permission": existing}

	doc = frappe.get_doc(
		{
			"doctype": "User Permission",
			"user": user,
			"allow": "Retail Branch",
			"for_value": branch,
			"apply_to_all_doctypes": 0,
		}
	)
	doc.insert()
	clear_user_branch_cache(user)
	return {"created": True, "user_permission": doc.name}


@frappe.whitelist()
def revoke_branch_access(user, branch):
	roles = set(frappe.get_roles())
	if not ({"System Manager", "Retail Admin"} & roles):
		frappe.throw(frappe._("Only a Retail Admin can revoke branch access"), frappe.PermissionError)
	names = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "Retail Branch", "for_value": branch},
		pluck="name",
	)
	for name in names:
		frappe.delete_doc("User Permission", name)
	clear_user_branch_cache(user)
	return {"revoked": len(names)}


__all__ = [
	"assert_branch_access",
	"get_branch",
	"branch_query_conditions",
	"warehouse_query_conditions",
	"session_query_conditions",
	"invoice_query_conditions",
	"stock_query_conditions",
	"branch_has_permission",
	"warehouse_has_permission",
	"document_has_permission",
	"clear_cache_after_user_permission_change",
	"get_my_branches",
	"grant_branch_access",
	"revoke_branch_access",
]
