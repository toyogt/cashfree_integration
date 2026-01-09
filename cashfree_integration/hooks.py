app_name = "cashfree_integration"
app_title = "CashFree Integration"
app_publisher = "Frappe"
app_description = "Cashfree API"
app_email = "nikhil@toyokombucha.com"
app_license = "mit"

# ========================================
# WEBHOOK - CRITICAL CONFIGURATION
# ========================================
ignore_csrf = [
    "cashfree_integration.api.webhook.cashfree_payout_webhook",
    "cashfree_integration.api.webhook.test_webhook"
]

# Permissions - REQUIRED for guest webhook
has_permission = {
    "Cashfree Webhook Log": "cashfree_integration.api.webhook.has_webhook_permission"
}

# ========================================
# DOCUMENT EVENTS (Your existing)
# ========================================
doc_events = {
    "Payment Request": {
        "on_update_after_submit": "cashfree_integration.api.payouts.trigger_payout_for_payment_request",
        "after_workflow_action": "cashfree_integration.api.payouts.trigger_payout_for_payment_request",
        "before_save": "cashfree_integration.api.payouts.trigger_payout_for_payment_request"
    }
}

# ========================================
# CLIENT SCRIPTS & JS (Your existing)
# ========================================
doctype_list_js = {
    "Payment Request": "public/js/payment_request_list.js"
}

doctype_js = {
    "Bank Account": "public/js/bank_account.js"
}

app_include_css = "/assets/cashfree_integration/css/cashfree_integration.css"
app_include_js = ["/assets/cashfree_integration/js/cashfree_integration.js"]

# ========================================
# SCHEDULER (Your existing)
# ========================================
scheduler_events = {
    "hourly": [
        "cashfree_integration.tasks.reconcile_payouts"
    ]
}

# ========================================
# DEFAULT SECTIONS (Required)
# ========================================
home_page = "login"
website_generators = []
ignore_translatable_strings_from = []
