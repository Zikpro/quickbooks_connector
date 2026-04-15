frappe.ui.form.on("Quickbooks Setting", {
    onload: function(frm) {
        setup_form_customizations(frm);
        if (frm.doc.is_connected) {
            start_connection_monitor(frm);
        }
    },

    refresh: function(frm) {
        frm.clear_custom_buttons();
        setup_quickbooks_buttons(frm);
        setup_sync_section(frm);
        setup_logs_section(frm);
        update_ui_based_on_connection(frm);
        toggle_environment_fields(frm);
    },

    environment: function(frm) {
        toggle_environment_fields(frm);
    }
});

// ======================================================
// FORM SETUP FUNCTIONS
// ======================================================

function setup_form_customizations(frm) {
    add_custom_styles();

    if (frm.fields_dict.connection_status) {
        frm.fields_dict.connection_status.df.read_only = 1;
    }

    if (!frm.doc.environment) {
        frm.set_value('environment', 'Production');
    }
}

function setup_quickbooks_buttons(frm) {
    const button_group = __("QuickBooks");

    add_connection_status_indicator(frm);

    if (!frm.doc.is_connected) {
        frm.add_custom_button(
            __("Connect to QuickBooks"),
            function() { connect_to_quickbooks(frm); },
            button_group
        );

        frm.add_custom_button(
            __("Validate Settings"),
            function() { validate_quickbooks_setting(frm); },
            button_group
        );

    } else {
        frm.add_custom_button(
            __("Disconnect QuickBooks"),
            function() { disconnect_quickbooks(frm); },
            button_group
        );

        frm.add_custom_button(
            __("Test Connection"),
            function() { test_quickbooks_connection(frm); },
            button_group
        );

        frm.add_custom_button(
            __("Refresh Tokens"),
            function() { refresh_quickbooks_tokens(frm); },
            button_group
        );

        if (frm.doc.company_name) {
            frm.add_custom_button(
                __("View Company Info"),
                function() { view_company_info(frm); },
                button_group
            );
        }
    }
}

