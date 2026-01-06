import frappe
import json
import hmac
import hashlib
import base64


@frappe.whitelist(allow_guest=True, methods=["POST"])
def cashfree_payout_webhook():
    try:
        # === safe parse incoming body ===
        raw_body = frappe.request.get_data(as_text=True)
        content_type = frappe.get_request_header("content-type") or ""

        try:
            data = json.loads(raw_body) if raw_body else {}
        except Exception:
            data = dict(frappe.local.form_dict)

        frappe.log_error(
            title="Cashfree Webhook Received",
            message=json.dumps({
                "content_type": content_type,
                "raw_body": raw_body[:500],
                "parsed_data": data
            }, indent=2, default=str)
        )

        # === signature verification ===
        sig_in_body = data.get("signature")
        sig_in_header = frappe.get_request_header("x-webhook-signature")

        if sig_in_body:
            if not verify_cashfree_signature_v1(data, sig_in_body):
                frappe.log_error("Invalid V1 signature", "Cashfree Webhook")
                return {"status": "error", "message": "Invalid signature"}
        elif sig_in_header:
            if not verify_cashfree_signature_v2(raw_body, sig_in_header):
                frappe.log_error("Invalid V2 signature", "Cashfree Webhook")
                return {"status": "error", "message": "Invalid signature"}

        # === extract core fields ===
        transfer_id = data.get("transferId") or data.get("transfer_id")
        status = data.get("status")
        utr = data.get("utr")
        failure_reason = data.get("reason") or data.get("failure_reason") or ""

        if not transfer_id:
            return {"status": "success", "message": "No transfer_id present"}

        # === find Payment Request ===
        pr_exists = frappe.db.exists("Payment Request", transfer_id)

        if not pr_exists:
            pr_list = frappe.db.sql("""
                SELECT name FROM `tabPayment Request`
                WHERE custom_cashfree_payout_id = %s
                LIMIT 1
            """, (transfer_id,), as_dict=True)

            if pr_list:
                transfer_id = pr_list[0].name
            else:
                frappe.log_error(
                    f"Payment Request not found: {transfer_id}",
                    "Cashfree Webhook - PR Not Found"
                )
                return {"status": "error", "message": "Payment Request not found"}

        # === map status ===
        status_mapping = {
            "SUCCESS": "Success",
            "FAILED": "Failed",
            "REVERSED": "Reversed",
            "PENDING": "Pending",
            "ERROR": "Failed"
        }

        new_status = status_mapping.get(str(status).upper(), "Pending")

        # === update Payment Request ===
        update_fields = {"custom_reconciliation_status": new_status}
        if utr:
            update_fields["custom_utr_number"] = utr
        if failure_reason:
            update_fields["custom_failure_reason"] = failure_reason

        set_clause = ", ".join([f"{k} = %s" for k in update_fields.keys()])
        values = list(update_fields.values()) + [transfer_id]

        frappe.db.sql(f"""
            UPDATE `tabPayment Request`
            SET {set_clause}
            WHERE name = %s
        """, tuple(values))

        # === update Cashfree Payout Log ===
        frappe.db.sql("""
            UPDATE `tabCashfree Payout Log`
            SET status = %s, response_payload = %s, modified = NOW()
            WHERE payment_request = %s
        """, (new_status, json.dumps(data), transfer_id))

        # === create draft Payment Entry on success ===
        pe_created = None
        if new_status == "Success":
            try:
                pe_created = create_payment_entry_from_webhook(transfer_id, utr, data)
            except Exception as e:
                frappe.log_error(str(e), "PE creation failed")

        frappe.db.commit()

        return {
            "status": "success",
            "transfer_id": transfer_id,
            "new_status": new_status,
            "utr": utr,
            "pe_created": bool(pe_created)
        }

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(str(e), "Cashfree Webhook Handler Error")
        return {"status": "error", "message": str(e)}


def verify_cashfree_signature_v1(data, received_signature):
    """V1: sort params, concat values, HMAC, base64"""
    try:
        from frappe.utils.password import get_decrypted_password
        secret = get_decrypted_password("Cashfree Settings", "Cashfree Settings", "client_secret")
        if not secret:
            return False

        stripped = {k: v for k, v in data.items() if k != "signature"}
        sorted_keys = sorted(stripped.keys())
        payload = "".join(str(stripped[k]) for k in sorted_keys)

        computed = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
        computed_signature = base64.b64encode(computed).decode()

        return hmac.compare_digest(computed_signature, received_signature)

    except Exception as e:
        frappe.log_error(str(e), "V1 signature verify error")
        return False


