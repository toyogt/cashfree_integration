# File: bav.py
# Bank Account Verification using Cashfree BAV V2 API

import frappe
import requests
from frappe.utils import now


@frappe.whitelist()
def verify_bank_account_button(bank_account_name):
    """
    Called when user clicks "Verify Bank Account" button
    Uses Cashfree BAV V2 Sync API for instant verification
    """
    
    if not frappe.has_permission("Bank Account", "write"):
        frappe.throw("You don't have permission to verify bank accounts")
    
    bank = frappe.get_doc("Bank Account", bank_account_name)
    
    # ✅ Check if already verified
    if bank.custom_bank_account_verified == 1:
        frappe.msgprint(
            f"✅ <b>Bank Account Already Verified</b><br><br>"
            f"<div style='background: #d4edda; padding: 15px; border-left: 4px solid #28a745;'>"
            f"<b>Account:</b> {bank.name}<br>"
            f"<b>Account Number:</b> {bank.bank_account_no}<br>"
            f"<b>Verified By:</b> {bank.custom_verified_by or 'Administrator'}<br>"
            f"<b>Status:</b> {bank.custom_bank_account_approval_status}<br>"
            f"</div><br>"
            f"This account is already verified and ready for payouts.<br>"
            f"No need to verify again.",
            alert=True,
            indicator="green",
            title="Already Verified"
        )
        return {"success": True, "message": "Already Verified", "skip": True}
    
    # Validate required fields
    if not bank.bank_account_no:
        frappe.throw("Bank Account Number is missing")
    
    ifsc = bank.branch_code or bank.get("custom_ifsc_code")
    if not ifsc:
        frappe.throw("IFSC Code is missing")
    
    # Get Cashfree settings
    settings = frappe.get_single("Cashfree Settings")
    
    # ✅ BAV V2 API uses different base URL than Payouts
    # Sandbox: https://sandbox.cashfree.com/verification
    # Production: https://api.cashfree.com/verification
    
    # Determine environment from settings
    if settings.environment == "Sandbox":
        verification_base = "https://sandbox.cashfree.com/verification"
    else:
        verification_base = "https://api.cashfree.com/verification"
    
    # Use Sync API for instant results
    url = f"{verification_base}/bank-account/sync"
    
    headers = {
        "Content-Type": "application/json",
        "x-client-id": settings.client_id,
        "x-client-secret": settings.get_password("client_secret")
    }
    
    # Get party name
    party_name = get_party_name_from_bank(bank)
    
    # Get contact details
    email, phone = get_contact_details_from_bank(bank)
    if not phone:
        phone = "9999999999"  # Fallback default
    
    # ✅ V2 API payload (name and phone are optional but recommended)
    payload = {
        "bank_account": bank.bank_account_no,
        "ifsc": ifsc,
        "name": party_name,  # Optional - enables name matching
        "phone": phone       # Optional
    }
    
    try:
        frappe.logger().info(f"Calling Cashfree BAV V2 Sync for {bank_account_name}")
        
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        
        # Log the request and response
        frappe.log_error(
            frappe.as_json({
                "api_version": "V2",
                "endpoint": url,
                "payload": payload,
                "response": resp.text[:1000],  # First 1000 chars
                "status_code": resp.status_code
            }),
            f"Cashfree BAV V2 - {bank_account_name}"
        )
        
        # Parse response
        try:
            data = resp.json()
        except:
            frappe.throw(f"Invalid JSON response from Cashfree: {resp.text[:200]}")
        
        # ✅ V2 API returns 200 for both success and failure
        # Check account_status field to determine result
        if resp.status_code == 200:
            # Extract V2 response fields
            reference_id = data.get("reference_id", "")
            name_at_bank = data.get("name_at_bank", "")
            bank_name = data.get("bank_name", "")
            branch = data.get("branch", "")
            city = data.get("city", "")
            micr = data.get("micr", "")
            account_status = data.get("account_status", "")
            account_status_code = data.get("account_status_code", "")
            name_match_score = data.get("name_match_score")
            name_match_result = data.get("name_match_result", "")
            utr = data.get("utr", "")
            ifsc_details = data.get("ifsc_details", {})
            
            # ✅ Check if account is VALID
            if account_status == "VALID":
                # ✅ SUCCESS - Update Bank Account
                
                # Check name match score if available
                name_match_warning = ""
                name_match_display = "Not Available"
                
                if name_match_score is not None and str(name_match_score).strip() not in ["", "null", "None"]:
                    try:
                        match_score_float = float(name_match_score)
                        name_match_display = f"{match_score_float}%"
                        
                        if match_score_float < 70:
                            name_match_warning = f"\n\n⚠️ NAME MATCH WARNING:\nScore {match_score_float}% is below 70%. Manual review recommended."
                    except:
                        name_match_display = "Not Available"
                        name_match_warning = "\n\n✓ NAME MATCH: Score parsing failed (account verification successful)"
                else:
                    name_match_warning = "\n\n✓ NAME MATCH: Not performed by bank (account verification successful)"
                
                # Update account_name with name_at_bank
                if name_at_bank:
                    bank.account_name = name_at_bank
                
                # Update bank name if different
                if bank_name:
                    try:
                        existing_banks = frappe.get_all("Bank", filters={"bank_name": bank_name}, limit=1)
                        if existing_banks:
                            bank.bank = bank_name
                        else:
                            # Try to find bank by partial match
                            banks = frappe.get_all("Bank", fields=["name", "bank_name"])
                            for b in banks:
                                if bank_name.upper() in b.bank_name.upper() or b.bank_name.upper() in bank_name.upper():
                                    bank.bank = b.name
                                    break
                    except:
                        pass
                
                # Confirm IFSC
                if ifsc:
                    bank.branch_code = ifsc
                    bank.custom_ifsc_code = ifsc
                
                # Set verification status
                bank.custom_bank_account_approval_status = "Approved"
                bank.custom_bank_account_verified = 1
                bank.custom_verified_by = frappe.session.user  # ✅ FIXED: Use current user, not string
                
                # Build verification notes
                bank.custom_verification_notesreason = (
                    f"✅ Bank Account Verified Successfully (BAV V2 - Cashfree API)\n\n"
                    f"═══════════════════════════════════\n"
                    f"VERIFICATION DETAILS\n"
                    f"═══════════════════════════════════\n"
                    f"Verification Method: Cashfree BAV V2 Sync API\n"
                    f"Reference ID: {reference_id}\n"
                    f"Verified Via: Cashfree Bank Account Verification\n"
                    f"Initiated By: {frappe.session.user}\n"
                    f"Timestamp: {now()}\n"
                    f"UTR: {utr or 'N/A'}\n\n"
                    f"═══════════════════════════════════\n"
                    f"BANK DETAILS (FROM BANK)\n"
                    f"═══════════════════════════════════\n"
                    f"Bank Name: {bank_name}\n"
                    f"Branch: {branch}\n"
                    f"City: {city}\n"
                    f"MICR: {micr}\n"
                    f"Name at Bank: {name_at_bank}\n"
                    f"Account Status: {account_status}\n"
                    f"Status Code: {account_status_code}\n\n"
                    f"═══════════════════════════════════\n"
                    f"NAME VERIFICATION\n"
                    f"═══════════════════════════════════\n"
                    f"Submitted Name: {party_name}\n"
                    f"Bank Records Show: {name_at_bank}\n"
                    f"Match Score: {name_match_display}\n"
                    f"Match Result: {name_match_result or 'Not Performed'}"
                    f"{name_match_warning}\n\n"
                    f"═══════════════════════════════════\n"
                    f"ACTIONS TAKEN\n"
                    f"═══════════════════════════════════\n"
                    f"✓ Account Name updated to: {name_at_bank}\n"
                    f"✓ Bank Name: {bank.bank}\n"
                    f"✓ IFSC Code confirmed: {ifsc}\n"
                    f"✓ Verification Status: Approved\n"
                    f"✓ Account marked as verified by Cashfree API"
                )
                
                bank.flags.ignore_permissions = True
                bank.flags.ignore_validate = True
                bank.flags.ignore_mandatory = True
                bank.save()
                frappe.db.commit()
                
                # Build success message
                name_warning_html = name_match_warning.replace('\n', '<br>') if name_match_warning else ""
                
                frappe.msgprint(
                    f"✅ <b>Bank Account Verified & Updated</b><br><br>"
                    f"<div style='background: #d4edda; padding: 15px; border-left: 4px solid #28a745; margin: 10px 0;'>"
                    f"<b>Account Number:</b> {bank.bank_account_no}<br>"
                    f"<b>Bank Name:</b> {bank_name}<br>"
                    f"<b>Branch:</b> {branch}<br>"
                    f"<b>Name at Bank:</b> {name_at_bank}<br>"
                    f"<b>Match Score:</b> {name_match_display}<br>"
                    f"<b>Status:</b> {account_status}"
                    f"</div><br>"
                    f"<b>Reference ID:</b> {reference_id}"
                    f"{name_warning_html}",
                    indicator='green',
                    title='Verification Successful'
                )
                
                return {"success": True, "message": "Verified", "data": data}
            
            else:
                # ❌ FAILURE - Account not VALID
                bank.custom_bank_account_approval_status = "Draft"
                bank.custom_bank_account_verified = 0
                
                failure_reason = f"Account Status: {account_status} ({account_status_code})"
                
                bank.custom_verification_notesreason = (
                    f"❌ Bank Account Verification Failed (BAV V2)\n\n"
                    f"Reference ID: {reference_id}\n"
                    f"Initiated By: {frappe.session.user}\n"
                    f"Timestamp: {now()}\n\n"
                    f"FAILURE REASON:\n{failure_reason}\n\n"
                    f"Bank Details:\n"
                    f"Name at Bank: {name_at_bank or 'N/A'}\n"
                    f"Submitted: {party_name}\n"
                    f"Account Status: {account_status}"
                )
                
                bank.flags.ignore_permissions = True
                bank.flags.ignore_validate = True
                bank.flags.ignore_mandatory = True
                bank.save()
                frappe.db.commit()
                
                frappe.msgprint(
                    f"❌ <b>Verification Failed</b><br><br>"
                    f"<b>Reason:</b> {failure_reason}",
                    indicator='red',
                    title='Verification Failed'
                )
                
                return {"success": False, "message": "Failed", "data": data}
        
        else:
            # API returned non-200 status
            code, friendly_error = _extract_cashfree_error(data, resp.status_code)
            
            bank.custom_bank_account_approval_status = "Draft"
            bank.custom_bank_account_verified = 0
            bank.custom_verification_notesreason = f"❌ API Error ({code})\n\n{friendly_error}"
            
            bank.flags.ignore_permissions = True
            bank.flags.ignore_validate = True
            bank.flags.ignore_mandatory = True
            bank.save()
            frappe.db.commit()
            
            frappe.throw(f"Cashfree API Error: {friendly_error}")
    
    except requests.exceptions.RequestException as e:
        bank.custom_bank_account_approval_status = "Draft"
        bank.custom_bank_account_verified = 0
        bank.custom_verification_notesreason = f"❌ Network Error\n\n{str(e)}"
        
        bank.flags.ignore_permissions = True
        bank.flags.ignore_validate = True
        bank.flags.ignore_mandatory = True
        bank.save()
        frappe.db.commit()
        
        frappe.throw(f"Network error: {str(e)}")
    
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"Bank Verification Error - {bank_account_name}")
        frappe.throw(f"Verification failed: {str(e)}")


