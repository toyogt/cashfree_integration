app_name = "cashfree_integration"
app_title = "CashFree Integration"
app_publisher = "Frappe"
app_description = "Cashfree API"
app_email = "nikhil@toyokombucha.com"
app_license = "mit"

# Document Events - UNCOMMENTED AND ENABLED ✅
doc_events = {
    "Payment Request": {
        "on_update_after_submit": "cashfree_integration.api.payouts.trigger_payout_for_payment_request",
        "after_workflow_action": "cashfree_integration.api.payouts.trigger_payout_for_payment_request",
        "before_save": "cashfree_integration.api.payouts.trigger_payout_for_payment_request"
    }
}

# Webhook Hooks
override_whitelisted_methods = {
    "cashfree_integration.api.webhook.cashfree_payout_webhook": "cashfree_integration.api.webhook.cashfree_payout_webhook"
}

ignore_csrf = [
    "cashfree_integration.api.webhook.cashfree_payout_webhook"
]

# Client Scripts and JS
doctype_list_js = {
    "Payment Request": "public/js/payment_request_list.js"
}

doctype_js = {
    "Bank Account": "public/js/bank_account.js"
}

# Scheduler Events (Optional - for payout reconciliation)
scheduler_events = {
    "hourly": [
        "cashfree_integration.tasks.reconcile_payouts"
    ]
}

# Default empty sections (required by Frappe)
# ------------------

app_include_css = "/assets/cashfree_integration/css/cashfree_integration.css"
app_include_js = "/assets/cashfree_integration/js/cashfree_integration.js"

# Home Pages
home_page = "login"

# Website Generators
website_generators = []

# Jinja
# jinja = {
#     "methods": "cashfree_integration.utils.jinja_methods",
#     "filters": "cashfree_integration.utils.jinja_filters"
# }

# Installation Hooks
# before_install = "cashfree_integration.install.before_install"
# after_install = "cashfree_integration.install.after_install"

# Document Permissions
# permission_query_conditions = {}
# has_permission = {}

# Override Doctype Class (Optional)
# override_doctype_class = {
#     "Payment Request": "cashfree_integration.overrides.payment_request.PaymentRequest"
# }

# Testing
# before_tests = "cashfree_integration.install.before_tests"

# User Data Protection
# user_data_fields = []

# Translation
ignore_translatable_strings_from = []

# Authentication
# auth_hooks = []

# Log Clearing
# default_log_clearing_doctypes = {}

# END OF HOOKS
