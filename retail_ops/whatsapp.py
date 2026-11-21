"""Send a retail bill as a PDF to the customer's WhatsApp number.

WhatsApp business-initiated conversations must use a pre-approved template
outside the 24 hour customer service window, so the flow is:

1. render the bill to PDF with Frappe's wkhtmltopdf wrapper,
2. upload that PDF to WhatsApp media (returns a media id),
3. send the configured template with a ``document`` header, or a plain
   document message when no template is configured.

Credentials and the template name live in the ``Retail WhatsApp Settings``
single doctype. Every attempt is recorded on the invoice
(``whatsapp_status``/``whatsapp_sent_at``/``whatsapp_error``).
"""

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt, fmt_money, now

from retail_ops.utils import get_branch

SETTINGS_DOCTYPE = "Retail WhatsApp Settings"
GRAPH_URL = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v23.0"
REQUEST_TIMEOUT = 30

BODY_VALUES = {
	"customer_name": lambda doc, branch: doc.customer_name or _("Customer"),
	"invoice_no": lambda doc, branch: doc.name,
	"grand_total": lambda doc, branch: fmt_money(flt(doc.grand_total), currency=branch.get("currency")),
	"outstanding": lambda doc, branch: fmt_money(flt(doc.outstanding_amount), currency=branch.get("currency")),
	"branch": lambda doc, branch: branch.get("branch_name") or doc.branch,
	"date": lambda doc, branch: str(doc.posting_datetime or now()),
}


class WhatsAppError(frappe.ValidationError):
	pass


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def get_settings():
	return frappe.get_single(SETTINGS_DOCTYPE)


def get_status():
	"""Connection state for the POS (also cached offline with the catalog)."""
	values = (
		frappe.db.get_value(
			SETTINGS_DOCTYPE,
			SETTINGS_DOCTYPE,
			["enabled", "auto_send", "phone_number_id"],
			as_dict=True,
		)
		or {}
	)
	return {
		"enabled": bool(cint(values.get("enabled")) and values.get("phone_number_id")),
		"auto_send": bool(cint(values.get("auto_send"))),
	}


