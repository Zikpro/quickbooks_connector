frappe.ui.form.on("Sales Invoice", {
    refresh(frm) {
        if (frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Push to QuickBooks"), async () => {
                const r = await frappe.call({
                    method: "quickbooks_connector.api.push_sales_invoice_to_quickbooks",
                    args: { sales_invoice_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Pushing to QuickBooks...")
                });
                if (r.message?.success) {
                    frappe.msgprint({ title: __("Success"), message: r.message.message, indicator: "green" });
                    frm.reload_doc();
                } else {
                    frappe.msgprint({ title: __("Error"), message: r.message?.error || "Failed", indicator: "red" });
                }
            }, __("QuickBooks"));
        }
    }
});