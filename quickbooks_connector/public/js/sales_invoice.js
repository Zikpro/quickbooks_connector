frappe.ui.form.on("Sales Invoice", {
    refresh(frm) {
        if (frm.doc.docstatus === 1) {

            if (frm.doc.is_return) {
                // Return Invoice — Credit Memo button
                if (!frm.doc.quickbooks_id) {
                    frm.add_custom_button(__("Create Credit Memo in QB"), function() {
                        frappe.call({
                            method: "quickbooks_connector.api.manual_create_credit_memo",
                            args: { sales_invoice_name: frm.doc.name },
                            freeze: true,
                            freeze_message: __("Creating Credit Memo in QuickBooks..."),
                            callback: function(r) {
                                if (r.message && r.message.success) {
                                    frappe.msgprint({
                                        title: __("Success"),
                                        message: r.message.message,
                                        indicator: "green"
                                    });
                                    frm.reload_doc();
                                } else {
                                    frappe.msgprint({
                                        title: __("Error"),
                                        message: r.message ? r.message.error : "Failed",
                                        indicator: "red"
                                    });
                                }
                            }
                        });
                    }, __("QuickBooks"));

                } else {
                 
                    frm.add_custom_button(__("Credit Memo Synced ✓"), function() {
                        frappe.msgprint({
                            title: __("Already Synced"),
                            message: __("Credit Memo QB ID: ") + frm.doc.quickbooks_id,
                            indicator: "green"
                        });
                    }, __("QuickBooks"));
                }

            } else {
                // Normal Invoice — Push to QB button
                if (!frm.doc.quickbooks_id) {
                    frm.add_custom_button(__("Push to QuickBooks"), async () => {
                        const r = await frappe.call({
                            method: "quickbooks_connector.api.push_sales_invoice_to_quickbooks",
                            args: { sales_invoice_name: frm.doc.name },
                            freeze: true,
                            freeze_message: __("Pushing to QuickBooks...")
                        });
                        if (r.message && r.message.success) {
                            frappe.msgprint({
                                title: __("Success"),
                                message: r.message.message,
                                indicator: "green"
                            });
                            frm.reload_doc();
                        } else {
                            frappe.msgprint({
                                title: __("Error"),
                                message: r.message ? r.message.error : "Failed",
                                indicator: "red"
                            });
                        }
                    }, __("QuickBooks"));

                } else {
                    // Amended invoice
                    if (frm.doc.amended_from) {
                        frm.add_custom_button(__("Update QB Invoice"), function() {
                            frappe.call({
                                method: "quickbooks_connector.api.manual_amend_invoice",
                                args: { sales_invoice_name: frm.doc.name },
                                freeze: true,
                                freeze_message: __("Updating QuickBooks Invoice..."),
                                callback: function(r) {
                                    if (r.message && r.message.success) {
                                        frappe.msgprint({
                                            title: __("Success"),
                                            message: r.message.message,
                                            indicator: "green"
                                        });
                                        frm.reload_doc();
                                    } else {
                                        frappe.msgprint({
                                            title: __("Error"),
                                            message: r.message ? r.message.error : "Failed",
                                            indicator: "red"
                                        });
                                    }
                                }
                            });
                        }, __("QuickBooks"));
                    }
                }
            }
        }
    }
});