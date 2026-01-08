app_name = "cashfree_integration"
app_title = "CashFree Integration"
app_publisher = "Frappe"
app_description = "Cashfree API"
app_email = "nikhil@toyokombucha.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
#   {
#       "name": "cashfree_integration",
#       "logo": "/assets/cashfree_integration/logo.png",
#       "title": "CashFree Integration",
#       "route": "/cashfree_integration",
#       "has_permission": "cashfree_integration.api.permission.has_app_permission"
#   }
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/cashfree_integration/css/cashfree_integration.css"
# app_include_js = "/assets/cashfree_integration/js/cashfree_integration.js"

# include js, css files in header of web template
# web_include_css = "/assets/cashfree_integration/css/cashfree_integration.css"
# web_include_js = "/assets/cashfree_integration/js/cashfree_integration.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "cashfree_integration/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "cashfree_integration/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
#   "Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
#   "methods": "cashfree_integration.utils.jinja_methods",
#   "filters": "cashfree_integration.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "cashfree_integration.install.before_install"
# after_install = "cashfree_integration.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "cashfree_integration.uninstall.before_uninstall"
# after_uninstall = "cashfree_integration.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "cashfree_integration.utils.before_app_install"
# after_app_install = "cashfree_integration.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "cashfree_integration.utils.before_app_uninstall"
# after_app_uninstall = "cashfree_integration.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "cashfree_integration.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
#   "Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
#   "Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
#   "ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# Document Events
# =============================================================================
# DOCUMENT EVENTS
# =============================================================================
# =============================================================================
# DOCTYPE CLASS OVERRIDE
# =============================================================================
# Override Payment Request class to add Director Override support


# =============================================================================
# DOCUMENT EVENTS
# =============================================================================

# =============================================================================
# DOCUMENT EVENTS
# =============================================================================

# =============================================================================
# DOCUMENT EVENTS
# =============================================================================

# =============================================================================
# DOCUMENT EVENTS (DISABLED)
# =============================================================================
# Director override disabled - uncomment to enable later

# doc_events = {
#     "Payment Request": {
#         "before_validate": "cashfree_integration.overrides.payment_request.validate_director_override",
#         "on_update_after_submit": "cashfree_integration.api.payouts.trigger_payout_for_payment_request"
#     }
# }


# Scheduled Tasks
# ---------------

# scheduler_events = {
#   "all": [
#       "cashfree_integration.tasks.all"
#   ],
#   "daily": [
#       "cashfree_integration.tasks.daily"
#   ],
#   "hourly": [
#       "cashfree_integration.tasks.hourly"
#   ],
#   "weekly": [
#       "cashfree_integration.tasks.weekly"
#   ],
#   "monthly": [
#       "cashfree_integration.tasks.monthly"
#   ],
# }

# Testing
# -------

# before_tests = "cashfree_integration.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
#   "frappe.desk.doctype.event.event.get_events": "cashfree_integration.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
#   "Task": "cashfree_integration.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["cashfree_integration.utils.before_request"]
# after_request = ["cashfree_integration.utils.after_request"]

# Job Events
# ----------
# before_job = ["cashfree_integration.utils.before_job"]
# after_job = ["cashfree_integration.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
#   {
#       "doctype": "{doctype_1}",
#       "filter_by": "{filter_by}",
#       "redact_fields": ["{field_1}", "{field_2}"],
#       "partial": 1,
#   },
#   {
#       "doctype": "{doctype_2}",
#       "filter_by": "{filter_by}",
#       "partial": 1,
#   },
#   {
#       "doctype": "{doctype_3}",
#       "strict": False,
#   },
#   {
#       "doctype": "{doctype_4}"
#   }
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
#   "cashfree_integration.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
#   "Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
# Add to ~/frappe-bench/apps/cashfree_integration/cashfree_integration/hooks.py

# Should be webhook.py (singular), not webhooks.py
override_whitelisted_methods = {
    "cashfree_integration.api.webhook.cashfree_payout_webhook": "cashfree_integration.api.webhook.cashfree_payout_webhook"
}

ignore_csrf = [
    "cashfree_integration.api.webhook.cashfree_payout_webhook"  # Fixed path
]

# Add to existing hooks.py

# Option 1: Using doctype_list_js (recommended)
doctype_list_js = {
    "Payment Request": "public/js/payment_request_list.js"
}

# OR Option 2: Using app_include_js (loads globally)
# app_include_js = [
#     "/assets/cashfree_integration/js/payment_request_list.js"
# ]


# Client Scripts for Verify Button
doctype_js = {
    "Bank Account": "public/js/bank_account.js"
}
