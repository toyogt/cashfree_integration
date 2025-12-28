# File: cashfree_integration/api/webhooks.py

import frappe
import json
import hmac
import hashlib
from frappe import _


@frappe.whitelist(allow_guest=True, methods=["POST"])
def cashfree_payout_webhook():
    """
    Handle Cashfree payout status webhooks
    
    Cashfree sends POST request with:
    - Signature in header: x-webhook-signature
    - Payload: JSON with event, data, timestamp
    """
    try:
        # Get raw data
        if frappe.request.data:
            data = json.loads(frappe.request.data)
        else:
            data = frappe.local.form_dict
        
        # Log all incoming webhooks
        frappe.log_error(
            json.dumps(data, indent=2),
            "Cashfree Webhook Received"
        )
        
        # ✅ SECURITY: Verify signature (recommended)
        signature = frappe.get_request_header("x-webhook-signature") or ""
        if signature:
            if not verify_cashfree_signature(data, signature):
                frappe.log_error("Invalid webhook signature", "Cashfree Webhook Security")
                return {"status": "error", "message": "Invalid signature"}
        
        # Extract transfer details
        event = data.get("event", "")
        transfer_data = data.get("data", {})
        
        transfer_id = transfer_data.get("transfer_id") or data.get("transfer_id")
        status = transfer_data.get("status") or data.get("status")
        utr = transfer_data.get("utr") or data.get("utr")
        failure_reason = transfer_data.get("reason") or transfer_data.get("failure_reason") or ""
        
        if not transfer_id:
            return {"status": "error", "message": "No transfer_id in webhook"}
        
        # Find Payment Request
        pr_exists = frappe.db.exists("Payment Request", transfer_id)
        
        if not pr_exists:
            # Try by custom_cashfree_payout_id
            pr_list = frappe.db.sql("""
                SELECT name FROM `tabPayment Request`
                WHERE custom_cashfree_payout_id = %s
                LIMIT 1
            """, (transfer_id,), as_dict=True)
            
            if pr_list:
                transfer_id = pr_list[0].name
            else:
                frappe.log_error(
                    f"Payment Request not found: {transfer_id}\nWebhook data: {json.dumps(data, indent=2)}",
                    "Cashfree Webhook - PR Not Found"
                )
                return {"status": "error", "message": "Payment Request not found"}
        
        # Map Cashfree status to ERPNext status
        status_mapping = {
            "SUCCESS": "Success",
            "FAILED": "Failed",
            "REVERSED": "Reversed",
            "PENDING": "Pending",
            "RECEIVED": "Pending",
            "ERROR": "Failed"
        }
        
        new_status = status_mapping.get(str(status).upper(), "Pending")
        
        # Update Payment Request
        update_fields = {
            "custom_reconciliation_status": new_status
        }
        
        if utr:
            update_fields["custom_utr_number"] = utr
        
        if failure_reason and new_status == "Failed":
            update_fields["custom_failure_reason"] = failure_reason
        
        # Build SQL UPDATE query
        set_clause = ", ".join([f"{k} = %s" for k in update_fields.keys()])
        values = list(update_fields.values()) + [transfer_id]
        
        frappe.db.sql(f"""
            UPDATE `tabPayment Request`
            SET {set_clause}
            WHERE name = %s
        """, tuple(values))
        
        # Update Cashfree Payout Log
        frappe.db.sql("""
            UPDATE `tabCashfree Payout Log`
            SET 
                status = %s,
                response_payload = %s,
                modified = NOW()
            WHERE payment_request = %s
        """, (status, json.dumps(transfer_data), transfer_id))
        
        # ✅ AUTO-CREATE PAYMENT ENTRY AS DRAFT ON SUCCESS
        pe_created = None
        if new_status == "Success":
            try:
                pe_created = create_payment_entry_from_webhook(transfer_id, utr, transfer_data)
            except Exception as pe_error:
                frappe.log_error(
                    f"Failed to create Payment Entry for {transfer_id}: {str(pe_error)}\n{frappe.get_traceback()}",
                    "Webhook - PE Creation Failed"
                )
                # Don't fail the webhook - we can create PE manually later
        
        # Commit all changes
        frappe.db.commit()
        
        return {
            "status": "success",
            "message": "Webhook processed successfully",
            "transfer_id": transfer_id,
            "payment_request": transfer_id,
            "new_status": new_status,
            "utr": utr,
            "pe_created": pe_created is not None,
            "payment_entry": pe_created
        }
        
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            title="Cashfree Webhook Error",
            message=f"Error: {str(e)}\n\nWebhook Data: {json.dumps(data, indent=2) if 'data' in locals() else 'N/A'}\n\nTraceback: {frappe.get_traceback()}"
        )
        return {"status": "error", "message": str(e)}


