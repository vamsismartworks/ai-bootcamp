"""
Payment domain tools for Bonus Feature 13: Agent Orchestration.

These are mock implementations that show the correct shape for a P2P payment
system. Comments throughout explain how to replace each mock with a real
payment processor (Stripe, PayPal, etc.).

IMPORTANT DESIGN NOTE — separation of concerns:
  These tools are purely functional. None of them fetches API credentials.
  `submit_payment` receives a token as a parameter — the CredentialsAgent
  (shared/credentials_agent.py) is the only component that touches the vault.
  This separation ensures no tool can accidentally log or leak a raw API key.

Tool registry shape used by the sub-agents:
  PaymentRequestAgent uses: validate_payment, submit_payment, request_payment_token
  PaymentHistoryAgent uses: get_transaction_history, filter_transactions_by_date
"""
import hashlib
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock data — replace with database calls in production
# ---------------------------------------------------------------------------

_MOCK_USERS: dict[str, dict] = {
    "user_123": {"name": "Alice", "balance": 500.00, "kyc_verified": True},
    "user_456": {"name": "Bob",   "balance": 250.00, "kyc_verified": True},
    "user_789": {"name": "Carol", "balance": 100.00, "kyc_verified": False},
}

_MOCK_TRANSACTIONS: list[dict] = [
    {
        "transaction_id": "txn_001",
        "user_id": "user_123",
        "amount": 20.00,
        "recipient_id": "user_456",
        "recipient_name": "Bob",
        "status": "completed",
        "timestamp": "2026-07-10T14:30:00Z",
        "description": "Dinner split",
    },
    {
        "transaction_id": "txn_002",
        "user_id": "user_123",
        "amount": 50.00,
        "recipient_id": "user_789",
        "recipient_name": "Carol",
        "status": "completed",
        "timestamp": "2026-07-15T09:00:00Z",
        "description": "Concert tickets",
    },
    {
        "transaction_id": "txn_003",
        "user_id": "user_456",
        "amount": 15.00,
        "recipient_id": "user_123",
        "recipient_name": "Alice",
        "status": "completed",
        "timestamp": "2026-07-18T20:00:00Z",
        "description": "Coffee",
    },
]

_PAYMENT_LIMIT_PER_TRANSACTION = 1000.00

# ---------------------------------------------------------------------------
# Tool: validate_payment
# ---------------------------------------------------------------------------

def validate_payment(amount: float, recipient_id: str, user_id: str) -> dict:
    """
    Validate a payment before submission.

    Checks:
      1. Amount is positive and within the per-transaction limit
      2. Recipient exists and has a verified KYC status
      3. Sender has sufficient balance

    Returns:
      {"valid": bool, "reason": str}

    In production: call your payments database. Check:
      - Recipient KYC/AML status via compliance API
      - Sender balance in real-time (not cached)
      - Fraud rules (velocity checks, unusual amounts, new recipients)
      - Sanctions screening (OFAC, EU, UN lists) for cross-border payments
    """
    if amount <= 0:
        return {"valid": False, "reason": f"Amount must be positive, got {amount}"}

    if amount > _PAYMENT_LIMIT_PER_TRANSACTION:
        return {
            "valid": False,
            "reason": f"Amount ${amount:.2f} exceeds per-transaction limit of ${_PAYMENT_LIMIT_PER_TRANSACTION:.2f}",
        }

    recipient = _MOCK_USERS.get(recipient_id)
    if recipient is None:
        return {"valid": False, "reason": f"Recipient '{recipient_id}' not found"}

    if not recipient.get("kyc_verified"):
        return {
            "valid": False,
            "reason": f"Recipient '{recipient_id}' has not completed identity verification",
        }

    sender = _MOCK_USERS.get(user_id)
    if sender is None:
        return {"valid": False, "reason": f"Sender '{user_id}' not found"}

    if sender["balance"] < amount:
        return {
            "valid": False,
            "reason": f"Insufficient balance: ${sender['balance']:.2f} available, ${amount:.2f} requested",
        }

    return {
        "valid": True,
        "reason": f"Payment of ${amount:.2f} to {recipient['name']} is valid",
    }


# OpenAI-format schema for the LLM
VALIDATE_PAYMENT_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "validate_payment",
        "description": "Validate a P2P payment before submission. Checks amount limits, recipient KYC status, and sender balance.",
        "parameters": {
            "type": "object",
            "properties": {
                "amount":       {"type": "number",  "description": "Payment amount in USD (must be positive)"},
                "recipient_id": {"type": "string",  "description": "The recipient's user ID"},
                "user_id":      {"type": "string",  "description": "The sender's user ID"},
            },
            "required": ["amount", "recipient_id", "user_id"],
        },
    },
}

# ---------------------------------------------------------------------------
# Tool: submit_payment
# ---------------------------------------------------------------------------

