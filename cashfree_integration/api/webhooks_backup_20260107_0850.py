import frappe
import json
import hmac
import hashlib
import base64
from datetime import datetime
import time


@frappe.whitelist(allow_guest=True, methods=["POST"])
def cashfree_payout_webhook():
    """
    Production-Ready Cashfree Webhook Handler
    
    Flow:
    1. LOG RAW DATA (ALWAYS) - Insurance against data loss
    2. Signature Validation - Security
    3. Create PE in DRAFT - Smart recovery strategy
    4. Run Business Validations - Data integrity
    5. Auto-submit if all pass, else keep draft + alert admin
    
    References: [web:400] [web:404]
    """
    start_time = time.time()
    webhook_log = None
    
    try:
        # ============================================
        # STEP 1: CAPTURE RAW DATA (ALWAYS)
        # ============================================
        raw_body = frappe.request.get_data(as_text=True)
        headers = {k: v for k, v in dict(frappe.request.headers).items()}
        
        # Parse JSON safely
        try:
            data = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError as e:
            return {"status": "error", "message": "Invalid JSON payload"}, 400
        
        # Extract transfer_id (idempotency key)
        transfer_id = data.get("transferId") or data.get("transfer_id")
        event_type = data.get("event") or "UNKNOWN"
        
        # Create webhook log IMMEDIATELY (before any processing)
        webhook_log = create_webhook_log(
            transfer_id=transfer_id or f"UNKNOWN-{int(time.time())}",
            webhook_event=event_type,
            raw_payload=raw_body,
            signature=headers.get("x-webhook-signature") or data.get("signature"),
            headers=json.dumps(headers, default=str),
            status="Received"
        )
        
        frappe.logger().info(f"[Cashfree Webhook] Received: {transfer_id} | Event: {event_type}")
        
        # ============================================
        # STEP 2: SIGNATURE VALIDATION
        # ============================================
        sig_in_body = data.get("signature")
        sig_in_header = headers.get("x-webhook-signature")
        
        signature_valid = False
        
        if sig_in_body:
            signature_valid = verify_cashfree_signature_v1(data, sig_in_body)
            if not signature_valid:
                update_webhook_log(webhook_log, {
                    "status": "Signature Failed",
                    "error_log": "V1 signature verification failed"
                })
                frappe.log_error("Invalid V1 signature", "Cashfree Webhook")
                return {"status": "error", "message": "Invalid signature"}, 401
                
        elif sig_in_header:
            signature_valid = verify_cashfree_signature_v2(raw_body, sig_in_header)
            if not signature_valid:
                update_webhook_log(webhook_log, {
                    "status": "Signature Failed",
                    "error_log": "V2 signature verification failed"
                })
                frappe.log_error("Invalid V2 signature", "Cashfree Webhook")
                return {"status": "error", "message": "Invalid signature"}, 401
        else:
            # No signature provided (only allow in development)
            if frappe.conf.get("developer_mode"):
                frappe.logger().warning("[Cashfree Webhook] No signature - DEV MODE ONLY")
                signature_valid = True
            else:
                update_webhook_log(webhook_log, {
                    "status": "Signature Failed",
                    "error_log": "No signature provided"
                })
                return {"status": "error", "message": "Missing signature"}, 401
        
        update_webhook_log(webhook_log, {"status": "Signature Verified"})
        
        # ============================================
        # STEP 3: IGNORE NON-SUCCESS EVENTS
        # ============================================
        if event_type != "TRANSFER_SUCCESS":
            update_webhook_log(webhook_log, {
                "status": "Ignored",
                "error_log": f"Event type {event_type} not processed"
            })
            
            # Still update PR status for failed transfers
            if transfer_id and event_type in ["TRANSFER_FAILED", "TRANSFER_REVERSED"]:
                update_payment_request_status(transfer_id, event_type, data)
            
            return {"status": "ignored", "message": f"Event {event_type} not processed"}, 200
        
        # ============================================
        # STEP 4: EXTRACT PAYMENT DATA
        # ============================================
        transfer_data = data.get("data", {}).get("transfer", {}) if "data" in data else data
        
        payment_data = {
            "transfer_id": transfer_id,
            "utr": transfer_data.get("utr"),
            "amount": float(transfer_data.get("amount", 0)),
            "status": transfer_data.get("transferStatus") or transfer_data.get("status"),
            "acknowledged_at": transfer_data.get("acknowledgedAt"),
            "processed_at": transfer_data.get("processedAt")
        }
        
        if not payment_data["transfer_id"]:
            update_webhook_log(webhook_log, {
                "status": "Error",
                "error_log": "No transfer_id found in payload"
            })
            return {"status": "error", "message": "Missing transfer_id"}, 400
        
        # ============================================
        # STEP 5: CREATE PE IN DRAFT + VALIDATE
        # ============================================
        result = create_payment_entry_with_validation(payment_data, webhook_log)
        
        # Calculate processing time
        processing_time = time.time() - start_time
        update_webhook_log(webhook_log, {"processing_time": processing_time})
        
        return result
        
    except Exception as e:
        error_trace = frappe.get_traceback()
        frappe.log_error(
            title=f"Cashfree Webhook Critical Error - {transfer_id}",
            message=error_trace
        )
        
        if webhook_log:
            update_webhook_log(webhook_log, {
                "status": "Error",
                "error_log": error_trace
            })
        
        # Alert admin immediately
        notify_admin_critical_failure(transfer_id, error_trace)
        
        return {"status": "error", "message": "Internal server error"}, 500


