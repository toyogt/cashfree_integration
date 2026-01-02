# File: payouts.py
# Cashfree Payout Integration V2 with Bank Verification + Director Override
# Updated to use centralized API Manager

import frappe
import traceback
import time
from frappe.utils import now
from cashfree_integration.api_manager import CashfreeAPIManager

def log_message(data, title="Cashfree Payout Log"):
    """Helper to log messages to Error Log"""
    try:
        text = frappe.as_json(data)
    except Exception:
        text = str(data)
    frappe.log_error(text, title)

def get_contact_details_from_bank(bank):
    """Extract email and phone from linked Contact"""
    email = ""
    phone = ""
    
    try:
        contacts = frappe.get_all(
            "Dynamic Link",
            filters={
                "link_doctype": "Bank Account",
                "link_name": bank.name,
                "parenttype": "Contact"
            },
            fields=["parent"]
        )
        
        if contacts:
            contact_name = contacts[0].parent
            contact = frappe.get_doc("Contact", contact_name)
            email = contact.email_id or ""
            phone_nos = contact.get('phone_nos', [])
            if phone_nos:
                phone = phone_nos[0].phone or ""
                
    except Exception as e:
        log_message(
            {"error": "Failed to get contact details", "bank": bank.name, "exception": str(e)},
            "Cashfree Contact Fetch Error"
        )
    
    # Clean phone number
    phone = ''.join(filter(str.isdigit, phone))
    
    return email, phone

def get_party_name_from_bank(bank):
    """Get party name for beneficiary"""
    if bank.party:
        try:
            party_doc = frappe.get_doc(bank.party_type, bank.party)
            if hasattr(party_doc, 'supplier_name'):
                return party_doc.supplier_name
            elif hasattr(party_doc, 'customer_name'):
                return party_doc.customer_name
            else:
                return party_doc.name
        except Exception as e:
            frappe.logger().warning(f"Could not fetch party name: {str(e)}")
    
    return bank.account_name or bank.party or ""

def generate_beneficiary_id(bank):
    """Generate consistent beneficiary_id"""
    party_name = get_party_name_from_bank(bank)
    
    party_clean = party_name.replace(" ", "_").replace("-", "_")
    party_clean = "".join(c for c in party_clean if c.isalnum() or c == "_")
    
    while "__" in party_clean:
        party_clean = party_clean.replace("__", "_")
    
    party_clean = party_clean.strip("_")
    party_clean = party_clean[:20]
    
    if not party_clean:
        party_clean = "UNKNOWN"
    
    account_suffix = bank.bank_account_no[-4:] if bank.bank_account_no else "0000"
    
    bene_id = f"BENE_{party_clean}_{account_suffix}"
    
    return bene_id[:50]

def create_or_get_beneficiary(bank, cf_manager):
    """
    Create or get existing beneficiary using API Manager
    
    CHANGE: Uses centralized API Manager
    """
    bene_id = generate_beneficiary_id(bank)
    
    # Check if already stored in Bank Account
    existing_bene = bank.get("custom_cashfree_beneficiary_id")
    if existing_bene:
        frappe.logger().info(f"Using existing beneficiary: {existing_bene}")
        return existing_bene
    
    # Get contact details
    email, phone = get_contact_details_from_bank(bank)
    
    # Get IFSC
    ifsc = bank.get("custom_ifsc_code") or bank.get("branch_code") or ""
    if not ifsc:
        raise Exception("IFSC code missing in Bank Account")
    
    party_name = get_party_name_from_bank(bank)
    
    # Create beneficiary using API Manager
    try:
        result = cf_manager.create_beneficiary(
            bene_id=bene_id,
            name=party_name,
            email=email or "default@example.com",
            phone=phone or "9999999999",
            bank_account=bank.bank_account_no or "",
            ifsc=ifsc,
            address1="India",
            city="Delhi",
            state="Delhi",
            pincode="110001"
        )
        
        log_message(
            {"result": result, "bene_id": bene_id},
            "Cashfree Beneficiary Created"
        )
        
        # Store beneficiary ID in Bank Account
        frappe.db.set_value(
            "Bank Account", 
            bank.name, 
            "custom_cashfree_beneficiary_id", 
            bene_id, 
            update_modified=False
        )
        frappe.db.commit()
        
        return bene_id
        
    except Exception as e:
        error_msg = str(e)
        
        # Handle duplicate beneficiary
        if "already exists" in error_msg.lower() or "conflict" in error_msg.lower():
            frappe.logger().info(f"Beneficiary already exists: {bene_id}")
            frappe.db.set_value(
                "Bank Account", 
                bank.name, 
                "custom_cashfree_beneficiary_id", 
                bene_id, 
                update_modified=False
            )
            frappe.db.commit()
            return bene_id
        
        # Log and re-raise other errors
        log_message(
            {"error": error_msg, "bene_id": bene_id, "traceback": traceback.format_exc()},
            "Cashfree Beneficiary Creation Error"
        )
        raise

