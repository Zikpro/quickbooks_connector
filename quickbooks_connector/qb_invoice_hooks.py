import frappe
from frappe import _
from quickbooks_connector.api import create_qb_invoice_from_sales_invoice, get_settings


def on_sales_invoice_submit(doc, method=None):
    """Auto sync Sales Invoice to QB on submit if enabled"""
    settings = get_settings()
    auto = getattr(settings, "auto_sync_sales_invoice", 0)
    if not auto:
        return
    try:
        if getattr(doc, "is_return", 0):
            create_qb_credit_memo_from_return(doc)
        else:
            create_qb_invoice_from_sales_invoice(doc)
    except Exception as e:
        frappe.log_error("QuickBooks Auto Invoice Sync Error", f"{doc.name}: {str(e)}")


def on_sales_invoice_cancel(doc, method=None):
    """Void Sales Invoice in QB when cancelled in ERPNext"""
    try:
        settings = get_settings()
        if not settings.is_connected:
            return

        qb_invoice_id = doc.quickbooks_id
        if not qb_invoice_id:
            return

        from quickbooks_connector.api import QuickBooksAPI, log_action
        api = QuickBooksAPI()

        qb_response = api.make_request(
            f"invoice/{qb_invoice_id}",
            params={"minorversion": 65}
        )
        invoice_data = qb_response.get("Invoice", {})
        sync_token = invoice_data.get("SyncToken")

        if not sync_token:
            frappe.log_error("QB Invoice Void Error", f"No SyncToken for QB Invoice ID: {qb_invoice_id}")
            return

        api.make_request(
            "invoice",
            method="POST",
            data={"Id": str(qb_invoice_id), "SyncToken": str(sync_token), "sparse": True},
            params={"minorversion": 65, "operation": "void"}
        )

        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_sync_status", "Voided")
        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_sync_error", "")

        frappe.msgprint(
            f"QuickBooks Invoice {qb_invoice_id} voided successfully.",
            title="QB Invoice Voided", indicator="green"
        )

        log_action(
            "ERPNext Invoice Cancelled -> QuickBooks Voided",
            {"sales_invoice": doc.name, "qb_invoice_id": qb_invoice_id},
            entity_type="Invoice", entity_id=qb_invoice_id
        )

    except Exception as e:
        frappe.log_error("QB Invoice Void Error", f"Sales Invoice: {doc.name}, Error: {str(e)}")


