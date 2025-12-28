# bulk_actions.py
import frappe
from frappe import _
from frappe.utils.password import check_password


def verify_password(password):
    """Verify user password for 2FA"""
    try:
        check_password(frappe.session.user, password)
        return True
    except Exception:
        return False


def check_user_role(required_roles):
    """
    Check if user has at least one of the required roles
    Args:
        required_roles: list of role names (e.g., ['Manager', 'Director'])
    Returns:
        True if user has at least one role, False otherwise
    """
    user_roles = frappe.get_roles(frappe.session.user)
    
    for role in required_roles:
        if role in user_roles:
            return True
    
    return False


@frappe.whitelist()
def bulk_verify_requests(payment_requests):
    """
    Bulk verify payment requests
    Changes workflow state from Draft → Verified
    ONLY Accounts Manager can execute
    """
    # ROLE CHECK
    if not check_user_role(['Accounts Manager']):
        frappe.throw(_("Access Denied: Only Accounts Manager can verify payment requests"))
    
    import json
    if isinstance(payment_requests, str):
        payment_requests = json.loads(payment_requests)
    
    results = {"success": [], "failed": [], "skipped": []}
    
    for pr_name in payment_requests:
        try:
            pr = frappe.get_doc("Payment Request", pr_name)
            
            # Check if in Draft state
            if pr.workflow_state != "Draft":
                results["skipped"].append({
                    "name": pr_name,
                    "reason": f"Not in Draft state (current: {pr.workflow_state})"
                })
                continue
            
            # Apply workflow action
            pr.workflow_state = "Verified"
            pr.save(ignore_permissions=True)
            frappe.db.commit()
            
            results["success"].append(pr_name)
            
        except Exception as e:
            results["failed"].append({
                "name": pr_name,
                "error": str(e)
            })
            frappe.log_error(frappe.get_traceback(), f"Bulk Verify Failed - {pr_name}")
    
    summary = f"✅ Verified: {len(results['success'])} | ❌ Failed: {len(results['failed'])} | ⚠️ Skipped: {len(results['skipped'])}"
    return summary


@frappe.whitelist()
def bulk_approve_payments(payment_requests):
    """
    Bulk approve payment requests
    Changes workflow state from Verified → Approved
    ONLY Accounts Manager can execute
    """
    # ROLE CHECK
    if not check_user_role(['Accounts Manager']):
        frappe.throw(_("Access Denied: Only Accounts Manager can approve payment requests"))
    
    import json
    if isinstance(payment_requests, str):
        payment_requests = json.loads(payment_requests)
    
    results = {"success": [], "failed": [], "skipped": []}
    
    for pr_name in payment_requests:
        try:
            pr = frappe.get_doc("Payment Request", pr_name)
            
            # Check if in Verified state
            if pr.workflow_state != "Verified":
                results["skipped"].append({
                    "name": pr_name,
                    "reason": f"Not in Verified state (current: {pr.workflow_state})"
                })
                continue
            
            # Apply workflow action
            pr.workflow_state = "Approved"
            pr.save(ignore_permissions=True)
            frappe.db.commit()
            
            results["success"].append(pr_name)
            
        except Exception as e:
            results["failed"].append({
                "name": pr_name,
                "error": str(e)
            })
            frappe.log_error(frappe.get_traceback(), f"Bulk Approve Failed - {pr_name}")
    
    summary = f"✅ Approved: {len(results['success'])} | ❌ Failed: {len(results['failed'])} | ⚠️ Skipped: {len(results['skipped'])}"
    return summary


@frappe.whitelist()
def bulk_queue_payouts(payment_requests, password, transfer_mode):
    """
    Bulk queue payouts with 2FA verification
    Changes workflow state from Approved → Queued (triggers payout)
    ONLY Manager/Director can execute
    """
    # ROLE CHECK
    if not check_user_role(['Manager', 'Director']):
        frappe.throw(_("Access Denied: Only Manager or Director can queue payouts"))
    
    import json
    if isinstance(payment_requests, str):
        payment_requests = json.loads(payment_requests)
    
    # 2FA Verification
    if not verify_password(password):
        frappe.throw(_("Invalid password. Bulk payout requires authentication."))
    
    # Validate transfer mode
    valid_modes = ["NEFT", "RTGS", "IMPS", "UPI"]
    if transfer_mode not in valid_modes:
        frappe.throw(_(f"Invalid transfer mode. Must be one of: {', '.join(valid_modes)}"))
    
    results = {"success": [], "failed": [], "skipped": []}
    
    for pr_name in payment_requests:
        try:
            pr = frappe.get_doc("Payment Request", pr_name)
            
            # Check if in Approved state
            if pr.workflow_state != "Approved":
                results["skipped"].append({
                    "name": pr_name,
                    "reason": f"Not in Approved state (current: {pr.workflow_state})"
                })
                continue
            
            # Check if payout already exists (unless failed)
            existing_payout = pr.get("custom_cashfree_payout_id")
            recon_status = (pr.get("custom_reconciliation_status") or "").upper()
            
            if existing_payout and recon_status not in ["FAILED", "REVERSED", "REJECTED"]:
                results["skipped"].append({
                    "name": pr_name,
                    "reason": f"Payout already exists (ID: {existing_payout}, Status: {recon_status})"
                })
                continue
            
            # Set transfer mode
            pr.custom_transfer_mode = transfer_mode
            
            # Apply workflow action (triggers payout via hook)
            pr.workflow_state = "Queued"
            pr.save(ignore_permissions=True)
            frappe.db.commit()
            
            results["success"].append(pr_name)
            
        except Exception as e:
            results["failed"].append({
                "name": pr_name,
                "error": str(e)
            })
            frappe.log_error(frappe.get_traceback(), f"Bulk Queue Failed - {pr_name}")
    
    # Return detailed results for frontend
    return results