def initiate_payout(doc, amount, bene_id, cf_manager, settings):
    """
    Initiate payout using API Manager
    
    CHANGE: Uses centralized API Manager with beneficiary_id reference
    """
    try:
        # Get remarks
        remarks = f"{getattr(settings, 'payout_remarks_prefix', 'TK')} {doc.name}"
        
        # Create transfer using API Manager
        result = cf_manager.create_transfer(
            bene_id=bene_id,
            amount=amount,
            transfer_id=doc.name,
            remarks=remarks
        )
        
        log_message(
            {"pr": doc.name, "response": result},
            "Cashfree Transfer Success"
        )
        
        # Extract data from response
        payout_id = result.get("cf_transfer_id") or result.get("transfer_id")
        raw_status = result.get("transfer_status") or result.get("status") or "PENDING"
        
        # Map status
        status_mapping = {
            "RECEIVED": "Pending",
            "SUCCESS": "Success",
            "PENDING": "Pending",
            "QUEUED": "Pending",
            "FAILED": "Failed",
            "ERROR": "Failed",
            "REVERSED": "Reversed",
            "REJECTED": "Failed",
        }
        status = status_mapping.get(str(raw_status).upper(), "Pending")
        
        return payout_id, status, result
        
    except Exception as e:
        log_message(
            {"exception": str(e), "traceback": traceback.format_exc(), "pr": doc.name},
            "Cashfree Transfer Error"
        )
        raise


