import frappe
from cashfree_integration.api.payouts import (
    get_cashfree_settings,
    check_beneficiary_exists,
    create_beneficiary_v2
)


@frappe.whitelist()
def verify_bank_account_standalone(bank_account_name):
    """
    Standalone bank account verification - independent of payment flow
    Can be called from Bank Account form "Verify" button
    
    Args:
        bank_account_name: Name of Bank Account document
    
    Returns:
        dict: {success, message, beneficiary_id, verification_details}
    """
    try:
        frappe.logger().info(f"🔍 Starting verification for bank account: {bank_account_name}")
        
        # Get Bank Account
        bank = frappe.get_doc("Bank Account", bank_account_name)
        
        # Validate required fields
        if not bank.bank_account_no:
            return {
                "success": False,
                "message": "Bank Account Number is required",
                "error_type": "MISSING_FIELD"
            }
        
        if not bank.branch_code:
            return {
                "success": False,
                "message": "IFSC Code (Branch Code) is required",
                "error_type": "MISSING_FIELD"
            }
        
        # Get Cashfree settings
        try:
            settings, base_url, client_id, client_secret = get_cashfree_settings()
        except Exception as e:
            return {
                "success": False,
                "message": f"Cashfree settings not configured: {str(e)}",
                "error_type": "SETTINGS_ERROR"
            }
        
        # Check if beneficiary already exists
        existing_bene_id = bank.get("custom_cashfree_beneficiary_id")
        verification_status = bank.get("custom_bank_account_approval_status")
        
        if existing_bene_id and verification_status == "Approved":
            # Beneficiary exists - verify it's still active in Cashfree
            frappe.logger().info(f"✅ Found existing beneficiary: {existing_bene_id}")
            
            if check_beneficiary_exists(existing_bene_id, client_id, client_secret, base_url):
                # Beneficiary still exists in Cashfree
                return {
                    "success": True,
                    "message": "Bank account already verified",
                    "beneficiary_id": existing_bene_id,
                    "already_verified": True,
                    "verification_details": {
                        "status": "Approved",
                        "beneficiary_id": existing_bene_id,
                        "verified_date": bank.get("custom_verified_date"),
                        "verified_by": bank.get("custom_verified_by")
                    }
                }
            else:
                # Beneficiary was deleted from Cashfree - need to recreate
                frappe.logger().info(f"⚠️ Beneficiary {existing_bene_id} not found in Cashfree - recreating")
        
        # Create/verify beneficiary in Cashfree
        frappe.logger().info(f"🔄 Creating beneficiary for {bank_account_name}")
        
        try:
            bene_id = create_beneficiary_v2(bank, client_id, client_secret, base_url)
            
            # Success - beneficiary created and bank account updated
            return {
                "success": True,
                "message": "Bank account verified successfully",
                "beneficiary_id": bene_id,
                "already_verified": False,
                "verification_details": {
                    "status": "Approved",
                    "beneficiary_id": bene_id,
                    "account_number": bank.bank_account_no[-4:],
                    "ifsc": bank.branch_code,
                    "account_name": bank.account_name,
                    "verified_date": frappe.utils.now(),
                    "verified_by": frappe.session.user
                }
            }
            
        except Exception as beneficiary_error:
            # Beneficiary creation failed
            error_message = str(beneficiary_error)
            frappe.logger().error(f"❌ Beneficiary creation failed: {error_message}")
            
            return {
                "success": False,
                "message": error_message,
                "error_type": "BENEFICIARY_ERROR",
                "verification_details": {
                    "status": "Failed",
                    "error": error_message,
                    "bank_account": bank_account_name,
                    "account_number": bank.bank_account_no[-4:] if bank.bank_account_no else None,
                    "ifsc": bank.branch_code
                }
            }
    
    except Exception as e:
        # General error
        frappe.logger().error(f"❌ Verification error: {str(e)}\n{frappe.get_traceback()}")
        
        return {
            "success": False,
            "message": f"Verification failed: {str(e)}",
            "error_type": "GENERAL_ERROR"
        }


@frappe.whitelist()
def bulk_verify_bank_accounts(bank_account_names):
    """
    Verify multiple bank accounts in one call
    
    Args:
        bank_account_names: JSON string or list of bank account names
    
    Returns:
        dict: {total, success_count, failed_count, results[]}
    """
    import json
    
    # Parse input
    if isinstance(bank_account_names, str):
        try:
            bank_account_names = json.loads(bank_account_names)
        except:
            return {
                "success": False,
                "message": "Invalid input format. Expected JSON array of bank account names."
            }
    
    results = []
    success_count = 0
    failed_count = 0
    
    for bank_name in bank_account_names:
        try:
            result = verify_bank_account_standalone(bank_name)
            
            if result.get("success"):
                success_count += 1
            else:
                failed_count += 1
            
            results.append({
                "bank_account": bank_name,
                "result": result
            })
            
        except Exception as e:
            failed_count += 1
            results.append({
                "bank_account": bank_name,
                "result": {
                    "success": False,
                    "message": str(e),
                    "error_type": "EXCEPTION"
                }
            })
    
    return {
        "total": len(bank_account_names),
        "success_count": success_count,
        "failed_count": failed_count,
        "results": results
    }
