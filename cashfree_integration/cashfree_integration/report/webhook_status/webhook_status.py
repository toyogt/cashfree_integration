# Copyright (c) 2025, K95 Foods
# License: MIT

import frappe
from frappe import _

def execute(filters=None):
    """Webhook Status Report - Shows Payment Requests processed via Cashfree webhook"""
    columns = get_columns()
    data = get_data(filters)
    return columns, data

def get_columns():
    return [
        {"label": _("Payment Request"), "fieldname": "payment_request", "fieldtype": "Link", "options": "Payment Request", "width": 160},
        {"label": _("Party"), "fieldname": "party", "fieldtype": "Link", "options": "Supplier", "width": 150},
        {"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 120},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
        {"label": _("UTR Number"), "fieldname": "utr", "fieldtype": "Data", "width": 150},
        {"label": _("PE Created"), "fieldname": "pe_created", "fieldtype": "Check", "width": 100},
        {"label": _("Payment Entry"), "fieldname": "pe_name", "fieldtype": "Link", "options": "Payment Entry", "width": 160},
        {"label": _("PE Status"), "fieldname": "pe_status", "fieldtype": "Data", "width": 100},
        {"label": _("Webhook Time"), "fieldname": "webhook_time", "fieldtype": "Datetime", "width": 160}
    ]

def get_data(filters):
    conditions = ["pr.mode_of_payment = 'Cashfree'", "pr.custom_reconciliation_status IS NOT NULL"]
    values = []
    
    if filters and filters.get("from_date"):
        conditions.append("pr.creation >= %s")
        values.append(filters.get("from_date"))
    
    if filters and filters.get("to_date"):
        conditions.append("pr.creation <= %s")
        values.append(filters.get("to_date"))
    
    if filters and filters.get("status"):
        conditions.append("pr.custom_reconciliation_status = %s")
        values.append(filters.get("status"))
    
    where_clause = " AND ".join(conditions)
    
    data = frappe.db.sql(f"""
        SELECT 
            pr.name as payment_request,
            pr.party,
            pr.grand_total as amount,
            pr.custom_reconciliation_status as status,
            pr.custom_utr_number as utr,
            CASE WHEN pe.name IS NOT NULL THEN 1 ELSE 0 END as pe_created,
            pe.name as pe_name,
            CASE 
                WHEN pe.docstatus = 0 THEN 'Draft'
                WHEN pe.docstatus = 1 THEN 'Submitted'
                WHEN pe.docstatus = 2 THEN 'Cancelled'
                ELSE NULL
            END as pe_status,
            pr.modified as webhook_time
        FROM `tabPayment Request` pr
        LEFT JOIN `tabPayment Entry` pe ON pe.payment_request = pr.name AND pe.docstatus != 2
        WHERE {where_clause}
        ORDER BY pr.modified DESC
        LIMIT 1000
    """, tuple(values), as_dict=1)
    
    return data
