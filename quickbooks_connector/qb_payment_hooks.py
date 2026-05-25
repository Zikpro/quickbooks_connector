import frappe
from quickbooks_connector.api import get_settings


def on_payment_entry_cancel(doc, method=None):
    """Void Payment in QB when Payment Entry is cancelled"""
    try:
        settings = get_settings()
        if not settings.is_connected:
            return

        qb_payment_id = getattr(doc, "quickbooks_payment_id", None)
        qb_bill_payment_id = getattr(doc, "quickbooks_bill_payment_id", None)

        if not qb_payment_id and not qb_bill_payment_id:
            return

        from quickbooks_connector.api import manual_void_payment
        result = manual_void_payment(doc.name)

        if not result.get("success"):
            frappe.log_error("QB Payment Void Error", f"{doc.name}: {result.get('error')}")

    except Exception as e:
        frappe.log_error("QB Payment Void Error", f"{doc.name}: {str(e)}")