def _extract_cashfree_error(data, status_code):
    """Extract and format Cashfree error messages"""
    msg = (
        data.get("message")
        or data.get("error")
        or data.get("error_description")
        or "Unknown error"
    )
    account_status_code = data.get("account_status_code") or data.get("sub_code")
    account_status = data.get("account_status")

    # Handle insufficient balance explicitly
    if account_status_code == "INSUFFICIENT_BALANCE" or "Insufficient balance" in str(msg):
        return (
            "INSUFFICIENT_BALANCE",
            "Cashfree Verification wallet is empty. "
            "Add funds to Secure ID wallet (Merchant Dashboard → Secure ID → Accounts → Wallet Recharge) "
            "or manually approve bank accounts instead."
        )

    return (
        account_status_code or f"HTTP_{status_code}", 
        f"{msg} (status={account_status}, code={account_status_code})"
    )


def get_party_name_from_bank(bank):
    """Get party name for verification"""
    if bank.party:
        try:
            party_doc = frappe.get_doc(bank.party_type, bank.party)
            if hasattr(party_doc, 'supplier_name'):
                return party_doc.supplier_name
            elif hasattr(party_doc, 'customer_name'):
                return party_doc.customer_name
            else:
                return party_doc.name
        except:
            pass
    return bank.account_name or bank.party or ""


def get_contact_details_from_bank(bank):
    """Get email and phone from linked Contact"""
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
    except:
        pass
    
    return email, phone
