"""Offline synchronisation.

The POS keeps a queue in IndexedDB. When the network comes back it calls
``push`` with the queue, then ``pull`` to refresh the catalog.

* Every queued bill carries an ``offline_uuid``; the server deduplicates on it,
  so a retried request can never create a second bill or a second stock
  movement.
* Queued day open/close operations are replayed in order (open -> close ->
  invoices) before the bills.
"""

import frappe
from frappe import _
from frappe.utils import flt

from retail_ops.api.bootstrap import build_catalog
from retail_ops.invoices import create_invoice
from retail_ops.sessions import close_session, find_session_by_offline_uuid, open_session, session_snapshot
from retail_ops.utils import as_dict, server_time

MAX_BATCH = 200


@frappe.whitelist()
def push(payload):
	"""Replay the offline queue. Returns one result per queued item."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in"), frappe.PermissionError)

	data = as_dict(payload)
	invoices = (data.get("invoices") or [])[:MAX_BATCH]
	sessions = (data.get("sessions") or [])[:MAX_BATCH]

	results = {"sessions": [], "invoices": [], "server_time": server_time()}

	# Day operations are replayed in the order they happened (open -> close ->
	# open...), and before the bills, because a bill cannot post to a session
	# that does not exist on the server yet.
	for operation in sessions:
		results["sessions"].append(_sync_session(operation))

	for invoice in invoices:
		results["invoices"].append(_sync_invoice(invoice))

	logged_invoices = [row for row in results["invoices"] if row.get("status") in ("created", "duplicate")]
	failed = [row for row in results["invoices"] if row.get("status") == "error"]
	failed += [row for row in results["sessions"] if row.get("status") == "error"]

	if invoices or sessions:
		_log_sync(
			branch=(invoices[0].get("branch") if invoices else None),
			item_count=len(invoices) + len(sessions),
			status="Failed" if failed and not logged_invoices else ("Partial" if failed else "Success"),
			message=_build_message(results),
			client_time=data.get("client_time"),
		)

	if failed:
		frappe.db.commit()
	return results


@frappe.whitelist()
def pull(since=None):
	"""Refresh catalog, stock and open sessions (optionally incremental)."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in"), frappe.PermissionError)
	catalog = build_catalog(since=since)
	catalog["server_time"] = server_time()
	return catalog


@frappe.whitelist()
def status():
	"""Lightweight reachability/clock probe used by the POS."""
	return {"server_time": server_time(), "user": frappe.session.user}


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _sync_session(operation):
	offline_uuid = operation.get("offline_uuid")
	op_type = operation.get("type") or "open"
	frappe.db.savepoint("retail_sync_session")
	try:
		if op_type == "open":
			doc, existing = open_session(
				branch=operation.get("branch"),
				warehouse=operation.get("warehouse"),
				opening_float=flt(operation.get("opening_float")),
				session_date=operation.get("session_date"),
				offline_uuid=offline_uuid,
				opened_at=operation.get("opened_at"),
			)
			return {
				"status": "duplicate" if existing else "created",
				"offline_uuid": offline_uuid,
				"session": doc.name,
				"session_data": session_snapshot(doc),
			}

		session_key = operation.get("session_offline_uuid") or offline_uuid
		session_name = operation.get("session") or find_session_by_offline_uuid(session_key)
		if not session_name:
			frappe.throw(_("Day session not found for this offline close"), frappe.DoesNotExistError)
		doc, already_closed = close_session(
			session_name,
			operation.get("closing_cash") or 0,
			notes=operation.get("notes"),
			closed_at=operation.get("closed_at"),
		)
		return {
			"status": "duplicate" if already_closed else "created",
			"offline_uuid": offline_uuid,
			"session": doc.name,
			"session_data": session_snapshot(doc),
		}
	except Exception as exc:
		frappe.db.rollback(save_point="retail_sync_session")
		_log_error("retail_ops.api.sync._sync_session", exc)
		frappe.clear_messages()
		return {
			"status": "error",
			"offline_uuid": offline_uuid,
			"type": op_type,
			"error": _error_text(exc),
		}


def _sync_invoice(invoice):
	offline_uuid = invoice.get("offline_uuid")
	frappe.db.savepoint("retail_sync_invoice")
	try:
		result = create_invoice(invoice, sync_source="Offline Sync")
		frappe.clear_messages()
		return result
	except Exception as exc:
		frappe.db.rollback(save_point="retail_sync_invoice")
		_log_error("retail_ops.api.sync._sync_invoice", exc)
		frappe.clear_messages()
		return {
			"status": "error",
			"offline_uuid": offline_uuid,
			"invoice": None,
			"error": _error_text(exc),
		}


def _error_text(exc):
	text = str(exc) or exc.__class__.__name__
	return text.replace("<", "&lt;").replace(">", "&gt;")[:400]


def _build_message(results):
	parts = []
	for row in results["sessions"]:
		parts.append(f"session {row.get('offline_uuid')}: {row.get('status')} {row.get('error', '')}")
	for row in results["invoices"]:
		parts.append(f"invoice {row.get('offline_uuid')}: {row.get('status')} {row.get('error', '')}")
	return "\n".join(parts)[:20000]


def _log_error(title, exc):
	frappe.log_error(title=title, message=frappe.get_traceback())


def _log_sync(branch, item_count, status, message, client_time=None):
	try:
		frappe.get_doc(
			{
				"doctype": "Retail Sync Log",
				"naming_series": "SYNC-.YYYY.-",
				"user": frappe.session.user,
				"branch": branch if branch and frappe.db.exists("Retail Branch", branch) else None,
				"direction": "Push",
				"status": status,
				"item_count": item_count,
				"message": message,
				"client_time": client_time,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="retail_ops: could not write sync log")
