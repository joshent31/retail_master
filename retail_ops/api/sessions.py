import frappe
from frappe import _
from frappe.utils import now

from retail_ops.sessions import (
	close_session,
	get_open_session,
	open_session,
	session_snapshot,
	update_session_totals,
)
from retail_ops.utils import assert_branch_access


@frappe.whitelist()
def open_day(branch, warehouse, opening_float=0, session_date=None, offline_uuid=None):
	"""Open the business day (idempotent: returns the existing open session)."""
	if not frappe.has_permission("Retail Day Session", "create"):
		frappe.throw(_("You are not allowed to open the day"), frappe.PermissionError)

	doc, existing = open_session(
		branch,
		warehouse,
		opening_float=opening_float,
		session_date=session_date,
		offline_uuid=offline_uuid,
	)
	return {"session": session_snapshot(doc), "existing": existing}


@frappe.whitelist()
def close_day(session, closing_cash=0, notes=None):
	"""Close the day. Totals are recomputed from the submitted bills."""
	if not frappe.has_permission("Retail Day Session", "write"):
		frappe.throw(_("You are not allowed to close the day"), frappe.PermissionError)

	doc, already_closed = close_session(session, closing_cash, notes=notes)
	return {"session": session_snapshot(doc), "already_closed": already_closed}


@frappe.whitelist()
def get_day(session):
	doc = frappe.get_doc("Retail Day Session", session)
	assert_branch_access(doc.branch)
	doc = update_session_totals(doc.name)
	return session_snapshot(doc)


@frappe.whitelist()
def list_my_days(branch=None, warehouse=None, limit=30):
	from retail_ops.utils import accessible_branch_names

	branches = accessible_branch_names()
	filters = {"branch": ["in", branches or [""]]}
	if branch:
		assert_branch_access(branch)
		filters["branch"] = branch
	if warehouse:
		filters["warehouse"] = warehouse

	sessions = frappe.get_all(
		"Retail Day Session",
		filters=filters,
		fields=["name", "branch", "warehouse", "session_date", "status", "sales_total", "variance"],
		order_by="session_date desc, creation desc",
		limit_page_length=int(limit),
	)
	return sessions


@frappe.whitelist()
def close_stale_day(branch, warehouse, closing_cash=0, notes=None):
	"""Close the day that is currently open for a branch + warehouse."""
	assert_branch_access(branch)
	doc = get_open_session(branch, warehouse)
	if not doc:
		return {"session": None, "message": "No open day"}
	closed, already = close_session(doc.name, closing_cash, notes or f"Auto closed at {now()}")
	return {"session": session_snapshot(closed), "already_closed": already}
