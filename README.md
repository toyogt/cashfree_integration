### CashFree Integration

Cashfree API

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app cashfree_integration
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/cashfree_integration
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit



///////////////////////////////////////////////////////////////////
<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# now create a documentation file for this project

with does and donts
environments
logics
and readme.md file\

This integration implements a full Cashfree Payouts v2 flow for ERPNext using explicit beneficiary creation and transfer APIs.

## Overview

This project connects ERPNext Payment Requests to Cashfree Payouts v2 so that supplier payments are triggered automatically when a Payment Request moves to a queued state. Beneficiaries are created explicitly in Cashfree using a deterministic `beneficiary_id`, stored on the Bank Account, and then reused for future payouts. Payout status and metadata are logged in a custom Payout Log and back on the Payment Request.

***

## Environments

### Cashfree

- **Sandbox**
    - Base URL: `https://sandbox.cashfree.com/payout`
    - Used only for initial development and testing.
    - Separate client ID and client secret.
- **Production**
    - Base URL: `https://api.cashfree.com/payout`
    - IP whitelisting required for each server/public IP.
    - Webhooks should point to the production ERPNext site.


### ERPNext / Frappe

- **Local development**
    - Site: `dev.local` or similar.
    - Webhooks from Cashfree cannot reach localhost; reconciliation is manual.
- **Staging / Production**
    - Public site (e.g. `https://k95foodspvtltd.erpnext.com`).
    - Webhook endpoint exposed over HTTPS.


### Cashfree Settings (Single Doctype)

Fields (key ones):

- `base_url` – environment-specific URL, no trailing `/`.
- `client_id` – Cashfree Payouts client ID.
- `client_secret` – stored encrypted and decrypted via `get_decrypted_password`.
- `payout_remarks_prefix` – prefix for `remarks` (e.g. `TK`).

***

## Core Logic

### 1. Getting Settings and Headers

```python
settings = frappe.get_single("Cashfree Settings")
base_url = (settings.base_url or "").rstrip("/")
client_id = settings.client_id
client_secret = get_decrypted_password("Cashfree Settings", settings.name, "client_secret")

headers = {
    "x-client-id": client_id,
    "x-client-secret": client_secret,
    "x-api-version": "2024-01-01",
    "Content-Type": "application/json",
}
```


### 2. Contact Resolution for Bank Account

Bank Account does not hold email/phone directly; they are stored in a linked Contact:

- A **Contact** is linked to Bank Account via **Dynamic Link** (`link_doctype = "Bank Account"`, `link_name = bank.name`).
- Email and phone are read from:
    - `Contact.email_id`
    - First row in `Contact.phone_nos` child table.

If no contact is found or fields are blank, validation should catch this before hitting the API.

### 3. Beneficiary ID Strategy

A deterministic `beneficiary_id` is generated per Bank Account:

```python
party_clean = str(bank.party).replace(" ", "_").replace("-", "_")[:20]
account_suffix = bank.bank_account_no[-4:] if bank.bank_account_no else "0000"
bene_id = f"BENE_{party_clean}_{account_suffix}"[:50]
# Example: BENE_Red_Rock_2439
```

Properties:

- Stable for the same party + account.
- Maximum length 50 characters.
- Safe characters only.


### 4. Beneficiary Lifecycle

#### a) Check if Beneficiary Exists in ERPNext

- Field on Bank Account: `custom_cashfree_beneficiary_id`.
- If present, first verify against Cashfree using:
    - `GET /beneficiaries/{beneficiary_id}`.
- If it exists in Cashfree, reuse it.


#### b) Check if Generated ID Already Exists in Cashfree

- If ERPNext has no ID, call `GET /beneficiaries/{generated_id}`.
- If found, save to Bank Account and reuse.


#### c) Create Beneficiary (if not found)

- Endpoint: `POST /beneficiary`.
- Payload (v2 style):

```python
{
  "beneficiary_id": bene_id,
  "beneficiary_name": bank.account_name,
  "beneficiary_instrument_details": {
    "bank_account_number": bank.bank_account_no,
    "bank_ifsc": ifsc
  },
  "beneficiary_contact_details": {
    "beneficiary_email": email or "default@example.com",
    "beneficiary_phone": phone or "9999999999",
    "beneficiary_country_code": "+91",
    "beneficiary_address": "India",
    "beneficiary_city": "Delhi",
    "beneficiary_state": "Delhi",
    "beneficiary_postal_code": "110001"
  }
}
```

