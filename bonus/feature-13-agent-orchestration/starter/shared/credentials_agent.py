"""
Zero-trust credential manager for Bonus Feature 13.

ZERO-TRUST CREDENTIALS AGENT:

SECURITY MODEL:
  - This agent is the ONLY agent that reads from the secrets vault
    (Infisical, Doppler, AWS KMS — from PROMPT 0.5.1 in shared/secrets.py)
  - It returns SHORT-LIVED TOKENS, never raw API keys
  - No other agent's context window ever contains a raw API key
  - Every credential request is logged with the requesting agent's identity
    for compliance auditing
  - Tokens are cached to avoid vault rate limits

WHY A SEPARATE AGENT?
  1. Blast radius: if the PaymentRequestAgent is compromised, the attacker
     can request tokens but never sees raw keys
  2. Auditability: all credential access goes through one choke point
  3. Rotation: update the key in vault once — agents get fresh tokens
     automatically when theirs expire
  4. Least privilege: each issued token carries explicit scopes

YOUR TASK — TODO 2:
  Implement CredentialsAgent._get_payment_token() below.

  What it should do:
    1. Build cache_key = f"token:{processor}" and get now = time.time()
    2. Check self._cache: if token not expired, return it with source="cache"
    3. Fetch raw key: raw_key = get_secret(f"{processor.upper()}_API_KEY")
       (wrap in try/except — fall back to a demo placeholder string if vault
        isn't configured, with a logger.warning)
    4. Build token_data dict: processor, scopes, iat, exp, key_fingerprint
       - key_fingerprint: hashlib.sha256(raw_key.encode()).hexdigest()[:16]
       - NEVER include raw_key in token_data
    5. Encode: token = base64.urlsafe_b64encode(json.dumps(token_data).encode()).decode()
    6. Cache: self._cache[cache_key] = (token, now + self._ttl)
    7. Audit log (logger.info): processor, requesting_agent, expires_in, correlation_id
    8. Return dict: token, source="vault", expires_in=self._ttl, scopes, processor

  See solution/shared/credentials_agent.py for the full implementation.
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
            return await self._get_payment_token(
                message.payload, message.sender, message.correlation_id
            )

        return {"error": f"Unknown intent '{message.intent}'. Supported: get_payment_token"}

    async def _get_payment_token(
        self,
        payload: dict,
        requesting_agent: str,
        correlation_id: str,
    ) -> dict:
        """
        Issue a short-lived scoped token for payment processor access.

        TODO 2: Implement this method.

        The processor name is in payload.get("processor", "stripe").

        Steps (in order):
          1. Build cache_key and check self._cache for a non-expired entry.
             Return {"token", "source": "cache", "expires_in", "scopes"} if found.
          2. Fetch the raw key from vault:
               from shared.secrets import get_secret
               raw_key = get_secret(f"{processor.upper()}_API_KEY")
             Wrap in try/except — if unavailable, use a demo placeholder string
             and log a warning.
          3. Build token_data = {processor, scopes, iat, exp, key_fingerprint}.
             key_fingerprint = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
             NEVER put raw_key in token_data.
          4. Encode to a base64 token string.
          5. Store in self._cache.
          6. Audit log (required for financial compliance):
             processor, requesting_agent, expires_in, correlation_id, token_fingerprint
          7. Return {"token", "source": "vault", "expires_in", "scopes", "processor"}.

        The scopes are: ["payment:create", "payment:read"]
        """
        processor = payload.get("processor", "stripe")

        # YOUR CODE HERE
        raise NotImplementedError("TODO 2: implement CredentialsAgent._get_payment_token()")

    def clear_cache(self) -> None:
        """Force re-fetch from vault on next request. Useful after key rotation."""
        self._cache.clear()
        logger.info("CredentialsAgent: token cache cleared")
