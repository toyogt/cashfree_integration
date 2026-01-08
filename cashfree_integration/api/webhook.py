# File: cashfree_integration/api/webhook.py
# Production-Ready Cashfree Payout Webhook Handler (Final Version)

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
    
    Supports both:
    - V1 webhooks (form-encoded)
    - V2 webhooks (JSON)
    
    Flow:
    1. LOG RAW DATA (ALWAYS) - Insurance against data loss
    2. Signature Validation - Security
    3. Create PE in DRAFT - Smart recovery strategy
    4. Run Business Validations - Data integrity
    5. Auto-submit if all pass, else keep draft for manual review
    
    Monitoring: Check "Cashfree Webhook Log" list daily for failures
    """
    start_time = time.time()
    webhook_log = None
    transfer_id = None
    
    try:
        # ============================================
        # STEP 1: CAPTURE RAW DATA (ALWAYS)
        # ============================================
        raw_body = frappe.request.get_data(as_text=True)
        headers = {k: v for k, v in dict(frappe.request.headers).items()}
        
        # Parse incoming body safely (JSON or form-encoded)
        if raw_body:
            try:
                data = json.loads(raw_body)
            except Exception:
                # Fallback to form-encoded (V1 webhooks)
                data = dict(frappe.local.form_dict)
        else:
            data = dict(frappe.local.form_dict)
        
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
        # STEP 2: SIGNATURE VALIDATION (PRODUCTION SECURE)
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
            # PRODUCTION: Always require signature
            update_webhook_log(webhook_log, {
                "status": "Signature Failed",
                "error_log": "No signature provided"
            })
            frappe.log_error("Webhook missing signature", "Cashfree Webhook Security")
            return {"status": "error", "message": "Missing signature"}, 401
        
        update_webhook_log(webhook_log, {"status": "Signature Verified"})
        
        # ============================================
        # STEP 3: IGNORE NON-SUCCESS EVENTS
        # ============================================
        if event_type.upper() != "TRANSFER_SUCCESS":
            update_webhook_log(webhook_log, {
                "status": "Ignored",
                "error_log": f"Event type {event_type} not processed"
            })
            
            # Still update PR status for failed transfers
            if transfer_id and event_type.upper() in ["TRANSFER_FAILED", "TRANSFER_REVERSED"]:
                update_payment_request_status(transfer_id, event_type, data)
            
            return {"status": "ignored", "message": f"Event {event_type} not processed"}, 200
        
        # ============================================
        # STEP 4: EXTRACT PAYMENT DATA
        # ============================================
        # Handle both form-encoded (V1) and JSON (V2) webhooks
        if "data" in data and isinstance(data["data"], dict):
            # V2 JSON format: nested structure
            transfer_data = data["data"].get("transfer") or data["data"]
        else:
            # V1 form-encoded: flat structure
            transfer_data = data
        
        payment_data = {
            "transfer_id": transfer_data.get("transferId") or transfer_data.get("transfer_id") or transfer_id,
            "utr": transfer_data.get("utr"),
            "amount": float(transfer_data.get("amount", 0)) if transfer_data.get("amount") else 0,
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
        
        # Safe error title
        try:
            error_title = f"Cashfree Webhook Critical Error - {transfer_id}"
        except:
            error_title = "Cashfree Webhook Critical Error - Unknown Transfer"
        
        frappe.log_error(title=error_title, message=error_trace)
        
        if webhook_log:
            update_webhook_log(webhook_log, {
                "status": "Error",
                "error_log": error_trace
            })
        
        return {"status": "error", "message": "Internal server error"}, 500


def create_webhook_log(transfer_id, webhook_event, raw_payload, signature, headers, status):
    """
    Create webhook log - ALWAYS succeeds
    Implements idempotency check
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
    All results logged to webhook_log for monitoring
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
                
                frappe.logger().error(f"[Cashfree Webhook] Payment Request not found: {transfer_id}")
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
            
            # CRITICAL: Stop here - can't create PE without account
            update_webhook_log(webhook_log, {
                "status": "Validation Failed",
                "validation_results": json.dumps(validation_results, indent=2)
            })
            
            frappe.log_error(
                f"Cashfree bank account missing for company: {pr_name.company}\n"
                f"Create account: Chart of Accounts > Add 'Cashfree - {frappe.get_cached_value('Company', pr_name.company, 'abbr')}'",
                "Cashfree Webhook - Missing Account"
            )
            
            return {
                "status": "error",
                "message": "Cashfree account not configured",
                "validation_failures": validation_results["failed"]
            }, 500
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
        # VALIDATION 5: Check Mode of Payment
        # ============================================
        if not frappe.db.exists("Mode of Payment", "Cashfree"):
            try:
                mop = frappe.get_doc({
                    "doctype": "Mode of Payment",
                    "mode_of_payment": "Cashfree",
                    "enabled": 1,
                    "type": "Bank"
                })
                mop.insert(ignore_permissions=True)
                frappe.db.commit()
                frappe.logger().info("[Cashfree Webhook] Auto-created Mode of Payment: Cashfree")
                validation_results["warnings"].append("Auto-created Mode of Payment: Cashfree")
            except Exception as mop_error:
                frappe.logger().error(f"Failed to create Mode of Payment: {str(mop_error)}")
                validation_results["warnings"].append(f"Mode of Payment creation failed: {str(mop_error)}")
        
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
        # VALIDATION 6: Smart Allocation
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
                validation_results["warnings"].append(f"Reference allocation failed: {str(ref_error)}")
                allocation_note = f"\n⚠️ Allocation skipped: {str(ref_error)}"
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
                
                return {
                    "status": "success",
                    "payment_entry": pe.name,
                    "validation": "All checks passed - PE submitted",
                    "warnings": validation_results["warnings"]
                }, 200
                
            except Exception as submit_error:
                # Submit failed - keep as draft
                validation_results["failed"].append({
                    "check": "PE Submission",
                    "reason": str(submit_error)
                })
                
                update_webhook_log(webhook_log, {
                    "status": "Validation Failed",
                    "validation_results": json.dumps(validation_results, indent=2),
                    "error_log": frappe.get_traceback()
                })
                
                frappe.logger().error(f"[Cashfree Webhook] PE submission failed: {pe.name} - {str(submit_error)}")
                
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
            
            frappe.logger().warning(f"[Cashfree Webhook] PE kept in draft: {pe.name} - Validation failures: {len(validation_results['failed'])}")
            
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
        
        frappe.logger().error(f"[Cashfree Webhook] Critical error for {transfer_id}: {str(e)}")
        
        return {"status": "error", "message": str(e)}, 500


