# Bonus Feature 13: Agent Orchestration

**BONUS MODULE** — Not part of the core 12-feature arc. Standalone folder at `bonus/feature-13-agent-orchestration/`.

Builds on Features 7–9 (agents + MCP) and PROMPT 0.5.1 (secrets vault). Uses a P2P payment system as the worked example throughout.

This module answers two questions the community keeps asking:
1. "How do I orchestrate a multi-agent system for a domain like P2P payments?"
2. "How does one agent securely ask another for credentials — without raw API keys ever touching a context window?"

---

## Concepts Covered

| Concept | What you build |
|---|---|
| **Agents-as-tools** | The orchestrator pattern: treating sub-agents as tools in TOOLS_REGISTRY |
| **A2A communication** | In-process AgentBus for message passing, inspired by Google's A2A protocol |
| **Zero-trust credentials** | CredentialsAgent — the only component that reads from vault |
| **Short-lived tokens** | Scoped tokens with TTL, never raw API keys in context |
| **Audit logging** | Every credential request logged with requesting agent's identity |
| **Token caching** | Avoid vault rate limits across payment requests |

---

## Architecture

```
User message
    │
    ▼
PaymentOrchestrator
    │  LLM decides: which sub-agent?
    │
    ├── payment_request_agent (tool)
    │       │  sub-agent loop
    │       │
    │       ├── validate_payment()         ← pure function
    │       ├── request_payment_token()    ← AgentBus → CredentialsAgent
    │       │       │
    │       │       └── vault lookup (ONLY agent that does this)
    │       │           returns: short-lived scoped token
    │       │
    │       └── submit_payment(token=...)  ← pure function, never sees vault
    │
    └── payment_history_agent (tool)
            │  sub-agent loop
            ├── get_transaction_history()
            └── filter_transactions_by_date()
```

The orchestrator never directly executes payment logic. It delegates.
Each sub-agent never fetches credentials. It requests a token.
Only `CredentialsAgent` reads from vault.

---

## Your Task

Three TODOs, one in each file:

### TODO 1 — `starter/shared/agent_bus.py`: `AgentBus.request()`

The message bus dispatcher. Steps:
1. Look up the handler for `message.recipient`
2. If not found: `raise ValueError` listing available agents
3. Log the dispatch (correlation_id, sender, recipient, intent)
4. `result = await handler(message)`
5. Log the response
6. Return `result`

### TODO 2 — `starter/shared/credentials_agent.py`: `CredentialsAgent._get_payment_token()`

The vault interface. Steps:
1. Check token cache (`self._cache`) — return cached token if not expired
2. `raw_key = get_secret(f"{processor.upper()}_API_KEY")`
3. Build `token_data` with scopes, iat, exp, and `key_fingerprint` (SHA256 of raw_key, first 16 chars) — **NEVER include raw_key**
4. Encode to base64 token string
5. Store in cache
6. Audit log (processor, requesting_agent, correlation_id)
7. Return `{"token", "source": "vault", "expires_in", "scopes", "processor"}`

### TODO 3 — `starter/shared/payment_orchestrator.py`: `run_payment_orchestrator()`

The two-call loop. Same pattern as Feature 7's `run_agent()`, one level up:
1. Build `tools_registry = _make_orchestrator_registry(user_id)`
2. Build `tool_schemas`
3. `messages = [system_prompt, user_message]`
4. **Call 1**: `call_llm(messages, tools=tool_schemas, temperature=0.2)`
5. If no tool_calls → return direct answer
6. Execute each tool call (each is a sub-agent — `await fn(**args)`)
7. Append tool result messages
8. **Call 2**: `call_llm(messages, temperature=0.3)` — synthesize answer
9. Return `{result, steps, tools_used}`

---

## Test the Full Flow

```bash
cd bonus/feature-13-agent-orchestration/starter/
uvicorn main:app --reload --port 8001
```

**Send a payment:**
```bash
curl -X POST http://localhost:8001/api/payment/execute \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_123", "message": "Send $25 to user_456 for coffee"}'
```

Expected flow:
1. Orchestrator → LLM decides to call `payment_request_agent`
2. `payment_request_agent` → LLM decides to call `validate_payment`
3. `payment_request_agent` → LLM calls `request_payment_token` → AgentBus → CredentialsAgent → vault → cached token
4. `payment_request_agent` → LLM calls `submit_payment(token=...)`
5. Transaction ID returned

