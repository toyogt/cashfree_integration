frappe.ui.form.on('Bank Account', {
    refresh: function(frm) {
        if (!frm.is_new() && frm.doc.docstatus === 0) {
            frm.add_custom_button(__('Verify Bank Account'), function() {
                frappe.call({
                    method: 'cashfree_integration.api.bav.verify_bank_account_button',
                    args: { bank_account_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Verifying with Cashfree...'),
                    callback: function(r) {
                        if (r.message && r.message.success) {
                            frappe.show_alert({
                                message: __('Bank Account Verified Successfully!'),
                                indicator: 'green'
                            });
                            frm.reload_doc();
                        }
                    }
                });
            }).addClass('btn-primary');
        }
    }
});