- On success (HTTP 200/201 or 409 “already exists”):
    - Save `beneficiary_id` back to Bank Account: `custom_cashfree_beneficiary_id`.


### 5. Transfer Creation

#### Trigger Point

- Hooked on Payment Request update (`trigger_payout_for_payment_request(doc, method=None)`).
- Only runs when:
    - `workflow_state` ∈ `["Queued", "Queue for Payout", "Queued for Payout"]`.
    - `custom_cashfree_payout_id` is empty.
    - `grand_total > 0`.
    - `bank_account` is set and valid.


#### Transfer API Call

- Endpoint: `POST /transfers`.

Payload:

```python
{
  "transfer_id": doc.name,             # Payment Request name
  "beneficiary_details": {
    "beneficiary_id": bene_id          # Explicit beneficiary
  },
  "transfer_amount": float(amount),
  "transfer_mode": "banktransfer",
  "remarks": f"{settings.payout_remarks_prefix or 'TK'} {doc.name}"
}
```


#### Status Handling

- Response fields used:
    - `cf_transfer_id` or `transfer_id` → saved as `custom_cashfree_payout_id`.
    - `status` or `status_code` → mapped to internal status:

```python
status_mapping = {
    "RECEIVED": "Pending",
    "SUCCESS": "Success",
    "PENDING": "Pending",
    "QUEUED": "Pending",   # low balance queuing
    "FAILED": "Failed",
    "ERROR": "Failed",
    "REVERSED": "Reversed",
    "REJECTED": "Failed",
}
```

- Result:
    - `custom_cashfree_payout_id` (if present) and
    - `custom_reconciliation_status` are updated on Payment Request using `frappe.db.set_value`.


### 6. Logging

- Helper: `log_message(data, title)` → writes JSON or text to Error Log.
- For each payout:
    - A `Cashfree Payout Log` document is created with:
        - `payment_request`
        - `payout_id`
        - `transfer_mode`
        - `amount`
        - `status`
        - `request_payload`
        - `response_payload`.
- Errors:
    - Separate Error Logs: settings, bank fetch, beneficiary creation, transfer failure, PR update failure, etc.

***

## Webhook and Reconciliation

(High-level, since webhook is separate file)

- Webhook endpoint (production):
`https://<site>/api/method/cashfree_integration.api.webhooks.cashfree_payout_webhook`
- Webhook receives terminal state from Cashfree:
    - SUCCESS, FAILED, REVERSED, etc.
    - UTR number.
- It updates:
    - `custom_reconciliation_status`
    - `custom_utr_number`
    - `workflow_state` (e.g. to “Completed”) on Payment Request.

On localhost:

- Webhook cannot be called by Cashfree.
- UTR and final reconciliation must be handled manually.

***

## Enforced \& Recommended Validations

These are typically done via ERPNext Server Scripts or form validation:

- On **Payment Request** when moving to “Verify” or “Queued”:
    - Must have `bank_account`.
    - Bank Account must have:
        - `account_name`
        - `bank_account_no`
        - IFSC (`branch_code` or `custom_ifsc_code`)
        - Linked Contact with:
            - `email_id`
            - at least one phone in `phone_nos`.

If any of these are missing, the workflow transition should be blocked with a clear error.

***

## Do’s and Don’ts

### Do’s

- Do **always** configure correct `base_url`, `client_id`, and `client_secret` per environment.
- Do **whitelist** server IPs in Cashfree for production.
- Do **use deterministic beneficiary IDs** (`BENE_{party}_{last4}`) so they are reusable and traceable.
- Do **store the beneficiary ID** on the corresponding Bank Account (`custom_cashfree_beneficiary_id`).
- Do **create beneficiaries explicitly** with the `/beneficiary` API before transfers.
- Do **use Payment Request name as `transfer_id`** for easy tracking and idempotency.
- Do **log every request and response** to `Cashfree Payout Log` plus Error Log for failures.
- Do **handle low balance** by treating QUEUED as `Pending` and monitoring the Cashfree dashboard.
- Do **test with small amounts** (₹1–₹5) in production when verifying integration.


### Don’ts

- Don’t mix sandbox and production credentials or base URLs.
- Don’t send inline `beneficiary_details` when your account requires `beneficiary_id` – always use explicit beneficiaries.
- Don’t assume email/phone exist on Bank Account; always resolve from Contact or validate.
- Don’t override `custom_cashfree_beneficiary_id` if it’s already set and valid in Cashfree.
- Don’t reuse the same Payment Request for repeated tests without resetting:
    - `custom_cashfree_payout_id`
    - `custom_reconciliation_status`
    - `workflow_state`.