function setup_sync_section(frm) {
    const sync_group = __("Sync");
    const debug_group = __("Debug");

    if (frm.doc.is_connected) {

        // SYNC ALL
        frm.add_custom_button(
            __("Sync All"),
            function() { sync_all_data(frm); },
            sync_group
        );

        // SYNC CUSTOMERS
        frm.add_custom_button(
            __("Sync Customers"),
            function() { sync_customers_data(frm); },
            sync_group
        );

        // SYNC SUPPLIERS
        frm.add_custom_button(
            __("Sync Suppliers"),
            function() {
                frappe.call({
                    method: "quickbooks_connector.api.sync_suppliers",
                    freeze: true,
                    freeze_message: __("Syncing Suppliers from QuickBooks..."),
                    callback(r) {
                        if (r.message && r.message.success) {
                            frappe.msgprint({ title: __("Sync Successful"), message: r.message.message, indicator: "green" });
                        } else {
                            frappe.msgprint({ title: __("Sync Failed"), message: r.message ? r.message.error : __("Unknown error"), indicator: "red" });
                        }
                    }
                });
            },
            sync_group
        );

        // SYNC ITEMS
        frm.add_custom_button(
            __("Sync Items"),
            function() { sync_items_data(frm); },
            sync_group
        );

        // SYNC ACCOUNTS
        frm.add_custom_button(
            __("Sync Accounts"),
            function() {
                frappe.call({
                    method: "quickbooks_connector.api.sync_accounts",
                    freeze: true,
                    freeze_message: __("Syncing accounts from QuickBooks..."),
                    callback: function(r) {
                        if (r.message && r.message.success) {
                            frappe.msgprint({ title: __("Sync Successful"), message: r.message.message, indicator: "green" });
                        } else {
                            frappe.msgprint({ title: __("Sync Failed"), message: r.message ? r.message.error : __("Unknown error"), indicator: "red" });
                        }
                    }
                });
            },
            sync_group
        );

        // SYNC PAYMENTS
        frm.add_custom_button(
            __("Sync Payments"),
            function() { sync_payments_data(frm); },
            sync_group
        );

        // SYNC BILL PAYMENTS
        frm.add_custom_button(
            __("Sync Bill Payments"),
            function() {
                frappe.call({
                    method: "quickbooks_connector.api.sync_bill_payments",
                    freeze: true,
                    freeze_message: __("Syncing bill payments from QuickBooks..."),
                    callback: function(r) {
                        if (r.message && r.message.success) {
                            frappe.msgprint({ title: __("Sync Successful"), message: r.message.message, indicator: "green" });
                        } else {
                            frappe.msgprint({ title: __("Sync Failed"), message: r.message ? r.message.error : __("Unknown error"), indicator: "red" });
                        }
                    }
                });
            },
            sync_group
        );

        // SYNC STATUS
        frm.add_custom_button(
            __("Sync Status"),
            function() { view_sync_status(frm); },
            sync_group
        );

        // DEBUG BUTTONS
        frm.add_custom_button(
            __("Debug Account Sync"),
            function() {
                frappe.call({
                    method: "quickbooks_connector.api.debug_account_sync",
                    callback: function(r) {
                        if (r.message && r.message.success) {
                            let message = `
                                <div style="max-height: 400px; overflow-y: auto;">
                                    <h4>QuickBooks Accounts (${r.message.qb_accounts.length}):</h4>
                                    <pre>${JSON.stringify(r.message.qb_accounts, null, 2)}</pre>
                                    <h4>ERPNext Accounts (${r.message.erp_accounts.length}):</h4>
                                    <pre>${JSON.stringify(r.message.erp_accounts, null, 2)}</pre>
                                </div>
                            `;
                            frappe.msgprint({ title: __("Account Debug Info"), message: message, width: 'large' });
                        }
                    }
                });
            },
            debug_group
        );

        frm.add_custom_button(
            __("Test Bill Creation"),
            function() {
                frappe.call({
                    method: "quickbooks_connector.api.test_bill_creation",
                    freeze: true,
                    callback: function(r) {
                        if (r.message && r.message.success) {
                            let message = `
                                <div style="max-height: 400px; overflow-y: auto;">
                                    <h4>✅ Test Bill Created Successfully!</h4>
                                    <p>${r.message.message}</p>
                                    <pre style="background: #f8f9fa; padding: 10px;">${JSON.stringify(r.message.payload, null, 2)}</pre>
                                    <pre style="background: #f8f9fa; padding: 10px;">${JSON.stringify(r.message.response, null, 2)}</pre>
                                </div>
                            `;
                            frappe.msgprint({ title: __("Bill Test Successful"), message: message, width: 'large' });
                        } else {
                            frappe.msgprint({ title: __("Bill Test Failed"), message: r.message ? r.message.error : __("Unknown error"), indicator: "red" });
                        }
                    }
                });
            },
            debug_group
        );

        frm.add_custom_button(
            __("Test Bill API"),
            function() {
                frappe.call({
                    method: "quickbooks_connector.api.test_bill_api",
                    freeze: true,
                    callback: function(r) {
                        frappe.msgprint(JSON.stringify(r.message, null, 2));
                    }
                });
            },
            debug_group
        );
    }
}

function setup_logs_section(frm) {
    frm.add_custom_button("View Sync Logs", () => {
        frappe.set_route("List", "Quickbooks Sync Log", { quickbook_settings: frm.doc.name });
    }, "Logs");

    frm.add_custom_button("Clear Logs", async () => {
        const r = await frappe.call({
            method: "quickbooks_connector.api.clear_sync_logs",
            args: { settings_name: frm.doc.name }
        });
        frappe.msgprint(r.message?.message || "Logs cleared");
    }, "Logs");
}

function update_ui_based_on_connection(frm) {
    const status_field = frm.get_field('connection_status');
    if (status_field && status_field.$wrapper) {
        const badge = frm.doc.is_connected ?
            `<span class="indicator green">● Connected</span>` :
            `<span class="indicator red">● Disconnected</span>`;

        status_field.$wrapper.find('.control-value').html(`
            <div style="display: flex; align-items: center; gap: 10px;">
                ${badge}
                ${frm.doc.company_name ? `<strong>${frm.doc.company_name}</strong>` : ''}
            </div>
        `);
    }

    if (frm.doc.is_connected && frm.doc.company_name) {
        frm.set_title(`${frm.doctype}: ${frm.doc.company_name}`);
    }

    frm.toggle_display(['last_sync', 'company_name'], frm.doc.is_connected);
}

function toggle_environment_fields(frm) {
    if (frm.doc.environment === 'Sandbox') {
        frm.toggle_display('api_endpoint_sandbox', true);
        frm.toggle_display('api_endpoint_production', false);
    } else {
        frm.toggle_display('api_endpoint_sandbox', false);
        frm.toggle_display('api_endpoint_production', true);
    }
}

