import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/retail-pos"
		raise frappe.Redirect

	context.no_cache = 1
	context.page_title = "Retail POS"
	context.full_name = frappe.utils.get_fullname(frappe.session.user)
	context.user = frappe.session.user
	context.csrf_token = _csrf_token()
	context.roles = frappe.get_roles()
	context.branch_count = frappe.db.count("Retail Branch", {"is_active": 1})

	if not context.branch_count:
		context.setup_hint = (
			"No branch is configured yet. Sign in to Desk and create a Retail Branch, "
			"a Retail Warehouse for it and at least one Retail Product."
		)
	else:
		context.setup_hint = ""


def _csrf_token():
	try:
		from frappe.sessions import get_csrf_token

		return get_csrf_token()
	except Exception:
		return frappe.local.session.data.csrf_token