- Don’t expose client secret in logs or UI.
- Don’t rely on webhooks when running purely on localhost.

***

## README.md (Suggested Content)

```markdown
# Cashfree Payouts v2 Integration for ERPNext

This app integrates ERPNext Payment Requests with Cashfree Payouts v2. It creates Cashfree beneficiaries explicitly, stores their IDs against Bank Accounts, and triggers payouts automatically when Payment Requests move to a queued state.

## Features

- Explicit beneficiary creation via Cashfree Payouts v2 `/beneficiary` API
- Deterministic beneficiary IDs: `BENE_{party}_{last4_digits}`
- Beneficiary ID stored on Bank Account for reuse
- Automatic payout trigger from Payment Request workflow
- Detailed logging via `Cashfree Payout Log` and Error Logs
- Support for low-balance queuing (QUEUED → Pending)
- Webhook-based reconciliation (UTR, final status) on production

## Requirements

- ERPNext / Frappe site
- Cashfree Payouts v2 account (sandbox and/or production)
- Valid IP whitelisting on Cashfree for production servers
- `Cashfree Settings` single doctype configured with:
  - `base_url`
  - `client_id`
  - `client_secret`
  - `payout_remarks_prefix` (optional)

## How It Works

1. **User creates a Payment Request** for a Supplier with a linked Bank Account.
2. When the Payment Request workflow changes to **Queued**, a hook calls:
   - `trigger_payout_for_payment_request(doc, method=None)`.
3. The hook:
   - Validates amount, bank account, and settings.
   - Loads the Bank Account and resolves email/phone from its linked Contact.
   - Generates a deterministic `beneficiary_id` for that bank account.
   - Ensures a beneficiary exists in Cashfree (create if needed).
   - Triggers a payout using the `/transfers` API with `beneficiary_id`.
   - Logs request/response to `Cashfree Payout Log`.
   - Updates the Payment Request with:
     - `custom_cashfree_payout_id`
     - `custom_reconciliation_status`.

4. On production, **Cashfree webhook** notifies ERPNext when payouts succeed or fail, and UTR is saved on the Payment Request.

## Configuration

1. **Cashfree Settings**
   - Go to: `Cashfree Settings`.
   - Set:
     - `Base URL`:
       - Sandbox: `https://sandbox.cashfree.com/payout`
       - Production: `https://api.cashfree.com/payout`
     - `Client ID` and `Client Secret`.
     - `Payout Remarks Prefix` (e.g. `TK`).

2. **Bank Accounts**
   - For each supplier bank account:
     - Fill:
       - `account_name`
       - `bank_account_no`
       - IFSC (`branch_code` or `custom_ifsc_code`)
     - Link a **Contact** with:
       - `email_id`
       - at least one phone in `phone_nos`.
   - `custom_cashfree_beneficiary_id` is filled automatically after first successful beneficiary creation.

3. **Webhooks (Production Only)**
   - Set Cashfree webhook URL to:
     - `https://<your-site>/api/method/cashfree_integration.api.webhooks.cashfree_payout_webhook`
   - Ensure HTTPS and valid certificate.

4. **IP Whitelisting**
   - From your server, run:
     - `curl ifconfig.me`
   - Add the IP to Cashfree dashboard under IP Whitelisting.

## Development vs Production

- **Local Development**
  - Use sandbox credentials and sandbox base URL.
  - Webhooks will not reach localhost.
  - Manually check Cashfree dashboard and logs.
- **Production**
  - Use production base URL and credentials.
  - Whitelist server IP.
  - Enable webhooks for auto-reconciliation.

## Do's and Don'ts

See `docs/INTEGRATION_GUIDE.md` for detailed do's, don'ts, and troubleshooting.

## Troubleshooting

- **Error: "IP not whitelisted"**
  - Whitelist the current public IP in Cashfree dashboard.
- **Error: "Please provide a valid Beneficiary Id"**
  - Ensure you are using explicit beneficiary creation via `/beneficiary` and passing `beneficiary_id` in `/transfers`.
- **Payout not created, status Pending**
  - Check `Cashfree Payout Log` and Error Logs for HTTP status and error messages.
  - Check Cashfree dashboard for low balance or queued transfers.

## License

Internal use for K95 Foods Pvt. Ltd.
```


***

If you want, the next step can be drafting a separate `INTEGRATION_GUIDE.md` or `TROUBLESHOOTING.md` with concrete error examples and how to resolve them.

