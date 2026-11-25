# Retail Ops — offline-first POS for Frappe v16+

A self-contained Frappe app for supermarkets and retail stores with **multiple
branches**, **multiple warehouses per branch**, **day-wise opening/closing tied
to billing**, **role- and user-based permissions**, and a **POS terminal that
keeps billing when the internet is down** and syncs automatically when it comes
back.

No ERPNext dependency: products, inventory and billing are the app's own
DocTypes, so it installs on a plain Frappe v16 bench.

---

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Install](#install)
- [First-time setup](#first-time-setup)
- [Permissions](#permissions)
- [Using the POS](#using-the-pos)
- [Using the POS on a phone](#using-the-pos-on-a-phone)
- [Returns](#returns)
- [Customers and loyalty points](#customers-and-loyalty-points)
- [Collecting a credit due](#collecting-a-credit-due)
- [Offers and MRP](#offers-and-mrp)
- [Receiving stock (GRN)](#receiving-stock-grn)
- [Suppliers and payables](#suppliers-and-payables)
- [Returning goods to a supplier (debit note)](#returning-goods-to-a-supplier-debit-note)
- [Moving stock between warehouses](#moving-stock-between-warehouses)
- [Stock adjustments (wastage, expiry, physical count)](#stock-adjustments-wastage-expiry-physical-count)
- [Batches and expiry](#batches-and-expiry)
- [Price labels](#price-labels)
- [GST](#gst)
- [Filing GSTR-1](#filing-gstr-1)
- [Reports](#reports)
- [Dashboard](#dashboard)
- [Offline mode and sync](#offline-mode-and-sync)
- [DocTypes](#doctypes)
- [API](#api)
- [Desk workflows](#desk-workflows)
- [Customising](#customising)
- [Limitations / roadmap](#limitations--roadmap)
- [License](#license)

---

## Features

- **Multi-branch, multi-warehouse** — branches own warehouses, warehouses own
  stock, every bill is stamped with its branch, warehouse and day session.
- **Day sessions** — cashiers open the day with an opening float; the app
  refuses bills until the day is open. Closing the day computes sales by
  payment mode, expected cash and cash variance from the submitted bills.
- **Offline-first POS** at `/retail-pos` — catalog cached in IndexedDB, bills
  queued locally, automatic push/pull when connectivity returns.
- **Idempotent sync** — every bill and day operation carries an `offline_uuid`;
  retries can never double-book a sale or its stock movement.
- **Role + user permissions** — role DocType permissions plus row-level
  branch isolation via standard *User Permission* records.
- **Price safety** — server-side recalculation of every line and total; price
  override is a per-branch switch.
- **Stock** — per-warehouse running balance updated on submit/cancel, with an
  optional "no negative stock" rule per branch, batch/expiry breakdown and
  first-expiring-first-out issuing.
- **Customers and loyalty** — a customer is found by mobile at the counter,
  earns points on every bill and can pay part of the next one with them.
- **Credit dues** — a bill left unpaid becomes a due; a customer receipt settles
  it oldest first, counts in the day's cash and clears it off the outstanding
  report.
- **Suppliers and payables** — receipts post against a supplier master, returns
  debit it and payments settle it, so what is still owed is one report away.
- **Stock corrections** — returns to a supplier and signed adjustment documents
  (wastage, expiry, physical count) move stock and the batch breakdown together.
- **GST filing** — tax invoices and credit notes, HSN-wise summary and a GSTR-1
  JSON download for the portal's offline tool.
- **Receipts** — print-friendly receipt with branch footer and cashier name,
  plus a printable shelf/barcode label per product.
- **Returns without the paper slip** — a bill is found by bill number or by the
  customer's mobile number.

## Architecture

```
retail_ops/
├── pyproject.toml                 # flit build, Frappe v16
├── retail_ops/
│   ├── hooks.py                   # doc_events, permissions, after_request
│   ├── modules.txt                # "Retail Ops"
│   ├── install.py                 # roles + composite indexes
│   ├── utils.py                   # branch access, request headers, helpers
│   ├── permissions.py             # query conditions / has_permission / grants
│   ├── sessions.py                # day open/close + totals
│   ├── invoices.py                # validation, totals, stock, idempotency
│   ├── stock.py                   # per-warehouse balances
│   ├── api/
│   │   ├── bootstrap.py           # catalog for the POS (cacheable)
│   │   ├── dashboard.py           # cards + chart series for the dashboard
│   │   ├── sessions.py            # open_day / close_day / summaries
│   │   └── sync.py                # push queue / pull catalog
│   ├── www/retail-pos.{py,html}   # the terminal page
│   ├── public/
│   │   ├── js/offline-db.js       # IndexedDB wrapper (+ memory fallback)
│   │   ├── js/sync.js             # queue push/pull engine
│   │   ├── js/pos.js              # terminal UI
│   │   ├── js/sw.js               # service worker (offline shell)
│   │   └── css/retail-pos.css
│   ├── retail_ops/doctype/        # 9 DocTypes
│   ├── retail_ops/page/retail_dashboard/   # /app/retail-dashboard
│   └── retail_ops/report/         # 6 script reports
```

The POS is plain JavaScript — no build step, no node_modules, works on the
built assets served by Frappe.

## Requirements

- Frappe **v16** (also works on v15)
- MariaDB/MySQL (the composite index helper is MySQL-specific; on Postgres it
  is skipped with a log entry)
- Python 3.10+
- A modern browser with IndexedDB and Service Worker support (Chrome, Edge,
  Safari, Firefox)

## Install

```bash
cd ~/frappe-bench

# 1. get the app (replace with your fork once you push it)
bench get-app https://github.com/<you>/retail_ops.git

# 2. create/choose a site
bench new-site retail.local
bench --site retail.local install-app retail_ops

# 3. build assets and restart
bench build --app retail_ops
bench --site retail.local clear-cache
bench restart
```

Verify:

```bash
bench --site retail.local list-apps
# frappe  retail_ops
```

## First-time setup

All of this can be done from Desk (`/app`) or with `bench execute`.

### 1. Create a branch

Desk → **Retail Branch** → New

| Field | Example | Notes |
|---|---|---|
| Branch Code | `MAIN` | becomes the document name |
| Branch Name | `Main Street` | |
| Currency | `INR` | ISO code, used on receipts |
| Allow Offline Billing | checked | POS may queue bills offline |
| Allow Negative Stock | checked | unchecked = stock check on submit |
| Allow Price Override | unchecked | checked lets cashiers edit rates |
| Allow Post-Close Billing | unchecked | checked allows billing after the day is closed |
| Receipt Footer | `Thank you!` | printed at the bottom |

### 2. Add warehouses to the branch

Desk → **Retail Warehouse** → New — set `Warehouse Code`, `Warehouse Name`,
`Branch`, and mark one per branch as `Is Default`.

### 3. Add products

Desk → **Retail Product** → New — `Item Code` (name), `Product Name`,
`Barcode`, `Category`, `UOM`, `Rate`, `Tax Rate`. Products are shared by all
branches; stock is per warehouse.

### 4. Create users and grant branch access

```bash
bench --site retail.local execute retail_ops.permissions.grant_branch_access \
  --args '["cashier@example.com", "MAIN"]'
```

or in Desk: **User Permission** → User = cashier, Allow = `Retail Branch`,
For Value = `MAIN`. A user with no branch User Permission and no Retail
Admin/System Manager role cannot see or bill any branch (fail closed).
`revoke_branch_access` removes it again.

Assign one of the app roles on the User: `Retail Admin`, `Retail Manager` or
`Retail Cashier`.

### 5. Open the POS

`https://retail.local/retail-pos` — sign in first if you are not signed in.

## Permissions

| DocType | Retail Admin | Retail Manager | Retail Cashier |
|---|---|---|---|
| Retail Branch | full | read/write (no delete) | read |
| Retail Warehouse | full | read/write | read |
| Retail Product | full | full | read |
| Retail Day Session | full | read/write/create | read/write/create (no delete/cancel) |
| Retail Invoice | full | full | create/write/submit/read (no cancel/delete/amend) |
| Retail Stock Balance | full | read/export | read |
| Retail Sync Log | full | read/export | — |

Row-level rules, applied to list views, reports, the REST API and the POS:

- `Retail Admin`, `System Manager`, `Administrator` see all branches.
- Everyone else sees only branches granted through **User Permission** on
  `Retail Branch`.
- Invoices, day sessions, warehouses and stock balances are filtered by their
  branch; opening a day or posting a bill for another branch throws
  `PermissionError`.

Branch caches are cleared automatically when a User Permission changes.

## Using the POS

1. Pick the **branch** and **warehouse** (remembered per device).
2. **Open Day** — enter the opening cash float. One open session per
   warehouse; opening again returns the existing session.
3. Scan a barcode / type in the search box (F2 focuses it) or tap a product.
   Quantities, rate override (if enabled) and discounts are per line.
4. Choose the payment mode, enter the amount received, press **Charge**.
   - Overpayment shows as change on the receipt.
   - `Credit` records the bill as unpaid (outstanding amount).
5. **Close Day** — enter the cash counted in the drawer. The app shows sales,
   cash/card/UPI/credit split, expected cash and the variance, then opens a
   summary once the server has the close.

## Using the POS on a phone

The same page (`/retail-pos`) is the mobile billing screen — no separate app.
Below 820px the layout switches to one screen at a time with a tab bar at the
bottom:

- **Products** — search/scan and tap to add, two products per row with stock
  and rate visible.
- **Bill** — line editing, customer details, totals, payment mode and
  **Charge**, all on one panel. The tab shows the item count and the running
  total, so the cashier can see the bill from the catalog screen.
- **Day & sync** — branch and warehouse selectors, **Open Day** / **Close
  Day** and **Sync**. Tapping the day pill in the header jumps here too.

Receipts, day summaries and the open/close forms open full screen, the tab bar
respects the phone's safe area, and a finished bill returns to the catalog for
the next customer. The layout works offline the same way the desktop one does.
The Desk dashboard also drops to a single column with two cards per row on a
phone; reports use the standard Desk list, which is already responsive.

**Install it as an app.** On Android Chrome, open `/retail-pos` and tap
**Install app** in the header (or *Menu → Install app*). On iPhone, use
*Share → Add to Home Screen* — the header button shows these steps on iOS.
The app then opens full screen without browser chrome, keeps its own icon, and
the service worker serves the same shell offline. Installation needs HTTPS
(or `localhost`); the manifest lives at `/assets/retail_ops/manifest.json` and
the icons at `/assets/retail_ops/images/`.

**Queue at a glance.** The header always shows how many bills are still
waiting to reach the server (`2 queued`) and when the queue last synced
(`Last sync 4m ago`), refreshed every 30 seconds and remembered across reloads
so a cashier can tell whether it is safe to close the tab. The badges sit in
the header on every tab, so they stay visible while billing on a phone.

**Camera scanning.** The **Scan** button next to the search box opens the rear
camera and adds the product whose barcode it reads (EAN-13/8, UPC-A/E,
Code-128/39, ITF, Codabar, QR). It uses the browser's `BarcodeDetector`, so it
works on Android Chrome over HTTPS; browsers without it (notably iOS Safari)
show a message and the cashier types or uses a Bluetooth scanner instead.

**Sharing the bill.** The receipt shown after **Charge** has a **Share image**
button: it draws the bill on a canvas (branch, bill no, items, totals, payment,
footer) and hands the PNG to the phone's share sheet, where the cashier picks
WhatsApp. The picture is produced on the device, so it works offline and needs
no WhatsApp settings; where the browser cannot share files the PNG is saved
to Downloads instead. When the WhatsApp Cloud API is configured, the separate
**WhatsApp bill** button still delivers the bill document straight from the
server, and `auto_send` keeps working as before.

## Returns

**Return** in the current-bill header opens the return flow: type the bill
number *or the customer's mobile number*, the server returns the matching bills
(the exact bill number wins, otherwise the recent bills for that number), pick
one, enter quantities, choose the refund mode and confirm. Each line shows how
much can still come back, and a fully returned bill is marked so it is not
picked by mistake.

- A return is an ordinary `Retail Invoice` with `is_return` set, negative
  quantities and `return_against` pointing at the original bill. Stock, day
  sessions, reports and the WhatsApp document therefore work unchanged.
- Quantities are checked against the bill minus everything already returned on
  it, so the same item cannot be refunded twice. `Return Within Days` on the
  branch limits how old a bill may be (0 = no limit).
- Refunds are paid at what the customer actually paid — the original line net
  of any offer or manual discount, at the tax rate charged then — so a
  discounted sale refunds the discounted amount even if the price changed since.
  Stock goes back to the warehouse the bill was posted to.
- Refund modes: Cash, Card, UPI, or **Store credit** (no money leaves the
  till; the negative bill is recorded as a Credit payment and settles as Paid).
- Returns need a connection, because the sold quantities live on the server.
  The credit note appears in the day session and reduces expected cash.

## Customers and loyalty points

A bill stays a walk-in sale unless the cashier types a mobile number. Doing so
looks the number up in `Retail Customer` (Desk → Retail Customer) and, on the
first visit, enrols it with the name that was typed — the bill is then linked to
that record and the points follow the customer, not the terminal.

Loyalty is switched on per branch, with four settings:

| Setting | Meaning |
|---|---|
| Enable Loyalty Points | Off by default; nothing is earned or spent while it is off |
| Spend per Point | Bill net total needed for one point (100 = 1 point per 100) |
| Value of One Point | Money a point takes off a bill when redeemed |
| Max % of Bill Paid With Points | Keeps a bill from being fully free |

- Points are a **payment mode** (`Loyalty`), not a discount, so the tax invoice
  still shows what the goods cost and the GST is untouched. The cashier enters
  how many points to use, the bill shows the remaining cash/card/UPI due and
  the receipt prints what was settled with points.
- Earning happens on submit, redeeming takes the balance down on submit and both
  are reversed when the bill is cancelled. A credit note refunded in `Loyalty`
  hands the points back instead of money.
- The balance is always the server's: redeeming needs a connection (the bill
  itself may still be queued), and a bill asking for more points than the
  customer has is rejected rather than silently discounted.

## Collecting a credit due

A bill paid with `Credit` (or left with no payment rows) keeps an
`outstanding_amount`. `Retail Customer Receipt` (`CRCPT-YYYY-…`, submittable)
is how that money comes back:

- Pick the customer, how much was received and how (cash, card, UPI or bank
  transfer, with a reference number), plus the branch and — at the counter —
  the warehouse whose drawer took it.
- The **server** spreads the money over the customer's open bills, oldest
  first, and writes the split to the receipt's `allocations` table. A till
  cannot choose which bill to settle, and a receipt for more than the customer
  owes is refused instead of banking a credit.
- Each settled bill moves the money into `collected_amount`, so its outstanding
  and status (Paid / Partially Paid) are updated without touching the bill's
  payments. Because the bill derives its outstanding as
  `grand − paid − collected`, re-saving it later cannot undo the collection.
- Cancelling the receipt hands the money back to the bills it settled.
- At the POS, selecting a customer shows **Due ₹X · Collect** under their
  details; it reads the live dues first, then takes the amount. Collecting a due
  needs a connection.
- Cash collected lands in the day session's **Dues Collected** and counts
  towards the expected cash of that day, so closing the till still reconciles.
- **Retail Credit Outstanding** is the desk view of the same thing, oldest dues
  first.

## Offers and MRP

`Retail Offer` (Desk → Retail Offer) holds promotions:

| Field | Meaning |
|---|---|
| Applies To | `Category`, `Product`, or `Bill` for a cart-level offer |
| Discount Type | `Percent` off, or `Amount` (per unit on a line offer, per bill on a bill offer) |
| Value / Minimum Qty | The discount and the quantity needed for it to apply (line offers) |
| Minimum Bill Amount | The basket total needed for a bill offer to apply |
| Branch / Valid From / Valid To | Optional store and date window |

- The best matching offer wins; offers never stack. A manual discount (rate
  override) survives only if it is larger.
- A **Bill** offer is a trade discount, so it is shared across the lines by
  value *before* tax: the taxable value, the GST and the GSTR-1 figures all come
  down with it. The total is kept on the bill as `bill_discount` with the offer
  title, so the receipt can say which offer was given.
- Offers arrive with the catalog and are applied on the device, so a phone
  shows the offer badge on the product card and the discount in the bill
  without a round trip. The server recalculates the same result on sync.
- `MRP` on the product is struck through on the card when it is above the
  selling rate, and the bill shows the discount line the customer saved. Selling
  a product above its MRP is rejected at billing.
- Enabling or editing an offer clears the cached offer list; terminals pick it
  up on their next catalog refresh.

## Receiving stock (GRN)

Bills only take stock out. Goods arrive through `Retail Purchase Receipt`
(Desk → Retail Purchase Receipt), one receipt per delivery:

- Branch, warehouse, supplier (picked from `Retail Supplier`), supplier
  invoice number and date on the header; one row per product with the cost
  rate, the printed MRP and, when the goods are batched, the batch number and
  expiry date.
- Submitting the receipt **adds** each row's quantity to that warehouse's stock
  balance, so the goods are immediately sellable at the POS. Stock On Hand and
  the POS stock badges pick it up on the next refresh.
- `Update Product MRP` (on by default) writes any MRP you enter back to the
  product, so a price change on the pack reaches the shelf cards without a
  separate catalog edit. The selling rate is never touched — cost is not price.
- Cancelling a receipt takes the quantities back out, and is refused if some of
  those goods have already been sold; the MRP it set stays as-is.
- The same product twice on one receipt is rejected (combine the rows), and a
  receipt only writes to a warehouse of its own branch.

## Suppliers and payables

`Retail Supplier` (Desk → Retail Supplier) is the supplier master: name, mobile,
email, GSTIN, address and payment terms in days. The name is the document name,
so a receipt's supplier link reads the way you type it.

- Goods are received against a supplier on the GRN; the supplier's GSTIN is
  filled in from the master when the receipt leaves it blank.
- Money paid is recorded with `Retail Supplier Payment` (`SPAY-YYYY-…`,
  submittable): supplier, date, amount, how it was paid, reference and
  optionally the branch whose cash was used. Cancelling a payment puts the
  liability back.
- **Retail Supplier Payables** adds it up: goods received, returned, paid,
  outstanding, oldest receipt, days overdue (after the supplier's payment
  terms) and the last payment date. A payment with no branch settles the
  supplier for every branch, which is how a central bank transfer is usually
  booked.

## Returning goods to a supplier (debit note)

`Retail Purchase Return` (`PRET-YYYY-…`, submittable) sends goods back — short
supply, wrong item, damage, expired stock:

- Branch, warehouse, supplier, date and a reason, then one row per product with
  the quantity, the cost rate and, when the goods are batched, the batch and
  expiry. Leaving the batch blank takes the quantity off the earliest expiring
  batch, the same way a sale would.
- Submitting **takes** the quantities out of the warehouse; cancelling puts them
  back. A return that would push stock (or a named batch) below zero is refused
  unless the branch allows negative stock.
- `Against Receipt` links the GRN it came from and the app refuses to return
  more than that receipt brought in, counting any earlier returns against it.
  A receipt from another supplier or branch is rejected.
- The value is debited from the supplier: payables show goods received, returned
  and paid, with outstanding as `received − returned − paid`.
- A purchase return moves no GST figures — it is the stock and payable side of
  the correction. The supplier's own debit note remains the tax document.

## Moving stock between warehouses

`Retail Stock Transfer` (Desk → Retail Stock Transfer) moves goods from one
warehouse to another — same branch or a sister branch — without a sale:

- Pick the **From** branch/warehouse and the **To** branch/warehouse, then the
  products and quantities. Submitting takes the quantities out of the source and
  adds them to the destination, so both shelves and both branches' Stock On Hand
  agree straight away. Cancelling moves them back.
- Nothing is moved while any line is short: the whole transfer is checked
  against the source warehouse before the first quantity leaves, so a "10 of
  this, 2 of that" move cannot half-apply.
- Cancelling is refused once the goods have been sold at the destination; that
  is what a return or an inter-branch adjustment is for.
- You need access to both branches to post a transfer, and the document is
  filed under the sending branch.
- Batches travel with the goods: the source hands over its earliest expiring
  batches first and the destination receives those same batch and expiry rows,
  so the expiry report is right on both shelves. Cancelling puts each batch
  back where it came from.

## Stock adjustments (wastage, expiry, physical count)

Neither a bill nor a receipt explains broken stock, expired stock or a counting
mistake. `Retail Stock Adjustment` (`SADJ-YYYY-…`, submittable) does:

- Branch, warehouse, when it happened and a **Reason** (wastage, damage,
  expiry, physical count, theft, other), then one row per product.
- Each row carries a signed **Adjust By**: negative writes stock off, positive
  adds it back. Entering the *difference* rather than the counted figure keeps
  the same document usable for both a write-off and a count correction — for a
  count of 7 where the app said 9, the adjustment is `-2`.
- `On Hand` and `New Qty` are filled in on each row so the effect is visible
  before submitting, and a row that would take the product (or a named batch)
  below zero is refused unless the branch allows negative stock.
- Named batches are adjusted on that batch; leaving the batch blank takes a
  write-off off the earliest expiring batch and puts a gain on `NO BATCH`, so
  the batch rows keep adding up to the warehouse balance.
- Cancelling the adjustment applies it in reverse. Adjustments and purchase
  returns are replayed by `rebuild_stock_balance`, so a rebuild does not wipe
  them out.

## Batches and expiry

Products with a printed batch or expiry date are tracked in `Retail Stock Batch`
(Desk → Retail Stock Batch), one row per product + warehouse + batch:

- A GRN row can carry a batch number and expiry date. Leave them blank for
  goods that are not batched — they land on a `NO BATCH` row so the batch
  quantities still add up to the warehouse balance.
- Billing issues **first expiring, first out**: the earliest expiry goes first,
  the batch that supplied each line is stamped on the invoice line (read-only),
  and a credit note puts the goods back on that batch.
- Stock sold into negative, or stock that existed before batches were tracked,
  falls back to the `NO BATCH` row rather than blocking the sale.
- **Retail Batch Stock** lists quantities per batch with days to expiry and a
  "expiring within N days" filter for the weekly near-expiry walk. Because the
  batch rows are a breakdown of the warehouse balance, their sum should match
  Retail Stock On Hand for the same warehouse.
- Rebuilding stock balances (`rebuild_stock_balance`) rewrites the warehouse
  quantities only; the batch breakdown is not reconstructed from history.

## Price labels

Open a product and press **Print Label** (or visit
`/retail-label?products=<item>&copies=2`):

- One 50 × 30 mm label per copy with the store name, product, selling price
  including tax, the MRP when there is one, a **Code 39** barcode and the code
  printed under it.
- The barcode is drawn by the page itself, with no library and no connection,
  so it prints on any label printer or plain paper. Barcode comes from the
  product's barcode field, otherwise its item code.
- Hardware that needs its own protocol (weighing scales, ESC/POS label
  printers) still needs that integration — see the limitations below.

## GST

Set `GSTIN` and `State Code` on the branch, `HSN/SAC` on products, and the
customer can type a GSTIN in the bill (optional).

- Without a customer GSTIN the bill is a plain retail bill. With one it is a
  **Tax Invoice** and the printed/WhatsApp document shows both GSTINs. The
  GSTIN is format-checked at the till and again on the server, so a typo cannot
  silently produce an inter-state bill.
- Tax is split into CGST + SGST when the customer's GSTIN state code matches the
  branch state code, and into IGST when it differs (that code also becomes the
  place of supply). Bills without a customer GSTIN are treated as intra-state.
- `Retail Invoice` stores `cgst_total`/`sgst_total`/`igst_total` and the item
  rows store the split plus HSN, so the Tax Summary report shows taxable value
  and each tax head per rate — ready for GSTR-1 style reconciliation. Set the
  report's **Group By** to `HSN/SAC` for the HSN-wise table GSTR-1 asks for.
- Returns carry negative GST in the same split, which nets out in the report.
- Bills are numbered `RI-.YYYY.-…` and credit notes `CN-.YYYY.-…`, so the two
  series stay separate the way GST filing expects them to.

## Filing GSTR-1

The Tax Summary report has a **Download GSTR-1** button. It writes the JSON the
GST portal's offline utility imports, for the report's date range and optional
branch:

- `b2b` for bills with a customer GSTIN, `b2cs` (rate-wise, intra/inter-state)
  for the rest, `cdnr` and `cdnur` for credit notes, and the HSN summary.
- The tax period (`fp`) comes from the report's To Date, and the GSTIN from the
  branch (or the first active branch when the report covers all of them).
- Credit notes go in with positive values, the way the form expects them, even
  though the bill itself stores negative quantities.
- It is a draft built from your own bills: upload it, read the portal's
  validation messages, and correct the master data (HSN digits, state codes,
  UQC) it complains about. The file uses `OTH` as the unit quantity code, which
  is what a retail shop without weighed goods should declare.

## Reports

Desk → **Reports** (or the Awesome Bar by name). All reports are branch-aware:
users with branch User Permissions only see their own branches, and a branch
filter outside their access is refused.

| Report | Answers | Filters |
|---|---|---|
| **Retail Sales Summary** | Daily sales per branch: bills, net, discount, tax, billed, and the cash/card/UPI/credit split | From/To date, Branch, Warehouse |
| **Retail Item Sales** | Which products sell, how much and their share of sales | From/To date, Branch, Category |
| **Retail Tax Summary** | Tax collected per tax rate, or per HSN code for GSTR-1 | From/To date, Group By, Branch |
| **Retail Stock On Hand** | Quantity and value of stock per product and warehouse | Branch, Warehouse, Category, Only In-Stock |
| **Retail Shift Reconciliation** | Closing cash vs expected cash for every day session, with variance | From/To date, Branch, Status, Only Cash Differences |
| **Retail Credit Outstanding** | Customers who still owe money, oldest dues first | As On date, Branch, Older Than (days) |
| **Retail Supplier Payables** | What each supplier is still owed: goods received, returned, paid, outstanding, days overdue | Branch, Supplier, Only Outstanding |
| **Retail Batch Stock** | Quantity per batch with days to expiry, the near-expiry list | Branch, Warehouse, Product, Batch, Expiring Within (days), Hide Empty |

Reports are script reports (`retail_ops/retail_ops/report/<name>/`) rather than
Query Reports, so they can honour branch permissions and compute columns such
as product share and dues age in Python. **Retail Cashier** can run Sales
Summary and Shift Reconciliation; the other reports are Admin/Manager only.

## Dashboard

Desk → `/app/retail-dashboard` (or the Awesome Bar → *Retail Dashboard*) shows
the shop at a glance. It is open to Admin, Manager and Cashier, and every
figure is limited to the branches the user is granted.

Number cards: sales today with yesterday's comparison, bills today, change
against yesterday, this month's sales, cash received today, credit
outstanding, open day sessions and stock value.

Charts: sales trend (day by day), sales by branch, payment mode split, top
products by quantity and stock value by category. The period selector switches
between 7, 30 and 90 days, and the branch selector narrows everything to one
store. All figures are recomputed from submitted invoices, payments, sessions
and stock balances on each refresh.

## Offline mode and sync

The terminal is offline-first at every layer:

| Layer | Offline behaviour |
|---|---|
| Catalog | Last bootstrap (branches, warehouses, products, stock, open sessions) is cached in IndexedDB and loaded first, before any network call. |
| Billing | A bill is written to the IndexedDB queue first and only then pushed. Without a connection it stays queued and can still be printed. |
| Day open/close | Queued like bills; the local session lets billing continue, and the server session is created on the next successful push. |
| Sales | Stock counts decrease locally so the cashier sees realistic availability; the server value wins after the next pull. |
| App shell | A service worker caches `/retail-pos` and its CSS/JS so the page opens with no connection. |

### How the queue is pushed

- `window` `online` event, a 20 second interval, tab focus, and every checkout
  trigger `RetailSync.flush()`.
- Queue order is preserved: day open → bills → day close (by `created_at`).
- The whole queue is sent to `retail_ops.api.sync.push`; the server replays day
  operations first, then bills, and returns one result per item.
- Items that succeed (`created` or `duplicate`) leave the queue. Failures stay
  queued with their error and attempt count and are retried automatically.
- After a push the catalog is pulled again (products, stock and open sessions).

### Why retries are safe

- `Retail Invoice.offline_uuid` and `Retail Day Session.offline_uuid` are
  unique; the server checks them before inserting.
- If two requests race, the unique index raises a duplicate entry error which
  is converted into a normal `duplicate` result.
- Stock is only moved inside `on_submit`, so a rejected or duplicate bill never
  changes inventory.

### Closed days and late bills

- A bill needs an open day session; if none exists the server refuses it.
- A bill queued offline for a day that has since been closed is still accepted
  when it belongs to that session's business date (`sync_source =
  Offline Sync`), and the session totals are recomputed afterwards so the
  closing figures stay correct.
- If you want to allow billing after closing in general, set **Allow Post-Close
  Billing** on the branch.

## DocTypes

| DocType | Purpose |
|---|---|
| Retail Branch | Store settings, currency, offline/stock/price switches |
| Retail Warehouse | Stock location inside a branch |
| Retail Product | Item master with barcode, rate, tax rate, MRP |
| Retail Customer | Customer master keyed by mobile, with loyalty balance and spend |
| Retail Supplier | Supplier master: contact, GSTIN, payment terms |
| Retail Supplier Payment | Submittable money paid to a supplier (`SPAY-YYYY-…`) |
| Retail Customer Receipt | Submittable collection of a customer's credit dues (`CRCPT-YYYY-…`) |
| Retail Customer Receipt Item | Bill settled by a receipt, with its split (child) |
| Retail Purchase Return | Submittable debit note (`PRET-YYYY-…`): goods sent back to a supplier |
| Retail Purchase Return Item | Return line with cost rate and batch (child) |
| Retail Stock Adjustment | Submittable stock correction (`SADJ-YYYY-…`): wastage, expiry, physical count |
| Retail Stock Adjustment Item | Adjustment line with signed quantity (child) |
| Retail Day Session | One business day for a branch + warehouse; opening and closing figures |
| Retail Invoice | Submittable bill (`RI-.YYYY.-`) or credit note (`CN-.YYYY.-`) linked to session, branch, warehouse |
| Retail Invoice Item | Bill line (child) |
| Retail Invoice Payment | Payment split (child) |
| Retail Purchase Receipt | Submittable GRN (`PR-YYYY-…`): goods received into a warehouse |
| Retail Purchase Receipt Item | GRN line with cost rate and MRP (child) |
| Retail Stock Transfer | Submittable move (`ST-YYYY-…`) of stock between two warehouses |
| Retail Stock Transfer Item | Transfer line with quantity (child) |
| Retail Stock Balance | Running quantity per product + warehouse |
| Retail Stock Batch | Quantity per product + warehouse + batch, with expiry |
| Retail Sync Log | Audit of each offline push (counts, failures, client time) |

## API

All endpoints are `POST` under `/api/method/` and require a signed-in session
plus the normal DocType permissions.

| Method | Purpose |
|---|---|
| `retail_ops.api.bootstrap.get_bootstrap` | Branch/warehouse/product/stock catalog + capabilities for the POS |
| `retail_ops.api.bootstrap.get_open_day` | Current open session for a branch + warehouse |
| `retail_ops.api.bootstrap.get_receipt` | Receipt data for an invoice |
| `retail_ops.api.sessions.open_day` | Open (or return) the day |
| `retail_ops.api.sessions.close_day` | Close the day, recompute totals and variance |
| `retail_ops.api.sessions.get_day` | Session snapshot with fresh totals |
| `retail_ops.api.customers.get_dues` | A customer's unpaid bills and total, oldest first |
| `retail_ops.api.customers.collect_due` | Take money against those bills and settle them |
| `retail_ops.api.sessions.list_my_days` | Recent sessions for the user's branches |
| `retail_ops.api.sync.push` | Replay offline day operations and bills |
| `retail_ops.api.sync.pull` | Refresh catalog + stock (optionally ``since``) |
| `retail_ops.api.sync.status` | Server time / reachability probe |
| `retail_ops.permissions.grant_branch_access` | Grant a user a branch |
| `retail_ops.permissions.revoke_branch_access` | Revoke a branch |
| `retail_ops.api.dashboard.get_dashboard` | Number cards and chart series for the Retail Dashboard |
| `retail_ops.api.returns.find_bills` | Bills for a return, by bill number or customer mobile |
| `retail_ops.api.returns.get_returnable` | Bill lines with how much can still be returned |
| `retail_ops.api.returns.create_return` | Post a credit note against a bill |
| `retail_ops.api.customers.find_customer` | Find a customer by mobile or name, with the points balance |
| `retail_ops.api.customers.save_customer` | Attach (or enrol) the customer on the bill |
| `retail_ops.gstr1.get_gstr1` | Build the GSTR-1 document for a period |
| `retail_ops.stock.get_stock_levels` | On-hand quantities |
| `retail_ops.stock.rebuild_stock_balance` | Recompute balances from submitted bills (admin) |

Example push payload:

```json
{
  "sessions": [
    {
      "type": "open",
      "offline_uuid": "9d1f...",
      "branch": "MAIN",
      "warehouse": "MAIN-STORE",
      "opening_float": 200,
      "session_date": "2026-11-18",
      "opened_at": "2026-11-18 09:02:11"
    }
  ],
  "invoices": [
    {
      "offline_uuid": "4c8a...",
      "branch": "MAIN",
      "warehouse": "MAIN-STORE",
      "day_session": "9d1f...",
      "posting_datetime": "2026-11-18 09:15:40",
      "items": [{ "product": "MILK-1L", "qty": 2, "rate": 60 }],
      "payments": [{ "mode": "Cash", "amount": 120 }]
    }
  ]
}
```

`day_session` may be the server document name or the session's `offline_uuid`;
both resolve.

## Desk workflows

- **Day sessions**: `Retail Day Session` list → open/close directly from the
  form (the POS and Desk share the same validation).
- **Bills**: submitted invoices show branch, session, payment split, change
  and outstanding amount; cancelling restores stock and session totals.
- **Sync audit**: `Retail Sync Log` records each pushed batch with status
  (`Success` / `Partial` / `Failed`) and per-item messages.
- **Stock**: `Retail Stock Balance` and `Retail Stock Batch` are read-only in
  Desk; use `retail_ops.stock.rebuild_stock_balance` if you ever need to
  recompute the warehouse quantities from submitted bills.
- **Customers**: `Retail Customer` shows the points balance and lifetime spend;
  editing a customer's points by hand is deliberately not offered — points move
  only through bills so the trail stays in the ledger.
- **Suppliers**: record a payment from `Retail Supplier Payment`; the payables
  report recomputes itself. Send goods back through `Retail Purchase Return`
  when the supplier owes you instead.
- **Dues**: `Retail Customer Receipt` (or **Collect** on the POS customer line)
  settles a customer's credit bills oldest first; the credit report shrinks as
  the money comes in.
- **Stock corrections**: `Retail Stock Adjustment` writes off breakage or expiry
  and fixes a physical count; `Retail Purchase Return` sends goods back.

## Customising

- **Receipt layout**: `public/js/pos.js` → `renderReceiptText`.
- **New payment mode**: add to the select in `www/retail-pos.html` and to
  `PAYMENT_MODES` in `invoices.py` (and the session totals if it needs its own
  column).
- **Tax**: currently a flat per-product `tax_rate`; line amounts are computed
  server-side in `invoices.validate_item`.
- **ERPNext integration**: `stock.py` is the single place that writes stock;
  swap the balance update for Stock Ledger Entries there.

## Limitations / roadmap

- Receiving is a plain GRN: no purchase orders and no landed-cost allocation.
  Supplier payables are goods-received minus returns minus payments, not a full
  accounting ledger (no advances, no TDS).
- A customer receipt settles bills oldest first and cannot be pointed at one
  particular bill; there is no part-payment against a future bill, no interest
  on overdue dues and no customer statement printout.
- Purchase returns carry cost only, so the debit note has no GST lines of its
  own — the supplier's tax document is still theirs. Stock adjustments likewise
  hold no value, so a write-off does not post a stock loss to any account.
- Offers cover product, category and bill level discounts; there is still no
  coupon-code entry, buy-X-get-Y or slab-by-quantity rule.
- Batch issuing is automatic FEFO at billing; a cashier cannot scan a specific
  batch at the till, and expiring stock is not blocked from sale — the batch
  report is what tells you to pull it.
- Loyalty points are earned on the net total and redeemed as payment; there are
  no tiers, expiry of points, or points on returns beyond handing back what the
  credit note refunded.
- Weighing scales and ESC/POS label printers need their own device integration;
  the label page prints through the browser's normal print dialog.
- Transfers move quantities, not costs: stock value stays at the product rate,
  and there is no in-transit state for goods still on the road.
- The service worker needs the `Service-Worker-Allowed` header set by
  `utils.add_service_worker_headers`; if a reverse proxy strips it, offline
  page reloads fall back to the browser cache (billing stays queued either
  way).
- Multi-terminal same-day: sessions are per branch + warehouse; all terminals
  on that warehouse share the open session.
- Postgres installs skip the composite index helper (the DocType-level unique
  on `offline_uuid` still applies).

## License

MIT — see [LICENSE](LICENSE).
