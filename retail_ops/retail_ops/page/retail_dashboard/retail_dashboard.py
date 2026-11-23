"""Retail Dashboard desk page.

The page shell and charts are built by retail_dashboard.js; every figure comes
from retail_ops.api.dashboard.get_dashboard, which enforces branch access the
same way the list views and reports do.
"""


def get_context(context):
	context.no_cache = 1
