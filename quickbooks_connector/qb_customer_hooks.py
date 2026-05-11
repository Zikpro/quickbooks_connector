import frappe
from frappe import _


def on_customer_update(doc, method=None):
    """Auto sync customer address/contact to QuickBooks on update"""
    try:
        # Check if connected
        from quickbooks_connector.api import get_settings
        settings = get_settings()

        if not settings.is_connected:
            return

        # Check if customer has QB ID
        qb_customer_id = doc.quickbooks_id
        if not qb_customer_id:
            return

        # Sync to QB
        sync_customer_to_qb(doc, qb_customer_id)

    except Exception as e:
        frappe.log_error(
            title="QB Customer Auto Sync Error",
            message=f"Customer: {doc.name}, Error: {str(e)}"
        )


def sync_customer_to_qb(doc, qb_customer_id):
    """Update customer in QuickBooks"""
    from quickbooks_connector.api import QuickBooksAPI

    api = QuickBooksAPI()

    # Pehle current QB customer fetch karo (SyncToken zaroori hai update ke liye)
    qb_customer = api.make_request(
        f"customer/{qb_customer_id}",
        params={"minorversion": 65}
    )

    customer_data = qb_customer.get("Customer", {})
    sync_token = customer_data.get("SyncToken")

    if not sync_token:
        frappe.log_error("QB Customer Sync Error", f"No SyncToken for customer {qb_customer_id}")
        return

    # Update payload banao
    payload = {
        "Id": str(qb_customer_id),
        "SyncToken": str(sync_token),
        "DisplayName": doc.customer_name,
        "sparse": True  # Sirf provided fields update honge
    }

    # Email update
    if doc.email_id:
        payload["PrimaryEmailAddr"] = {"Address": doc.email_id}

    # Phone update
    if doc.mobile_no:
        payload["PrimaryPhone"] = {"FreeFormNumber": doc.mobile_no}

    # Address update — primary billing address dhundo
    # Customer primary address dhundo via Dynamic Link table
    address_name = frappe.db.get_value(
        "Dynamic Link",
        {
            "link_doctype": "Customer",
            "link_name": doc.name,
            "parenttype": "Address"
        },
        "parent"
    )

    # Agar primary address set hai toh use karo
    if doc.customer_primary_address:
        address_name = doc.customer_primary_address

    primary_address = None
    if address_name:
        primary_address = frappe.db.get_value(
            "Address",
            address_name,
            ["address_line1", "address_line2", "city", "state", 
            "pincode", "country", "email_id"],
            as_dict=True
        )

    if primary_address:
        bill_addr = {}
        if primary_address.address_line1:
            bill_addr["Line1"] = primary_address.address_line1
        if primary_address.address_line2:
            bill_addr["Line2"] = primary_address.address_line2
        if primary_address.city:
            bill_addr["City"] = primary_address.city
        if primary_address.state:
            bill_addr["CountrySubDivisionCode"] = primary_address.state
        if primary_address.pincode:
            bill_addr["PostalCode"] = primary_address.pincode
        if primary_address.country:
            bill_addr["Country"] = primary_address.country
        if bill_addr:
            payload["BillAddr"] = bill_addr

        # Address email bhi update karo
        if primary_address.email_id and not doc.email_id:
            payload["PrimaryEmailAddr"] = {"Address": primary_address.email_id}

    # QB mein update karo
    api.make_request(
        "customer",
        method="POST",
        data=payload,
        params={"minorversion": 65}
    )

    frappe.logger().info(f"Customer {doc.name} synced to QB successfully")