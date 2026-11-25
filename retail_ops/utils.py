import json

import frappe
from frappe import _
from frappe.utils import cint, flt, get_fullname, now

ADMIN_ROLES = {"System Manager", "Retail Admin"}
BRANCH_USER_PERMISSION = "Retail Branch"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def add_service_worker_headers(response):
	"""POS headers that static file serving does not set by itself.

	- sw.js needs `Service-Worker-Allowed: /` to control /retail-pos.
	- manifest.json is stored as .json (so Frappe serves it like any asset) but
	  browsers want the web manifest MIME type.
	"""
	try:
		path = (frappe.local.request.path or "") if getattr(frappe.local, "request", None) else ""
	except Exception:
		path = ""

	if path.endswith("/retail_ops/js/sw.js"):
		response.headers["Service-Worker-Allowed"] = "/"
		response.headers["Cache-Control"] = "no-cache"
	elif path.endswith("/retail_ops/manifest.json"):
		response.headers["Content-Type"] = "application/manifest+json"
		response.headers["Cache-Control"] = "no-cache"
	return response


# ---------------------------------------------------------------------------
# Users / roles
# ---------------------------------------------------------------------------
def is_retail_admin(user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(ADMIN_ROLES.intersection(frappe.get_roles(user)))


def user_branches(user=None):
	"""Branch names the user may access.

	``None`` means "all branches" (retail admin / system manager).
	An empty list means the user has no branch at all.
	"""
	user = user or frappe.session.user
	if user == "Guest":
		return []
	if is_retail_admin(user):
		return None

	cache_key = f"retail_ops:branches:{user}"
	cached = frappe.cache.get_value(cache_key)
	if cached is not None:
		return json.loads(cached)

	branches = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": BRANCH_USER_PERMISSION},
		pluck="for_value",
	)
	frappe.cache.set_value(cache_key, json.dumps(branches), expires_in_sec=300)
	return branches


def clear_user_branch_cache(user=None):
	if user:
		frappe.cache.delete_value(f"retail_ops:branches:{user}")
	else:
		frappe.cache.delete_keys("retail_ops:branches:")


def assert_branch_access(branch, user=None):
	if not branch:
		frappe.throw(_("Branch is required"), frappe.ValidationError)
	branches = user_branches(user)
	if branches is None:
		return
	if branch not in branches:
		frappe.throw(
			_("You do not have access to branch {0}").format(frappe.bold(branch)),
			frappe.PermissionError,
		)


def has_branch_access(branch, user=None):
	branches = user_branches(user)
	return branches is None or branch in branches


def report_branch_filter(filters):
	"""Branch names a report query must be limited to.

	Returns ``None`` when the user may see every branch, or the list of
	allowed branches (possibly empty) otherwise.
	"""
	branch = (filters or {}).get("branch")
	branches = user_branches()
	if branch:
		if branches is not None and branch not in branches:
			frappe.throw(
				_("You do not have access to branch {0}").format(frappe.bold(branch)),
				frappe.PermissionError,
			)
		return [branch]
	return branches


def accessible_branch_names(user=None):
	branches = user_branches(user)
	if branches is not None:
		return branches
	return frappe.get_all("Retail Branch", filters={"is_active": 1}, pluck="name")


# ---------------------------------------------------------------------------
# Branch / warehouse settings
# ---------------------------------------------------------------------------
BRANCH_DEFAULTS = {
	"allow_negative_stock": 1,
	"allow_price_override": 0,
	"allow_post_close_billing": 0,
	"allow_offline_billing": 1,
	"loyalty_enabled": 0,
	"loyalty_earn_per_amount": 100,
	"loyalty_point_value": 1,
	"loyalty_max_percent": 50,
	"receipt_footer": "",
	"currency": None,
}


def get_branch(branch):
	"""Return branch settings as a dict, cached for the duration of the request."""
	if not branch:
		frappe.throw(_("Branch is required"), frappe.ValidationError)
	cache = frappe.local.__dict__.setdefault("retail_ops_branches", {})
	if branch in cache:
		return cache[branch]

	fields = [
		"name",
		"branch_name",
		"branch_code",
		"is_active",
		"gstin",
		"state_code",
		"return_within_days",
		"allow_negative_stock",
		"allow_price_override",
		"allow_post_close_billing",
		"allow_offline_billing",
		"loyalty_enabled",
		"loyalty_earn_per_amount",
		"loyalty_point_value",
		"loyalty_max_percent",
		"receipt_footer",
		"currency",
	]
	doc = frappe.db.get_value("Retail Branch", branch, fields, as_dict=True)
	if not doc:
		frappe.throw(_("Branch {0} does not exist").format(frappe.bold(branch)), frappe.DoesNotExistError)
	if not cint(doc.is_active):
		frappe.throw(_("Branch {0} is inactive").format(frappe.bold(branch)), frappe.ValidationError)

	for key, default in BRANCH_DEFAULTS.items():
		if doc.get(key) is None:
			doc[key] = default
	cache[branch] = doc
	return doc


def get_warehouse(warehouse, branch=None):
	if not warehouse:
		frappe.throw(_("Warehouse is required"), frappe.ValidationError)
	doc = frappe.db.get_value(
		"Retail Warehouse",
		warehouse,
		["name", "warehouse_name", "warehouse_code", "branch", "is_active"],
		as_dict=True,
	)
	if not doc:
		frappe.throw(_("Warehouse {0} does not exist").format(frappe.bold(warehouse)), frappe.DoesNotExistError)
	if branch and doc.branch != branch:
		frappe.throw(
			_("Warehouse {0} does not belong to branch {1}").format(
				frappe.bold(warehouse), frappe.bold(branch)
			),
			frappe.ValidationError,
		)
	if not cint(doc.is_active):
		frappe.throw(_("Warehouse {0} is inactive").format(frappe.bold(warehouse)), frappe.ValidationError)
	return doc


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
def as_dict(value):
	if isinstance(value, str):
		try:
			return json.loads(value)
		except ValueError:
			frappe.throw(_("Invalid JSON payload"), frappe.ValidationError)
	return value or {}


def money(value):
	return flt(value, 2)


def get_currency(branch=None):
	branch_doc = get_branch(branch) if branch else None
	return (branch_doc and branch_doc.currency) or frappe.db.get_single_value(
		"System Settings", "currency"
	) or "USD"


def session_user_name():
	return get_fullname(frappe.session.user) or frappe.session.user


def server_time():
	return now()