function add_connection_status_indicator(frm) {
    const indicator = frm.doc.is_connected ?
        `<div class="qb-status-indicator connected">
            <span class="indicator green"></span>
            Connected to ${frm.doc.company_name || 'QuickBooks'}
            ${frm.doc.last_sync ? `<br><small>Last sync: ${moment(frm.doc.last_sync).fromNow()}</small>` : ''}
        </div>` :
        `<div class="qb-status-indicator disconnected">
            <span class="indicator red"></span>
            Not connected to QuickBooks
        </div>`;

    if (frm.doc.is_connected) {
        frappe.show_alert({ message: __(`Connected to ${frm.doc.company_name || 'QuickBooks'}`), indicator: 'green' }, 10);
    }

    const $wrapper = $(frm.wrapper);
    const existingIndicator = $wrapper.find('.qb-status-indicator');
    if (existingIndicator.length) {
        existingIndicator.replaceWith(indicator);
    } else {
        $wrapper.find('.form-section:first').before(`<div class="qb-status-section">${indicator}</div>`);
    }
}

// ======================================================
// QUICKBOOKS ACTION FUNCTIONS
// ======================================================

function connect_to_quickbooks(frm) {
    const required_fields = ['client_id', 'client_secret', 'redirect_uri'];
    const missing_fields = [];

    required_fields.forEach(field => {
        if (!frm.doc[field]) {
            missing_fields.push(field.replace(/_/g, ' '));
        }
    });

    if (missing_fields.length > 0) {
        frappe.msgprint({
            title: __("Missing Required Fields"),
            message: __("Please fill in the following fields before connecting:") + "<br><br>" +
                missing_fields.map(field => `• ${field}`).join("<br>"),
            indicator: "orange"
        });
        return;
    }

    frappe.confirm(
        __("<b>Connect to QuickBooks</b><br><br>You will be redirected to QuickBooks to authorize ERPNext access."),
        function() {
            frappe.show_progress(__('Connecting...'), 10, 100, __('Preparing connection'));

            frappe.call({
                method: "quickbooks_connector.api.get_authorization_url",
                freeze: true,
                freeze_message: __("Generating QuickBooks authorization URL..."),
                callback: function(r) {
                    frappe.hide_progress();

                    if (r.message && r.message.success) {
                        const authWindow = window.open(r.message.authorization_url, 'QuickBooks Auth', 'width=800,height=600,scrollbars=yes');

                        if (!authWindow) {
                            frappe.msgprint({ title: __("Popup Blocked"), message: __("Please allow popups for this site."), indicator: "orange" });
                            return;
                        }

                        const pollTimer = setInterval(function() {
                            if (authWindow.closed) {
                                clearInterval(pollTimer);
                                setTimeout(() => { frm.reload_doc(); }, 1000);
                            }
                        }, 500);

                    } else {
                        frappe.msgprint({
                            title: __("Connection Error"),
                            message: r.message ? (r.message.error || __("Failed to generate authorization URL")) : __("Server error occurred"),
                            indicator: "red"
                        });
                    }
                }
            });
        },
        __("Cancel")
    );
}

function test_quickbooks_connection(frm) {
    frappe.call({
        method: "quickbooks_connector.api.test_connection",
        freeze: true,
        freeze_message: __("Testing QuickBooks connection..."),
        callback: function(r) {
            if (r.message && r.message.success) {
                frappe.msgprint({ title: __("Connection Successful"), message: __("Successfully connected to: ") + `<b>${r.message.company_name}</b>`, indicator: "green" });
                frm.reload_doc();
            } else {
                frappe.msgprint({ title: __("Connection Failed"), message: r.message ? (r.message.error || __("Unknown error")) : __("Server error"), indicator: "red" });
            }
        }
    });
}

function disconnect_quickbooks(frm) {
    frappe.confirm(
        __("<b>Disconnect QuickBooks</b><br><br>This will remove all connection data and tokens. Are you sure?"),
        function() {
            frappe.call({
                method: "quickbooks_connector.api.disconnect",
                freeze: true,
                freeze_message: __("Disconnecting from QuickBooks..."),
                callback: function(r) {
                    if (r.message && r.message.success) {
                        frappe.msgprint({ title: __("Disconnected"), message: __("Successfully disconnected from QuickBooks."), indicator: "green" });
                        frm.reload_doc();
                    } else {
                        frappe.msgprint({ title: __("Disconnect Error"), message: r.message ? r.message.error : __("Server error"), indicator: "red" });
                    }
                }
            });
        },
        __("Cancel")
    );
}

