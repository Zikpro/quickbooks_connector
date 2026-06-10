import frappe


def after_install():
    """Create default Quickbooks Setting after installation"""
    try:
        if not frappe.db.exists("Quickbooks Setting", "Quickbooks Setting"):
            doc = frappe.get_doc({
                "doctype": "Quickbooks Setting",
                "name": "Quickbooks Setting"
            })
            doc.insert(ignore_permissions=True)
            frappe.db.commit()
    except Exception as e:
        frappe.log_error("QuickBooks Install Error", str(e))


def after_uninstall():
    """Cleanup after uninstallation"""
    try:
        # Custom fields cleanup
        frappe.db.delete("Custom Field", {
            "fieldname": ["like", "quickbooks_%"]
        })
        frappe.db.commit()
    except Exception as e:
        frappe.log_error("QuickBooks Uninstall Error", str(e))