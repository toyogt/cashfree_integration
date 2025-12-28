"""
Retry Failed Payment Entry Creation

This script retries creating Payment Entries for Payment Requests that:
- Have status = Success (webhook received)
- Do not have a Payment Entry created
- Have a UTR number

Usage:
    bench console
    >>> from cashfree_integration.utils.retry_pe import retry_failed_pe_creation
    >>> retry_failed_pe_creation()
"""

import frappe
from cashfree_integration.api.webhooks import create_payment_entry_from_webhook


def retry_failed_pe_creation(limit=50):
    """
    Retry Payment Entry creation for successful payouts that failed PE creation
    
    Args:
        limit: Maximum number of PRs to retry (default: 50)
    
    Returns:
        dict: Summary of results
    """
    frappe.set_user("Administrator")
    
    print("\n" + "="*60)
    print("🔄 RETRYING FAILED PE CREATION")
    print("="*60)
    
    # Find PRs with Success status but no PE
    failed_prs = frappe.db.sql("""
        SELECT 
            pr.name,
            pr.custom_utr_number,
            pr.grand_total,
            pr.party
        FROM `tabPayment Request` pr
        LEFT JOIN `tabPayment Entry` pe 
            ON pe.payment_request = pr.name 
            AND pe.docstatus != 2
        WHERE pr.custom_reconciliation_status = 'Success'
        AND pr.mode_of_payment = 'Cashfree'
        AND pe.name IS NULL
        AND pr.custom_utr_number IS NOT NULL
        ORDER BY pr.modified DESC
        LIMIT %s
    """, (limit,), as_dict=True)
    
    if not failed_prs:
        print("✅ No failed PRs found - all good!")
        return {"total": 0, "success": 0, "failed": 0, "results": []}
    
    print(f"\n📋 Found {len(failed_prs)} PRs to retry\n")
    
    results = {
        "total": len(failed_prs),
        "success": 0,
        "failed": 0,
        "results": []
    }
    
    for idx, pr in enumerate(failed_prs, 1):
        print(f"[{idx}/{len(failed_prs)}] Processing {pr.name}...")
        
        try:
            # Retry PE creation
            pe_name = create_payment_entry_from_webhook(
                pr.name,
                pr.custom_utr_number,
                {}
            )
            
            results["success"] += 1
            results["results"].append({
                "pr": pr.name,
                "party": pr.party,
                "amount": pr.grand_total,
                "status": "✅ Success",
                "pe": pe_name,
                "error": None
            })
            
            print(f"   ✅ PE Created: {pe_name}")
            
        except Exception as e:
            results["failed"] += 1
            error_msg = str(e)
            
            results["results"].append({
                "pr": pr.name,
                "party": pr.party,
                "amount": pr.grand_total,
                "status": "❌ Failed",
                "pe": None,
                "error": error_msg
            })
            
            print(f"   ❌ Error: {error_msg}")
    
    # Commit all changes
    frappe.db.commit()
    
    # Print summary
    print("\n" + "="*60)
    print("📊 RETRY SUMMARY")
    print("="*60)
    print(f"Total PRs: {results['total']}")
    print(f"✅ Success: {results['success']}")
    print(f"❌ Failed: {results['failed']}")
    print("="*60 + "\n")
    
    return results