def verify_cashfree_signature(data, signature):
    """
    Verify webhook signature from Cashfree
    
    Cashfree signature format:
    HMAC-SHA256(timestamp + json_data, client_secret)
    """
    try:
        settings = frappe.get_single("Cashfree Settings")
        secret = settings.get_password("client_secret")
        
        # Cashfree sends timestamp in data
        timestamp = str(data.get("timestamp", ""))
        payload_json = json.dumps(data.get("data", {}), separators=(',', ':'), sort_keys=True)
        
        # Construct payload
        payload = timestamp + payload_json
        
        # Compute signature
        computed_signature = hmac.new(
            secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Compare signatures (timing-safe)
        return hmac.compare_digest(computed_signature, signature)
    
    except Exception as e:
        frappe.log_error(f"Signature verification error: {str(e)}", "Webhook Signature Error")
        return False


def create_payment_entry_from_webhook(payment_request_name, utr, transfer_data):
    """
    Create Payment Entry as DRAFT (NOT submitted) for manual review
    SMART ALLOCATION: Allocates to PO/PI only if outstanding exists
    
    Args:
        payment_request_name: Payment Request name
        utr: UTR number from Cashfree
        transfer_data: Full transfer data from webhook
    
    Returns:
        Payment Entry name if created, None otherwise
    """
    # Get Payment Request details
    pr = frappe.db.get_value(
        "Payment Request",
        payment_request_name,
        ["party_type", "party", "grand_total", "company", "currency", "cost_center",
         "reference_doctype", "reference_name"],
        as_dict=True
    )
    
    if not pr:
        raise Exception(f"Payment Request {payment_request_name} not found")
    
    # Check if PE already exists (Draft or Submitted)
    existing_pe = frappe.db.exists("Payment Entry", {
        "payment_request": payment_request_name,
        "docstatus": ["!=", 2]  # Not cancelled
    })
    
    if existing_pe:
        frappe.log_error(
            f"Payment Entry already exists: {existing_pe}",
            "Webhook - Duplicate PE Prevention"
        )
        return existing_pe
    
    # ✅ CREATE PAYMENT ENTRY (DRAFT)
    pe = frappe.new_doc("Payment Entry")
    
    # Basic details
    pe.payment_type = "Pay"
    pe.party_type = pr.party_type
    pe.party = pr.party
    pe.company = pr.company
    pe.posting_date = frappe.utils.today()
    
    # Set Mode of Payment = Cashfree
    pe.mode_of_payment = "Cashfree"
    
    # Set Paid From account (Cashfree - Company)
    cashfree_account = get_cashfree_bank_account(pr.company)
    if not cashfree_account:
        raise Exception(f"Cashfree bank account not found for company {pr.company}")
    
    pe.paid_from = cashfree_account
    pe.paid_from_account_currency = pr.currency
    
    # Better supplier account detection
    if pr.party_type == "Supplier":
        # Try to get supplier-specific payable account
        supplier_account = frappe.db.get_value("Party Account", {
            "parent": pr.party,
            "parenttype": "Supplier",
            "company": pr.company
        }, "account")
        
        # Fallback to company default
        if not supplier_account:
            supplier_account = frappe.get_cached_value("Company", pr.company, "default_payable_account")
        
        pe.paid_to = supplier_account
        pe.paid_to_account_currency = pr.currency
    else:
        raise Exception(f"Unsupported party type: {pr.party_type}")
    
    # Amounts
    pe.paid_amount = pr.grand_total
    pe.received_amount = pr.grand_total
    pe.source_exchange_rate = 1.0
    pe.target_exchange_rate = 1.0
    
    # Reference details (UTR from webhook)
    pe.reference_no = utr
    pe.reference_date = frappe.utils.today()
    
    # Link to Payment Request
    pe.payment_request = payment_request_name
    
    # SMART REFERENCE ALLOCATION
    reference_allocated = False
    allocation_note = ""
    
    if pr.reference_doctype and pr.reference_name:
        try:
            if pr.reference_doctype == "Purchase Order":
                # Get PO details
                po = frappe.get_doc("Purchase Order", pr.reference_name)
                
                # Calculate outstanding
                outstanding = po.grand_total - (po.advance_paid or 0)
                
                if outstanding > 0:
                    # Allocate up to outstanding amount
                    allocated_amt = min(pr.grand_total, outstanding)
                    
                    pe.append("references", {
                        "reference_doctype": pr.reference_doctype,
                        "reference_name": pr.reference_name,
                        "allocated_amount": allocated_amt
                    })
                    
                    reference_allocated = True
                    
                    # Note for remarks
                    if allocated_amt < pr.grand_total:
                        advance_amt = pr.grand_total - allocated_amt
                        allocation_note = f"\n\n⚠️ PARTIAL ALLOCATION:\n- Allocated to PO: ₹{allocated_amt}\n- Advance/Unallocated: ₹{advance_amt}"
                    else:
                        allocation_note = f"\n✅ Fully allocated to {pr.reference_doctype}: {pr.reference_name}"
                else:
                    allocation_note = f"\n⚠️ NOT ALLOCATED: {pr.reference_doctype} {pr.reference_name} has no outstanding amount.\nFull ₹{pr.grand_total} recorded as advance payment."
                    
            elif pr.reference_doctype == "Purchase Invoice":
                # Get PI outstanding
                outstanding = frappe.db.get_value(
                    "Purchase Invoice",
                    pr.reference_name,
                    "outstanding_amount"
                )
                
                if outstanding and outstanding > 0:
                    allocated_amt = min(pr.grand_total, outstanding)
                    
                    pe.append("references", {
                        "reference_doctype": pr.reference_doctype,
                        "reference_name": pr.reference_name,
                        "allocated_amount": allocated_amt
                    })
                    
                    reference_allocated = True
                    
                    if allocated_amt < pr.grand_total:
                        advance_amt = pr.grand_total - allocated_amt
                        allocation_note = f"\n\n⚠️ PARTIAL ALLOCATION:\n- Allocated to PI: ₹{allocated_amt}\n- Advance/Unallocated: ₹{advance_amt}"
                    else:
                        allocation_note = f"\n✅ Fully allocated to {pr.reference_doctype}: {pr.reference_name}"
                else:
                    allocation_note = f"\n⚠️ NOT ALLOCATED: Purchase Invoice already paid.\nFull ₹{pr.grand_total} recorded as advance payment."
                    
        except Exception as ref_error:
            # Don't fail PE creation if reference allocation fails
            allocation_note = f"\n❌ Reference allocation failed: {str(ref_error)}\nPayment recorded as advance - manual allocation required."
            frappe.log_error(
                f"Reference allocation error for {pr.reference_name}: {str(ref_error)}\n{frappe.get_traceback()}",
                "Webhook - Reference Allocation Failed"
            )
    
    # Enhanced remarks with allocation details
    pe.remarks = (
        f"Payment via Cashfree Payout\n"
        f"Payment Request: {payment_request_name}\n"
        f"UTR: {utr}\n"
        f"Status: SUCCESS\n"
        f"Auto-created by webhook on {frappe.utils.now()}"
        f"{allocation_note}"
    )
    
    # Cost Center (if available)
    if pr.cost_center:
        pe.cost_center = pr.cost_center
    
    # INSERT AS DRAFT (docstatus=0)
    pe.flags.ignore_permissions = True
    pe.flags.ignore_mandatory = True
    pe.insert()
    
    # Enhanced logging
    frappe.log_error(
        f"✅ Payment Entry {pe.name} created as DRAFT for PR {payment_request_name}\n"
        f"UTR: {utr}\n"
        f"Amount: ₹{pr.grand_total}\n"
        f"Party: {pr.party}\n"
        f"Reference Allocated: {reference_allocated}\n"
        f"{allocation_note}\n"
        f"⚠️ REQUIRES MANUAL REVIEW AND SUBMISSION",
        "Webhook - PE Created (Draft)"
    )
    
    # Notify accountant
    try:
        notify_accountant_for_review(pe.name, payment_request_name, utr, pr.grand_total)
    except:
        pass  # Don't fail if notification fails
    
    # ✅ NEW: Notify vendor about payment
    try:
        send_payment_notification_to_vendor(pe.name, utr, pr.grand_total)
    except:
        pass  # Don't fail if email fails
    
    return pe.name


def get_cashfree_bank_account(company):
    """
    Get Cashfree bank account for company
    
    Args:
        company: Company name
    
    Returns:
        Account name (full path like "Cashfree - KFPL")
    """
    # Try: "Cashfree - {Company Abbr}"
    company_abbr = frappe.get_cached_value("Company", company, "abbr")
    account_name = f"Cashfree - {company_abbr}"
    
    account = frappe.db.get_value("Account", account_name, "name")
    
    if account:
        return account
    
    # Fallback 1: Search by "Cashfree" only
    account = frappe.db.get_value("Account", {
        "account_name": "Cashfree",
        "company": company,
        "account_type": "Bank",
        "is_group": 0
    }, "name")
    
    if account:
        return account
    
    # Fallback 2: Search with LIKE
    accounts = frappe.db.sql("""
        SELECT name
        FROM `tabAccount`
        WHERE company = %s
        AND account_type = 'Bank'
        AND is_group = 0
        AND account_name LIKE %s
        LIMIT 1
    """, (company, "%Cashfree%"), as_dict=True)
    
    if accounts:
        return accounts[0].name
    
    # Not found
    frappe.log_error(
        f"Cashfree bank account not found for company: {company}\n"
        f"Please create an Account with:\n"
        f"- Account Name: Cashfree - {company_abbr}\n"
        f"- Account Type: Bank\n"
        f"- Is Group: No",
        "Webhook - Bank Account Missing"
    )
    
    return None


def notify_accountant_for_review(pe_name, pr_name, utr, amount):
    """
    Notify accountant to review and submit Payment Entry
    
    Args:
        pe_name: Payment Entry name
        pr_name: Payment Request name
        utr: UTR number
        amount: Payment amount
    """
    try:
        # Get Accounts team users
        accountants = frappe.get_all("Has Role", 
            filters={"role": "Accounts Manager"},
            fields=["parent"]
        )
        
        for acc in accountants:
            frappe.publish_realtime(
                event='msgprint',
                message=f'<b>Payment Entry Created</b><br><br>'
                        f'A new Payment Entry <b>{pe_name}</b> has been created from Cashfree webhook.<br><br>'
                        f'<b>Details:</b><br>'
                        f'Payment Request: {pr_name}<br>'
                        f'UTR: {utr}<br>'
                        f'Amount: ₹{amount}<br><br>'
                        f'⚠️ Please review and submit the Payment Entry.',
                user=acc.parent
            )
    except:
        pass  # Fail silently


def send_payment_notification_to_vendor(pe_name, utr, amount):
    """
    ✅ NEW FUNCTION: Send email notification to vendor about payment
    
    Args:
        pe_name: Payment Entry name
        utr: UTR number
        amount: Payment amount
    
    Returns:
        bool: True if email sent successfully, False otherwise
    """
    try:
        pe = frappe.get_doc("Payment Entry", pe_name)
        
        # Get supplier email
        supplier_email = frappe.db.get_value("Supplier", pe.party, "email_id")
        
        if not supplier_email:
            frappe.log_error(
                f"No email found for supplier {pe.party}",
                "Vendor Email Missing"
            )
            return False
        
        # Email subject
        subject = f"Payment Processed - {pe.company}"
        
        # Email body (HTML)
        message = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #2e7d32;">Payment Processed Successfully</h2>
            
            <p>Dear <strong>{pe.party}</strong>,</p>
            
            <p>We are pleased to inform you that your payment has been processed successfully through Cashfree.</p>
            
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr style="background-color: #f5f5f5;">
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>Payment Entry</strong></td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{pe.name}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>Amount Paid</strong></td>
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>₹{amount:,.2f}</strong></td>
                </tr>
                <tr style="background-color: #f5f5f5;">
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>UTR Number</strong></td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{utr}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>Payment Date</strong></td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{pe.posting_date}</td>
                </tr>
                <tr style="background-color: #f5f5f5;">
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>Payment Mode</strong></td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{pe.mode_of_payment}</td>
                </tr>
            </table>
            
            <p><strong>Note:</strong> The amount will be credited to your registered bank account within 24 hours.</p>
            
            <p>If you have any questions, please contact our accounts department.</p>
            
            <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
            
            <p style="color: #666; font-size: 12px;">
                This is an automated notification from {pe.company}.<br>
                Please do not reply to this email.
            </p>
        </div>
        """
        
        # Send email
        frappe.sendmail(
            recipients=[supplier_email],
            subject=subject,
            message=message,
            reference_doctype="Payment Entry",
            reference_name=pe.name,
            now=True  # Send immediately
        )
        
        # Log success
        frappe.log_error(
            f"✅ Payment notification sent to {supplier_email}\n"
            f"PE: {pe.name}\n"
            f"Amount: ₹{amount}\n"
            f"UTR: {utr}",
            "Vendor Payment Notification Sent"
        )
        
        return True
        
    except Exception as e:
        frappe.log_error(
            f"Failed to send email for PE {pe_name}: {str(e)}\n{frappe.get_traceback()}",
            "Vendor Email Failed"
        )
        return False