def create_webhook_log(transfer_id, webhook_event, raw_payload, signature, headers, status):
    """
    Create webhook log - ALWAYS succeeds
    Implements idempotency check [web:404]
    """
    try:
        # Check if webhook already processed (idempotency)
        existing = frappe.db.exists("Cashfree Webhook Log", {"transfer_id": transfer_id})
        
        if existing:
            log = frappe.get_doc("Cashfree Webhook Log", existing)
            log.retry_count = (log.retry_count or 0) + 1
            log.status = status
            log.save(ignore_permissions=True)
            frappe.db.commit()
            
            frappe.logger().info(f"[Cashfree Webhook] Duplicate webhook detected: {transfer_id} (Retry: {log.retry_count})")
            return log
        
        # Create new log
        log = frappe.get_doc({
            "doctype": "Cashfree Webhook Log",
            "transfer_id": transfer_id,
            "webhook_event": webhook_event,
            "raw_payload": raw_payload,
            "signature": signature,
            "headers": headers,
            "status": status,
            "retry_count": 0
        })
        log.insert(ignore_permissions=True)
        frappe.db.commit()
        
        frappe.logger().info(f"[Cashfree Webhook] Log created: {log.name}")
        return log
        
    except Exception as e:
        # Even if log creation fails, don't crash webhook
        frappe.log_error(f"Failed to create webhook log: {str(e)}", "Cashfree Webhook Log Error")
        return None


def update_webhook_log(webhook_log, updates):
    """Update webhook log fields safely"""
    if not webhook_log:
        return
    
    try:
        for field, value in updates.items():
            webhook_log.db_set(field, value, update_modified=False)
        frappe.db.commit()
    except Exception as e:
        frappe.logger().error(f"Failed to update webhook log: {str(e)}")


