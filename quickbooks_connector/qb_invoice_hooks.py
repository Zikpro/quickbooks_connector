import frappe
from quickbooks_connector.api import create_qb_invoice_from_sales_invoice, get_settings


def on_sales_invoice_submit(doc, method=None):
    """Auto sync Sales Invoice to QB on submit if enabled"""
    settings = get_settings()
    auto = getattr(settings, "auto_sync_sales_invoice", 0)
    if not auto:
        return
    try:
        create_qb_invoice_from_sales_invoice(doc)
    except Exception as e:
        frappe.log_error("QuickBooks Auto Invoice Sync Error", f"{doc.name}: {str(e)}")


def on_sales_invoice_cancel(doc, method=None):
    """Void Sales Invoice in QB when cancelled in ERPNext"""
    try:
        settings = get_settings()
        if not settings.is_connected:
            return

        # Check karo QB mein invoice hai ya nahi
        qb_invoice_id = doc.quickbooks_id
        if not qb_invoice_id:
            return  # QB mein nahi hai — kuch karna nahi

        from quickbooks_connector.api import QuickBooksAPI
        api = QuickBooksAPI()

        # Step 1: QB se current invoice fetch karo (SyncToken zaroori hai)
        qb_response = api.make_request(
            f"invoice/{qb_invoice_id}",
            params={"minorversion": 65}
        )
        invoice_data = qb_response.get("Invoice", {})
        sync_token = invoice_data.get("SyncToken")

        if not sync_token:
            frappe.log_error(
                "QB Invoice Void Error",
                f"No SyncToken found for QB Invoice ID: {qb_invoice_id}"
            )
            return

        # Step 2: Void payload banao
        void_payload = {
            "Id": str(qb_invoice_id),
            "SyncToken": str(sync_token),
            "sparse": True
        }

        # Step 3: QB mein void karo
        api.make_request(
            "invoice",
            method="POST",
            data=void_payload,
            params={"minorversion": 65, "operation": "void"}
        )

        # Step 4: ERP mein status update karo
        doc.db_set("quickbooks_sync_status", "Voided")
        doc.db_set("quickbooks_sync_error", "")

        frappe.msgprint(
            f"QuickBooks Invoice {qb_invoice_id} has been voided successfully.",
            title="QB Invoice Voided",
            indicator="green"
        )

        from quickbooks_connector.api import log_action
        log_action(
            "ERPNext Invoice Cancelled -> QuickBooks Voided",
            {
                "sales_invoice": doc.name,
                "qb_invoice_id": qb_invoice_id
            },
            entity_type="Invoice",
            entity_id=qb_invoice_id
        )

    except Exception as e:
        frappe.log_error(
            "QB Invoice Void Error",
            f"Sales Invoice: {doc.name}, Error: {str(e)}"
        )