**Check history:**
```bash
curl -X POST http://localhost:8001/api/payment/history \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_123", "message": "Show my last 3 transactions"}'
```

---

## Design Decisions

### Why CredentialsAgent is isolated

If any other agent were allowed to call `get_secret()`:
- A prompt injection attack against the payment agent could exfiltrate the raw API key
- Every agent's logs would contain key material
- You'd need to rotate keys everywhere if any agent was compromised

With CredentialsAgent isolation: the attacker can request a token (which expires), but never sees the raw key. The blast radius of any individual agent being compromised is contained.

### Why tokens, not keys

A scoped, short-lived token has:
- **Minimum privileges**: only `["payment:create", "payment:read"]`, not admin
- **Expiry**: the token is useless after `cache_ttl_seconds`
- **Revocability**: revoke the token without changing the underlying key
- **Auditability**: the token fingerprint links audit logs to the specific issuance

### Why AgentBus logs every message

In financial systems, every action must be traceable. If a customer disputes a payment, you need to reconstruct exactly:
- Which agent requested credentials
- When
- What the correlation_id was
- Whether the request succeeded

The AgentBus provides this audit trail automatically — you don't rely on individual agents to log their own credential requests.

### Why the orchestrator's system prompt has a security rule

```
SECURITY RULE (non-negotiable):
  Never ask for or handle API keys, secrets, or credentials directly.
```

This is an LLM-level guardrail — it makes the model less likely to generate tool calls that bypass CredentialsAgent. It's paired with code-level enforcement: no tool in the orchestrator's registry can call `get_secret()` directly. Defense in depth.

---

## Google A2A Protocol Bridge

Our AgentBus is an in-process, same-runtime implementation of the core A2A concept.

Google's A2A protocol (April 2025, now under Linux Foundation) extends this to cross-process and cross-organization communication:

| | AgentBus (this module) | Google A2A |
|---|---|---|
| Transport | Python async call | HTTP + JSON-RPC 2.0 |
| Discovery | `register()` at startup | Agent Card at `.well-known/agent.json` |
| Auth | Same-process trust | OAuth 2.0 |
| Correlation | `correlation_id` UUID | Task ID |
| Message | `AgentMessage` dataclass | `Task` JSON object |
| Result | `dict` | `Artifact` |

**Upgrade path:**

```python
# This module (in-process)
result = await agent_bus.request(AgentMessage(
    sender="payment_request_agent",
    recipient="credentials_agent",
    intent="get_payment_token",
    payload={"processor": "stripe"},
))

# Google A2A (cross-process, cross-org)
# pip install a2a-sdk
from a2a.client import A2AClient
client = A2AClient("https://credentials-agent.example.com")
task = await client.send_task({
    "type": "get_payment_token",
    "input": {"processor": "stripe"},
})
result = task.artifacts[0].content
```

**When to use A2A instead of AgentBus:**
- Agents run in different containers or cloud accounts
- Agents are built with different frameworks (LangGraph + CrewAI)
- You need to delegate to agents operated by another company
- You need OAuth-level authentication between agents

---

## Vault Integration

This module uses `shared/secrets.py` from PROMPT 0.5.1. Configure your secrets provider in `.env`:

```bash
# Option A: .env file (development)
SECRETS_PROVIDER=env
STRIPE_API_KEY=sk_test_your_key_here

# Option B: Infisical vault (staging/production)
SECRETS_PROVIDER=infisical
INFISICAL_CLIENT_ID=your_machine_id
INFISICAL_CLIENT_SECRET=your_machine_secret
INFISICAL_PROJECT_ID=your_project_id
# Add STRIPE_API_KEY to Infisical, not to .env

# Option C: Doppler (CI/CD friendly)
SECRETS_PROVIDER=doppler
# Run: doppler run -- uvicorn main:app
# Add STRIPE_API_KEY to Doppler
```

**Production upgrade — HashiCorp Vault dynamic secrets:**

HashiCorp Vault can generate a NEW payment processor key per request, with auto-expiry:

```python
# Instead of: raw_key = get_secret("STRIPE_API_KEY")
# Use:
import hvac
client = hvac.Client(url="https://vault.internal")
creds = client.secrets.database.generate_credentials("stripe-payments-role")
raw_key = creds["data"]["api_key"]  # expires automatically after TTL
```

This means no key is ever reused across agents or sessions — each payment gets a fresh, single-use credential.
