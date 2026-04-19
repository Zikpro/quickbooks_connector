frappe.listview_settings['Sales Invoice'] = {
    get_indicator: function(doc) {
        // Show only Synced or Error status, remove Ready to Sync
        if (doc.quickbooks_synced_status === "Synced") {
            return [__("Synced"), "green", "quickbooks_synced_status,=,Synced"];
        }
        if (doc.quickbooks_sync_error) {
            return [__("Sync Error"), "red", "quickbooks_sync_error,is,set"];
        }
        // Remove the Ready to Sync indicator
        return null;
    },
    
    onload: function(listview) {
        // Add bulk action button only (no row-level button)
        listview.page.add_action_item(__('Push to QuickBooks'), function() {
            // Get selected items
            const selected = listview.get_checked_items();
            
            // Filter only unsynced invoices from selected
            const unsynced_selected = selected.filter(doc => 
                doc.docstatus === 1 && 
                doc.quickbooks_synced_status !== "Synced"
            );
            
            if (unsynced_selected.length === 0 && selected.length > 0) {
                // All selected invoices are already synced
                frappe.msgprint({
                    title: __('Already Synced'),
                    message: __('All selected invoices are already synced to QuickBooks.'),
                    indicator: 'blue'
                });
                return;
            }
            
            if (unsynced_selected.length === 0) {
                // If nothing selected, get all unsynced invoices
                frappe.call({
                    method: 'frappe.client.get_list',
                    args: {
                        doctype: 'Sales Invoice',
                        filters: [
                            ['docstatus', '=', 1],
                            ['quickbooks_synced_status', '!=', 'Synced']
                        ],
                        fields: ['name', 'customer_name', 'grand_total', 'posting_date'],
                        limit: 1000
                    },
                    callback: function(r) {
                        if (r.message && r.message.length > 0) {
                            show_bulk_push_dialog(listview, r.message);
                        } else {
                            frappe.msgprint({
                                title: __('No Invoices to Sync'),
                                message: __('No unsynced invoices found.'),
                                indicator: 'blue'
                            });
                        }
                    }
                });
            } else {
                // Use filtered unsynced items
                show_bulk_push_dialog(listview, unsynced_selected);
            }
        });
    }
    // Removed the "button" section completely to remove row-level buttons
};

// Helper functions
function show_bulk_push_dialog(listview, invoices) {
    if (!invoices || invoices.length === 0) {
        frappe.msgprint({
            title: __('No Invoices Selected'),
            message: __('Please select invoices to push to QuickBooks.'),
            indicator: 'orange'
        });
        return;
    }
    
    const invoice_names = invoices.map(inv => inv.name);
    const message = invoices.length === 1 
        ? __('Push 1 invoice to QuickBooks?')
        : __('Push ' + invoices.length + ' invoices to QuickBooks?');
    
    // Create invoice details list (simplified without currency formatting)
    const invoiceDetails = invoices.slice(0, 10).map(inv => {
        return `• ${inv.name}${inv.customer_name ? ` (${inv.customer_name})` : ''}`;
    }).join('<br>');
    
    frappe.confirm(
        `<b>${message}</b><br><br>
        <div style="max-height: 200px; overflow-y: auto; margin: 10px 0; padding: 10px; background: #f8f9fa; border-radius: 4px;">
            <strong>Invoices to sync:</strong><br>
            ${invoiceDetails}
            ${invoices.length > 10 ? `<br>... and ${invoices.length - 10} more` : ''}
        </div>`,
        function() {
            execute_bulk_push(listview, invoice_names);
        },
        __('Cancel')
    );
}

function execute_bulk_push(listview, invoice_names) {
    if (!Array.isArray(invoice_names)) {
        frappe.msgprint({
            title: __('Error'),
            message: __('Invalid invoice names format.'),
            indicator: 'red'
        });
        return;
    }
    
    frappe.call({
        method: 'quickbooks_connector.api.bulk_push_sales_invoices',
        args: { invoice_names: invoice_names },
        freeze: true,
        freeze_message: __('Pushing invoices to QuickBooks...'),
        callback: function(r) {
            if (r.message) {
                const result = r.message;
                let message = `
                    <div style="max-height: 300px; overflow-y: auto;">
                        <table class="table table-bordered" style="width: 100%;">
                            <tr><th>Result</th><th>Count</th></tr>
                            <tr><td>✅ Success</td><td>${result.success || 0}</td></tr>
                            <tr><td>⏭️ Skipped</td><td>${result.skipped || 0}</td></tr>
                            <tr><td>❌ Failed</td><td>${result.failed || 0}</td></tr>
                        </table>
                    </div>
                `;
                
                frappe.msgprint({
                    title: __('Bulk Push Completed'),
                    indicator: result.failed > 0 ? 'orange' : 'green',
                    message: message
                });
                
                setTimeout(() => listview.refresh(), 1000);
            }
        }
    });
}