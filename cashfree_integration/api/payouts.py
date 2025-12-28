# File: payouts.py
# Cashfree Payout Integration with Bank Verification + Director Override

import frappe
import traceback
import requests
import time
from frappe.utils import now


def log_message(data, title="Cashfree Payout Log"):
    """Helper to log messages to Error Log"""
    try:
        text = frappe.as_json(data)
    except Exception:
        text = str(data)
    frappe.log_error(text, title)


def get_cashfree_settings():
    """Get decrypted Cashfree settings for v2"""
    settings = frappe.get_single("Cashfree Settings")

    base_url = (settings.base_url or "").rstrip("/")
    if not base_url:
        frappe.throw("Cashfree base URL is not configured")

    client_id = settings.client_id
    if not client_id:
        frappe.throw("Cashfree Client ID is not configured")

    from frappe.utils.password import get_decrypted_password
    client_secret = get_decrypted_password("Cashfree Settings", settings.name, "client_secret")

    return settings, base_url, client_id, client_secret


def get_v2_headers(client_id, client_secret):
    """Headers for true Cashfree v2 API"""
    return {
        "x-client-id": client_id,
        "x-client-secret": client_secret,
        "x-api-version": "2024-01-01",
        "Content-Type": "application/json",
    }


def get_contact_details_from_bank(bank):
    """
    Extract email and phone from linked Contact doctype
    Bank Account uses Dynamic Link to Contact for email/phone storage
    """
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
    
    return email, phone


def get_party_name_from_bank(bank):
    """
    Get party name for beneficiary
    Priority: supplier_name/customer_name > party > account_name
    
    CHANGE: New helper function to get standardized party name
    """
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
    """
    Generate consistent beneficiary_id from bank details
    Format: BENE_{party_name}_{last_4_digits}
    Max length: 50 characters
    
    CHANGE: Now uses party name instead of party ID
    """
    # Get party name using helper function
    party_name = get_party_name_from_bank(bank)
    
    # Clean party name for use in ID
    party_clean = party_name.replace(" ", "_").replace("-", "_")
    party_clean = "".join(c for c in party_clean if c.isalnum() or c == "_")
    
    # Remove consecutive underscores
    while "__" in party_clean:
        party_clean = party_clean.replace("__", "_")
    
    # Remove leading/trailing underscores
    party_clean = party_clean.strip("_")
    
    # Limit to 20 characters
    party_clean = party_clean[:20]
    
    # Fallback if empty
    if not party_clean:
        party_clean = "UNKNOWN"
    
    # Get last 4 digits
    account_suffix = bank.bank_account_no[-4:] if bank.bank_account_no else "0000"
    
    # Combine
    bene_id = f"BENE_{party_clean}_{account_suffix}"
    
    # Ensure max 50 chars
    return bene_id[:50]


def check_beneficiary_exists(bene_id, client_id, client_secret, base_url):
    """Check if beneficiary already exists in Cashfree"""
    headers = get_v2_headers(client_id, client_secret)
    url = f"{base_url}/beneficiaries/{bene_id}"
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("beneficiary_id"):
                log_message({
                    "message": "Beneficiary exists",
                    "beneficiary_id": bene_id,
                    "status": data.get("beneficiary_status")
                }, "Cashfree Beneficiary Exists")
                return True
        return False
    except Exception as e:
        log_message({
            "error": "Failed to check beneficiary",
            "bene_id": bene_id,
            "exception": str(e)
        }, "Cashfree Check Beneficiary Error")
        return False