def submit_payment(
    amount: float,
    recipient_id: str,
    user_id: str,
    api_token: str,
    description: str = "",
) -> dict:
    """
    Submit a payment to the processor.

    NOTE: This tool receives the api_token as a parameter — it does NOT
    call vault or fetch credentials itself. That separation is intentional.
    See shared/credentials_agent.py for the security reasoning.

    In production: call your payment processor's API:
      - Stripe: stripe.PaymentIntent.create(amount=..., currency="usd", ...)
      - PayPal: paypalrestsdk.Payment.create(...)
      - Modern Money APIs: call /v1/payments with the scoped API token
      The token comes from CredentialsAgent, which fetched it from vault
      with the minimal scopes needed for this transaction only.

    Returns:
      {transaction_id, status, timestamp, amount, recipient_id}
    """
    # Verify the token has the right shape (real impl: verify signature/expiry)
    if not api_token:
        return {"error": "No API token provided. CredentialsAgent must be called first."}

    # Mock: simulate successful submission
    transaction_id = f"txn_{hashlib.sha256((user_id + recipient_id + str(amount)).encode()).hexdigest()[:8]}"
    timestamp = datetime.now(timezone.utc).isoformat()

    # Mock: update balance
    if user_id in _MOCK_USERS and recipient_id in _MOCK_USERS:
        _MOCK_USERS[user_id]["balance"] -= amount
        _MOCK_USERS[recipient_id]["balance"] += amount

    # Mock: record transaction
    _MOCK_TRANSACTIONS.append({
        "transaction_id": transaction_id,
        "user_id": user_id,
        "amount": amount,
        "recipient_id": recipient_id,
        "recipient_name": _MOCK_USERS.get(recipient_id, {}).get("name", "Unknown"),
        "status": "completed",
        "timestamp": timestamp,
        "description": description,
    })

    logger.info({
        "event": "payment_submitted",
        "transaction_id": transaction_id,
        "amount": amount,
        "user_id": user_id,
        "recipient_id": recipient_id,
    })

    return {
        "transaction_id": transaction_id,
        "status": "completed",
        "timestamp": timestamp,
        "amount": amount,
        "recipient_id": recipient_id,
        "message": f"Payment of ${amount:.2f} to {_MOCK_USERS.get(recipient_id, {}).get('name', recipient_id)} completed successfully.",
    }


SUBMIT_PAYMENT_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "submit_payment",
        "description": "Submit a validated payment to the payment processor. Requires an API token from the credentials agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "amount":       {"type": "number",  "description": "Payment amount in USD"},
                "recipient_id": {"type": "string",  "description": "The recipient's user ID"},
                "user_id":      {"type": "string",  "description": "The sender's user ID"},
                "api_token":    {"type": "string",  "description": "Short-lived API token from the credentials agent"},
                "description":  {"type": "string",  "description": "Optional payment description (e.g. 'dinner split')"},
            },
            "required": ["amount", "recipient_id", "user_id", "api_token"],
        },
    },
}

# ---------------------------------------------------------------------------
# Tool: get_transaction_history
# ---------------------------------------------------------------------------

def get_transaction_history(user_id: str, limit: int = 10) -> list[dict]:
    """
    Return the most recent transactions for a user.

    Returns a list of transaction records (newest first), limited to `limit`.

    In production: query your payments database filtered by user_id.
    Index the `timestamp` column and the `user_id` column for fast lookups.
    For compliance, retain all transactions for at least 5 years (7 in some
    jurisdictions). Never delete payment records — use soft-delete with
    an `archived_at` timestamp.
    """
    user_txns = [
        t for t in _MOCK_TRANSACTIONS
        if t["user_id"] == user_id or t.get("recipient_id") == user_id
    ]
    # Sort newest-first
    user_txns.sort(key=lambda t: t["timestamp"], reverse=True)
    return user_txns[:limit]


GET_TRANSACTION_HISTORY_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "get_transaction_history",
        "description": "Return the most recent payment transactions for a user (as sender or recipient).",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string",  "description": "The user's ID"},
                "limit":   {"type": "integer", "description": "Maximum number of transactions to return (default 10)"},
            },
            "required": ["user_id"],
        },
    },
}

# ---------------------------------------------------------------------------
# Tool: filter_transactions_by_date
# ---------------------------------------------------------------------------

def filter_transactions_by_date(
    transactions: list[dict],
    from_date: str,
    to_date: str,
) -> list[dict]:
    """
    Filter a transaction list to a date range.

    Dates are ISO 8601 strings: "2026-07-01" or "2026-07-01T00:00:00Z".
    Both bounds are inclusive.

    In production: push this filter down to the database query (WHERE
    timestamp BETWEEN from_date AND to_date). Never pull all rows into
    memory and filter in Python at scale.
    """
    from_dt = datetime.fromisoformat(from_date.replace("Z", "+00:00"))
    to_dt   = datetime.fromisoformat(to_date.replace("Z", "+00:00"))

    result = []
    for txn in transactions:
        try:
            txn_dt = datetime.fromisoformat(txn["timestamp"].replace("Z", "+00:00"))
            if from_dt <= txn_dt <= to_dt:
                result.append(txn)
        except (KeyError, ValueError):
            continue
    return result


FILTER_TRANSACTIONS_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "filter_transactions_by_date",
        "description": "Filter a list of transactions to a specific date range (inclusive).",
        "parameters": {
            "type": "object",
            "properties": {
                "transactions": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of transaction records from get_transaction_history",
                },
                "from_date": {"type": "string", "description": "Start date (ISO 8601, e.g. '2026-07-01')"},
                "to_date":   {"type": "string", "description": "End date (ISO 8601, e.g. '2026-07-31')"},
            },
            "required": ["transactions", "from_date", "to_date"],
        },
    },
}