def on_sales_invoice_amend(doc, method=None):
    """Update QB Invoice when Sales Invoice is amended in ERPNext"""
    try:
        # after_insert fires on every new doc — only proceed if amended
        if not getattr(doc, "amended_from", None):
            return

        # Only proceed when submitted
        if doc.docstatus != 1:
            return

        settings = get_settings()
        if not settings.is_connected:
            return

        original_qb_id = frappe.db.get_value(
            "Sales Invoice", doc.amended_from, "quickbooks_id"
        )
        if not original_qb_id:
            return

        from quickbooks_connector.api import QuickBooksAPI, log_action
        api = QuickBooksAPI()

        qb_response = api.make_request(
            f"invoice/{original_qb_id}",
            params={"minorversion": 65}
        )
        invoice_data = qb_response.get("Invoice", {})
        sync_token = invoice_data.get("SyncToken")

        if not sync_token:
            frappe.log_error("QB Invoice Amend Error", f"No SyncToken for QB Invoice ID: {original_qb_id}")
            return

        default_tax_code = getattr(settings, 'default_tax_code', '12') or '12'

        def get_qb_tax_code(tax_rate):
            rate = float(tax_rate or 0)
            if rate >= 20: return "3"
            elif rate >= 5: return "8"
            elif rate > 0: return "10"
            return default_tax_code

        invoice_tax_rate = 0
        if doc.taxes:
            for tax in doc.taxes:
                if float(tax.rate or 0) > 0:
                    invoice_tax_rate = float(tax.rate)
                    break

        qb_customer_id = frappe.db.get_value("Customer", doc.customer, "quickbooks_id")
        if not qb_customer_id:
            return

        lines = []
        for row in doc.items:
            qb_item_id = frappe.db.get_value("Item", row.item_code, "quickbooks_id")
            if not qb_item_id:
                continue
            lines.append({
                "DetailType": "SalesItemLineDetail",
                "Amount": float(row.amount),
                "Description": row.description or row.item_name or row.item_code,
                "SalesItemLineDetail": {
                    "ItemRef": {"value": str(qb_item_id)},
                    "Qty": float(row.qty),
                    "UnitPrice": float(row.rate),
                    "TaxCodeRef": {"value": get_qb_tax_code(invoice_tax_rate)}
                }
            })

        update_payload = {
            "Id": str(original_qb_id),
            "SyncToken": str(sync_token),
            "CustomerRef": {"value": str(qb_customer_id)},
            "TxnDate": str(doc.posting_date),
            "DueDate": str(doc.due_date),
            "Line": lines,
            "sparse": True
        }

        if invoice_tax_rate > 0:
            update_payload["GlobalTaxCalculation"] = "TaxExcluded"
        else:
            update_payload["GlobalTaxCalculation"] = "NotApplicable"

        api.make_request(
            "invoice",
            method="POST",
            data=update_payload,
            params={"minorversion": 65}
        )

        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_id", original_qb_id)
        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_sync_status", "Synced")
        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_sync_error", "")
        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_last_sync", frappe.utils.now_datetime())

        frappe.msgprint(
            f"QuickBooks Invoice {original_qb_id} updated successfully.",
            title="QB Invoice Updated", indicator="green"
        )

        log_action(
            "ERPNext Invoice Amended -> QuickBooks Invoice Updated",
            {"sales_invoice": doc.name, "qb_invoice_id": original_qb_id, "amended_from": doc.amended_from},
            entity_type="Invoice", entity_id=original_qb_id
        )

    except Exception as e:
        frappe.log_error("QB Invoice Amend Error", f"Sales Invoice: {doc.name}, Error: {str(e)}")


def create_qb_credit_memo_from_return(doc):
    """Create Credit Memo in QB from ERPNext Return Sales Invoice"""
    try:
        settings = get_settings()
        if not settings.is_connected:
            return

        if getattr(doc, "quickbooks_id", None):
            return

        from quickbooks_connector.api import QuickBooksAPI, log_action
        api = QuickBooksAPI()

        qb_customer_id = frappe.db.get_value("Customer", doc.customer, "quickbooks_id")
        if not qb_customer_id:
            frappe.throw(_(f"Customer '{doc.customer}' missing QuickBooks ID. Sync customers first."))

        default_tax_code = getattr(settings, 'default_tax_code', '12') or '12'

        def get_qb_tax_code(tax_rate):
            rate = float(tax_rate or 0)
            if rate >= 20: return "3"
            elif rate >= 5: return "8"
            elif rate > 0: return "10"
            return default_tax_code

        invoice_tax_rate = 0
        if doc.taxes:
            for tax in doc.taxes:
                if float(tax.rate or 0) > 0:
                    invoice_tax_rate = float(tax.rate)
                    break

        lines = []
        for row in doc.items:
            qb_item_id = frappe.db.get_value("Item", row.item_code, "quickbooks_id")
            if not qb_item_id:
                frappe.throw(_(f"Item '{row.item_code}' missing QuickBooks ID."))
            lines.append({
                "DetailType": "SalesItemLineDetail",
                "Amount": abs(float(row.amount)),
                "Description": row.description or row.item_name or row.item_code,
                "SalesItemLineDetail": {
                    "ItemRef": {"value": str(qb_item_id)},
                    "Qty": abs(float(row.qty)),
                    "UnitPrice": float(row.rate),
                    "TaxCodeRef": {"value": get_qb_tax_code(invoice_tax_rate)}
                }
            })

        payload = {
            "CustomerRef": {"value": str(qb_customer_id)},
            "DocNumber": str(doc.name),
            "TxnDate": str(doc.posting_date),
            "Line": lines
        }

        if invoice_tax_rate > 0:
            payload["GlobalTaxCalculation"] = "TaxExcluded"
        else:
            payload["GlobalTaxCalculation"] = "NotApplicable"

        if doc.customer_address:
            try:
                addr = frappe.get_doc("Address", doc.customer_address)
                bill_addr = {}
                if addr.address_line1: bill_addr["Line1"] = addr.address_line1
                if addr.address_line2: bill_addr["Line2"] = addr.address_line2
                if addr.city: bill_addr["City"] = addr.city
                if addr.state: bill_addr["CountrySubDivisionCode"] = addr.state
                if addr.pincode: bill_addr["PostalCode"] = addr.pincode
                if addr.country: bill_addr["Country"] = addr.country
                if bill_addr:
                    payload["BillAddr"] = bill_addr
            except Exception:
                pass

        qb_response = api.make_request(
            "creditmemo",
            method="POST",
            data=payload,
            params={"minorversion": 65}
        )

        credit_memo = qb_response.get("CreditMemo", {})
        credit_memo_id = credit_memo.get("Id")

        if not credit_memo_id:
            frappe.throw(_(f"QB Credit Memo creation failed: {frappe.as_json(qb_response)}"))

        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_id", credit_memo_id)
        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_sync_status", "Synced")
        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_sync_error", "")
        frappe.db.set_value("Sales Invoice", doc.name, "quickbooks_last_sync", frappe.utils.now_datetime())

        frappe.msgprint(
            f"Credit Memo created in QuickBooks. ID: {credit_memo_id}",
            title="QB Credit Memo Created", indicator="green"
        )

        log_action(
            "ERPNext Return Invoice -> QuickBooks Credit Memo Created",
            {"sales_invoice": doc.name, "qb_credit_memo_id": credit_memo_id},
            entity_type="CreditMemo", entity_id=credit_memo_id
        )

    except Exception as e:
        frappe.log_error("QB Credit Memo Error", f"Return Invoice: {doc.name}, Error: {str(e)}")
        frappe.throw(_(f"QB Credit Memo Error: {str(e)}"))


