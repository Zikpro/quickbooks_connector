import frappe
from quickbooks_connector.api import create_qb_invoice_from_sales_invoice, get_settings


def on_sales_invoice_submit(doc, method=None):
    """Auto sync Sales Invoice to QB on submit if enabled"""
    settings = get_settings()
    auto = getattr(settings, "auto_sync_sales_invoice", 0)
    if not auto:
        return
    try:
        if getattr(doc, "is_return", 0):
            from quickbooks_connector.api import manual_create_credit_memo
            manual_create_credit_memo(doc.name)
        else:
            create_qb_invoice_from_sales_invoice(doc)
    except Exception as e:
        frappe.log_error("QB Auto Invoice Sync Error", f"{doc.name}: {str(e)}")


def on_sales_invoice_cancel(doc, method=None):
    """Void Sales Invoice in QB when cancelled in ERPNext"""
    try:
        settings = get_settings()
        if not settings.is_connected:
            return
        if not doc.quickbooks_id:
            return
        from quickbooks_connector.api import QuickBooksAPI, log_action
        api = QuickBooksAPI()
        qb_response = api.make_request(
            f"invoice/{doc.quickbooks_id}",
            params={"minorversion": 65}
        )
        invoice_data = qb_response.get("Invoice", {})
        sync_token = invoice_data.get("SyncToken")
        if not sync_token:
            return
        api.make_request(
            "invoice",
            method="POST",
            data={"Id": str(doc.quickbooks_id), "SyncToken": str(sync_token), "sparse": True},
            params={"minorversion": 65, "operation": "void"}
        )
        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_sync_status", "Voided")
        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_sync_error", "")
        log_action(
            "ERPNext Invoice Cancelled -> QuickBooks Voided",
            {"sales_invoice": doc.name, "qb_invoice_id": doc.quickbooks_id},
            entity_type="Invoice", entity_id=doc.quickbooks_id
        )
    except Exception as e:
        frappe.log_error("QB Invoice Void Error", f"{doc.name}: {str(e)}")


def on_sales_invoice_amend(doc, method=None):
    """Update QB Invoice when Sales Invoice is amended"""
    if not getattr(doc, "amended_from", None):
        return
    if doc.docstatus != 1:
        return
    try:
        from quickbooks_connector.api import manual_amend_invoice
        manual_amend_invoice(doc.name)
    except Exception as e:
        frappe.log_error("QB Invoice Amend Error", f"{doc.name}: {str(e)}")