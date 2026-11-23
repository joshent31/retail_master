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
  optional "no negative stock" rule per branch.
- **Receipts** — print-friendly receipt with branch footer and cashier name.

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

## Reports

Desk → **Reports** (or the Awesome Bar by name). All reports are branch-aware:
users with branch User Permissions only see their own branches, and a branch
filter outside their access is refused.

| Report | Answers | Filters |
|---|---|---|
| **Retail Sales Summary** | Daily sales per branch: bills, net, discount, tax, billed, and the cash/card/UPI/credit split | From/To date, Branch, Warehouse |
| **Retail Item Sales** | Which products sell, how much and their share of sales | From/To date, Branch, Category |
| **Retail Tax Summary** | Tax collected per tax rate, for filing | From/To date, Branch |
| **Retail Stock On Hand** | Quantity and value of stock per product and warehouse | Branch, Warehouse, Category, Only In-Stock |
| **Retail Shift Reconciliation** | Closing cash vs expected cash for every day session, with variance | From/To date, Branch, Status, Only Cash Differences |
| **Retail Credit Outstanding** | Customers who still owe money, oldest dues first | As On date, Branch, Older Than (days) |

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
| Retail Product | Item master with barcode, rate, tax rate |
| Retail Day Session | One business day for a branch + warehouse; opening and closing figures |
| Retail Invoice | Submittable bill (`RI-.YYYY.-`) linked to session, branch, warehouse |
| Retail Invoice Item | Bill line (child) |
| Retail Invoice Payment | Payment split (child) |
| Retail Stock Balance | Running quantity per product + warehouse |
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
| `retail_ops.api.sessions.list_my_days` | Recent sessions for the user's branches |
| `retail_ops.api.sync.push` | Replay offline day operations and bills |
| `retail_ops.api.sync.pull` | Refresh catalog + stock (optionally ``since``) |
| `retail_ops.api.sync.status` | Server time / reachability probe |
| `retail_ops.permissions.grant_branch_access` | Grant a user a branch |
| `retail_ops.permissions.revoke_branch_access` | Revoke a branch |
| `retail_ops.api.dashboard.get_dashboard` | Number cards and chart series for the Retail Dashboard |
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
- **Stock**: `Retail Stock Balance` is read-only in Desk; use
  `retail_ops.stock.rebuild_stock_balance` if you ever need to recompute it
  from submitted bills.

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

- No purchase/receiving or returns/credit notes yet (only invoice cancel).
- Stock balances are per warehouse; there is no inter-warehouse transfer UI.
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