@frappe.whitelist()
def manual_create_credit_memo(sales_invoice_name):
    """Manually create Credit Memo in QB for a return Sales Invoice"""
    try:
        si = frappe.get_doc("Sales Invoice", sales_invoice_name)
        if not si.is_return:
            return {"success": False, "error": "This is not a return invoice"}
        
        from quickbooks_connector.qb_invoice_hooks import create_qb_credit_memo_from_return
        # Temporarily clear QB ID to allow processing
        original_qb_id = si.quickbooks_id
        si.quickbooks_id = None
        create_qb_credit_memo_from_return(si)
        return {"success": True, "message": "Credit Memo created successfully"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def manual_create_vendor_credit(purchase_invoice_name):
    """Manually create Vendor Credit in QB for a return Purchase Invoice"""
    try:
        pi = frappe.get_doc("Purchase Invoice", purchase_invoice_name)
        if not pi.is_return:
            return {"success": False, "error": "This is not a return invoice"}
        
        from quickbooks_connector.qb_purchase_hooks import create_qb_vendor_credit_from_return
        pi.quickbooks_id = None
        create_qb_vendor_credit_from_return(pi)
        return {"success": True, "message": "Vendor Credit created successfully"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def manual_void_bill(purchase_invoice_name):
    """Manually void a Bill in QB"""
    try:
        pi = frappe.get_doc("Purchase Invoice", purchase_invoice_name)
        from quickbooks_connector.qb_purchase_hooks import on_purchase_invoice_cancel
        on_purchase_invoice_cancel(pi)
        return {"success": True, "message": "Bill voided successfully"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@frappe.whitelist()
def manual_void_payment(payment_entry_name):
    """Manually void a Payment in QB"""
    try:
        pe = frappe.get_doc("Payment Entry", payment_entry_name)
        from quickbooks_connector.qb_payment_hooks import on_payment_entry_cancel
        on_payment_entry_cancel(pe)
        return {"success": True, "message": "Payment voided successfully"}
    except Exception as e:
        return {"success": False, "error": str(e)}