# -*- coding: utf-8 -*-
# cashfree_integration/api_manager.py

import frappe
import requests
from frappe import _
from datetime import datetime


class CashfreeAPIManager:
    """
    Unified API Manager for Cashfree
    - Supports SEPARATE credentials for Verification and Payout APIs
    - Falls back to single credential set if separate ones not configured
    """

    def __init__(self):
        self.settings = frappe.get_single("Cashfree Settings")

        if not self.settings.enabled:
            frappe.throw(_("Cashfree Integration is not enabled."))

        self.environment = (self.settings.environment or "sandbox").lower()

        # Load API URLs
        self._load_urls()

        # Load credentials
        self._load_credentials()

        # Log initialization
        frappe.logger().info("✅ Cashfree API Manager initialized")
        frappe.logger().info(f"   Environment: {self.environment}")
        frappe.logger().info(
            f"   Verification Client: {self.verification_client_id[:15] if self.verification_client_id else 'None'}..."
        )
        frappe.logger().info(
            f"   Payout Client: {self.payout_client_id[:15] if self.payout_client_id else 'None'}..."
        )

    def _load_urls(self):
        """Load and validate API URLs based on environment"""
        if self.environment == "production":
            self.verification_url = (self.settings.verification_production_url or "").rstrip("/")
            self.payout_base_url = (self.settings.payout_production_url or "").rstrip("/")
        else:
            self.verification_url = (self.settings.verification_sandbox_url or "").rstrip("/")
            self.payout_base_url = (self.settings.payout_sandbox_url or "").rstrip("/")

        if not self.verification_url:
            frappe.throw(_("Verification URL not configured for {0} environment").format(self.environment))

        if not self.payout_base_url:
            frappe.throw(_("Payout URL not configured for {0} environment").format(self.environment))

    def _load_credentials(self):
        """
        Load API credentials with intelligent fallback:
        1. Try separate credentials (verification_client_id, payout_client_id)
        2. Fall back to legacy single credential (client_id)
        """
        verification_client_id = getattr(self.settings, "verification_client_id", None)
        payout_client_id = getattr(self.settings, "payout_client_id", None)

        legacy_client_id = self.settings.client_id
        legacy_client_secret = self.settings.get_password("client_secret")

        if payout_client_id:
            frappe.logger().info("✅ Using SEPARATE credentials for Verification and Payout")

            self.verification_client_id = verification_client_id or legacy_client_id
            self.verification_client_secret = (
                self._get_password_safe("verification_client_secret") or legacy_client_secret
            )

            self.payout_client_id = payout_client_id
            self.payout_client_secret = self._get_password_safe("payout_client_secret")

            if not self.payout_client_id or not self.payout_client_secret:
                frappe.throw(_("Payout credentials not configured"))
        else:
            frappe.logger().warning("⚠️ Using LEGACY single credential for all APIs")

            if not legacy_client_id or not legacy_client_secret:
                frappe.throw(_("Cashfree credentials not configured"))

            self.verification_client_id = legacy_client_id
            self.verification_client_secret = legacy_client_secret
            self.payout_client_id = legacy_client_id
            self.payout_client_secret = legacy_client_secret

    def _get_password_safe(self, fieldname):
        """Safely get password field without throwing error if not exists"""
        try:
            return self.settings.get_password(fieldname, raise_exception=False) or ""
        except Exception:
            return ""

    def _get_headers_verification(self):
        """Headers for Verification (BAV) API"""
        return {
            "x-client-id": self.verification_client_id,
            "x-client-secret": self.verification_client_secret,
            "Content-Type": "application/json",
        }

    def _get_headers_payout(self):
        """Headers for Payouts API v2"""
        return {
            "x-client-id": self.payout_client_id,
            "x-client-secret": self.payout_client_secret,
            "x-api-version": "2024-01-01",
            "Content-Type": "application/json",
        }

    # ==================== BANK VERIFICATION API ====================

    def verify_bank_account(self, account_number, ifsc, name=None, phone=None):
        """Verify bank account using BAV Sync API"""
        if not self.settings.enable_verification:
            frappe.throw(_("Bank Verification API is not enabled."))

        url = f"{self.verification_url}/bank-account/sync"
        headers = self._get_headers_verification()

        payload = {"bank_account": account_number, "ifsc": ifsc}
        if name:
            payload["name"] = name
        if phone:
            payload["phone"] = phone

        try:
            frappe.logger().info(f"🔍 Verifying bank account: {account_number}")

            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()

            result = response.json()
            frappe.logger().info("✅ Bank verification success")
            return result

        except requests.exceptions.HTTPError as e:
            error_msg = self._extract_error_message(e)
            frappe.log_error(
                title="Cashfree BAV Failed",
                message=f"URL: {url}\nAccount: {account_number}\nError: {error_msg}",
            )
            frappe.throw(_("Bank verification failed: {0}").format(error_msg))

        except Exception as e:
            frappe.log_error(f"Bank Verification Exception: {str(e)}", "Cashfree BAV Error")
            frappe.throw(_("Unexpected error during bank verification: {0}").format(str(e)))

    # ==================== BENEFICIARY API (V2) ====================

    def create_beneficiary(
        self,
        bene_id,
        name,
        email,
        phone,
        bank_account,
        ifsc,
        address1=None,
        city=None,
        state=None,
        pincode=None,
    ):
        """Create beneficiary using Payout API v2"""
        if not self.settings.enable_payout:
            frappe.throw(_("Payout API is not enabled."))

        url = f"{self.payout_base_url}/payout/beneficiary"
        headers = self._get_headers_payout()

        payload = {
            "beneficiary_id": bene_id[:50],
            "beneficiary_name": name[:100],
            "beneficiary_instrument_details": {
                "bank_account_number": str(bank_account).strip(),
                "bank_ifsc": str(ifsc).strip().upper(),
            },
            "beneficiary_contact_details": {
                "beneficiary_email": email or "purchase@toyokombucha.com",
                "beneficiary_phone": phone or "9999999999",
                "beneficiary_country_code": "+91",
                "beneficiary_address": address1 or "India",
                "beneficiary_city": city or "Delhi",
                "beneficiary_state": state or "Delhi",
                "beneficiary_postal_code": str(pincode or "110001"),
            },
        }

        try:
            frappe.logger().info(f"🔄 Creating beneficiary: {bene_id}")
            frappe.logger().info(f"   URL: {url}")

            response = requests.post(url, json=payload, headers=headers, timeout=30)

            try:
                result = response.json()
            except Exception:
                result = {"message": response.text, "status_code": response.status_code}

            frappe.logger().info(f"   Response: {result}")

            if response.status_code == 409:
                if "already exists" in str(result.get("message", "")).lower():
                    frappe.logger().info(f"✅ Beneficiary {bene_id} already exists")
                    return {"status": "SUCCESS", "message": "Beneficiary already exists", "beneficiary_id": bene_id}

            if response.status_code in [200, 201]:
                if result.get("beneficiary_id") or result.get("beneficiary_status") or result.get("data"):
                    frappe.logger().info(f"✅ Beneficiary created: {bene_id}")
                    return result

            response.raise_for_status()
            return result

        except requests.exceptions.HTTPError as e:
            error_msg = self._extract_error_message(e)
            frappe.log_error(
                title="Cashfree Beneficiary Creation Failed",
                message=f"URL: {url}\nBeneficiary ID: {bene_id}\nPayload: {frappe.as_json(payload)}\nError: {error_msg}",
            )
            raise Exception(f"Failed to create beneficiary: {error_msg}")

        except Exception as e:
            frappe.log_error(
                title="Cashfree Beneficiary API Error",
                message=f"Beneficiary ID: {bene_id}\nURL: {url}\nError: {str(e)}",
            )
            raise Exception(f"Beneficiary API error: {str(e)}")

    def check_beneficiary_exists(self, bene_id):
        """Check if beneficiary exists (V2)"""
        if not self.settings.enable_payout:
            frappe.throw(_("Payout API is not enabled."))

        url = f"{self.payout_base_url}/payout/beneficiary"
        headers = self._get_headers_payout()

        try:
            response = requests.get(
                url,
                headers=headers,
                params={"beneficiary_id": bene_id},
                timeout=10,
            )

            if response.status_code == 200:
                result = response.json()
                return bool(result.get("beneficiary_id"))

            return False

        except Exception as e:
            frappe.logger().error(f"❌ Check beneficiary failed: {str(e)}")
            return False

    def get_beneficiary(self, bene_id):
        """Get beneficiary details (V2)"""
        if not self.settings.enable_payout:
            frappe.throw(_("Payout API is not enabled."))

        url = f"{self.payout_base_url}/payout/beneficiary"
        headers = self._get_headers_payout()

        try:
            response = requests.get(
                url,
                headers=headers,
                params={"beneficiary_id": bene_id},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise Exception(f"Failed to fetch beneficiary: {str(e)}")

        except Exception as e:
            raise Exception(f"Failed to fetch beneficiary: {str(e)}")

    # ==================== PAYOUT API (V2) ====================

    def create_transfer(self, bene_id, amount, transfer_id, remarks=None, transfer_mode="banktransfer"):
        """Create payout transfer (V2)"""
        if not self.settings.enable_payout:
            frappe.throw(_("Payout API is not enabled."))

        url = f"{self.payout_base_url}/payout/transfers"
        headers = self._get_headers_payout()

        remarks_prefix = self.settings.payout_remarks_prefix or "TK"
        clean_remarks = remarks_prefix + " " + transfer_id.replace("-", " ")
        payload = {
    "transfer_id": transfer_id.replace("-", "_"),  # only alphanumeric + underscore allowed
    "beneficiary_details": {
        "beneficiary_id": bene_id
},
    "transfer_amount": float(amount),
    "transfer_mode": transfer_mode,
    "transfer_remarks": clean_remarks or f"{remarks_prefix} {transfer_id.replace('-', ' ')}"
} 


        try:
            frappe.logger().info(f"💸 Creating transfer: {transfer_id}")
            frappe.logger().info(f"   URL: {url}")
            frappe.logger().info(f"   Beneficiary: {bene_id}")

            response = requests.post(url, json=payload, headers=headers, timeout=30)

            try:
                result = response.json()
            except Exception:
                result = {"message": response.text, "status_code": response.status_code}

            frappe.logger().info(f"   Response: {result}")

            if response.status_code in [200, 201]:
                frappe.logger().info("✅ Transfer created successfully")
                return result

            response.raise_for_status()
            return result

        except requests.exceptions.HTTPError as e:
            error_msg = self._extract_error_message(e)
            frappe.log_error(
                title="Cashfree Transfer Failed",
                message=f"URL: {url}\nTransfer ID: {transfer_id}\nBeneficiary: {bene_id}\nPayload: {frappe.as_json(payload)}\nError: {error_msg}",
            )
            raise Exception(f"Transfer failed: {error_msg}")

        except Exception as e:
            frappe.log_error(
                title="Cashfree Transfer API Error",
                message=f"Transfer ID: {transfer_id}\nURL: {url}\nError: {str(e)}",
            )
            raise Exception(f"Transfer API error: {str(e)}")

    def get_transfer_status(self, transfer_id):
        """Get transfer status (V2)"""
        if not self.settings.enable_payout:
            frappe.throw(_("Payout API is not enabled."))

        url = f"{self.payout_base_url}/payout/transfers"
        headers = self._get_headers_payout()

        try:
            response = requests.get(
                url,
                headers=headers,
                params={"transfer_id": transfer_id},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        except Exception as e:
            frappe.log_error(f"Transfer Status Check Failed: {str(e)}", "Cashfree Payout Error")
            raise Exception(f"Failed to get transfer status: {str(e)}")

    # ==================== HELPER METHODS ====================

    def _extract_error_message(self, http_error):
        """Extract meaningful error message from HTTPError"""
        try:
            error_data = http_error.response.json()
            return error_data.get("message", str(http_error))
        except Exception:
            return http_error.response.text if http_error.response else str(http_error)


# ==================== PUBLIC API METHODS ====================

@frappe.whitelist()
def verify_bank_account(account_number, ifsc, name=None, phone=None):
    manager = CashfreeAPIManager()
    return manager.verify_bank_account(account_number, ifsc, name, phone)


@frappe.whitelist()
def create_beneficiary(bene_id, name, email, phone, bank_account, ifsc, **kwargs):
    manager = CashfreeAPIManager()
    return manager.create_beneficiary(bene_id, name, email, phone, bank_account, ifsc, **kwargs)


@frappe.whitelist()
def create_transfer(bene_id, amount, transfer_id, remarks=None, transfer_mode="banktransfer"):
    manager = CashfreeAPIManager()
    return manager.create_transfer(bene_id, amount, transfer_id, remarks, transfer_mode)


@frappe.whitelist()
def get_transfer_status(transfer_id):
    manager = CashfreeAPIManager()
    return manager.get_transfer_status(transfer_id)
