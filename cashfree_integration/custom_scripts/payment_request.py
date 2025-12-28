import frappe
from frappe import _

def validate_payment_request(doc, method):
    """Main validation hook - runs BEFORE ERPNext validation"""
    
    # Only validate for Outward payments
    if doc.payment_request_type != "Outward":
        return
    
    # Validate PO payment limit (with Director Override logic)
    if doc.reference_doctype == "Purchase Order" and doc.reference_name:
        validate_po_payment_limit(doc)


def validate_po_payment_limit(doc):
    """
    Validate that payment doesn't exceed Purchase Order limit
    Unless Director Override is enabled
    """
    
    # Get Purchase Order
    po = frappe.get_doc("Purchase Order", doc.reference_name)
    po_total = po.grand_total
    
    # Get already paid amount for this PO
    already_paid = frappe.db.sql("""
        SELECT IFNULL(SUM(grand_total), 0) as total
        FROM `tabPayment Request`
        WHERE reference_doctype = 'Purchase Order'
        AND reference_name = %s
        AND docstatus = 1
        AND name != %s
    """, (doc.reference_name, doc.name or ""))[0][0]
    
    # Calculate available balance
    available_balance = po_total - already_paid
    
    # Check if payment exceeds available balance
    if doc.grand_total > available_balance:
        
        # If Director Override is enabled, allow it
        if doc.custom_director_override:
            frappe.msgprint(
                msg=_(
                    "<b>⚠️ Director Override Enabled</b><br><br>"
                    f"Payment Amount: ₹{doc.grand_total:,.2f}<br>"
                    f"PO Available Balance: ₹{available_balance:,.2f}<br>"
                    f"<b>Overpayment: ₹{doc.grand_total - available_balance:,.2f}</b><br><br>"
                    f"This payment exceeds the Purchase Order limit but is allowed due to Director Override."
                ),
                title=_("Overpayment Approved"),
                indicator="orange",
                alert=True
            )
            
            # ✨ CRITICAL: Skip ERPNext's validation by setting flag
            doc.flags.skip_payment_request_amount_validation = True
            return  # Allow the transaction
        
        # No Director Override - Block the payment with detailed error
        frappe.throw(
            _(
                "<b>Payment amount exceeds Purchase Order total.</b><br><br>"
                f"<b>Purchase Order:</b> {doc.reference_name}<br>"
                f"<b>PO Total:</b> ₹{po_total:,.2f}<br>"
                f"<b>Already Paid:</b> ₹{already_paid:,.2f}<br>"
                f"<b>Available Balance:</b> ₹{available_balance:,.2f}<br>"
                f"<b>Requested Amount:</b> ₹{doc.grand_total:,.2f}<br>"
                f"<b>Excess Amount:</b> ₹{doc.grand_total - available_balance:,.2f}<br><br>"
                f"<i>Enable 'Director Override' checkbox to proceed with over-payment.</i>"
            ),
            title=_("Payment Limit Exceeded")
        )


# Monkey patch ERPNext's validation to respect our flag
def override_erpnext_validation():
    """Override ERPNext's payment request amount validation"""
    from erpnext.accounts.doctype.payment_request.payment_request import PaymentRequest
    
    original_validate = PaymentRequest.validate_payment_request_amount
    
    def custom_validate_payment_request_amount(self):
        # Skip validation if Director Override flag is set
        if self.flags.get("skip_payment_request_amount_validation"):
            return
        
        # Otherwise run original validation
        return original_validate(self)
    
    PaymentRequest.validate_payment_request_amount = custom_validate_payment_request_amount

# Apply monkey patch on module load
override_erpnext_validation()
