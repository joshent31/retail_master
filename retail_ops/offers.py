"""Product and category offers.

An offer is a *line* discount, so it lands in the same ``discount_amount`` the
cashier can type by hand and every report that already reads discounts keeps
working. When several offers match a line the largest one wins (they never
stack), and a manual discount only survives if it is bigger than the offer.

The POS gets the same rules through ``get_bootstrap`` and repeats the
calculation locally, so the price on the card, the cart total and the server's
recalculation after sync always agree.
"""

import frappe
from frappe.utils import cint, flt, getdate, nowdate

OFFER_DOCTYPE = "Retail Offer"
FIELDS = [
	"name",
	"title",
	"apply_on",
	"category",
	"product",
	"discount_type",
	"value",
	"min_qty",
	"branch",
	"valid_from",
	"valid_to",
]


def active_offers(branch=None, on_date=None):
	"""Offers usable for a branch on a date, cached for the request."""
	cache = getattr(frappe.local, "_retail_offers", None)
	if cache is None:
		cache = frappe.local._retail_offers = {}
	key = (branch or "", str(on_date or nowdate()))
	if key in cache:
		return cache[key]

	day = getdate(on_date or nowdate())
	rows = frappe.get_all(OFFER_DOCTYPE, filters={"enabled": 1}, fields=FIELDS, order_by="modified desc")
	usable = []
	for row in rows:
		if row.branch:
			if not branch or row.branch != branch:
				continue
		if row.valid_from and getdate(row.valid_from) > day:
			continue
		if row.valid_to and getdate(row.valid_to) < day:
			continue
		usable.append(row)
	cache[key] = usable
	return usable


def matches(offer, product, qty):
	if flt(qty) < flt(offer.min_qty or 0):
		return False
	if offer.apply_on == "Product":
		return offer.product == product.get("name")
	if offer.apply_on == "Category":
		left = (offer.category or "").strip().lower()
		right = (product.get("category") or "").strip().lower()
		return bool(left) and left == right
	return False


def discount_amount(offer, qty, line_value):
	value = flt(offer.value)
	if offer.discount_type == "Percent":
		amount = flt(line_value) * value / 100.0
	else:
		amount = value * abs(flt(qty))
	amount = min(amount, max(flt(line_value), 0))
	return flt(amount, 2)


def offer_discount(branch, product, qty, line_value):
	"""Best discount for one line, in money. Caps at the line value."""
	best = 0.0
	for offer in active_offers(branch=branch):
		if not matches(offer, product, qty):
			continue
		best = max(best, discount_amount(offer, qty, line_value))
	return best


def offer_label(offer):
	value = flt(offer.value)
	if flt(value) == int(value):
		value = int(value)
	if offer.discount_type == "Percent":
		return "{0}% off".format(value)
	return "{0} off".format(value)


def describe(offer):
	"""Shape the POS can cache offline and apply without another round trip."""
	return {
		"name": offer.name,
		"title": offer.title,
		"apply_on": offer.apply_on,
		"category": (offer.category or "").strip(),
		"product": offer.product,
		"branch": offer.branch or "",
		"discount_type": offer.discount_type,
		"value": flt(offer.value),
		"min_qty": flt(offer.min_qty),
		"valid_from": str(offer.valid_from or ""),
		"valid_to": str(offer.valid_to or ""),
		"label": offer_label(offer),
	}


def get_offers_for_branches(branches, on_date=None):
	"""[cint] Every offer visible to the POS, ignoring per-branch duplicates."""
	seen = {}
	for branch in branches or [None]:
		for offer in active_offers(branch=branch, on_date=on_date):
			seen[offer.name] = describe(offer)
	return list(seen.values())


def clear_cache():
	frappe.local._retail_offers = {}


def validate(doc, method=None):
	"""Keep the cached offers honest when one is edited from Desk."""
	clear_cache()


def on_trash(doc, method=None):
	clear_cache()
