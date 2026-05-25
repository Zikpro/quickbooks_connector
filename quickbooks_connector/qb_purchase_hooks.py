import frappe
from quickbooks_connector.api import create_qb_bill_from_purchase_invoice, get_settings


def on_purchase_invoice_submit(doc, method=None):
    """Auto sync Purchase Invoice to QB on submit if enabled"""
    settings = get_settings()
    auto = getattr(settings, "auto_sync_purchase_invoice", 0)
    if not auto:
        return
    try:
        if getattr(doc, "is_return", 0):
            from quickbooks_connector.api import manual_create_vendor_credit
            manual_create_vendor_credit(doc.name)
        else:
            create_qb_bill_from_purchase_invoice(doc)
    except Exception as e:
        frappe.log_error("QB Auto Purchase Invoice Sync Error", f"{doc.name}: {str(e)}")


def on_purchase_invoice_cancel(doc, method=None):
    """Void Purchase Invoice Bill in QB when cancelled"""
    try:
        settings = get_settings()
        if not settings.is_connected:
            return
        if not doc.quickbooks_id:
            return
        from quickbooks_connector.api import manual_void_bill
        result = manual_void_bill(doc.name)
        if not result.get("success"):
            frappe.log_error("QB Bill Void Error", f"{doc.name}: {result.get('error')}")
    except Exception as e:
        frappe.log_error("QB Bill Void Error", f"{doc.name}: {str(e)}")


def on_purchase_invoice_amend(doc, method=None):
    """Update QB Bill when Purchase Invoice is amended"""
    if not getattr(doc, "amended_from", None):
        return
    if doc.docstatus != 1:
        return
    try:
        from quickbooks_connector.api import manual_amend_bill
        manual_amend_bill(doc.name)
    except Exception as e:
        frappe.log_error("QB Bill Amend Error", f"{doc.name}: {str(e)}")