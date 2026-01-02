# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nikhil and contributors
# For license information, please see license.txt

import frappe
import requests
from frappe import _
from datetime import datetime, timedelta

class CashfreeAPIManager:
    """
    Unified API Manager for Cashfree Payout and Verification APIs
    Automatically switches between sandbox/production based on settings
    """
    
    def __init__(self):
        self.settings = frappe.get_single("Cashfree Settings")
        
        # Validate settings
        if not self.settings.enabled:
            frappe.throw(_("Cashfree Integration is not enabled. Please enable it in Cashfree Settings."))
        
        if not self.settings.client_id or not self.settings.get_password("client_secret"):
            frappe.throw(_("Cashfree credentials not configured. Please add Client ID and Secret."))
        
        # Store credentials
        self.client_id = self.settings.client_id
        self.client_secret = self.settings.get_password("client_secret")
        self.environment = self.settings.environment or "sandbox"
        
        # Set API URLs based on environment
        if self.environment == "sandbox":
            self.payout_url = self.settings.payout_sandbox_url
            self.verification_url = self.settings.verification_sandbox_url
        else:
            self.payout_url = self.settings.payout_production_url
            self.verification_url = self.settings.verification_production_url
        
        # Token management
        self.token = None
        self.token_expiry = None
    
    def _get_headers_basic(self):
        """Returns headers with basic auth for BAV API"""
        return {
            "x-client-id": self.client_id,
            "x-client-secret": self.client_secret,
            "Content-Type": "application/json"
        }
    
    def _get_token(self):
        """Generate Bearer token for Payout API"""
        # Check if token is still valid
        if self.token and self.token_expiry and datetime.now() < self.token_expiry:
            return self.token
        
        # Generate new token
        url = f"{self.payout_url}/authorize"
        headers = {
            "x-client-id": self.client_id,
            "x-client-secret": self.client_secret,
            "x-api-version": "2024-01-01"
        }
        
        try:
            response = requests.post(url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            self.token = data.get("data", {}).get("token")
            
            # Token expires in 5 minutes, cache it
            self.token_expiry = datetime.now() + timedelta(minutes=4, seconds=30)
            
            return self.token
            
        except Exception as e:
            frappe.log_error(f"Cashfree Token Generation Failed: {str(e)}", "Cashfree API Error")
            frappe.throw(_("Failed to authenticate with Cashfree. Please check credentials."))
    
    def _get_headers_bearer(self):
        """Returns headers with Bearer token for Payout API"""
        token = self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "x-client-id": self.client_id,
            "x-client-secret": self.client_secret,
            "x-api-version": "2024-01-01",
            "Content-Type": "application/json"
        }
    
    # ==================== BANK VERIFICATION API ====================
    
    def verify_bank_account(self, account_number, ifsc, name=None):
        """
        Verify bank account using BAV API (uses direct credentials)
        
        Args:
            account_number (str): Bank account number
            ifsc (str): IFSC code
            name (str, optional): Account holder name for name match
            
        Returns:
            dict: Verification response with status and details
        """
        if not self.settings.enable_verification:
            frappe.throw(_("Bank Verification API is not enabled."))
        
        url = f"{self.verification_url}/bank-account/sync"
        headers = self._get_headers_basic()
        
        payload = {
            "bank_account": account_number,
            "ifsc": ifsc
        }
        
        if name:
            payload["name"] = name
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            
            # Log successful verification
            frappe.logger().info(f"Bank Verification Success: {account_number}")
            
            return result
            
        except requests.exceptions.HTTPError as e:
            error_msg = str(e)
            if e.response is not None:
                error_msg = e.response.text
            
            frappe.log_error(f"Bank Verification Failed: {error_msg}", "Cashfree BAV Error")
            frappe.throw(_("Bank verification failed: {0}").format(error_msg))
        
        except Exception as e:
            frappe.log_error(f"Bank Verification Exception: {str(e)}", "Cashfree BAV Error")
            frappe.throw(_("Unexpected error during bank verification: {0}").format(str(e)))
    
    # ==================== PAYOUT API ====================
    
    def create_beneficiary(self, bene_id, name, email, phone, bank_account, ifsc, address1=None, city=None, state=None, pincode=None):
        """
        Create beneficiary for payouts (uses Bearer token)
        
        Args:
            bene_id (str): Unique beneficiary ID
            name (str): Beneficiary name
            email (str): Email address
            phone (str): Phone number
            bank_account (str): Bank account number
            ifsc (str): IFSC code
            address1 (str, optional): Address
            city (str, optional): City
            state (str, optional): State
            pincode (str, optional): PIN code
            
        Returns:
            dict: Beneficiary creation response
        """
        if not self.settings.enable_payout:
            frappe.throw(_("Payout API is not enabled."))
        
        url = f"{self.payout_url}/beneficiary"
        headers = self._get_headers_bearer()
        
        payload = {
            "beneficiary_id": bene_id,
            "beneficiary_name": name,
            "beneficiary_instrument_details": {
                "bank_account_number": bank_account,
                "bank_ifsc": ifsc
            },
            "beneficiary_contact_details": {
                "beneficiary_email": email,
                "beneficiary_phone": phone,
                "beneficiary_address": address1 or "NA",
                "beneficiary_city": city or "Bangalore",
                "beneficiary_state": state or "Karnataka",
                "beneficiary_postal_code": pincode or "560001"
            }
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            frappe.logger().info(f"Beneficiary Created: {bene_id}")
            
            return result
            
        except requests.exceptions.HTTPError as e:
            error_msg = str(e)
            if e.response is not None:
                error_msg = e.response.text
            
            frappe.log_error(f"Beneficiary Creation Failed: {error_msg}", "Cashfree Payout Error")
            frappe.throw(_("Failed to create beneficiary: {0}").format(error_msg))
        
        except Exception as e:
            frappe.log_error(f"Beneficiary Creation Exception: {str(e)}", "Cashfree Payout Error")
            frappe.throw(_("Unexpected error: {0}").format(str(e)))
    
    def create_transfer(self, bene_id, amount, transfer_id, remarks=None):
        """
        Create payout transfer (uses Bearer token)
        
        Args:
            bene_id (str): Beneficiary ID
            amount (float): Transfer amount
            transfer_id (str): Unique transfer ID
            remarks (str, optional): Transfer remarks
            
        Returns:
            dict: Transfer response with status and reference ID
        """
        if not self.settings.enable_payout:
            frappe.throw(_("Payout API is not enabled."))
        
        url = f"{self.payout_url}/transfers"
        headers = self._get_headers_bearer()
        
        payload = {
            "transfer_id": transfer_id,
            "transfer_amount": float(amount),
            "beneficiary_details": {
                "beneficiary_id": bene_id
            },
            "transfer_purpose": remarks or "Payout from ERPNext"
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            frappe.logger().info(f"Transfer Created: {transfer_id}")
            
            return result
            
        except requests.exceptions.HTTPError as e:
            error_msg = str(e)
            if e.response is not None:
                error_msg = e.response.text
            
            frappe.log_error(f"Transfer Failed: {error_msg}", "Cashfree Payout Error")
            frappe.throw(_("Transfer failed: {0}").format(error_msg))
        
        except Exception as e:
            frappe.log_error(f"Transfer Exception: {str(e)}", "Cashfree Payout Error")
            frappe.throw(_("Unexpected error: {0}").format(str(e)))
    
    def get_transfer_status(self, transfer_id):
        """
        Get status of a transfer (uses Bearer token)
        
        Args:
            transfer_id (str): Transfer ID to check
            
        Returns:
            dict: Transfer status details
        """
        if not self.settings.enable_payout:
            frappe.throw(_("Payout API is not enabled."))
        
        url = f"{self.payout_url}/transfers/status"
        headers = self._get_headers_bearer()
        
        params = {"transfer_id": transfer_id}
        
        try:
            response = requests.get(url, params=params, headers=headers)
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            frappe.log_error(f"Transfer Status Check Failed: {str(e)}", "Cashfree Payout Error")
            frappe.throw(_("Failed to get transfer status: {0}").format(str(e)))


# ==================== PUBLIC API METHODS ====================

@frappe.whitelist()
def verify_bank_account(account_number, ifsc, name=None):
    """Public method to verify bank account"""
    manager = CashfreeAPIManager()
    return manager.verify_bank_account(account_number, ifsc, name)

@frappe.whitelist()
def create_beneficiary(bene_id, name, email, phone, bank_account, ifsc, **kwargs):
    """Public method to create beneficiary"""
    manager = CashfreeAPIManager()
    return manager.create_beneficiary(bene_id, name, email, phone, bank_account, ifsc, **kwargs)

@frappe.whitelist()
def create_transfer(bene_id, amount, transfer_id, remarks=None):
    """Public method to create transfer"""
    manager = CashfreeAPIManager()
    return manager.create_transfer(bene_id, amount, transfer_id, remarks)

@frappe.whitelist()
def get_transfer_status(transfer_id):
    """Public method to get transfer status"""
    manager = CashfreeAPIManager()
    return manager.get_transfer_status(transfer_id)
