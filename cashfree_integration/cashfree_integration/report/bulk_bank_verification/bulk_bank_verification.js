// Copyright (c) 2026, Nikhil and contributors
// For license information, please see license.txt

frappe.query_reports["Bulk Bank Verification"] = {
    "filters": [
        {
            fieldname: "party_type",
            label: __("Party Type"),
            fieldtype: "Select",
            options: ["", "Supplier", "Customer", "Employee"],
            default: ""
        },
        {
            fieldname: "party",
            label: __("Party"),
            fieldtype: "Dynamic Link",
            get_options: function() {
                let party_type = frappe.query_report.get_filter_value('party_type');
                return party_type;
            }
        },
        {
            fieldname: "bank",
            label: __("Bank"),
            fieldtype: "Link",
            options: "Bank"
        },
        {
            fieldname: "verification_status",
            label: __("Verification Status"),
            fieldtype: "Select",
            options: ["", "Verified", "Not Verified", "Pending"],
            default: ""
        }
    ],
    
    onload: function(report) {
        // Add custom buttons
        report.page.add_inner_button(__("Verify Selected"), function() {
            verify_selected_accounts(report);
        });
        
        report.page.add_inner_button(__("Verify All Unverified"), function() {
            verify_all_unverified(report);
        });
    },
    
    formatter: function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        
        // Color code verification status
        if (column.fieldname === "verification_status") {
            if (value && value.includes("✅")) {
                value = `<span style="color: green; font-weight: bold;">${value}</span>`;
            } else if (value && value.includes("❌")) {
                value = `<span style="color: red; font-weight: bold;">${value}</span>`;
            } else if (value && value.includes("⏳")) {
                value = `<span style="color: orange; font-weight: bold;">${value}</span>`;
            }
        }
        
        // Add verify button
        if (column.fieldname === "action") {
            let button_label = data.verification_status.includes("✅") ? "Re-verify" : "Verify";
            value = `<button class="btn btn-xs btn-primary" 
                onclick="verify_account('${data.name}')">${button_label}</button>`;
        }
        
        return value;
    }
};

// Verify single account
function verify_account(account_name) {
    frappe.show_alert({
        message: __('Verifying account...'),
        indicator: 'blue'
    });
    
    frappe.call({
        method: "cashfree_integration.cashfree_integration.report.bulk_bank_verification.bulk_bank_verification.verify_single_account",
        args: {
            bank_account_name: account_name
        },
        callback: function(r) {
            if (r.message) {
                if (r.message.status === "success") {
                    frappe.show_alert({
                        message: r.message.message,
                        indicator: 'green'
                    }, 5);
                } else {
                    frappe.show_alert({
                        message: r.message.message,
                        indicator: 'red'
                    }, 5);
                }
                frappe.query_report.refresh();
            }
        }
    });
}

// Verify selected accounts
function verify_selected_accounts(report) {
    let selected_rows = report.datatable.rowmanager.getCheckedRows();
    
    if (selected_rows.length === 0) {
        frappe.msgprint(__("Please select at least one account to verify"));
        return;
    }
    
    let account_names = selected_rows.map(row => report.data[row].name);
    
    frappe.confirm(
        __('Verify {0} selected accounts?', [account_names.length]),
        function() {
            frappe.show_progress(__('Verifying Accounts'), 0, account_names.length);
            
            frappe.call({
                method: "cashfree_integration.cashfree_integration.report.bulk_bank_verification.bulk_bank_verification.verify_multiple_accounts",
                args: {
                    bank_accounts: account_names
                },
                callback: function(r) {
                    frappe.hide_progress();
                    
                    if (r.message) {
                        let msg = `
                            <div>
                                <p><strong>Verification Complete!</strong></p>
                                <p>Total: ${r.message.total}</p>
                                <p style="color: green;">✅ Success: ${r.message.success}</p>
                                <p style="color: red;">❌ Failed: ${r.message.failed}</p>
                            </div>
                        `;
                        frappe.msgprint(msg);
                        frappe.query_report.refresh();
                    }
                }
            });
        }
    );
}

// Verify all unverified accounts
function verify_all_unverified(report) {
    let unverified = report.data.filter(row => row.verification_status.includes("❌"));
    
    if (unverified.length === 0) {
        frappe.msgprint(__("No unverified accounts found"));
        return;
    }
    
    let account_names = unverified.map(row => row.name);
    
    frappe.confirm(
        __('Verify all {0} unverified accounts?', [account_names.length]),
        function() {
            frappe.show_progress(__('Verifying Accounts'), 0, account_names.length);
            
            frappe.call({
                method: "cashfree_integration.cashfree_integration.report.bulk_bank_verification.bulk_bank_verification.verify_multiple_accounts",
                args: {
                    bank_accounts: account_names
                },
                callback: function(r) {
                    frappe.hide_progress();
                    
                    if (r.message) {
                        let msg = `
                            <div>
                                <p><strong>Bulk Verification Complete!</strong></p>
                                <p>Total: ${r.message.total}</p>
                                <p style="color: green;">✅ Success: ${r.message.success}</p>
                                <p style="color: red;">❌ Failed: ${r.message.failed}</p>
                            </div>
                        `;
                        frappe.msgprint(msg);
                        frappe.query_report.refresh();
                    }
                }
            });
        }
    );
}