def create_payment_entry_with_validation(payment_data, webhook_log):
    """
    Core logic: Create PE in DRAFT → Validate → Submit if all pass
    [web:400] [web:404]
    """
    
    validation_results = {
        "passed": [],
        "failed": [],
        "warnings": []
    }
    
    transfer_id = payment_data["transfer_id"]
    utr = payment_data["utr"]
    amount = payment_data["amount"]
    
    try:
        # ============================================
        # VALIDATION 1: Find Payment Request
        # ============================================
        pr_name = frappe.db.get_value(
            "Payment Request",
            {"name": transfer_id},
            ["name", "party_type", "party", "grand_total", "company", "currency",
             "reference_doctype", "reference_name", "mode_of_payment"],
            as_dict=True
        )
        
        if not pr_name:
            # Fallback: Check custom field
            pr_list = frappe.db.sql("""
                SELECT name, party_type, party, grand_total, company, currency,
                       reference_doctype, reference_name, mode_of_payment
                FROM `tabPayment Request`
                WHERE custom_cashfree_payout_id = %s
                LIMIT 1
            """, (transfer_id,), as_dict=True)
            
            if pr_list:
                pr_name = pr_list[0]
            else:
                validation_results["failed"].append({
                    "check": "Payment Request Lookup",
                    "reason": f"PR not found for Transfer ID: {transfer_id}"
                })
                
                update_webhook_log(webhook_log, {
                    "status": "Validation Failed",
                    "validation_results": json.dumps(validation_results, indent=2)
                })
                
                notify_admin_validation_failure(transfer_id, validation_results, webhook_log.name)
                return {"status": "error", "message": "Payment Request not found"}, 404
        
        validation_results["passed"].append(f"Payment Request found: {pr_name.name}")
        update_webhook_log(webhook_log, {"payment_request": pr_name.name})
        
        # ============================================
        # VALIDATION 2: Check for Duplicate PE
        # ============================================
        existing_pe = frappe.db.exists("Payment Entry", {
            "reference_no": utr,
            "party": pr_name.party,
            "docstatus": ["!=", 2]  # Not cancelled
        })
        
        if existing_pe:
            validation_results["warnings"].append(f"PE already exists: {existing_pe}")
            
            update_webhook_log(webhook_log, {
                "status": "Duplicate",
                "payment_entry": existing_pe,
                "validation_results": json.dumps(validation_results, indent=2)
            })
            
            frappe.logger().info(f"[Cashfree Webhook] Duplicate PE detected: {existing_pe}")
            return {
                "status": "success",
                "message": "Payment already processed",
                "payment_entry": existing_pe
            }, 200
        
        validation_results["passed"].append("No duplicate PE found")
        
        # ============================================
        # VALIDATION 3: Check Cashfree Account
        # ============================================
        cashfree_account = get_cashfree_bank_account(pr_name.company)
        
        if not cashfree_account:
            validation_results["failed"].append({
                "check": "Cashfree Account",
                "reason": f"Cashfree account not found for company {pr_name.company}"
            })
        else:
            validation_results["passed"].append(f"Cashfree account: {cashfree_account}")
        
        # ============================================
        # VALIDATION 4: Check Supplier Account
        # ============================================
        supplier_account = frappe.db.get_value("Party Account", {
            "parent": pr_name.party,
            "parenttype": "Supplier",
            "company": pr_name.company
        }, "account")
        
        if not supplier_account:
            supplier_account = frappe.get_cached_value("Company", pr_name.company, "default_payable_account")
            validation_results["warnings"].append("Using default payable account")
        else:
            validation_results["passed"].append(f"Supplier account: {supplier_account}")
        
        # ============================================
        # CREATE PAYMENT ENTRY IN DRAFT
        # ============================================
        update_webhook_log(webhook_log, {"status": "PE Draft Created"})
        
        pe = frappe.new_doc("Payment Entry")
        
        # Basic details
        pe.payment_type = "Pay"
        pe.party_type = pr_name.party_type
        pe.party = pr_name.party
        pe.company = pr_name.company
        pe.posting_date = frappe.utils.today()
        
        # Mode of Payment
        pe.mode_of_payment = "Cashfree"
        
        # Accounts
        pe.paid_from = cashfree_account
        pe.paid_from_account_currency = pr_name.currency
        pe.paid_to = supplier_account
        pe.paid_to_account_currency = pr_name.currency
        
        # Amounts
        pe.paid_amount = amount
        pe.received_amount = amount
        pe.source_exchange_rate = 1.0
        pe.target_exchange_rate = 1.0
        
        # Reference (UTR)
        pe.reference_no = utr
        pe.reference_date = frappe.utils.today()
        
        # ============================================
        # VALIDATION 5: Smart Allocation
        # ============================================
        allocation_note = ""
        
        if pr_name.reference_doctype and pr_name.reference_name:
            try:
                if pr_name.reference_doctype == "Purchase Order":
                    po = frappe.get_doc("Purchase Order", pr_name.reference_name)
                    outstanding = po.grand_total - (po.advance_paid or 0)
                    
                    if outstanding > 0:
                        allocated_amt = min(amount, outstanding)
                        
                        reference_row = {
                            "reference_doctype": pr_name.reference_doctype,
                            "reference_name": pr_name.reference_name,
                            "allocated_amount": allocated_amt
                        }
                        
                        # Check payment terms
                        if hasattr(po, 'payment_schedule') and len(po.payment_schedule) > 0:
                            for term in po.payment_schedule:
                                if term.outstanding > 0:
                                    reference_row["payment_term"] = term.payment_term
                                    break
                        
                        pe.append("references", reference_row)
                        validation_results["passed"].append(f"Allocated ₹{allocated_amt} to {pr_name.reference_name}")
                        allocation_note = f"\n✅ Allocated to PO: {pr_name.reference_name}"
                        
                        if allocated_amt < amount:
                            advance_amt = amount - allocated_amt
                            validation_results["warnings"].append(f"₹{advance_amt} recorded as advance")
                            allocation_note += f"\n⚠️ Advance: ₹{advance_amt}"
                    else:
                        validation_results["warnings"].append("PO has no outstanding - recorded as advance")
                        allocation_note = f"\n⚠️ PO fully paid - Amount recorded as advance"
                        
                elif pr_name.reference_doctype == "Purchase Invoice":
                    outstanding = frappe.db.get_value("Purchase Invoice", pr_name.reference_name, "outstanding_amount")
                    
                    if outstanding and outstanding > 0:
                        allocated_amt = min(amount, outstanding)
                        
                        pe.append("references", {
                            "reference_doctype": pr_name.reference_doctype,
                            "reference_name": pr_name.reference_name,
                            "allocated_amount": allocated_amt
                        })
                        
                        validation_results["passed"].append(f"Allocated ₹{allocated_amt} to {pr_name.reference_name}")
                        allocation_note = f"\n✅ Allocated to PI: {pr_name.reference_name}"
                        
                        if allocated_amt < amount:
                            advance_amt = amount - allocated_amt
                            validation_results["warnings"].append(f"₹{advance_amt} recorded as advance")
                            allocation_note += f"\n⚠️ Advance: ₹{advance_amt}"
                    else:
                        validation_results["warnings"].append("PI already paid - recorded as advance")
                        allocation_note = f"\n⚠️ PI already paid - Amount recorded as advance"
                        
            except Exception as ref_error:
                validation_results["failed"].append({
                    "check": "Reference Allocation",
                    "reason": str(ref_error)
                })
                allocation_note = f"\n❌ Allocation failed: {str(ref_error)}"
                frappe.logger().error(f"Reference allocation error: {str(ref_error)}")
        
        # Enhanced remarks
        pe.remarks = (
            f"Payment via Cashfree Payout\n"
            f"Payment Request: {pr_name.name}\n"
            f"UTR: {utr}\n"
            f"Transfer ID: {transfer_id}\n"
            f"Status: SUCCESS\n"
            f"Auto-created by webhook on {frappe.utils.now()}"
            f"{allocation_note}"
        )
        
        # Save as DRAFT
        pe.flags.ignore_permissions = True
        pe.flags.ignore_mandatory = True
        pe.insert()
        frappe.db.commit()
        
        update_webhook_log(webhook_log, {"payment_entry": pe.name})
        
        frappe.logger().info(f"[Cashfree Webhook] PE draft created: {pe.name}")
        
        # ============================================
        # DECISION: SUBMIT OR KEEP DRAFT?
        # ============================================
        can_submit = len(validation_results["failed"]) == 0
        
        if can_submit:
            # All critical validations passed → Auto-submit
            try:
                pe.submit()
                frappe.db.commit()
                
                update_webhook_log(webhook_log, {
                    "status": "PE Submitted",
                    "processed_at": datetime.now(),
                    "validation_results": json.dumps(validation_results, indent=2)
                })
                
                # Update Payment Request
                frappe.db.sql("""
                    UPDATE `tabPayment Request`
                    SET custom_reconciliation_status = 'Success'
                    WHERE name = %s
                """, (pr_name.name,))
                
                # Update Cashfree Payout Log (if exists)
                frappe.db.sql("""
                    UPDATE `tabCashfree Payout Log`
                    SET status = 'Success'
                    WHERE payment_request = %s
                """, (pr_name.name,))
                
                frappe.db.commit()
                
                frappe.logger().info(f"[Cashfree Webhook] ✅ PE submitted: {pe.name}")
                
                # Notify accountant (real-time notification only - NO EMAIL)
                notify_accountant_pe_submitted(pe.name, pr_name.name, utr, amount)
                
                return {
                    "status": "success",
                    "payment_entry": pe.name,
                    "validation": "All checks passed - PE submitted",
                    "warnings": validation_results["warnings"]
                }, 200
                
            except Exception as submit_error:
                # Submit failed - keep as draft and alert
                validation_results["failed"].append({
                    "check": "PE Submission",
                    "reason": str(submit_error)
                })
                
                update_webhook_log(webhook_log, {
                    "status": "Validation Failed",
                    "validation_results": json.dumps(validation_results, indent=2),
                    "error_log": frappe.get_traceback()
                })
                
                notify_admin_draft_review(pe.name, transfer_id, validation_results, webhook_log.name)
                
                return {
                    "status": "partial_success",
                    "payment_entry": pe.name,
                    "message": "PE created in draft - submission failed - review required",
                    "validation_failures": validation_results["failed"]
                }, 200
        else:
            # Some critical validations failed → Keep in draft
            update_webhook_log(webhook_log, {
                "status": "Validation Failed",
                "validation_results": json.dumps(validation_results, indent=2)
            })
            
            notify_admin_draft_review(pe.name, transfer_id, validation_results, webhook_log.name)
            
            return {
                "status": "partial_success",
                "payment_entry": pe.name,
                "message": "PE created in draft - admin review required",
                "validation_failures": validation_results["failed"],
                "warnings": validation_results["warnings"]
            }, 200
            
    except Exception as e:
        error_trace = frappe.get_traceback()
        
        update_webhook_log(webhook_log, {
            "status": "Error",
            "error_log": error_trace,
            "validation_results": json.dumps(validation_results, indent=2)
        })
        
        frappe.log_error(
            title=f"PE Creation Failed - {transfer_id}",
            message=error_trace
        )
        
        notify_admin_critical_failure(transfer_id, error_trace, webhook_log.name)
        
        return {"status": "error", "message": str(e)}, 500