def resolve_number(value, settings=None):
	"""Best effort E.164 number: digits only, with the default country code."""
	settings = settings or get_settings()
	raw = str(value or "").strip()
	digits = "".join(char for char in raw if char.isdigit())
	if not digits:
		return ""
	if raw.startswith("+"):
		return digits
	if digits.startswith("00"):
		return digits[2:]

	country_code = str(settings.default_country_code or "").strip().lstrip("+")
	if country_code and digits.startswith(country_code) and len(digits) > len(country_code) + 5:
		return digits
	digits = digits.lstrip("0")
	if country_code and len(digits) <= 10:
		return country_code + digits
	return digits


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _request(method, url, settings, **kwargs):
	headers = {"Authorization": "Bearer {0}".format(settings.get_password("access_token"))}
	try:
		response = requests.request(method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
	except requests.RequestException as exc:
		raise WhatsAppError(_("WhatsApp request failed: {0}").format(exc)) from exc

	try:
		payload = response.json()
	except ValueError:
		payload = {}

	if response.status_code >= 400:
		error = payload.get("error") or {}
		message = error.get("message") or response.text or response.reason
		details = error.get("error_data")
		if isinstance(details, dict) and details.get("details"):
			message = "{0} - {1}".format(message, details["details"])
		raise WhatsAppError(str(message)[:500])
	return payload


def _api_url(settings, path):
	return "{0}/{1}/{2}/{3}".format(
		GRAPH_URL,
		settings.api_version or DEFAULT_API_VERSION,
		settings.phone_number_id,
		path,
	)


# ---------------------------------------------------------------------------
# Bill PDF
# ---------------------------------------------------------------------------
def get_bill_pdf(doc):
	branch = get_branch(doc.branch)
	currency = branch.get("currency")
	template_path = frappe.get_app_path("retail_ops", "templates", "print_formats", "retail_bill.html")
	with open(template_path, encoding="utf-8") as handle:
		template = handle.read()
	html = frappe.render_template(
		template,
		{
			"doc": doc,
			"branch": branch,
			"currency": currency,
			"money": lambda value: fmt_money(flt(value), currency=currency),
			"printed_on": now(),
		},
	)
	from frappe.utils.pdf import get_pdf

	pdf = get_pdf(
		html,
		options={
			"page-size": "A5",
			"margin-top": "8mm",
			"margin-bottom": "8mm",
			"margin-left": "8mm",
			"margin-right": "8mm",
		},
	)
	return pdf, "{0}.pdf".format(doc.name)


def upload_media(settings, pdf, filename):
	payload = _request(
		"POST",
		_api_url(settings, "media"),
		settings,
		data={"messaging_product": "whatsapp", "type": "application/pdf"},
		files={"file": (filename, pdf, "application/pdf")},
	)
	media_id = payload.get("id")
	if not media_id:
		raise WhatsAppError(_("WhatsApp did not return a media id"))
	return media_id


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def build_message(settings, doc, number, media_id, filename):
	branch = get_branch(doc.branch)
	if not settings.template_name:
		return {
			"messaging_product": "whatsapp",
			"recipient_type": "individual",
			"to": number,
			"type": "document",
			"document": {
				"id": media_id,
				"filename": filename,
				"caption": _("Bill {0}").format(doc.name),
			},
		}

	components = [
		{
			"type": "header",
			"parameters": [{"type": "document", "document": {"id": media_id, "filename": filename}}],
		}
	]
	parameters = []
	for key in str(settings.body_parameters or "").split(","):
		builder = BODY_VALUES.get(key.strip())
		if builder:
			parameters.append({"type": "text", "text": str(builder(doc, branch))})
	if parameters:
		components.append({"type": "body", "parameters": parameters})

	return {
		"messaging_product": "whatsapp",
		"recipient_type": "individual",
		"to": number,
		"type": "template",
		"template": {
			"name": settings.template_name,
			"language": {"code": settings.template_language or "en"},
			"components": components,
		},
	}


def mark(doc, status, number=None, message_id=None, error=None):
	frappe.db.set_value(
		"Retail Invoice",
		doc.name,
		{
			"whatsapp_status": status,
			"whatsapp_sent_at": now() if status == "Sent" else None,
			"whatsapp_number": number,
			"whatsapp_message_id": message_id,
			"whatsapp_error": error,
		},
		update_modified=False,
	)


def send_bill(doc, number=None, force=False):
	"""Send one submitted bill. Returns the outcome for the POS."""
	settings = get_settings()
	if not cint(settings.enabled):
		frappe.throw(_("WhatsApp is not enabled in Retail WhatsApp Settings"))
	if doc.docstatus != 1:
		frappe.throw(_("Only submitted bills can be sent on WhatsApp"))
	if doc.whatsapp_status == "Sent" and not force:
		return {"status": "already_sent", "phone": doc.whatsapp_number, "message_id": doc.whatsapp_message_id}

	number = resolve_number(number or doc.customer_mobile, settings)
	if not number:
		frappe.throw(_("Bill {0} has no valid customer mobile number").format(doc.name))

	try:
		pdf, filename = get_bill_pdf(doc)
		media_id = upload_media(settings, pdf, filename)
		payload = _request(
			"POST",
			_api_url(settings, "messages"),
			settings,
			json=build_message(settings, doc, number, media_id, filename),
		)
	except Exception as exc:
		# Return instead of raising: an exception would roll back the commit
		# that records the failure on the invoice.
		mark(doc, "Failed", number, error=str(exc))
		frappe.log_error(
			title=_("WhatsApp bill {0}").format(doc.name),
			message="{0}\n\n{1}".format(number, exc),
		)
		return {"status": "failed", "error": str(exc), "phone": number}

	messages = payload.get("messages") or []
	message_id = (messages[0].get("id") if messages else "") or ""
	mark(doc, "Sent", number, message_id=message_id)
	return {"status": "sent", "message_id": message_id, "phone": number}


def send_bill_job(invoice):
	"""Background entry point used by auto-send."""
	doc = frappe.get_doc("Retail Invoice", invoice)
	try:
		send_bill(doc)
	except Exception:
		frappe.log_error(
			title=_("WhatsApp bill {0}").format(invoice),
			message=frappe.get_traceback(),
		)
	finally:
		frappe.db.commit()


def maybe_send_after_submit(doc):
	"""Queue an automatic send when enabled; the message goes out after commit."""
	settings = get_settings()
	if not (cint(settings.enabled) and cint(settings.auto_send)):
		return
	if not doc.customer_mobile or doc.whatsapp_status == "Sent":
		return
	frappe.enqueue(
		"retail_ops.whatsapp.send_bill_job",
		queue="short",
		timeout=300,
		enqueue_after_commit=True,
		invoice=doc.name,
	)
