"""What is still owed to each supplier.

Purchases are the submitted purchase receipts (GRNs), goods sent back are the
submitted purchase returns (debit notes) and payments are the submitted
supplier payments; purchases minus returns minus payments is what is
outstanding. A payment without a branch counts for every branch, which is how a
central bank transfer is usually recorded.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from retail_ops.utils import report_branch_filter


def execute(filters=None):
	filters = frappe._dict(filters or {})
	branches = report_branch_filter(filters)
	only_outstanding = bool(filters.get("only_outstanding"))

	columns = [
		{"fieldname": "supplier", "label": _("Supplier"), "fieldtype": "Link", "options": "Retail Supplier", "width": 200},
		{"fieldname": "mobile", "label": _("Mobile"), "fieldtype": "Data", "width": 120},
		{"fieldname": "gstin", "label": _("GSTIN"), "fieldtype": "Data", "width": 140},
		{"fieldname": "purchases", "label": _("Goods Received"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "returns", "label": _("Returned"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "paid", "label": _("Paid"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "outstanding", "label": _("Outstanding"), "fieldtype": "Currency", "width": 140},
		{"fieldname": "oldest_bill", "label": _("Oldest Receipt"), "fieldtype": "Date", "width": 120},
		{"fieldname": "due_days", "label": _("Days Overdue"), "fieldtype": "Int", "width": 110},
		{"fieldname": "last_payment", "label": _("Last Payment"), "fieldtype": "Date", "width": 120},
	]

	if branches is not None and not branches:
		return columns, []

	conditions = ["pr.docstatus = 1"]
	values = {}
	if branches is not None:
		conditions.append("pr.branch in %(branches)s")
		values["branches"] = branches
	if filters.get("supplier"):
		conditions.append("pr.supplier = %(supplier)s")
		values["supplier"] = filters.supplier

	purchases = frappe.db.sql(
		f"""
		select
			pr.supplier,
			sum(pr.total_amount) as purchases,
			min(pr.receipt_date) as oldest_bill,
			max(pr.receipt_date) as last_purchase
		from `tabRetail Purchase Receipt` pr
		where {" and ".join(conditions)}
		group by pr.supplier
		""",
		values,
		as_dict=True,
	)

	payment_conditions = ["sp.docstatus = 1"]
	payment_values = {}
	if branches is not None:
		# a payment booked centrally (no branch) settles the purchase everywhere
		payment_conditions.append("(sp.branch in %(branches)s or ifnull(sp.branch, '') = '')")
		payment_values["branches"] = branches
	if filters.get("supplier"):
		payment_conditions.append("sp.supplier = %(supplier)s")
		payment_values["supplier"] = filters.supplier

	payments = frappe.db.sql(
		f"""
		select sp.supplier, sum(sp.amount) as paid, max(sp.payment_date) as last_payment
		from `tabRetail Supplier Payment` sp
		where {" and ".join(payment_conditions)}
		group by sp.supplier
		""",
		payment_values,
		as_dict=True,
	)
	paid_by_supplier = {row.supplier: row for row in payments}

	return_conditions = ["pret.docstatus = 1"]
	return_values = {}
	if branches is not None:
		return_conditions.append("pret.branch in %(branches)s")
		return_values["branches"] = branches
	if filters.get("supplier"):
		return_conditions.append("pret.supplier = %(supplier)s")
		return_values["supplier"] = filters.supplier

	returns = frappe.db.sql(
		f"""
		select pret.supplier, sum(pret.total_amount) as returned
		from `tabRetail Purchase Return` pret
		where {" and ".join(return_conditions)}
		group by pret.supplier
		""",
		return_values,
		as_dict=True,
	)
	returned_by_supplier = {row.supplier: flt(row.returned) for row in returns}

	masters = {
		row.name: row
		for row in frappe.get_all("Retail Supplier", fields=["name", "mobile", "gstin", "payment_terms_days"])
	}

	today = getdate(nowdate())
	rows = []
	for row in purchases:
		payment = paid_by_supplier.get(row.supplier)
		returned = returned_by_supplier.get(row.supplier, 0)
		outstanding = flt(row.purchases) - returned - flt(payment.paid if payment else 0)
		if only_outstanding and outstanding <= 0.005:
			continue

		master = masters.get(row.supplier) or frappe._dict()
		oldest = getdate(row.oldest_bill) if row.oldest_bill else None
		due_days = 0
		if oldest and outstanding > 0.005:
			due_days = max(0, (today - oldest).days - int(master.get("payment_terms_days") or 0))

		rows.append(
			{
				"supplier": row.supplier,
				"mobile": master.get("mobile") or "",
				"gstin": master.get("gstin") or "",
				"purchases": flt(row.purchases),
				"returns": returned,
				"paid": flt(payment.paid if payment else 0),
				"outstanding": outstanding,
				"oldest_bill": oldest,
				"due_days": due_days,
				"last_payment": getdate(payment.last_payment) if payment and payment.last_payment else None,
			}
		)

	rows.sort(key=lambda row: (-row["outstanding"], row["supplier"]))
	return columns, rows
