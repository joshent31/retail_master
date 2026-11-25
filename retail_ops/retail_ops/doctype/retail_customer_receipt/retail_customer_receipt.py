"""Money a customer hands over for bills that were left on credit.

A credit bill keeps an ``outstanding_amount`` and nothing else in the app touches
it again. This document is that missing half: each receipt settles the customer's
open bills oldest first, adding to ``collected_amount`` on the bill so a later
validate of the bill still derives the right outstanding figure, and cancelling
the receipt hands the money back.

The allocation is decided on the server, not by the caller, so a till (or a
tampered payload) cannot point money at whichever bill it likes.
"""

import frappe
from frappe import _  # noqa: F401  (used in the messages thrown here)
from frappe.model.document import Document
from frappe.utils import flt, now

from retail_ops.sessions import get_open_session, update_session_totals
from retail_ops.utils import assert_branch_access, get_branch, get_warehouse, money

CUSTOMER = "Retail Customer"
INVOICE = "Retail Invoice"

# Store credit and points cannot be collected at the counter, so they are not
# accepted here. Anything with a reference number (card, UPI, bank) is.
RECEIPT_MODES = ("Cash", "Card", "UPI", "Bank")


# ---------------------------------------------------------------------------
# Dues
# ---------------------------------------------------------------------------
def open_dues(customer, branch=None):
	"""A customer's unpaid bills, oldest first."""
	filters = {
		"customer": customer,
		"docstatus": 1,
		"is_return": 0,
		"outstanding_amount": [">", 0.005],
	}
	if branch:
		filters["branch"] = branch

	return frappe.get_all(
		INVOICE,
		filters=filters,
		fields=[
			"name",
			"branch",
			"posting_datetime",
			"grand_total",
			"paid_amount",
			"collected_amount",
			"outstanding_amount",
		],
		order_by="posting_datetime asc, name asc",
	)


def due_total(customer, branch=None):
	return money(sum(flt(row.outstanding_amount) for row in open_dues(customer, branch)))


# ---------------------------------------------------------------------------
# Writing the settlement back to the bill
# ---------------------------------------------------------------------------
def apply_settlement(invoice, amount):
	"""Move ``amount`` of what is owed on a bill to what has been collected.

	``amount`` is negative when a receipt is cancelled. The bill's own validate
	derives outstanding as ``grand - paid - collected``, so this stays correct
	even if the bill is saved again later.
	"""
	row = frappe.db.get_value(
		INVOICE,
		invoice,
		["name", "docstatus", "grand_total", "paid_amount", "collected_amount"],
		as_dict=True,
	)
	if not row:
		return

	collected = money(flt(row.collected_amount) + flt(amount))
	diff = flt(row.grand_total) - flt(row.paid_amount) - collected
	outstanding = money(max(0, diff))

	values = {"collected_amount": collected, "outstanding_amount": outstanding}
	if row.docstatus == 1:
		if outstanding <= 0.005:
			values["status"] = "Paid"
		elif flt(row.paid_amount) or collected:
			values["status"] = "Partially Paid"
		else:
			values["status"] = "Unpaid"
	frappe.db.set_value(INVOICE, invoice, values, update_modified=False)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class RetailCustomerReceipt(Document):
	def validate(self):
		assert_branch_access(self.branch)
		get_branch(self.branch)
		self.posting_datetime = self.posting_datetime or now()
		self.mode = self.mode or "Cash"
		if self.mode not in RECEIPT_MODES:
			frappe.throw(
				_("A due can be collected by cash, card, UPI or bank transfer"),
				frappe.ValidationError,
			)
		self.reference_no = (self.reference_no or "").strip() or None
		self.amount = money(self.amount)
		if self.amount <= 0:
			frappe.throw(_("A receipt must be more than zero"), frappe.ValidationError)

		customer = frappe.db.get_value(
			CUSTOMER, self.customer, ["customer_name", "mobile", "is_active"], as_dict=True
		)
		if not customer:
			frappe.throw(
				_("Customer {0} does not exist").format(frappe.bold(self.customer)),
				frappe.DoesNotExistError,
			)
		if not customer.is_active:
			frappe.throw(
				_("Customer {0} is inactive").format(frappe.bold(customer.customer_name)),
				frappe.ValidationError,
			)
		# the master owns the name and the number, a typo here must not split
		# the customer's history in two
		self.customer_name = customer.customer_name
		self.customer_mobile = customer.mobile

		if self.warehouse:
			self.warehouse = get_warehouse(self.warehouse, branch=self.branch).name
			session = get_open_session(self.branch, self.warehouse)
			if session:
				self.day_session = session.name
		if not self.day_session:
			self.day_session = frappe.db.get_value(
				"Retail Day Session",
				{"branch": self.branch, "status": "Open"},
				"name",
				order_by="session_date desc, creation desc",
			)

		self.allocate()

	def allocate(self):
		"""Spread the money over the open bills, oldest first."""
		dues = open_dues(self.customer, self.branch)
		total_due = money(sum(flt(row.outstanding_amount) for row in dues))
		if not dues:
			frappe.throw(
				_("{0} has no unpaid bills in this branch").format(
					frappe.bold(self.customer_name or self.customer)
				),
				frappe.ValidationError,
			)

		remaining = flt(self.amount)
		self.set("allocations", [])
		for bill in dues:
			if remaining <= 0.005:
				break
			take = min(flt(bill.outstanding_amount), remaining)
			if take <= 0.005:
				continue
			self.append(
				"allocations",
				{
					"invoice": bill.name,
					"posting_datetime": bill.posting_datetime,
					"grand_total": flt(bill.grand_total),
					"outstanding_before": flt(bill.outstanding_amount),
					"amount": money(take),
				},
			)
			remaining = flt(remaining - take)

		if remaining > 0.005:
			frappe.throw(
				_("{0} only owes {1}, so {2} cannot be collected").format(
					frappe.bold(self.customer_name or self.customer),
					frappe.bold(money(total_due)),
					frappe.bold(money(self.amount)),
				),
				frappe.ValidationError,
			)

		self.total_allocated = money(sum(flt(row.amount) for row in self.allocations))

	def on_submit(self):
		for row in self.allocations:
			still_due = flt(
				frappe.db.get_value(INVOICE, row.invoice, "outstanding_amount")
			)
			if flt(row.amount) > still_due + 0.005:
				frappe.throw(
					_("Bill {0} only has {1} left to collect").format(
						frappe.bold(row.invoice), frappe.bold(money(still_due))
					),
					frappe.ValidationError,
				)
			apply_settlement(row.invoice, row.amount)

		if self.day_session and frappe.db.exists("Retail Day Session", self.day_session):
			update_session_totals(self.day_session)

	def on_cancel(self):
		for row in self.allocations:
			if frappe.db.get_value(INVOICE, row.invoice, "docstatus") != 1:
				# the bill itself was cancelled; there is nothing to reopen
				continue
			apply_settlement(row.invoice, -flt(row.amount))

		if self.day_session and frappe.db.exists("Retail Day Session", self.day_session):
			update_session_totals(self.day_session)