def update_payment_request_status(transfer_id, event_type, data):
    """Update PR status for failed/reversed transfers"""
    try:
        status_mapping = {
            "TRANSFER_FAILED": "Failed",
            "TRANSFER_REVERSED": "Reversed"
        }
        
        new_status = status_mapping.get(event_type.upper(), "Pending")
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
    """
    V1: sort params, concat values, HMAC, base64
    Excludes Frappe-added params (cmd, doctype)
    """
    try:
        from frappe.utils.password import get_decrypted_password
        secret = get_decrypted_password("Cashfree Settings", "Cashfree Settings", "client_secret")
        
        if not secret:
            frappe.log_error(
                "Cashfree client_secret not configured in Cashfree Settings. "
                "Go to: Setup > Cashfree Settings > Client Secret",
                "Cashfree Webhook - Missing Secret"
            )
            return False

        # Exclude signature and Frappe-added params#
        stripped = {
            k: v for k, v in data.items()
            if k not in ("signature", "cmd", "doctype")
        }

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
            frappe.log_error(
                "Cashfree client_secret not configured in Cashfree Settings. "
                "Go to: Setup > Cashfree Settings > Client Secret",
                "Cashfree Webhook - Missing Secret"
            )
            return False

        timestamp = frappe.get_request_header("x-webhook-timestamp") or ""
        payload = timestamp + raw_body
        
        computed = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
        computed_signature = base64.b64encode(computed).decode()
        
        return hmac.compare_digest(computed_signature, received_signature)
        
    except Exception as e:
        frappe.log_error(str(e), "V2 signature verify error")
        return False