def trigger_payout_for_payment_request(doc, method=None):
    """
    Triggered when Payment Request updates
    
    CHANGE: Uses centralized API Manager
    """
    
    log_message(
        {"pr": doc.name, "workflow_state": doc.workflow_state, "method": method},
        "Cashfree Trigger Start V2"
    )
    
    state = (doc.workflow_state or "").strip().lower()
    if state not in ["queued", "queue for payout", "queued for payout"]:
        return
    
    # RETRY SUPPORT
    existing_payout = doc.get("custom_cashfree_payout_id")
    recon_status = (doc.get("custom_reconciliation_status") or "").upper()
    
    if existing_payout:
        if recon_status in ["FAILED", "REVERSED", "REJECTED"]:
            frappe.logger().info(
                f"🔄 Retry detected for {doc.name}: Clearing old payout {existing_payout}"
            )
            
            frappe.db.set_value("Payment Request", doc.name, "custom_cashfree_payout_id", None, update_modified=False)
            frappe.db.set_value("Payment Request", doc.name, "custom_utr_number", None, update_modified=False)
            frappe.db.set_value("Payment Request", doc.name, "custom_reconciliation_status", "Pending", update_modified=False)
            frappe.db.commit()
            
            log_message(
                {"pr": doc.name, "action": "Retry payout", "old_payout_id": existing_payout},
                "Cashfree Payout Retry"
            )
            
            frappe.msgprint(
                f"🔄 Retrying payout after failure<br>"
                f"<b>Old Payout ID:</b> {existing_payout}<br>"
                f"<b>Previous Status:</b> {recon_status}",
                alert=True,
                indicator="blue"
            )
        else:
            frappe.msgprint(
                f"⚠️ Payout already exists<br>"
                f"<b>Payout ID:</b> {existing_payout}<br>"
                f"<b>Status:</b> {recon_status}",
                alert=True,
                indicator="orange"
            )
            return
    
    # Validate amount
    try:
        amount = float(doc.grand_total or 0)
    except Exception:
        amount = 0
    
    if amount <= 0:
        log_message({"error": "Invalid amount", "pr": doc.name}, "Cashfree Invalid Amount")
        return
    
    # Get bank account
    if not doc.get("bank_account"):
        log_message({"error": "No Bank Account", "pr": doc.name}, "Cashfree No Bank Account")
        frappe.throw("No Bank Account selected in Payment Request")
    
    try:
        bank = frappe.get_doc("Bank Account", doc.bank_account)
    except Exception as e:
        log_message({"error": "Bank fetch failed", "pr": doc.name, "exception": str(e)}, "Cashfree Bank Fetch Error")
        frappe.throw(f"Bank account not found: {str(e)}")
    
    # BANK VERIFICATION CHECK
    approval_status = bank.get("custom_bank_account_approval_status")
    verified = bank.get("custom_bank_account_verified")
    
    if approval_status != "Approved" or verified != 1:
        verified_by = bank.get("custom_verified_by") or "Not verified"
        
        error_message = (
            f"⚠️ <b>Bank Account Not Verified</b><br><br>"
            f"<div style='background: #fff3cd; padding: 10px; border-left: 4px solid #ffc107;'>"
            f"<b>Bank Account:</b> {bank.name}<br>"
            f"<b>Account Number:</b> {bank.bank_account_no or 'N/A'}<br>"
            f"<b>Current Status:</b> {approval_status or 'Not Verified'}<br>"
            f"<b>Verified:</b> {'Yes ✓' if verified else 'No ✗'}<br>"
            f"<b>Last Checked By:</b> {verified_by}<br>"
            f"</div><br>"
            f"<b>⚡ Action Required:</b><br>"
            f"<ol>"
            f"<li>Open Bank Account: <a href='/app/bank-account/{bank.name}' target='_blank'><b>{bank.name}</b></a></li>"
            f"<li>Click <b>'Verify Bank Account'</b> button</li>"
            f"<li>Wait for verification (5-10 seconds)</li>"
            f"<li>Return here and retry</li>"
            f"</ol>"
        )
        
        log_message(
            {"pr": doc.name, "bank": bank.name, "error": "Unverified bank"},
            "Cashfree Payout Blocked - Unverified Bank"
        )
        
        frappe.throw(error_message, title="Bank Verification Required")
    
    # DIRECTOR OVERRIDE CHECK
    if doc.reference_doctype == "Purchase Order" and doc.reference_name:
        try:
            po = frappe.get_doc("Purchase Order", doc.reference_name)
            po_amount = float(po.grand_total or 0)
            payment_amount = float(doc.grand_total or 0)
            
            if payment_amount > po_amount:
                director_override = doc.get("custom_director_override")
                over_amount = payment_amount - po_amount
                
                if not director_override or director_override == 0:
                    error_message = (
                        f"🔒 <b>Over-PO Payment Blocked</b><br><br>"
                        f"<div style='background: #fff3cd; padding: 15px; border-left: 4px solid #ff9800;'>"
                        f"<b>Purchase Order:</b> {doc.reference_name}<br>"
                        f"<b>PO Amount:</b> ₹{po_amount:,.2f}<br>"
                        f"<b>Payment Amount:</b> ₹{payment_amount:,.2f}<br>"
                        f"<b>⚠️ Over Amount:</b> <span style='color: #d32f2f; font-weight: bold;'>₹{over_amount:,.2f}</span>"
                        f"</div><br>"
                        f"<div style='background: #f8d7da; padding: 15px; border-left: 4px solid #dc3545;'>"
                        f"<b>🔒 Director Override Required</b><br><br>"
                        f"<b>To proceed:</b><br>"
                        f"<ol>"
                        f"<li>Get approval from Director</li>"
                        f"<li>Enable <b>'Director Override'</b> checkbox</li>"
                        f"<li>Save document</li>"
                        f"<li>Retry payout</li>"
                        f"</ol>"
                        f"</div>"
                    )
                    
                    log_message(
                        {"pr": doc.name, "po": doc.reference_name, "po_amount": po_amount, 
                         "payment_amount": payment_amount, "error": "Director override required"},
                        "Payout Blocked - Over PO Payment"
                    )
                    
                    frappe.throw(error_message, title="Director Override Required")
                
                else:
                    frappe.logger().info(
                        f"⚠️ DIRECTOR OVERRIDE: {doc.name} (PO: ₹{po_amount}, Payment: ₹{payment_amount})"
                    )
                    
                    log_message(
                        {"pr": doc.name, "po": doc.reference_name, "po_amount": po_amount,
                         "payment_amount": payment_amount, "override_by": frappe.session.user,
                         "action": "Director Override Approved"},
                        f"Director Override - {doc.name}"
                    )
                    
                    frappe.msgprint(
                        f"⚠️ <b>Director Override Active</b><br><br>"
                        f"Over-PO payment approved by: <b>{frappe.session.user}</b><br>"
                        f"Over Amount: ₹{over_amount:,.2f}",
                        alert=True,
                        indicator="orange"
                    )
        except Exception as e:
            frappe.logger().error(f"Error checking PO: {str(e)}")
    
    # CHANGE: Initialize API Manager
    try:
        cf_manager = CashfreeAPIManager()
        settings = cf_manager.settings
    except Exception as e:
        log_message({"error": "API Manager init failed", "pr": doc.name, "exception": str(e)}, "Cashfree Init Error")
        frappe.throw(f"Cashfree initialization failed: {str(e)}")
    
    # Create/get beneficiary
    try:
        bene_id = create_or_get_beneficiary(bank, cf_manager)
        frappe.logger().info(f"Beneficiary ready: {bene_id}")
    except Exception as e:
        log_message({"error": "Beneficiary failed", "pr": doc.name, "exception": str(e)}, "Cashfree Beneficiary Failed")
        frappe.throw(f"Beneficiary creation failed: {str(e)}")
    
    time.sleep(1)
    
    # Initiate payout
    try:
        payout_id, status, response_data = initiate_payout(doc, amount, bene_id, cf_manager, settings)
    except Exception as e:
        frappe.throw(f"Payout failed: {str(e)}")
    
    # Create log
    try:
        pl = frappe.get_doc({
            "doctype": "Cashfree Payout Log",
            "payment_request": doc.name,
            "payout_id": payout_id or doc.name,
            "transfer_mode": doc.get("custom_transfer_mode") or "NEFT",
            "amount": amount,
            "status": status,
            "request_payload": frappe.as_json({"bene_id": bene_id, "amount": amount}),
            "response_payload": frappe.as_json(response_data),
        })
        pl.insert(ignore_permissions=True)
    except Exception as e:
        frappe.logger().error(f"Failed to create Payout Log: {str(e)}")
    
    # Update Payment Request
    try:
        if payout_id and payout_id != doc.name:
            frappe.db.set_value("Payment Request", doc.name, "custom_cashfree_payout_id", payout_id, update_modified=False)
        frappe.db.set_value("Payment Request", doc.name, "custom_reconciliation_status", status, update_modified=False)
        frappe.db.commit()
        
        frappe.msgprint(
            f"✅ <b>Payout Initiated</b><br><br>"
            f"<b>Payment Request:</b> {doc.name}<br>"
            f"<b>Payout ID:</b> {payout_id}<br>"
            f"<b>Amount:</b> ₹{amount:,.2f}<br>"
            f"<b>Status:</b> {status}<br>"
            f"<b>Beneficiary ID:</b> {bene_id}",
            alert=True,
            indicator='green',
            title='Payout Success'
        )
        
        log_message({"pr": doc.name, "payout_id": payout_id, "status": status}, "Cashfree Payout Success")
    except Exception as e:
        log_message({"error": "PR update failed", "pr": doc.name, "exception": str(e)}, "Cashfree PR Update Error")
