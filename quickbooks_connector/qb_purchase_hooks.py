import frappe
from frappe import _
from quickbooks_connector.api import create_qb_bill_from_purchase_invoice, get_settings


def on_purchase_invoice_submit(doc, method=None):
    """Auto sync Purchase Invoice to QB on submit if enabled"""
    settings = get_settings()
    auto = getattr(settings, "auto_sync_purchase_invoice", 0)
    if not auto:
        return
    try:
        if getattr(doc, "is_return", 0):
            create_qb_vendor_credit_from_return(doc)
        else:
            create_qb_bill_from_purchase_invoice(doc)
    except Exception as e:
        frappe.log_error("QuickBooks Auto Purchase Invoice Sync Error", f"{doc.name}: {str(e)}")


def on_purchase_invoice_cancel(doc, method=None):
    """Void Purchase Invoice (Bill) in QB when cancelled in ERPNext"""
    try:
        settings = get_settings()
        if not settings.is_connected:
            return

        qb_bill_id = doc.quickbooks_id
        if not qb_bill_id:
            return

        from quickbooks_connector.api import QuickBooksAPI, log_action
        api = QuickBooksAPI()

        qb_response = api.make_request(
            f"bill/{qb_bill_id}",
            params={"minorversion": 65}
        )
        bill_data = qb_response.get("Bill", {})
        sync_token = bill_data.get("SyncToken")

        if not sync_token:
            frappe.log_error("QB Bill Void Error", f"No SyncToken for QB Bill ID: {qb_bill_id}")
            return

        api.make_request(
            "bill",
            method="POST",
            data={"Id": str(qb_bill_id), "SyncToken": str(sync_token), "sparse": True},
            params={"minorversion": 65, "operation": "void"}
        )

        frappe.db.set_value("Purchase Invoice", doc.name, "quickbooks_sync_status", "Voided")
        frappe.db.set_value("Purchase Invoice", doc.name, "quickbooks_sync_error", "")

        frappe.msgprint(
            f"QuickBooks Bill {qb_bill_id} voided successfully.",
            title="QB Bill Voided", indicator="green"
        )

        log_action(
            "ERPNext Purchase Invoice Cancelled -> QuickBooks Bill Voided",
            {"purchase_invoice": doc.name, "qb_bill_id": qb_bill_id},
            entity_type="Bill", entity_id=qb_bill_id
        )

    except Exception as e:
        frappe.log_error("QB Bill Void Error", f"Purchase Invoice: {doc.name}, Error: {str(e)}")


def on_purchase_invoice_amend(doc, method=None):
    """Update QB Bill when Purchase Invoice is amended in ERPNext"""
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
            "Purchase Invoice", doc.amended_from, "quickbooks_id"
        )
        if not original_qb_id:
            return

        from quickbooks_connector.api import QuickBooksAPI, log_action
        api = QuickBooksAPI()

        qb_response = api.make_request(
            f"bill/{original_qb_id}",
            params={"minorversion": 65}
        )
        bill_data = qb_response.get("Bill", {})
        sync_token = bill_data.get("SyncToken")

        if not sync_token:
            frappe.log_error("QB Bill Amend Error", f"No SyncToken for QB Bill ID: {original_qb_id}")
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

        qb_vendor_id = frappe.db.get_value("Supplier", doc.supplier, "quickbooks_id")
        if not qb_vendor_id:
            return

        qb_account_id = frappe.db.get_value(
            "Account",
            [
                ["company", "=", settings.company],
                ["quickbooks_id", "!=", ""],
                ["quickbooks_id", "!=", None],
                ["account_type", "in", ["Expense Account", "Cost of Goods Sold"]]
            ],
            "quickbooks_id"
        ) or "69"

        tax_code_id = get_qb_tax_code(invoice_tax_rate)

        lines = []
        for row in doc.items:
            lines.append({
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": float(row.amount or 0),
                "Description": f"{row.item_code}: {row.description or row.item_name or ''}",
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": qb_account_id},
                    "TaxCodeRef": {"value": tax_code_id}
                }
            })

        update_payload = {
            "Id": str(original_qb_id),
            "SyncToken": str(sync_token),
            "VendorRef": {"value": str(qb_vendor_id)},
            "TxnDate": str(doc.posting_date),
            "Line": lines,
            "sparse": True
        }

        if getattr(doc, "due_date", None):
            update_payload["DueDate"] = str(doc.due_date)

        if invoice_tax_rate > 0:
            update_payload["GlobalTaxCalculation"] = "TaxExcluded"
        else:
            update_payload["GlobalTaxCalculation"] = "NotApplicable"

        api.make_request(
            "bill",
            method="POST",
            data=update_payload,
            params={"minorversion": 65}
        )

        frappe.db.set_value("Purchase Invoice", doc.name, "quickbooks_id", original_qb_id)
        frappe.db.set_value("Purchase Invoice", doc.name, "quickbooks_sync_status", "Synced")
        frappe.db.set_value("Purchase Invoice", doc.name, "quickbooks_sync_error", "")
        frappe.db.set_value("Purchase Invoice", doc.name, "quickbooks_last_sync", frappe.utils.now_datetime())

        frappe.msgprint(
            f"QuickBooks Bill {original_qb_id} updated successfully.",
            title="QB Bill Updated", indicator="green"
        )

        log_action(
            "ERPNext Purchase Invoice Amended -> QuickBooks Bill Updated",
            {"purchase_invoice": doc.name, "qb_bill_id": original_qb_id, "amended_from": doc.amended_from},
            entity_type="Bill", entity_id=original_qb_id
        )

    except Exception as e:
        frappe.log_error("QB Bill Amend Error", f"Purchase Invoice: {doc.name}, Error: {str(e)}")


