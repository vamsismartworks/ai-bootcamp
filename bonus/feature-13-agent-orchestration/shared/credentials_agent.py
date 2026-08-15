"""
Zero-trust credential manager for Bonus Feature 13.

ZERO-TRUST CREDENTIALS AGENT:

SECURITY MODEL:
  - This agent is the ONLY agent that reads from the secrets vault
    (Infisical, Doppler, AWS KMS — from PROMPT 0.5.1 in shared/secrets.py)
  - It returns SHORT-LIVED TOKENS, never raw API keys
  - No other agent's context window ever contains a raw API key
  - Every credential request is logged with the requesting agent's identity
    (from message.sender) for compliance auditing
  - Tokens are cached for cache_ttl_seconds to avoid vault rate limits

WHY A SEPARATE AGENT?
  1. Blast radius: if the PaymentRequestAgent is compromised (prompt injection,
     malicious tool call), the attacker can request tokens but never sees raw
     keys — CredentialsAgent is the only process that touches vault
  2. Auditability: all credential access goes through one choke point, giving
     you a single audit log for compliance (PCI DSS, SOC 2)
  3. Rotation: update the key in vault once — all agents get fresh tokens
     automatically when theirs expire (cache_ttl_seconds has passed)
  4. Least privilege: each issued token carries explicit scopes. The payment
     agent gets ["payment:create", "payment:read"] — not admin rights

IN PRODUCTION — move to dynamic secrets:
  HashiCorp Vault can generate a NEW payment processor key per request and
  auto-expire it. This means:
    - No key is ever reused across agents or sessions
    - Vault handles rotation automatically
    - A leaked token is useless after TTL
  Replace get_secret() → vault_client.secrets.generate("stripe/creds/payments")
  See: https://developer.hashicorp.com/vault/docs/secrets/databases

WHAT THIS ISN'T:
  This is NOT a full OAuth server or a payment processor SDK. The token here
  is a structured envelope containing metadata about the credential (processor,
  scopes, expiry) — in production, you'd call the payment processor's API to
  issue a real restricted key (Stripe restricted keys, PayPal app tokens).
"""
import base64
import hashlib
import json
import logging
import time

logger = logging.getLogger(__name__)


class CredentialsAgent:
    """
    The sole keeper of payment processor credentials.

    Receives AgentMessage requests on the AgentBus, fetches secrets from vault,
    and returns short-lived scoped tokens. All access is logged.

    Register on the bus at startup:
        credentials_agent = CredentialsAgent()
        agent_bus.register("credentials_agent", credentials_agent.handle)
    """

    def __init__(self, cache_ttl_seconds: int = 300) -> None:
        self._cache: dict[str, tuple[str, float]] = {}
        self._ttl = cache_ttl_seconds

    async def handle(self, message: "AgentMessage") -> dict:  # noqa: F821
        """Dispatch incoming AgentBus messages to the appropriate handler."""
        logger.info(
            "CredentialsAgent: credential request  sender=%s  intent=%s  correlation=%s",
            message.sender,
            message.intent,
            message.correlation_id[:8],
        )

        if message.intent == "get_payment_token":
            return await self._get_payment_token(message.payload, message.sender, message.correlation_id)

        return {"error": f"Unknown intent '{message.intent}'. Supported: get_payment_token"}

    async def _get_payment_token(
        self,
        payload: dict,
        requesting_agent: str,
        correlation_id: str,
    ) -> dict:
        """
        Issue a short-lived scoped token for payment processor access.

        Flow:
          1. Check token cache (avoids hitting vault on every payment)
          2. If cache miss: fetch raw key from vault via get_secret()
          3. Derive a scoped token (NOT the raw key) with processor, scopes, expiry
          4. Cache token for cache_ttl_seconds
          5. Log the credential issuance (required for financial compliance)
          6. Return token + metadata (never the raw key)
        """
        processor = payload.get("processor", "stripe")
        cache_key = f"token:{processor}"
        now = time.time()

        # --- 1. Cache check ---
        if cache_key in self._cache:
            token, expires_at = self._cache[cache_key]
            if now < expires_at:
                logger.info(
                    "CredentialsAgent: cache hit  processor=%s  expires_in=%ds",
                    processor,
                    int(expires_at - now),
                )
                return {
                    "token": token,
                    "source": "cache",
                    "expires_in": int(expires_at - now),
                    "scopes": ["payment:create", "payment:read"],
                }

        # --- 2. Fetch raw key from vault (ONLY this agent does this) ---
        try:
            from shared.secrets import get_secret
            raw_key = get_secret(f"{processor.upper()}_API_KEY")
        except (ValueError, ImportError):
            # Fall back to a placeholder for demo environments where vault
            # isn't configured. In production, let this raise — a missing
            # key should fail loudly, not silently use a placeholder.
            raw_key = f"demo_key_{processor}_placeholder"
            logger.warning(
                "CredentialsAgent: vault unavailable, using demo placeholder for '%s'. "
                "Configure SECRETS_PROVIDER and %s_API_KEY for production.",
                processor,
                processor.upper(),
            )

        # --- 3. Derive a scoped token ---
        # In production: call the payment processor's API to issue a real
        # restricted key with exactly these scopes and this TTL.
        # Here: encode metadata as a signed envelope for demonstration.
        token_data: dict = {
            "processor": processor,
            "scopes": ["payment:create", "payment:read"],
            "iat": int(now),
            "exp": int(now) + self._ttl,
            # NEVER include raw_key in the token — use only a fingerprint for tracing
            "key_fingerprint": hashlib.sha256(raw_key.encode()).hexdigest()[:16],
        }
        token = base64.urlsafe_b64encode(json.dumps(token_data).encode()).decode()

        # --- 4. Cache ---
        self._cache[cache_key] = (token, now + self._ttl)

        # --- 5. Audit log ---
        logger.info(
            "CredentialsAgent: token issued  processor=%s  requesting_agent=%s  "
            "expires_in=%ds  correlation=%s  token_fingerprint=%s",
            processor,
            requesting_agent,
            self._ttl,
            correlation_id[:8],
            hashlib.sha256(token.encode()).hexdigest()[:8],
        )

        # --- 6. Return token — NOT the raw key ---
        return {
            "token": token,
            "source": "vault",
            "expires_in": self._ttl,
            "scopes": token_data["scopes"],
            "processor": processor,
        }

    def clear_cache(self) -> None:
        """Force re-fetch from vault on next request. Useful after key rotation."""
        self._cache.clear()
        logger.info("CredentialsAgent: token cache cleared")
