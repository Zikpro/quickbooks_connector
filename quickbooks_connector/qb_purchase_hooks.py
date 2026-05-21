import frappe
from quickbooks_connector.api import create_qb_bill_from_purchase_invoice, get_settings


def on_purchase_invoice_submit(doc, method=None):
    """Auto sync Purchase Invoice to QB on submit if enabled"""
    settings = get_settings()
    auto = getattr(settings, "auto_sync_purchase_invoice", 0)
    if not auto:
        return
    try:
        create_qb_bill_from_purchase_invoice(doc)
    except Exception as e:
        frappe.log_error("QuickBooks Auto Purchase Invoice Sync Error", f"{doc.name}: {str(e)}")


def on_purchase_invoice_cancel(doc, method=None):
    """Void Purchase Invoice (Bill) in QB when cancelled in ERPNext"""
    try:
        settings = get_settings()
        if not settings.is_connected:
            return

        # Check karo QB mein bill hai ya nahi
        qb_bill_id = doc.quickbooks_id
        if not qb_bill_id:
            return  # QB mein nahi hai — kuch karna nahi

        from quickbooks_connector.api import QuickBooksAPI
        api = QuickBooksAPI()

        # Step 1: QB se current bill fetch karo (SyncToken zaroori hai)
        qb_response = api.make_request(
            f"bill/{qb_bill_id}",
            params={"minorversion": 65}
        )
        bill_data = qb_response.get("Bill", {})
        sync_token = bill_data.get("SyncToken")

        if not sync_token:
            frappe.log_error(
                "QB Bill Void Error",
                f"No SyncToken found for QB Bill ID: {qb_bill_id}"
            )
            return

        # Step 2: Void payload banao
        void_payload = {
            "Id": str(qb_bill_id),
            "SyncToken": str(sync_token),
            "sparse": True
        }

        # Step 3: QB mein void karo
        api.make_request(
            "bill",
            method="POST",
            data=void_payload,
            params={"minorversion": 65, "operation": "void"}
        )

        # Step 4: ERP mein status update karo
        doc.db_set("quickbooks_sync_status", "Voided")
        doc.db_set("quickbooks_sync_error", "")

        frappe.msgprint(
            f"QuickBooks Bill {qb_bill_id} has been voided successfully.",
            title="QB Bill Voided",
            indicator="green"
        )

        from quickbooks_connector.api import log_action
        log_action(
            "ERPNext Purchase Invoice Cancelled -> QuickBooks Bill Voided",
            {
                "purchase_invoice": doc.name,
                "qb_bill_id": qb_bill_id
            },
            entity_type="Bill",
            entity_id=qb_bill_id
        )

    except Exception as e:
        frappe.log_error(
            "QB Bill Void Error",
            f"Purchase Invoice: {doc.name}, Error: {str(e)}"
        )