def notify_accountant_pe_submitted(pe_name, pr_name, utr, amount):
    """
    Real-time notification ONLY (NO EMAIL)
    Notify users with "Accountant" role via bell icon
    """
    try:
        accountants = frappe.get_all("Has Role", 
            filters={"role": "Accountant"},
            fields=["parent"]
        )
        
        for acc in accountants:
            frappe.publish_realtime(
                event='msgprint',
                message=f'<b>✅ Payment Entry Auto-Submitted</b><br><br>'
                        f'Payment Entry <b>{pe_name}</b> created from Cashfree webhook.<br><br>'
                        f'<b>Details:</b><br>'
                        f'Payment Request: {pr_name}<br>'
                        f'UTR: {utr}<br>'
                        f'Amount: ₹{amount:,.2f}<br><br>'
                        f'<a href="/app/payment-entry/{pe_name}" target="_blank">View Payment Entry</a>',
                user=acc.parent
            )
        
        frappe.logger().info(f"[Cashfree Webhook] Notified {len(accountants)} accountants")
        
    except Exception as e:
        frappe.logger().error(f"Failed to notify accountants: {str(e)}")


def notify_admin_draft_review(pe_name, transfer_id, validation_results, webhook_log_name):
    """
    Alert admin when PE is in draft due to validation warnings/failures
    Email notification for manual review
    """
    try:
        failed_checks = validation_results.get("failed", [])
        warnings = validation_results.get("warnings", [])
        
        message = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px;">
            <h2 style="color: #ff9800;">⚠️ Payment Entry Requires Review</h2>
            
            <p>A Payment Entry was created in <strong>DRAFT</strong> status due to validation issues:</p>
            
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr style="background-color: #f5f5f5;">
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>Payment Entry</strong></td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{pe_name}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>Transfer ID</strong></td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{transfer_id}</td>
                </tr>
                <tr style="background-color: #f5f5f5;">
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>Webhook Log</strong></td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{webhook_log_name}</td>
                </tr>
            </table>
            
            <h3 style="color: #d32f2f;">❌ Failed Validations:</h3>
            <ul style="color: #d32f2f;">
                {"".join([f"<li><strong>{item['check']}:</strong> {item['reason']}</li>" for item in failed_checks])}
            </ul>
            
            {"<h3 style='color: #ff9800;'>⚠️ Warnings:</h3><ul style='color: #ff9800;'>" + "".join([f"<li>{w}</li>" for w in warnings]) + "</ul>" if warnings else ""}
            
            <h3>📋 Action Required:</h3>
            <ol>
                <li>Review the Payment Entry details</li>
                <li>Verify allocations and accounts</li>
                <li>Submit manually if everything is correct</li>
            </ol>
            
            <p>
                <a href="/app/payment-entry/{pe_name}" 
                   style="background-color: #2196f3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 10px 0;">
                    Open Payment Entry
                </a>
                
                <a href="/app/cashfree-webhook-log/{webhook_log_name}" 
                   style="background-color: #607d8b; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block; margin: 10px 0;">
                    View Webhook Log
                </a>
            </p>
            
            <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
            <p style="color: #666; font-size: 12px;">
                Automated notification from Cashfree Webhook Handler<br>
                Timestamp: {frappe.utils.now()}
            </p>
        </div>
        """
        
        # Get admin emails
        admin_emails = frappe.get_all("User", 
            filters={"role_profile_name": ["in", ["System Manager", "Accounts Manager"]]},
            fields=["email"]
        )
        
        recipients = [u.email for u in admin_emails if u.email]
        
        if recipients:
            frappe.sendmail(
                recipients=recipients,
                subject=f"⚠️ Draft PE Review Required: {pe_name}",
                message=message,
                reference_doctype="Payment Entry",
                reference_name=pe_name,
                now=False  # Queue for sending
            )
            
            frappe.logger().info(f"[Cashfree Webhook] Draft review email sent to {len(recipients)} admins")
        
        # Also send real-time notification
        for email in recipients:
            frappe.publish_realtime(
                event='msgprint',
                message=f'⚠️ Draft PE {pe_name} requires review - Check email for details',
                user=email
            )
        
    except Exception as e:
        frappe.logger().error(f"Failed to notify admin for draft review: {str(e)}")


def notify_admin_validation_failure(transfer_id, validation_results, webhook_log_name):
    """Alert admin when PE cannot be created at all"""
    try:
        failed_checks = validation_results.get("failed", [])
        
        message = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px;">
            <h2 style="color: #d32f2f;">❌ Webhook Processing Failed</h2>
            
            <p>Critical validation failure - <strong>Payment Entry NOT created</strong>:</p>
            
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr style="background-color: #f5f5f5;">
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>Transfer ID</strong></td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{transfer_id}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>Webhook Log</strong></td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{webhook_log_name}</td>
                </tr>
            </table>
            
            <h3>Failed Validations:</h3>
            <ul style="color: #d32f2f;">
                {"".join([f"<li><strong>{item['check']}:</strong> {item['reason']}</li>" for item in failed_checks])}
            </ul>
            
            <p><strong>Raw webhook data preserved</strong> in Cashfree Webhook Log for manual processing.</p>
            
            <p>
                <a href="/app/cashfree-webhook-log/{webhook_log_name}" 
                   style="background-color: #d32f2f; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
                    View Webhook Log
                </a>
            </p>
        </div>
        """
        
        admin_emails = frappe.get_all("User", 
            filters={"role_profile_name": "System Manager"},
            fields=["email"]
        )
        
        recipients = [u.email for u in admin_emails if u.email]
        
        if recipients:
            frappe.sendmail(
                recipients=recipients,
                subject=f"❌ Webhook Failed: {transfer_id}",
                message=message,
                now=False
            )
        
    except Exception as e:
        frappe.logger().error(f"Failed to notify admin of validation failure: {str(e)}")


