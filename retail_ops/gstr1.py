"""GSTR-1 as the GST portal's offline utility reads it.

The tax summary report shows the numbers; this turns the same bills into the
JSON file that is uploaded (or keyed in through the offline tool) for the
monthly return. Returns land in the credit note sections with positive values,
the way the form expects them.

The file is a starting point for filing, not a substitute for checking it: the
portal validates HSN digits, place of supply and UQC codes against the current
year's rules, so upload it and read the errors back.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from retail_ops.utils import report_branch_filter

UQC = "OTH"


def _money(value):
	return flt(value, 2)


def _date(value):
	return getdate(value).strftime("%d-%m-%Y")


def _tax_period(to_date):
	day = getdate(to_date)
	return "%02d%04d" % (day.month, day.year)


def _gstin(branch):
	if branch:
		gstin = frappe.db.get_value("Retail Branch", branch, "gstin")
	else:
		gstin = frappe.db.get_value("Retail Branch", {"is_active": 1}, "gstin")
	if not gstin:
		frappe.throw(
			_("Set the GSTIN on the branch before filing"), frappe.ValidationError
		)
	return gstin


def _invoice_rows(from_date, to_date, branches):
	conditions = [
		"i.docstatus = 1",
		"i.posting_datetime >= %(from_date)s",
		"i.posting_datetime < %(to_date)s",
	]
	values = {"from_date": from_date, "to_date": add_days(getdate(to_date), 1)}
	if branches is not None:
		conditions.append("i.branch in %(branches)s")
		values["branches"] = branches

	return frappe.db.sql(
		f"""
		select
			i.name,
			i.posting_datetime,
			i.customer_gstin,
			i.customer_name,
			i.grand_total,
			i.place_of_supply,
			i.is_return,
			it.hsn_code,
			it.tax_rate,
			it.qty,
			it.amount,
			it.tax_amount,
			it.cgst_amount,
			it.sgst_amount,
			it.igst_amount,
			it.item_name
		from `tabRetail Invoice` i
		inner join `tabRetail Invoice Item` it on it.parent = i.name
		where {" and ".join(conditions)}
		order by i.posting_datetime asc, i.name asc
		""",
		values,
		as_dict=True,
	)


def _group_invoices(rows):
	invoices = {}
	for row in rows:
		invoice = invoices.get(row.name)
		if not invoice:
			invoice = invoices[row.name] = {
				"name": row.name,
				"posting_datetime": row.posting_datetime,
				"customer_gstin": (row.customer_gstin or "").strip().upper(),
				"customer_name": row.customer_name or "",
				"grand_total": flt(row.grand_total),
				"place_of_supply": (row.place_of_supply or "").strip(),
				"is_return": cint(row.is_return),
				"items": {},
			}
		# one item detail per tax rate keeps the file small and matches how
		# the portal groups a bill's lines
		rate = flt(row.tax_rate)
		item = invoice["items"].get(rate)
		if not item:
			item = invoice["items"][rate] = {
				"taxable": 0.0,
				"cgst": 0.0,
				"sgst": 0.0,
				"igst": 0.0,
				"tax": 0.0,
				"hsn": (row.hsn_code or "").strip(),
				"qty": 0.0,
			}
		item["taxable"] += flt(row.amount)
		item["cgst"] += flt(row.cgst_amount)
		item["sgst"] += flt(row.sgst_amount)
		item["igst"] += flt(row.igst_amount)
		item["tax"] += flt(row.tax_amount)
		item["qty"] += flt(row.qty)
		if not item["hsn"] and row.hsn_code:
			item["hsn"] = row.hsn_code.strip()
	return list(invoices.values())


def _item_details(items, sign=1):
	details = []
	for number, (rate, item) in enumerate(sorted(items.items()), start=1):
		details.append(
			{
				"num": number,
				"itm_det": {
					"txval": _money(sign * item["taxable"]),
					"rt": flt(rate),
					"camt": _money(sign * item["cgst"]),
					"samt": _money(sign * item["sgst"]),
					"iamt": _money(sign * item["igst"]),
					"csamt": 0,
				},
			}
		)
	return details


def build_gstr1(from_date, to_date, branch=None):
	"""The GSTR-1 document for a month (or any date range)."""
	branches = report_branch_filter({"branch": branch})
	if branches is not None and not branches:
		return {"gstin": "", "fp": _tax_period(to_date), "version": "GSTR1", "b2b": [], "b2cs": []}

	invoices = _group_invoices(_invoice_rows(getdate(from_date), getdate(to_date), branches))

	b2b = {}
	b2cs = {}
	cdnr = {}
	cdnur = []
	hsn = {}

	for invoice in invoices:
		pos = invoice["place_of_supply"] or ""
		inter_state = any(item["igst"] for item in invoice["items"].values())
		sply_ty = "INTER" if inter_state else "INTRA"
		registered = bool(invoice["customer_gstin"])

		# HSN summary covers both sales and returns, netted out
		for rate, item in invoice["items"].items():
			key = (item["hsn"] or "", flt(rate))
			bucket = hsn.get(key)
			if not bucket:
				bucket = hsn[key] = {
					"hsn_sc": item["hsn"] or "",
					"rt": flt(rate),
					"qty": 0.0,
					"txval": 0.0,
					"camt": 0.0,
					"samt": 0.0,
					"iamt": 0.0,
				}
			bucket["qty"] += flt(item["qty"])
			bucket["txval"] += flt(item["taxable"])
			bucket["camt"] += flt(item["cgst"])
			bucket["samt"] += flt(item["sgst"])
			bucket["iamt"] += flt(item["igst"])

		if invoice["is_return"]:
			if registered:
				# credit note against a registered buyer
				entry = cdnr.setdefault(invoice["customer_gstin"], {"ctin": invoice["customer_gstin"], "nt": []})
				entry["nt"].append(
					{
						"ntty": "C",
						"nt_num": invoice["name"],
						"nt_dt": _date(invoice["posting_datetime"]),
						"val": _money(abs(invoice["grand_total"])),
						"pos": pos,
						"rchrg": "N",
						"inv_typ": "R",
						# the note carries positive amounts for what it reverses
						"itms": _item_details(invoice["items"], sign=-1),
					}
				)
			else:
				for rate, item in sorted(invoice["items"].items()):
					cdnur.append(
						{
							"typ": "OE",
							"ntty": "C",
							"nt_num": invoice["name"],
							"nt_dt": _date(invoice["posting_datetime"]),
							"val": _money(abs(invoice["grand_total"])),
							"pos": pos,
							"rt": flt(rate),
							"txval": _money(abs(item["taxable"])),
							"camt": _money(abs(item["cgst"])),
							"samt": _money(abs(item["sgst"])),
							"iamt": _money(abs(item["igst"])),
							"csamt": 0,
						}
					)
			continue

		if registered:
			entry = b2b.setdefault(invoice["customer_gstin"], {"ctin": invoice["customer_gstin"], "inv": []})
			entry["inv"].append(
				{
					"inum": invoice["name"],
					"invdt": _date(invoice["posting_datetime"]),
					"val": _money(invoice["grand_total"]),
					"pos": pos,
					"rchrg": "N",
					"inv_ty": "R",
					"itms": _item_details(invoice["items"]),
				}
			)
			continue

		for rate, item in invoice["items"].items():
			key = (pos, flt(rate), sply_ty)
			bucket = b2cs.get(key)
			if not bucket:
				bucket = b2cs[key] = {
					"sply_ty": sply_ty,
					"pos": pos,
					"typ": "OE",
					"rt": flt(rate),
					"txval": 0.0,
					"camt": 0.0,
					"samt": 0.0,
					"iamt": 0.0,
					"csamt": 0,
				}
			bucket["txval"] += flt(item["taxable"])
			bucket["camt"] += flt(item["cgst"])
			bucket["samt"] += flt(item["sgst"])
			bucket["iamt"] += flt(item["igst"])

	for bucket in list(b2cs.values()):
		bucket["txval"] = _money(bucket["txval"])
		bucket["camt"] = _money(bucket["camt"])
		bucket["samt"] = _money(bucket["samt"])
		bucket["iamt"] = _money(bucket["iamt"])

	hsn_rows = []
	for number, bucket in enumerate(
		sorted(hsn.values(), key=lambda row: (row["hsn_sc"], row["rt"])), start=1
	):
		hsn_rows.append(
			{
				"num": number,
				"hsn_sc": bucket["hsn_sc"],
				"uqc": UQC,
				"qty": flt(bucket["qty"], 3),
				"txval": _money(bucket["txval"]),
				"rt": bucket["rt"],
				"camt": _money(bucket["camt"]),
				"samt": _money(bucket["samt"]),
				"iamt": _money(bucket["iamt"]),
				"csamt": 0,
			}
		)

	return {
		"gstin": _gstin(branch),
		"fp": _tax_period(to_date),
		"version": "GSTR1",
		"b2b": list(b2b.values()),
		"b2cs": list(b2cs.values()),
		"cdnr": list(cdnr.values()),
		"cdnur": cdnur,
		"hsn": {"data": hsn_rows},
	}


@frappe.whitelist()
def get_gstr1(from_date=None, to_date=None, branch=None):
	"""Build the return and hand it to the report page."""
	if not ({"System Manager", "Retail Admin", "Retail Manager"} & set(frappe.get_roles())):
		frappe.throw(_("Only a manager can prepare the GST return"), frappe.PermissionError)

	to_date = getdate(to_date or nowdate())
	from_date = getdate(from_date) if from_date else to_date.replace(day=1)
	document = build_gstr1(from_date, to_date, branch or None)
	document["filename"] = "GSTR1_{0}_{1}.json".format(
		_clean(document["gstin"]), document["fp"]
	)
	return document


@frappe.whitelist()
def get_gstr1_text(from_date=None, to_date=None, branch=None):
	document = get_gstr1(from_date, to_date, branch)
	return json.dumps(document, indent=1, default=str)


def _clean(value):
	return (value or "").replace("/", "").replace(" ", "")