function refresh_quickbooks_tokens(frm) {
    frappe.call({
        method: "quickbooks_connector.api.refresh_tokens",
        freeze: true,
        freeze_message: __("Refreshing QuickBooks tokens..."),
        callback: function(r) {
            if (r.message && r.message.success) {
                frappe.msgprint({ title: __("Tokens Refreshed"), message: __("Tokens refreshed successfully."), indicator: "green" });
                frm.reload_doc();
            } else {
                frappe.msgprint({ title: __("Token Refresh Failed"), message: r.message ? r.message.error : __("Server error"), indicator: "orange" });
            }
        }
    });
}

function validate_quickbooks_setting(frm) {
    const errors = [];

    if (!frm.doc.client_id) errors.push("Client ID is required");
    if (!frm.doc.client_secret) errors.push("Client Secret is required");
    if (!frm.doc.redirect_uri) errors.push("Redirect URI is required");
    if (!frm.doc.authorization_endpoint) errors.push("Authorization Endpoint is required");
    if (!frm.doc.token_endpoint) errors.push("Token Endpoint is required");

    if (frm.doc.environment === 'Sandbox' && !frm.doc.api_endpoint_sandbox) {
        errors.push("Sandbox API Endpoint is required");
    }
    if (frm.doc.environment === 'Production' && !frm.doc.api_endpoint_production) {
        errors.push("Production API Endpoint is required");
    }

    if (errors.length > 0) {
        frappe.msgprint({
            title: __("Validation Failed"),
            message: `<b>${__("Please fix the following issues:")}</b><br><br>` + errors.map(e => `• ${e}`).join("<br>"),
            indicator: "orange"
        });
    } else {
        frappe.msgprint({ title: __("Validation Successful"), message: __("All required settings are properly configured."), indicator: "green" });
    }
}

function view_company_info(frm) {
    frappe.call({
        method: "quickbooks_connector.api.get_company_info",
        freeze: true,
        freeze_message: __("Fetching company information..."),
        callback: function(r) {
            if (r.message && r.message.CompanyInfo) {
                const company = r.message.CompanyInfo;
                let message = `
                    <div style="max-height: 400px; overflow-y: auto;">
                        <table class="table table-bordered" style="width: 100%;">
                            <tr><th>Company Name</th><td>${company.CompanyName || 'N/A'}</td></tr>
                            <tr><th>Legal Name</th><td>${company.LegalName || 'N/A'}</td></tr>
                            <tr><th>Country</th><td>${company.Country || 'N/A'}</td></tr>
                            <tr><th>Email</th><td>${company.Email ? company.Email.Address : 'N/A'}</td></tr>
                            <tr><th>Fiscal Year Start</th><td>${company.FiscalYearStartMonth || 'N/A'}</td></tr>
                        </table>
                    </div>
                `;
                frappe.msgprint({ title: __("QuickBooks Company Information"), message: message, indicator: "blue", width: "large" });
            } else {
                frappe.msgprint({ title: __("Error"), message: __("Failed to fetch company information"), indicator: "red" });
            }
        }
    });
}

// ======================================================
// SYNC FUNCTIONS
// ======================================================

function sync_all_data(frm) {
    frappe.confirm(
        __("<b>Sync All Data</b><br><br>This will sync all data types from QuickBooks to ERPNext. Continue?"),
        function() {
            frappe.call({
                method: "quickbooks_connector.api.sync_all",
                freeze: true,
                freeze_message: __("Syncing data from QuickBooks..."),
                callback: function(r) { handle_sync_response(frm, r, "Full Sync"); }
            });
        },
        __("Cancel")
    );
}

function sync_customers_data(frm) {
    frappe.confirm(
        __("Sync customers from QuickBooks?"),
        function() {
            frappe.call({
                method: "quickbooks_connector.api.sync_customers",
                freeze: true,
                freeze_message: __("Syncing customers..."),
                callback: function(r) { handle_sync_response(frm, r, "Customers Sync"); }
            });
        }
    );
}

