import frappe
from quickbooks_connector.api import create_qb_invoice_from_sales_invoice, get_settings

def on_sales_invoice_submit(doc, method=None):
    settings = get_settings()

    # optional toggle field
    auto = getattr(settings, "auto_sync_sales_invoice", 0)
    if not auto:
        return

    try:
        create_qb_invoice_from_sales_invoice(doc)
    except Exception as e:
        # Don't block invoice submission
        frappe.log_error("QuickBooks Auto Invoice Sync Error", f"{doc.name}: {str(e)}")
