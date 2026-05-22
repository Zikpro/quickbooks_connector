import frappe
from frappe import _
from quickbooks_connector.api import get_settings


def on_payment_entry_cancel(doc, method=None):
    """Void Payment in QB when Payment Entry is cancelled in ERPNext"""
    try:
        settings = get_settings()
        if not settings.is_connected:
            return

        from quickbooks_connector.api import QuickBooksAPI, log_action
        api = QuickBooksAPI()

        # Check payment type — Customer payment or Supplier bill payment
        qb_payment_id = getattr(doc, "quickbooks_payment_id", None)
        qb_bill_payment_id = getattr(doc, "quickbooks_bill_payment_id", None)

        if not qb_payment_id and not qb_bill_payment_id:
            return  # Not synced to QB

        if qb_payment_id:
            # Customer Payment — void via /payment endpoint
            _void_qb_payment(api, qb_payment_id, doc, log_action)

        elif qb_bill_payment_id:
            # Supplier Bill Payment — void via /billpayment endpoint
            _void_qb_bill_payment(api, qb_bill_payment_id, doc, log_action)

    except Exception as e:
        frappe.log_error("QB Payment Void Error", f"Payment Entry: {doc.name}, Error: {str(e)}")


def _void_qb_payment(api, qb_payment_id, doc, log_action):
    """Void a customer payment in QB"""
    try:
        # Fetch current payment for SyncToken
        qb_response = api.make_request(
            f"payment/{qb_payment_id}",
            params={"minorversion": 65}
        )
        payment_data = qb_response.get("Payment", {})
        sync_token = payment_data.get("SyncToken")

        if not sync_token:
            frappe.log_error("QB Payment Void Error", f"No SyncToken for QB Payment ID: {qb_payment_id}")
            return

        # Void in QB
        api.make_request(
            "payment",
            method="POST",
            data={"Id": str(qb_payment_id), "SyncToken": str(sync_token), "sparse": True},
            params={"minorversion": 65, "operation": "void"}
        )

        # Update ERP
        frappe.db.set_value("Payment Entry", doc.name, "quickbooks_payment_id", "")

        frappe.msgprint(
            f"QuickBooks Payment {qb_payment_id} voided successfully.",
            title="QB Payment Voided", indicator="green"
        )

        log_action(
            "ERPNext Payment Entry Cancelled -> QuickBooks Payment Voided",
            {"payment_entry": doc.name, "qb_payment_id": qb_payment_id},
            entity_type="Payment", entity_id=qb_payment_id
        )

    except Exception as e:
        frappe.log_error("QB Payment Void Error", f"Payment: {doc.name}, Error: {str(e)}")


def _void_qb_bill_payment(api, qb_bill_payment_id, doc, log_action):
    """Void a supplier bill payment in QB"""
    try:
        # Fetch current bill payment for SyncToken
        qb_response = api.make_request(
            f"billpayment/{qb_bill_payment_id}",
            params={"minorversion": 65}
        )
        bill_payment_data = qb_response.get("BillPayment", {})
        sync_token = bill_payment_data.get("SyncToken")

        if not sync_token:
            frappe.log_error("QB Bill Payment Void Error", f"No SyncToken for QB Bill Payment ID: {qb_bill_payment_id}")
            return

        # Void in QB
        api.make_request(
            "billpayment",
            method="POST",
            data={"Id": str(qb_bill_payment_id), "SyncToken": str(sync_token), "sparse": True},
            params={"minorversion": 65, "operation": "void"}
        )

        # Update ERP
        frappe.db.set_value("Payment Entry", doc.name, "quickbooks_bill_payment_id", "")

        frappe.msgprint(
            f"QuickBooks Bill Payment {qb_bill_payment_id} voided successfully.",
            title="QB Bill Payment Voided", indicator="green"
        )

        log_action(
            "ERPNext Payment Entry Cancelled -> QuickBooks Bill Payment Voided",
            {"payment_entry": doc.name, "qb_bill_payment_id": qb_bill_payment_id},
            entity_type="BillPayment", entity_id=qb_bill_payment_id
        )

    except Exception as e:
        frappe.log_error("QB Bill Payment Void Error", f"Bill Payment: {doc.name}, Error: {str(e)}")