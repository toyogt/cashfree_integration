# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nikhil and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from cashfree_integration.api_manager import CashfreeAPIManager

def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data

def get_columns():
    return [
        {
            "fieldname": "name",
            "label": _("Bank Account ID"),
            "fieldtype": "Link",
            "options": "Bank Account",
            "width": 200
        },
        {
            "fieldname": "account_name",
            "label": _("Account Name"),
            "fieldtype": "Data",
            "width": 180
        },
        {
            "fieldname": "bank_account_no",
            "label": _("Account Number"),
            "fieldtype": "Data",
            "width": 150
        },
        {
            "fieldname": "ifsc",
            "label": _("IFSC Code"),
            "fieldtype": "Data",
            "width": 120
        },
        {
            "fieldname": "party_type",
            "label": _("Party Type"),
            "fieldtype": "Data",
            "width": 100
        },
        {
            "fieldname": "party",
            "label": _("Party"),
            "fieldtype": "Dynamic Link",
            "options": "party_type",
            "width": 150
        },
        {
            "fieldname": "verification_status",
            "label": _("Verification Status"),
            "fieldtype": "Data",
            "width": 150
        },
        {
            "fieldname": "cashfree_status",
            "label": _("Cashfree Status"),
            "fieldtype": "Data",
            "width": 120
        },
        {
            "fieldname": "name_at_bank",
            "label": _("Name at Bank"),
            "fieldtype": "Data",
            "width": 180
        },
        {
            "fieldname": "bank_name",
            "label": _("Bank Name"),
            "fieldtype": "Data",
            "width": 200
        },
        {
            "fieldname": "action",
            "label": _("Action"),
            "fieldtype": "Button",
            "width": 100
        }
    ]

def get_data(filters):
    conditions = []
    values = {}
    
    # Build query conditions based on filters
    if filters.get("party_type"):
        conditions.append("ba.party_type = %(party_type)s")
        values["party_type"] = filters.get("party_type")
    
    if filters.get("party"):
        conditions.append("ba.party = %(party)s")
        values["party"] = filters.get("party")
    
    if filters.get("bank"):
        conditions.append("ba.bank = %(bank)s")
        values["bank"] = filters.get("bank")
    
    if filters.get("verification_status"):
        status = filters.get("verification_status")
        if status == "Verified":
            conditions.append("ba.custom_bank_account_verified = 1")
        elif status == "Not Verified":
            conditions.append("(ba.custom_bank_account_verified = 0 OR ba.custom_bank_account_verified IS NULL)")
        elif status == "Pending":
            conditions.append("ba.custom_bank_account_approval_status = 'Pending'")
    
    where_clause = " AND " + " AND ".join(conditions) if conditions else ""
    
    query = f"""
        SELECT 
            ba.name,
            ba.account_name,
            ba.bank_account_no,
            COALESCE(ba.custom_ifsc_code, ba.branch_code) as ifsc,
            ba.party_type,
            ba.party,
            ba.bank,
            ba.custom_bank_account_verified,
            ba.custom_bank_account_approval_status,
            ba.custom_cashfree_beneficiary_id
        FROM 
            `tabBank Account` ba
        WHERE 
            ba.disabled = 0
            AND ba.bank_account_no IS NOT NULL
            AND ba.bank_account_no != ''
            AND COALESCE(ba.custom_ifsc_code, ba.branch_code) IS NOT NULL
            {where_clause}
        ORDER BY 
            ba.creation DESC
    """
    
    accounts = frappe.db.sql(query, values, as_dict=True)
    
    data = []
    for acc in accounts:
        # Determine verification status
        if acc.custom_bank_account_verified:
            verification_status = "✅ Verified"
        elif acc.custom_bank_account_approval_status == "Pending":
            verification_status = "⏳ Pending"
        else:
            verification_status = "❌ Not Verified"
        
        row = {
            "name": acc.name,
            "account_name": acc.account_name,
            "bank_account_no": acc.bank_account_no,
            "ifsc": acc.ifsc,
            "party_type": acc.party_type,
            "party": acc.party,
            "verification_status": verification_status,
            "cashfree_status": "",
            "name_at_bank": "",
            "bank_name": acc.bank,
            "action": "Verify" if not acc.custom_bank_account_verified else "Re-verify"
        }
        
        data.append(row)
    
    return data

