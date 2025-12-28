# Copyright (c) 2025, K95 Foods
# License: MIT

import frappe
from frappe import _

def execute(filters=None):
    """Payout Dashboard - Complete view of Cashfree payouts"""
    columns = get_columns()
    data = get_data(filters)
    return columns, data

def get_columns():
    return [
        {"label": _("Payout Log"), "fieldname": "log_name", "fieldtype": "Link", "options": "Cashfree Payout Log", "width": 160},
        {"label": _("Payment Request"), "fieldname": "payment_request", "fieldtype": "Link", "options": "Payment Request", "width": 160},
        {"label": _("Payment Entry"), "fieldname": "payment_entry", "fieldtype": "Link", "options": "Payment Entry", "width": 160},
        {"label": _("Party"), "fieldname": "party", "fieldtype": "Link", "options": "Supplier", "width": 150},
        {"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
        {"label": _("UTR"), "fieldname": "utr", "fieldtype": "Data", "width": 150},
        {"label": _("Created"), "fieldname": "created", "fieldtype": "Datetime", "width": 160}
    ]

def get_data(filters):
    conditions = []
    values = []
    
    if filters and filters.get("from_date"):
        conditions.append("log.creation >= %s")
        values.append(filters.get("from_date"))
    
    if filters and filters.get("to_date"):
        conditions.append("log.creation <= %s")
        values.append(filters.get("to_date"))
    
    if filters and filters.get("status"):
        conditions.append("log.status = %s")
        values.append(filters.get("status"))
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    data = frappe.db.sql(f"""
        SELECT 
            log.name as log_name,
            log.payment_request,
            pe.name as payment_entry,
            pr.party,
            pr.grand_total as amount,
            log.status,
            pr.custom_utr_number as utr,
            log.creation as created
        FROM `tabCashfree Payout Log` log
        LEFT JOIN `tabPayment Request` pr ON pr.name = log.payment_request
        LEFT JOIN `tabPayment Entry` pe ON pe.payment_request = log.payment_request AND pe.docstatus != 2
        WHERE {where_clause}
        ORDER BY log.creation DESC
        LIMIT 1000
    """, tuple(values), as_dict=1)
    
    return data