function sync_items_data(frm) {
    frappe.confirm(
        __("Sync items from QuickBooks?"),
        function() {
            frappe.call({
                method: "quickbooks_connector.api.sync_items",
                freeze: true,
                freeze_message: __("Syncing items..."),
                callback: function(r) { handle_sync_response(frm, r, "Items Sync"); }
            });
        }
    );
}

function sync_payments_data(frm) {
    frappe.confirm(
        __("Sync payments from QuickBooks?"),
        function() {
            frappe.call({
                method: "quickbooks_connector.api.sync_payments",
                freeze: true,
                freeze_message: __("Syncing payments..."),
                callback: function(r) { handle_sync_response(frm, r, "Payments Sync"); }
            });
        }
    );
}

function handle_sync_response(frm, r, sync_type) {
    if (r.message && r.message.success) {
        let message = `<b>${sync_type} Completed Successfully</b><br><br>`;
        if (sync_type === "Full Sync" && r.message.results) {
            Object.keys(r.message.results).forEach(key => {
                if (r.message.results[key].success) {
                    message += `• ${key}: ${r.message.results[key].message}<br>`;
                }
            });
        } else if (r.message.message) {
            message += r.message.message;
        }
        frappe.msgprint({ title: __("Sync Successful"), message: message, indicator: "green" });
        frm.reload_doc();
    } else {
        frappe.msgprint({
            title: __("Sync Failed"),
            message: `<b>${sync_type} Failed</b><br><br>` + (r.message ? r.message.error : __("Unknown error")),
            indicator: "red"
        });
    }
}

function view_sync_status(frm) {
    frappe.call({
        method: "quickbooks_connector.api.get_sync_status",
        callback: function(r) {
            if (r.message && r.message.success) {
                let message = `
                    <table class="table table-bordered" style="width: 100%;">
                        <tr><th>Connection Status</th><td>${r.message.is_connected ? '<span class="indicator green">Connected</span>' : '<span class="indicator red">Disconnected</span>'}</td></tr>
                        <tr><th>Company Name</th><td>${r.message.company_name || 'N/A'}</td></tr>
                        <tr><th>Realm ID</th><td>${r.message.realm_id || 'N/A'}</td></tr>
                        <tr><th>Last Sync</th><td>${r.message.last_sync ? moment(r.message.last_sync).format('LLL') : 'Never'}</td></tr>
                    </table>
                `;
                frappe.msgprint({ title: __("Sync Status"), message: message, indicator: "blue" });
            }
        }
    });
}

// ======================================================
// UTILITY FUNCTIONS
// ======================================================

function format_address(addr) {
    if (!addr) return '';
    const parts = [];
    if (addr.Line1) parts.push(addr.Line1);
    if (addr.City) parts.push(addr.City);
    if (addr.PostalCode) parts.push(addr.PostalCode);
    if (addr.Country) parts.push(addr.Country);
    return parts.join(', ');
}

function start_connection_monitor(frm) {
    if (window.qbConnectionMonitor) {
        clearInterval(window.qbConnectionMonitor);
    }
    window.qbConnectionMonitor = setInterval(function() {
        if (frm.doc.is_connected && frm.doc.last_sync) {
            const $wrapper = $(frm.wrapper);
            const lastSyncElement = $wrapper.find('.qb-status-indicator small');
            if (lastSyncElement.length) {
                lastSyncElement.text(`Last sync: ${moment(frm.doc.last_sync).fromNow()}`);
            }
        }
    }, 300000);
}

function add_custom_styles() {
    if (document.getElementById('qb-custom-styles')) return;

    const style = document.createElement('style');
    style.id = 'qb-custom-styles';
    style.textContent = `
        .qb-status-indicator { padding: 10px; border-radius: 4px; margin-bottom: 10px; font-size: 13px; }
        .qb-status-indicator.connected { background-color: #e7f7e7; border-left: 4px solid #2CA01C; }
        .qb-status-indicator.disconnected { background-color: #ffeaea; border-left: 4px solid #ff5858; }
        .qb-status-section { margin-bottom: 15px; }
    `;
    document.head.appendChild(style);
}

frappe.ui.form.on("Quickbooks Setting", "before_unload", function(frm) {
    if (window.qbConnectionMonitor) {
        clearInterval(window.qbConnectionMonitor);
        delete window.qbConnectionMonitor;
    }
    const customStyle = document.getElementById('qb-custom-styles');
    if (customStyle) customStyle.remove();
});