@frappe.whitelist()
def verify_single_account(bank_account_name):
    """Verify a single bank account from the report"""
    try:
        bank_account = frappe.get_doc("Bank Account", bank_account_name)
        
        if not bank_account.bank_account_no:
            frappe.throw(_("Bank account number is missing"))
        
        ifsc = bank_account.custom_ifsc_code or bank_account.branch_code
        if not ifsc:
            frappe.throw(_("IFSC code is missing"))
        
        # Initialize Cashfree API Manager
        cf_manager = CashfreeAPIManager()
        
        # Verify bank account
        result = cf_manager.verify_bank_account(
            account_number=bank_account.bank_account_no,
            ifsc=ifsc,
            name=bank_account.account_name
        )
        
        # Update bank account with verification results
        if result.get("account_status") == "VALID":
            bank_account.custom_bank_account_verified = 1
            bank_account.custom_bank_account_approval_status = "Approved"
            bank_account.custom_verified_by = frappe.session.user
            
            # Update name if different
            name_at_bank = result.get("name_at_bank")
            if name_at_bank:
                bank_account.account_name = name_at_bank
            
            # Update bank name
            bank_name = result.get("bank_name")
            if bank_name and bank_account.bank != bank_name:
                bank_account.bank = bank_name
            
            # Add verification notes
            verification_notes = f"""
✅ Bank Account Verified Successfully (Bulk Verification)

═══════════════════════════════════
VERIFICATION DETAILS
═══════════════════════════════════
Reference ID: {result.get('reference_id')}
Verified On: {frappe.utils.now()}
Verified By: {frappe.session.user}

═══════════════════════════════════
BANK DETAILS
═══════════════════════════════════
Name at Bank: {name_at_bank or 'N/A'}
Bank: {bank_name or 'N/A'}
Branch: {result.get('branch', 'N/A')}
MICR: {result.get('micr', 'N/A')}
Account Status: {result.get('account_status_code', 'N/A')}

Name Match Score: {result.get('name_match_score', 'N/A')}
Name Match Result: {result.get('name_match_result', 'N/A')}
"""
            bank_account.custom_verification_notesreason = verification_notes
            
            bank_account.save(ignore_permissions=True)
            frappe.db.commit()
            
            return {
                "status": "success",
                "message": f"✅ Account verified successfully! Name at bank: {name_at_bank}",
                "result": result
            }
        else:
            return {
                "status": "failed",
                "message": f"❌ Verification failed: {result.get('message', 'Unknown error')}",
                "result": result
            }
    
    except Exception as e:
        frappe.log_error(f"Bulk Verification Failed for {bank_account_name}: {str(e)}", "Cashfree Bulk Verification")
        return {
            "status": "error",
            "message": f"❌ Error: {str(e)}"
        }

@frappe.whitelist()
def verify_multiple_accounts(bank_accounts):
    """Verify multiple bank accounts at once"""
    import json
    
    if isinstance(bank_accounts, str):
        bank_accounts = json.loads(bank_accounts)
    
    results = []
    success_count = 0
    failed_count = 0
    
    for account_name in bank_accounts:
        result = verify_single_account(account_name)
        results.append({
            "account": account_name,
            "status": result["status"],
            "message": result["message"]
        })
        
        if result["status"] == "success":
            success_count += 1
        else:
            failed_count += 1
    
    return {
        "total": len(bank_accounts),
        "success": success_count,
        "failed": failed_count,
        "results": results
    }
