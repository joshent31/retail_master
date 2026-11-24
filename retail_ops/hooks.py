app_name = "retail_ops"
app_title = "Retail Ops"
app_publisher = "Retail Ops"
app_description = "Offline-first multi-branch / multi-warehouse retail POS for Frappe v16+"
app_email = "dev@example.com"
app_license = "MIT"
app_version = "0.1.0"
required_apps = []

# ---------------------------------------------------------------------------
# Website
# ---------------------------------------------------------------------------
# POS terminal page: https://your-site/retail-pos
# The service worker is served from /assets, so the response needs the
# Service-Worker-Allowed header to control the /retail-pos scope.
website_route_rules = []

after_request = ["retail_ops.utils.add_service_worker_headers"]

# ---------------------------------------------------------------------------
# Document events
# ---------------------------------------------------------------------------
doc_events = {
    "Retail Invoice": {
        "validate": "retail_ops.invoices.validate_invoice",
        "on_submit": "retail_ops.invoices.on_invoice_submit",
        "on_cancel": "retail_ops.invoices.on_invoice_cancel",
    },
    "Retail Day Session": {
        "validate": "retail_ops.sessions.validate_session",
    },
    "Retail Offer": {
        "on_update": "retail_ops.offers.validate",
        "on_trash": "retail_ops.offers.on_trash",
    },
    "User Permission": {
        "after_insert": "retail_ops.permissions.clear_cache_after_user_permission_change",
        "on_update": "retail_ops.permissions.clear_cache_after_user_permission_change",
        "on_trash": "retail_ops.permissions.clear_cache_after_user_permission_change",
    },
}

# ---------------------------------------------------------------------------
# Row level permissions (branch isolation)
# ---------------------------------------------------------------------------
permission_query_conditions = {
    "Retail Branch": "retail_ops.permissions.branch_query_conditions",
    "Retail Warehouse": "retail_ops.permissions.warehouse_query_conditions",
    "Retail Day Session": "retail_ops.permissions.session_query_conditions",
    "Retail Invoice": "retail_ops.permissions.invoice_query_conditions",
    "Retail Stock Balance": "retail_ops.permissions.stock_query_conditions",
    "Retail Purchase Receipt": "retail_ops.permissions.purchase_receipt_query_conditions",
}

has_permission = {
    "Retail Branch": "retail_ops.permissions.branch_has_permission",
    "Retail Warehouse": "retail_ops.permissions.warehouse_has_permission",
    "Retail Day Session": "retail_ops.permissions.document_has_permission",
    "Retail Invoice": "retail_ops.permissions.document_has_permission",
    "Retail Stock Balance": "retail_ops.permissions.document_has_permission",
    "Retail Purchase Receipt": "retail_ops.permissions.document_has_permission",
}

# ---------------------------------------------------------------------------
# Install / migrate
# ---------------------------------------------------------------------------
after_install = "retail_ops.install.after_install"
after_migrate = "retail_ops.install.after_migrate"

# ---------------------------------------------------------------------------
# Fixtures kept in sync with `bench export-fixtures`
# ---------------------------------------------------------------------------
fixtures = []