def create_beneficiary_v2(bank, client_id, client_secret, base_url):
    """
    Create beneficiary explicitly using v2 API
    Returns beneficiary_id for use in transfer
    
    CHANGES:
    - Removed all update_bank_account_on_verification() calls
    - Now ONLY creates beneficiary and saves beneficiary_id
    - Uses party name for beneficiary_name
    """
    
    # Generate consistent beneficiary_id
    bene_id = generate_beneficiary_id(bank)
    
    # Check if already saved in Bank Account
    existing_bene = bank.get("custom_cashfree_beneficiary_id")
    if existing_bene:
        if check_beneficiary_exists(existing_bene, client_id, client_secret, base_url):
            return existing_bene
    
    # Check if beneficiary with generated ID already exists
    if check_beneficiary_exists(bene_id, client_id, client_secret, base_url):
        frappe.db.set_value("Bank Account", bank.name, "custom_cashfree_beneficiary_id", bene_id, update_modified=False)
        frappe.db.commit()
        return bene_id
    
    # Create new beneficiary
    headers = get_v2_headers(client_id, client_secret)
    url = f"{base_url}/beneficiary"

    # Get contact details
    email, phone = get_contact_details_from_bank(bank)
    
    # Get IFSC
    ifsc = bank.get("branch_code") or bank.get("custom_ifsc_code") or ""
    if not ifsc:
        raise Exception("IFSC code missing in Bank Account")

    # CHANGE: Use party name instead of account_name
    party_name = get_party_name_from_bank(bank)

    payload = {
        "beneficiary_id": bene_id,
        "beneficiary_name": party_name,  # ← CHANGED
        "beneficiary_instrument_details": {
            "bank_account_number": bank.bank_account_no or "",
            "bank_ifsc": ifsc,
        },
        "beneficiary_contact_details": {
            "beneficiary_email": email or "default@example.com",
            "beneficiary_phone": phone or "9999999999",
            "beneficiary_country_code": "+91",
            "beneficiary_address": "India",
            "beneficiary_city": "Delhi",
            "beneficiary_state": "Delhi",
            "beneficiary_postal_code": "110001"
        }
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        data = resp.json()

        log_message(
            {"payload": payload, "response": data, "http_status": resp.status_code},
            "Cashfree Create Beneficiary V2",
        )

        # Success cases
        if resp.status_code in [200, 201]:
            if data.get("beneficiary_id") or data.get("beneficiary_status"):
                # CHANGE: ONLY save beneficiary_id, no verification
                frappe.db.set_value("Bank Account", bank.name, "custom_cashfree_beneficiary_id", bene_id, update_modified=False)
                frappe.db.commit()
                return bene_id
        
        # 409 Conflict - already exists
        if resp.status_code == 409:
            if "already exists" in str(data.get("message", "")).lower():
                frappe.db.set_value("Bank Account", bank.name, "custom_cashfree_beneficiary_id", bene_id, update_modified=False)
                frappe.db.commit()
                return bene_id

        # CHANGE: No bank account verification updates on failure
        raise Exception(f"Create Beneficiary failed: {data.get('message', 'Unknown error')}")

    except requests.exceptions.RequestException as e:
        log_message(
            {"error": str(e), "payload": payload, "url": url},
            "Cashfree Beneficiary Creation Error",
        )
        raise
    except Exception as e:
        log_message(
            {"error": str(e), "payload": payload, "url": url, "traceback": traceback.format_exc()},
            "Cashfree Beneficiary Creation Error",
        )
        raise


def standard_transfer_v2(doc, amount, bene_id, client_id, client_secret, base_url, settings):
    """
    Initiate transfer using beneficiary_id
    UNCHANGED
    """
    headers = get_v2_headers(client_id, client_secret)
    url = f"{base_url}/transfers"

    payload = {
        "transfer_id": doc.name,
        "beneficiary_details": {
            "beneficiary_id": bene_id
        },
        "transfer_amount": float(amount),
        "transfer_mode": "banktransfer",
        "remarks": f"{settings.payout_remarks_prefix or 'TK'} {doc.name}",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        data = resp.json()

        log_message(
            {"pr": doc.name, "payload": payload, "response": data, "http_status": resp.status_code},
            "Cashfree Standard Transfer V2",
        )

        if resp.status_code == 200:
            payout_id = data.get("cf_transfer_id") or data.get("transfer_id")
            raw_status = data.get("status") or data.get("status_code") or "PENDING"

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

            return payout_id, status, data, payload, bene_id
        else:
            raise Exception(f"Transfer failed: {data.get('message', 'Unknown error')}")

    except Exception as e:
        log_message(
            {"exception": str(e), "traceback": traceback.format_exc(), "pr": doc.name, "url": url},
            "Cashfree Transfer V2 Error",
        )
        raise


def trigger_payout_for_payment_request(doc, method=None):
    """
    Triggered when Payment Request updates
    WITH: Retry Support + Bank Verification + Director Override
    
    CHANGES:
    - Added bank verification check
    - Added director override for over-PO payments
    - Better error messages
    """

    log_message(
        {"pr": doc.name, "workflow_state": doc.workflow_state, "method": method},
        "Cashfree Trigger Start V2",
    )

    # Only run for queued state
    state = (doc.workflow_state or "").strip().lower()
    if state not in ["queued", "queue for payout", "queued for payout"]:
        return

    # ===== RETRY SUPPORT =====
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

    # ===== NEW: CHECK BANK VERIFICATION =====
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

    # ===== NEW: CHECK DIRECTOR OVERRIDE FOR OVER-PO =====
    if doc.reference_doctype == "Purchase Order" and doc.reference_name:
        try:
            po = frappe.get_doc("Purchase Order", doc.reference_name)
            po_amount = float(po.grand_total or 0)
            payment_amount = float(doc.grand_total or 0)
            
            if payment_amount > po_amount:
                director_override = doc.get("custom_director_override")
                over_amount = payment_amount - po_amount
                
                if not director_override or director_override == 0:
                    # Block payout
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
                    # Override enabled - log and proceed
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

    # Get settings
    try:
        settings, base_url, client_id, client_secret = get_cashfree_settings()
    except Exception as e:
        log_message({"error": "Settings failed", "pr": doc.name, "exception": str(e)}, "Cashfree Settings Error")
        return

    # Create beneficiary
    try:
        bene_id = create_beneficiary_v2(bank, client_id, client_secret, base_url)
    except Exception as e:
        log_message({"error": "Beneficiary failed", "pr": doc.name, "exception": str(e)}, "Cashfree Beneficiary Failed")
        frappe.throw(f"Failed to create beneficiary: {str(e)}")

    # Small delay
    time.sleep(1)

    # Create transfer
    try:
        payout_id, status, response_data, request_payload, bene_id = standard_transfer_v2(
            doc, amount, bene_id, client_id, client_secret, base_url, settings
        )
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
            "request_payload": frappe.as_json(request_payload),
            "response_payload": frappe.as_json(response_data),
        })
        pl.insert(ignore_permissions=True)
    except:
        pass

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
