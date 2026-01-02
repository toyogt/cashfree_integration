# Copyright (c) 2026, Nikhil and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CashfreeSettings(Document):
    """Cashfree API Configuration"""
    
    def validate(self):
        """Validate credentials format"""
        if self.client_id and not self.client_id.startswith("CF"):
            frappe.msgprint("Client ID should start with 'CF'", indicator="orange")
        
        if self.enabled and not (self.client_id and self.client_secret):
            frappe.throw("Client ID and Secret are required when Cashfree is enabled")
    
    def get_base_url(self, api_type):
        """Get base URL for specified API type"""
        if api_type == "payout":
            if self.environment == "sandbox":
                return self.payout_sandbox_url
            return self.payout_production_url
        
        elif api_type == "verification":
            if self.environment == "sandbox":
                return self.verification_sandbox_url
            return self.verification_production_url
        
        frappe.throw(f"Invalid API type: {api_type}")
