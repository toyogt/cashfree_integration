import frappe
from frappe import _


def validate_po_payment_limit(doc, method=None):
    """
    Validate that payment doesn't exceed Purchase Order total
    Unless director override is enabled
    
    Triggered on: Payment Request validation
    """
    
    # Only validate for Purchase Orders
    if doc.reference_doctype != "Purchase Order":
        return
    
    if not doc.reference_name:
        return
    
    # Skip if director override is enabled
    if doc.get("custom_director_override"):
        frappe.msgprint(
            f"⚠️ <b>Director Override Active</b><br><br>"
            f"Payment limit validation bypassed for this Payment Request.",
            alert=True,
            indicator='orange'
        )
        return
    
    try:
        # Get Purchase Order
        po = frappe.get_doc("Purchase Order", doc.reference_name)
        
        # Get all Payment Requests for this PO (including current one)
        existing_prs = frappe.get_all(
            "Payment Request",
            filters={
                "reference_doctype": "Purchase Order",
                "reference_name": doc.reference_name,
                "docstatus": ["!=", 2],  # Not cancelled
                "name": ["!=", doc.name]  # Exclude current PR
            },
            fields=["name", "grand_total", "custom_reconciliation_status", "docstatus"]
        )
        
        # Calculate total payments
        total_existing_payments = sum([pr.grand_total for pr in existing_prs])
        current_payment = doc.grand_total or 0
        total_payments = total_existing_payments + current_payment
        
        # Get PO total
        po_total = po.grand_total or 0
        
        # Check if exceeds
        if total_payments > po_total:
            excess_amount = total_payments - po_total
            
            error_html = f"""
            <div style="padding: 15px;">
                <h4 style="color: red;">❌ Payment Exceeds Purchase Order Total</h4>
                
                <table class="table table-bordered" style="margin-top: 15px;">
                    <tr>
                        <th style="width: 50%;">Purchase Order Total</th>
                        <td><b>₹{po_total:,.2f}</b></td>
                    </tr>
                    <tr>
                        <th>Existing Payments</th>
                        <td>₹{total_existing_payments:,.2f}</td>
                    </tr>
                    <tr>
                        <th>Current Payment</th>
                        <td>₹{current_payment:,.2f}</td>
                    </tr>
                    <tr style="background-color: #f8d7da;">
                        <th>Total Payments</th>
                        <td><b>₹{total_payments:,.2f}</b></td>
                    </tr>
                    <tr style="background-color: #f8d7da;">
                        <th>Excess Amount</th>
                        <td style="color: red;"><b>₹{excess_amount:,.2f}</b></td>
                    </tr>
                </table>
                
                <div style="margin-top: 20px; padding: 15px; background-color: #fff3cd; border-left: 4px solid #ffc107;">
                    <h5>Options to Proceed:</h5>
                    <ol>
                        <li><b>Reduce Payment Amount</b> to ₹{po_total - total_existing_payments:,.2f} or less</li>
                        <li><b>Enable Director Override</b> if this excess payment is authorized</li>
                        <li><b>Cancel/Modify</b> existing Payment Requests</li>
                    </ol>
                </div>
                
                <div style="margin-top: 15px; padding: 10px; background-color: #d1ecf1; border-left: 4px solid #0c5460;">
                    <b>ℹ️ Existing Payment Requests ({len(existing_prs)}):</b><br>
                    <ul style="margin-top: 10px;">
            """
            
            for pr in existing_prs:
                status_color = {
                    "Success": "green",
                    "Pending": "orange",
                    "Failed": "red"
                }.get(pr.custom_reconciliation_status, "gray")
                
                doc_status = {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(pr.docstatus, "Unknown")
                
                error_html += f"""
                        <li>
                            <b>{pr.name}</b>: ₹{pr.grand_total:,.2f} 
                            (<span style="color: {status_color};">{pr.custom_reconciliation_status or 'Pending'}</span>, {doc_status})
                        </li>
                """
            
            error_html += """
                    </ul>
                </div>
            </div>
            """
            
            frappe.throw(error_html, title=_("Payment Limit Exceeded"))
    
    except frappe.DoesNotExistError:
        frappe.throw(f"Purchase Order {doc.reference_name} not found")
    except Exception as e:
        frappe.log_error(f"Error in PO payment validation: {str(e)}")
        # Don't block submission if validation fails due to error
        pass


def validate_reference_document(doc, method=None):
    """
    Validate that reference document exists and is valid
    
    Triggered on: Payment Request validation
    """
    
    if not doc.reference_doctype or not doc.reference_name:
        return
    
    # Check if reference document exists
    if not frappe.db.exists(doc.reference_doctype, doc.reference_name):
        frappe.throw(
            f"❌ <b>Invalid Reference Document</b><br><br>"
            f"{doc.reference_doctype} <b>{doc.reference_name}</b> does not exist.<br><br>"
            f"Please select a valid {doc.reference_doctype}.",
            title=_("Invalid Reference")
        )
    
    # Additional validation for Purchase Orders
    if doc.reference_doctype == "Purchase Order":
        po = frappe.get_doc("Purchase Order", doc.reference_name)
        
        # Check if PO is submitted
        if po.docstatus != 1:
            frappe.throw(
                f"❌ <b>Purchase Order Not Submitted</b><br><br>"
                f"Purchase Order <b>{doc.reference_name}</b> must be submitted before creating a Payment Request.<br><br>"
                f"Current Status: <b>{['Draft', 'Submitted', 'Cancelled'][po.docstatus]}</b>",
                title=_("Invalid PO Status")
            )
        
        # Check if PO is cancelled
        if po.docstatus == 2:
            frappe.throw(
                f"❌ <b>Purchase Order Cancelled</b><br><br>"
                f"Purchase Order <b>{doc.reference_name}</b> is cancelled.<br><br>"
                f"Cannot create Payment Request for cancelled document.",
                title=_("Cancelled PO")
            )
        
        # Check if supplier matches
        if po.supplier != doc.party:
            frappe.throw(
                f"❌ <b>Supplier Mismatch</b><br><br>"
                f"Purchase Order supplier (<b>{po.supplier}</b>) does not match Payment Request party (<b>{doc.party}</b>).<br><br>"
                f"Please select the correct Purchase Order or Supplier.",
                title=_("Supplier Mismatch")
            )


def validate_bank_account_required(doc, method=None):
    """
    Validate that bank account is selected for outward payments
    
    Triggered on: Payment Request validation
    """
    
    # Only for outward payments
    if doc.payment_request_type != "Outward":
        return
    
    # Check if bank account is selected
    if not doc.bank_account:
        frappe.throw(
            f"❌ <b>Bank Account Required</b><br><br>"
            f"Bank Account is mandatory for Outward Payment Requests.<br><br>"
            f"Please select a verified bank account for the supplier <b>{doc.party}</b>.",
            title=_("Bank Account Missing")
        )
    
    # Check if bank account is verified
    bank = frappe.get_doc("Bank Account", doc.bank_account)
    
    verification_status = bank.get("custom_bank_account_approval_status")
    is_verified = bank.get("custom_bank_account_verified")
    
    if verification_status != "Approved" or not is_verified:
        frappe.msgprint(
            f"⚠️ <b>Bank Account Not Verified</b><br><br>"
            f"Bank Account <b>{doc.bank_account}</b> is not verified with Cashfree.<br><br>"
            f"Current Status: <b>{verification_status or 'Draft'}</b><br><br>"
            f"<b>Action Required:</b><br>"
            f"1. Open Bank Account: <a href='/app/bank-account/{doc.bank_account}'>{doc.bank_account}</a><br>"
            f"2. Click <b>Cashfree → Verify Bank Account</b><br>"
            f"3. Complete verification before processing payment",
            alert=True,
            indicator='orange',
            title='Verification Warning'
        )


@frappe.whitelist()
def check_po_payment_status(purchase_order):
    """
    Check payment status for a Purchase Order
    Returns total paid, remaining amount, and list of payment requests
    
    Args:
        purchase_order: Name of Purchase Order
    
    Returns:
        dict: Payment status details
    """
    
    try:
        po = frappe.get_doc("Purchase Order", purchase_order)
        
        # Get all Payment Requests
        payment_requests = frappe.get_all(
            "Payment Request",
            filters={
                "reference_doctype": "Purchase Order",
                "reference_name": purchase_order,
                "docstatus": ["!=", 2]
            },
            fields=[
                "name",
                "grand_total",
                "custom_reconciliation_status",
                "custom_cashfree_payout_id",
                "docstatus",
                "creation",
                "workflow_state"
            ],
            order_by="creation desc"
        )
        
        # Calculate totals
        total_paid = sum([pr.grand_total for pr in payment_requests if pr.custom_reconciliation_status == "Success"])
        total_pending = sum([pr.grand_total for pr in payment_requests if pr.custom_reconciliation_status in ["Pending", "Queued"]])
        total_failed = sum([pr.grand_total for pr in payment_requests if pr.custom_reconciliation_status == "Failed"])
        
        po_total = po.grand_total or 0
        remaining = po_total - total_paid - total_pending
        
        return {
            "success": True,
            "po_name": purchase_order,
            "po_total": po_total,
            "total_paid": total_paid,
            "total_pending": total_pending,
            "total_failed": total_failed,
            "remaining": remaining,
            "payment_requests": payment_requests,
            "payment_count": len(payment_requests)
        }
        
    except Exception as e:
        return {
            "success": False,
            "message": str(e)
        }