def verify_cashfree_signature_v2(raw_body, received_signature):
    """V2: HMAC(timestamp + raw_body), base64"""
    try:
        from frappe.utils.password import get_decrypted_password
        secret = get_decrypted_password("Cashfree Settings", "Cashfree Settings", "client_secret")
        if not secret:
            return False

        timestamp = frappe.get_request_header("x-webhook-timestamp") or ""
        payload = timestamp + raw_body
        
        computed = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
        computed_signature = base64.b64encode(computed).decode()
        
        return hmac.compare_digest(computed_signature, received_signature)
        
    except Exception as e:
        frappe.log_error(str(e), "V2 signature verify error")
        return False
def create_payment_entry_from_webhook(payment_request_name, utr, transfer_data):
    """
    Create Payment Entry as DRAFT (NOT submitted) for manual review
    SMART ALLOCATION: Allocates to PO/PI only if outstanding exists
    """
    # Get Payment Request details
    pr = frappe.db.get_value(
        "Payment Request",
        payment_request_name,
        ["party_type", "party", "grand_total", "company", "currency", "cost_center",
         "reference_doctype", "reference_name", "mode_of_payment"],
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
        frappe.logger().info(f"Payment Entry already exists: {existing_pe}")
        return existing_pe
    
    # ✅ CREATE PAYMENT ENTRY (DRAFT)
    pe = frappe.new_doc("Payment Entry")
    
    # Basic details
    pe.payment_type = "Pay"
    pe.party_type = pr.party_type
    pe.party = pr.party
    pe.company = pr.company
    pe.posting_date = frappe.utils.today()
    
    # Set Mode of Payment
    pe.mode_of_payment = pr.mode_of_payment or "Bank Transfer"
    
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
    allocation_note = ""
    
    if pr.reference_doctype and pr.reference_name:
        try:
            if pr.reference_doctype == "Purchase Order":
                po = frappe.get_doc("Purchase Order", pr.reference_name)
                outstanding = po.grand_total - (po.advance_paid or 0)
                
                if outstanding > 0:
                    allocated_amt = min(pr.grand_total, outstanding)
                    pe.append("references", {
                        "reference_doctype": pr.reference_doctype,
                        "reference_name": pr.reference_name,
                        "allocated_amount": allocated_amt
                    })
                    
                    if allocated_amt < pr.grand_total:
                        advance_amt = pr.grand_total - allocated_amt
                        allocation_note = f"\n\n⚠️ PARTIAL ALLOCATION:\n- Allocated to PO: ₹{allocated_amt}\n- Advance: ₹{advance_amt}"
                    else:
                        allocation_note = f"\n✅ Fully allocated to {pr.reference_doctype}: {pr.reference_name}"
                else:
                    allocation_note = f"\n⚠️ NOT ALLOCATED: PO has no outstanding. Full ₹{pr.grand_total} recorded as advance."
                    
            elif pr.reference_doctype == "Purchase Invoice":
                outstanding = frappe.db.get_value("Purchase Invoice", pr.reference_name, "outstanding_amount")
                
                if outstanding and outstanding > 0:
                    allocated_amt = min(pr.grand_total, outstanding)
                    pe.append("references", {
                        "reference_doctype": pr.reference_doctype,
                        "reference_name": pr.reference_name,
                        "allocated_amount": allocated_amt
                    })
                    
                    if allocated_amt < pr.grand_total:
                        advance_amt = pr.grand_total - allocated_amt
                        allocation_note = f"\n\n⚠️ PARTIAL ALLOCATION:\n- Allocated to PI: ₹{allocated_amt}\n- Advance: ₹{advance_amt}"
                    else:
                        allocation_note = f"\n✅ Fully allocated to {pr.reference_doctype}: {pr.reference_name}"
                else:
                    allocation_note = f"\n⚠️ NOT ALLOCATED: PI already paid. Full ₹{pr.grand_total} recorded as advance."
                    
        except Exception as ref_error:
            allocation_note = f"\n❌ Reference allocation failed: {str(ref_error)}\nPayment recorded as advance."
            frappe.logger().error(f"Reference allocation error: {str(ref_error)}")
    
    # Enhanced remarks
    pe.remarks = (
        f"Payment via Cashfree Payout\n"
        f"Payment Request: {payment_request_name}\n"
        f"UTR: {utr}\n"
        f"Status: SUCCESS\n"
        f"Auto-created by webhook on {frappe.utils.now()}"
        f"{allocation_note}"
    )
    
    # Cost Center
    if pr.cost_center:
        pe.cost_center = pr.cost_center
    
    # INSERT AS DRAFT (docstatus=0)
    pe.flags.ignore_permissions = True
    pe.flags.ignore_mandatory = True
    pe.insert()
    
    frappe.logger().info(f"✅ Payment Entry {pe.name} created as DRAFT for PR {payment_request_name}")
    
    # Notify accountant
    try:
        notify_accountant_for_review(pe.name, payment_request_name, utr, pr.grand_total)
    except:
        pass
    
    # Notify vendor
    try:
        send_payment_notification_to_vendor(pe.name, pr.party, utr, pr.grand_total)
    except:
        pass
    
    return pe.name


def get_cashfree_bank_account(company):
    """Get Cashfree bank account for company"""
    company_abbr = frappe.get_cached_value("Company", company, "abbr")
    account_name = f"Cashfree - {company_abbr}"
    
    account = frappe.db.get_value("Account", account_name, "name")
    if account:
        return account
    
    # Fallback 1
    account = frappe.db.get_value("Account", {
        "account_name": "Cashfree",
        "company": company,
        "account_type": "Bank",
        "is_group": 0
    }, "name")
    
    if account:
        return account
    
    # Fallback 2
    accounts = frappe.db.sql("""
        SELECT name FROM `tabAccount`
        WHERE company = %s AND account_type = 'Bank' AND is_group = 0
        AND account_name LIKE %s LIMIT 1
    """, (company, "%Cashfree%"), as_dict=True)
    
    if accounts:
        return accounts[0].name
    
    frappe.logger().error(f"Cashfree bank account not found for company: {company}")
    return None


def notify_accountant_for_review(pe_name, pr_name, utr, amount):
    """Notify accountant to review Payment Entry"""
    try:
        accountants = frappe.get_all("Has Role", 
            filters={"role": "Accounts Manager"},
            fields=["parent"]
        )
        
        for acc in accountants:
            frappe.publish_realtime(
                event='msgprint',
                message=f'<b>Payment Entry Created</b><br><br>'
                        f'Payment Entry <b>{pe_name}</b> created from Cashfree webhook.<br><br>'
                        f'<b>Details:</b><br>'
                        f'Payment Request: {pr_name}<br>'
                        f'UTR: {utr}<br>'
                        f'Amount: ₹{amount}<br><br>'
                        f'⚠️ Please review and submit.',
                user=acc.parent
            )
    except:
        pass


def send_payment_notification_to_vendor(pe_name, party, utr, amount):
    """Send email notification to vendor"""
    try:
        pe = frappe.get_doc("Payment Entry", pe_name)
        
        supplier_email = frappe.db.get_value("Supplier", party, "email_id")
        
        if not supplier_email:
            contact = frappe.db.get_value("Dynamic Link", {
                "link_doctype": "Supplier",
                "link_name": party
            }, "parent")
            
            if contact:
                supplier_email = frappe.db.get_value("Contact", contact, "email_id")
        
        if not supplier_email:
            return False
        
        subject = f"Payment Processed - {pe.company}"
        
        message = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #2e7d32;">Payment Processed Successfully</h2>
            <p>Dear <strong>{party}</strong>,</p>
            <p>Your payment has been processed successfully through Cashfree.</p>
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
            </table>
            <p><strong>Note:</strong> Amount will be credited within 24 hours.</p>
            <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
            <p style="color: #666; font-size: 12px;">
                Automated notification from {pe.company}.<br>
                Please do not reply to this email.
            </p>
        </div>
        """
        
        frappe.sendmail(
            recipients=[supplier_email],
            subject=subject,
            message=message,
            reference_doctype="Payment Entry",
            reference_name=pe.name,
            now=False
        )
        
        frappe.logger().info(f"✅ Payment notification queued for {supplier_email}")
        return True
        
    except Exception as e:
        frappe.logger().error(f"Failed to send email: {str(e)}")
        return False
