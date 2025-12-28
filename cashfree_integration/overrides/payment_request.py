# File: overrides/payment_request.py
# Monkey patch Payment Request to support Director Override

import frappe
from frappe import _
from erpnext.accounts.doctype.payment_request.payment_request import PaymentRequest


# Store original validate method
_original_validate = PaymentRequest.validate


def patched_validate(self):
    """
    Patched validate method that handles Director Override
    """
    
    # Check if this is a PO with director override BEFORE calling original validate
    bypass_validation = False
    original_grand_total = None
    
    if self.reference_doctype == "Purchase Order" and self.reference_name:
        director_override = self.get("custom_director_override")
        
        if director_override and director_override == 1:
            try:
                po = frappe.get_doc("Purchase Order", self.reference_name)
                po_amount = float(po.grand_total or 0)
                payment_amount = float(self.grand_total or 0)
                
                if payment_amount > po_amount:
                    over_amount = payment_amount - po_amount
                    
                    # Store original and temporarily reduce
                    original_grand_total = payment_amount
                    self.grand_total = po_amount
                    bypass_validation = True
                    
                    # Show warning
                    frappe.msgprint(
                        _(f"⚠️ <b>Director Override Active</b><br><br>"
                          f"Over-PO payment approved.<br>"
                          f"PO: ₹{po_amount:,.2f} | Payment: ₹{payment_amount:,.2f}<br>"
                          f"Over: ₹{over_amount:,.2f}"),
                        alert=True,
                        indicator="orange",
                        title=_("Director Override")
                    )
                    
                    # Log for audit
                    frappe.log_error(
                        frappe.as_json({
                            "pr": self.name or "New",
                            "po": self.reference_name,
                            "po_amount": po_amount,
                            "payment_amount": payment_amount,
                            "override_by": frappe.session.user,
                            "action": "Director Override - Bypassed PO limit"
                        }),
                        f"Director Override - {self.name or 'New'}"
                    )
            
            except Exception as e:
                frappe.log_error(str(e), "Director Override Patch Error")
    
    # Call original validate
    _original_validate(self)
    
    # Restore original amount if bypass was used
    if bypass_validation and original_grand_total:
        self.grand_total = original_grand_total


def validate_director_override(doc, method=None):
    """
    Pre-validation check for director override requirement
    """
    
    # Only for Purchase Orders
    if doc.reference_doctype != "Purchase Order" or not doc.reference_name:
        return
    
    try:
        po = frappe.get_doc("Purchase Order", doc.reference_name)
        po_amount = float(po.grand_total or 0)
        payment_amount = float(doc.grand_total or 0)
        
        # Check if over-PO payment
        if payment_amount > po_amount:
            director_override = doc.get("custom_director_override")
            over_amount = payment_amount - po_amount
            
            if not director_override or director_override == 0:
                # BLOCK - No override
                frappe.throw(
                    _(f"<b>Over-PO Payment Blocked</b><br><br>"
                      f"<div style='background: #fff3cd; padding: 15px; border-left: 4px solid #ff9800;'>"
                      f"<b>PO Amount:</b> ₹{po_amount:,.2f}<br>"
                      f"<b>Payment Amount:</b> ₹{payment_amount:,.2f}<br>"
                      f"<b>Over Amount:</b> ₹{over_amount:,.2f}"
                      f"</div><br>"
                      f"<b>🔒 Director Override Required</b><br><br>"
                      f"Enable 'Director Override' checkbox to proceed."),
                    title=_("Director Override Required")
                )
    
    except frappe.DoesNotExistError:
        pass
    except Exception as e:
        frappe.log_error(str(e), "Director Override Validation Error")


# Apply monkey patch
PaymentRequest.validate = patched_validate