@frappe.whitelist()
def bulk_retry_payouts(payment_requests, password):
    """
    Bulk retry failed payouts with 2FA verification
    For Payment Requests in Failed/Reversed/Rejected reconciliation status
    ONLY Manager/Director can execute
    """
    # ROLE CHECK
    if not check_user_role(['Manager', 'Director']):
        frappe.throw(_("Access Denied: Only Manager or Director can retry payouts"))
    
    import json
    if isinstance(payment_requests, str):
        payment_requests = json.loads(payment_requests)
    
    # 2FA Verification
    if not verify_password(password):
        frappe.throw(_("Invalid password. Bulk retry requires authentication."))
    
    results = {"success": [], "failed": [], "skipped": []}
    
    for pr_name in payment_requests:
        try:
            pr = frappe.get_doc("Payment Request", pr_name)
            
            # Check if payout failed
            recon_status = (pr.get("custom_reconciliation_status") or "").upper()
            
            if recon_status not in ["FAILED", "REVERSED", "REJECTED"]:
                results["skipped"].append({
                    "name": pr_name,
                    "reason": f"Not in failed status (current: {recon_status})"
                })
                continue
            
            # Clear old payout data
            frappe.db.set_value("Payment Request", pr_name, "custom_cashfree_payout_id", None, update_modified=False)
            frappe.db.set_value("Payment Request", pr_name, "custom_utr_number", None, update_modified=False)
            frappe.db.set_value("Payment Request", pr_name, "custom_reconciliation_status", "Pending", update_modified=False)
            
            # Reset to Approved state
            pr.workflow_state = "Approved"
            pr.save(ignore_permissions=True)
            
            # Queue again
            pr.workflow_state = "Queued"
            pr.save(ignore_permissions=True)
            frappe.db.commit()
            
            results["success"].append(pr_name)
            
            frappe.log_error(
                frappe.as_json({
                    "pr": pr_name,
                    "action": "Bulk retry",
                    "user": frappe.session.user,
                    "old_status": recon_status
                }),
                f"Bulk Retry - {pr_name}"
            )
            
        except Exception as e:
            results["failed"].append({
                "name": pr_name,
                "error": str(e)
            })
            frappe.log_error(frappe.get_traceback(), f"Bulk Retry Failed - {pr_name}")
    
    # Return detailed results
    return results


@frappe.whitelist()
def bulk_reject_requests(payment_requests, reason):
    """
    Bulk reject payment requests
    ONLY Manager/Director can execute
    """
    # ROLE CHECK
    if not check_user_role(['Manager', 'Director']):
        frappe.throw(_("Access Denied: Only Manager or Director can reject payment requests"))
    
    import json
    if isinstance(payment_requests, str):
        payment_requests = json.loads(payment_requests)
    
    results = {"success": [], "failed": [], "skipped": []}
    
    for pr_name in payment_requests:
        try:
            pr = frappe.get_doc("Payment Request", pr_name)
            
            # Cannot reject already paid or rejected
            if pr.workflow_state in ["Paid", "Rejected"]:
                results["skipped"].append({
                    "name": pr_name,
                    "reason": f"Cannot reject (already {pr.workflow_state})"
                })
                continue
            
            # Apply rejection
            pr.workflow_state = "Rejected"
            pr.add_comment("Comment", text=f"Rejected by {frappe.session.user}: {reason}")
            pr.save(ignore_permissions=True)
            frappe.db.commit()
            
            results["success"].append(pr_name)
            
        except Exception as e:
            results["failed"].append({
                "name": pr_name,
                "error": str(e)
            })
            frappe.log_error(frappe.get_traceback(), f"Bulk Reject Failed - {pr_name}")
    
    summary = f"✅ Rejected: {len(results['success'])} | ❌ Failed: {len(results['failed'])} | ⚠️ Skipped: {len(results['skipped'])}"
    return summary