def create_qb_vendor_credit_from_return(doc):
    """Create Vendor Credit in QB from ERPNext Return Purchase Invoice"""
    try:
        settings = get_settings()
        if not settings.is_connected:
            return

        if getattr(doc, "quickbooks_id", None):
            return

        from quickbooks_connector.api import QuickBooksAPI, log_action
        api = QuickBooksAPI()

        qb_vendor_id = frappe.db.get_value("Supplier", doc.supplier, "quickbooks_id")
        if not qb_vendor_id:
            frappe.throw(_(f"Supplier '{doc.supplier}' missing QuickBooks ID. Sync suppliers first."))

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

        qb_account_id = frappe.db.get_value(
            "Account",
            [
                ["company", "=", settings.company],
                ["quickbooks_id", "!=", ""],
                ["quickbooks_id", "!=", None],
                ["account_type", "in", ["Expense Account", "Cost of Goods Sold"]]
            ],
            "quickbooks_id"
        ) or "69"

        tax_code_id = get_qb_tax_code(invoice_tax_rate)

        lines = []
        for row in doc.items:
            lines.append({
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": abs(float(row.amount or 0)),
                "Description": f"{row.item_code}: {row.description or row.item_name or ''}",
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": qb_account_id},
                    "TaxCodeRef": {"value": tax_code_id}
                }
            })

        if not lines:
            frappe.throw(_("Return Purchase Invoice has no items."))

        payload = {
            "VendorRef": {"value": str(qb_vendor_id)},
            "TxnDate": str(doc.posting_date),
            "DocNumber": str(doc.name),
            "Line": lines
        }

        if invoice_tax_rate > 0:
            payload["GlobalTaxCalculation"] = "TaxExcluded"
        else:
            payload["GlobalTaxCalculation"] = "NotApplicable"

        if getattr(doc, "due_date", None):
            payload["DueDate"] = str(doc.due_date)

        qb_response = api.make_request(
            "vendorcredit",
            method="POST",
            data=payload,
            params={"minorversion": 65}
        )

        vendor_credit = qb_response.get("VendorCredit", {})
        vendor_credit_id = vendor_credit.get("Id")

        if not vendor_credit_id:
            frappe.throw(_(f"QB Vendor Credit creation failed: {frappe.as_json(qb_response)}"))

        frappe.db.set_value("Purchase Invoice", doc.name, "quickbooks_id", vendor_credit_id)
        frappe.db.set_value("Purchase Invoice", doc.name, "quickbooks_sync_status", "Synced")
        frappe.db.set_value("Purchase Invoice", doc.name, "quickbooks_sync_error", "")
        frappe.db.set_value("Purchase Invoice", doc.name, "quickbooks_last_sync", frappe.utils.now_datetime())

        frappe.msgprint(
            f"Vendor Credit created in QuickBooks. ID: {vendor_credit_id}",
            title="QB Vendor Credit Created", indicator="green"
        )

        log_action(
            "ERPNext Return Purchase Invoice -> QuickBooks Vendor Credit Created",
            {"purchase_invoice": doc.name, "qb_vendor_credit_id": vendor_credit_id},
            entity_type="VendorCredit", entity_id=vendor_credit_id
        )

    except Exception as e:
        frappe.log_error("QB Vendor Credit Error", f"Return Purchase Invoice: {doc.name}, Error: {str(e)}")
        frappe.throw(_(f"QB Vendor Credit Error: {str(e)}"))