def notify_admin_critical_failure(transfer_id, error_trace, webhook_log_name=None):
    """Alert for critical system errors"""
    try:
        message = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px;">
            <h2 style="color: #d32f2f;">🚨 CRITICAL: Webhook System Error</h2>
            
            <p><strong>Unexpected error during webhook processing</strong></p>
            
            <table style="width: 100%; border-collapse: collapse; margin: 20px 0;">
                <tr style="background-color: #f5f5f5;">
                    <td style="padding: 10px; border: 1px solid #ddd;"><strong>Transfer ID</strong></td>
                    <td style="padding: 10px; border: 1px solid #ddd;">{transfer_id}</td>
                </tr>
                {"<tr><td style='padding: 10px; border: 1px solid #ddd;'><strong>Webhook Log</strong></td><td style='padding: 10px; border: 1px solid #ddd;'>" + webhook_log_name + "</td></tr>" if webhook_log_name else ""}
            </table>
            
            <h3>Error Details:</h3>
            <pre style="background-color: #f5f5f5; padding: 15px; overflow-x: auto; font-size: 12px;">{error_trace}</pre>
            
            <p><strong style="color: #4caf50;">✅ RAW DATA PRESERVED</strong> - Check Cashfree Webhook Log for recovery</p>
            
            {"<p><a href='/app/cashfree-webhook-log/" + webhook_log_name + "' style='background-color: #d32f2f; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;'>View Webhook Log</a></p>" if webhook_log_name else ""}
        </div>
        """
        
        admin_emails = frappe.get_all("User", 
            filters={"role_profile_name": "System Manager"},
            fields=["email"]
        )
        
        recipients = [u.email for u in admin_emails if u.email]
        
        if recipients:
            frappe.sendmail(
                recipients=recipients,
                subject=f"🚨 CRITICAL: Webhook Error - {transfer_id}",
                message=message,
                now=True  # Send immediately
            )
        
    except Exception as e:
        frappe.logger().error(f"Failed to notify admin of critical failure: {str(e)}")


def update_payment_request_status(transfer_id, event_type, data):
    """Update PR status for failed/reversed transfers"""
    try:
        status_mapping = {
            "TRANSFER_FAILED": "Failed",
            "TRANSFER_REVERSED": "Reversed"
        }
        
        new_status = status_mapping.get(event_type, "Pending")
        failure_reason = data.get("reason") or data.get("failure_reason") or ""
        
        frappe.db.sql("""
            UPDATE `tabPayment Request`
            SET custom_reconciliation_status = %s, custom_failure_reason = %s
            WHERE name = %s OR custom_cashfree_payout_id = %s
        """, (new_status, failure_reason, transfer_id, transfer_id))
        
        frappe.db.commit()
        frappe.logger().info(f"[Cashfree Webhook] PR status updated: {transfer_id} → {new_status}")
        
    except Exception as e:
        frappe.logger().error(f"Failed to update PR status: {str(e)}")


def get_cashfree_bank_account(company):
    """Get Cashfree bank account for company"""
    company_abbr = frappe.get_cached_value("Company", company, "abbr")
    account_name = f"Cashfree - {company_abbr}"
    
    account = frappe.db.get_value("Account", account_name, "name")
    if account:
        return account
    
    # Fallback
    account = frappe.db.get_value("Account", {
        "account_name": "Cashfree",
        "company": company,
        "account_type": "Bank",
        "is_group": 0
    }, "name")
    
    if account:
        return account
    
    # Last resort
    accounts = frappe.db.sql("""
        SELECT name FROM `tabAccount`
        WHERE company = %s AND account_type = 'Bank' AND is_group = 0
        AND account_name LIKE %s LIMIT 1
    """, (company, "%Cashfree%"), as_dict=True)
    
    if accounts:
        return accounts[0].name
    
    frappe.logger().error(f"Cashfree bank account not found for company: {company}")
    return None


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
