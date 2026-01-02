// Copyright (c) 2026, Nikhil and contributors
// For license information, please see license.txt

frappe.ui.form.on('Cashfree Settings', {
    refresh: function(frm) {
        // Show environment warning
        if (frm.doc.environment === 'production') {
            frm.dashboard.add_comment(__('You are in PRODUCTION mode. Real money will be transferred!'), 'red', true);
        } else {
            frm.dashboard.add_comment(__('You are in SANDBOX mode. Safe for testing.'), 'blue', true);
        }
    },
    
    environment: function(frm) {
        frm.refresh();
    }
});
