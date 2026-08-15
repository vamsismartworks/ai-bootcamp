# Agent Communication Guide

A practical guide to how agents communicate — from the simple in-process case in Feature 7 to the orchestrator pattern in Bonus Feature 13 to cross-organization protocols.

---

## Section 1: The Orchestrator Pattern (Agents-as-Tools)

In Feature 7 (`shared/agent.py`), the agent calls atomic tools:

```
User
  │
  ▼
run_agent()
  │  LLM + tool schemas
  │
  ├── check_availability()   ← Python function
  ├── create_ticket()        ← Python function
  └── lookup_info()          ← Python function
```

In Bonus Feature 13, the orchestrator calls *sub-agents* as tools:

```
User
  │
  ▼
run_payment_orchestrator()
  │  LLM + sub-agent schemas
  │
  ├── payment_request_agent()   ← runs its OWN agent loop
  │       ├── validate_payment()
  │       ├── request_payment_token()  → AgentBus → CredentialsAgent
  │       └── submit_payment()
  │
  └── payment_history_agent()   ← runs its OWN agent loop
          ├── get_transaction_history()
          └── filter_transactions_by_date()
```

**Key insight:** Each layer sees the next layer as an opaque callable. The orchestrator doesn't know HOW `payment_request_agent` validates payments — it just calls it and gets a result. This is the same separation of concerns as Feature 7's `run_agent()`, but one architectural level higher.

**TOOLS_REGISTRY comparison:**

```python
# Feature 7: atomic tools
TOOLS_REGISTRY = {
    "check_availability": (check_availability, CHECK_SCHEMA),
    "create_ticket":      (create_ticket,      TICKET_SCHEMA),
}

# Feature 13: sub-agents as tools
ORCHESTRATOR_TOOLS = {
    "payment_request_agent": (call_payment_request_agent, REQUEST_SCHEMA),
    "payment_history_agent": (call_payment_history_agent, HISTORY_SCHEMA),
}
```

The orchestrator code (`run_payment_orchestrator`) is nearly identical to Feature 7's `run_agent()` — same two-call loop. The difference is what's in the registry.

**Framework equivalent:**

```python
# LangChain equivalent: AgentExecutor with sub-chains as tools
from langchain.tools import Tool

payment_tool = Tool(
    name="payment_request_agent",
    func=payment_request_chain.run,  # a Chain is a callable
    description="Execute a P2P payment request",
)
orchestrator = initialize_agent([payment_tool, history_tool], llm, ...)
```

---

## Section 2: AgentMessage + AgentBus

The AgentBus (`shared/agent_bus.py`) provides structured message passing between agents in the same process.

**AgentMessage fields:**

```python
@dataclass
class AgentMessage:
    sender: str           # "payment_request_agent"
    recipient: str        # "credentials_agent"
    intent: str           # "get_payment_token"
    payload: dict         # {"processor": "stripe"}
    correlation_id: str   # UUID — links request ↔ response in logs
```

**AgentBus lifecycle:**

```python
# 1. Startup: register agents
agent_bus.register("credentials_agent", credentials_agent.handle)

# 2. At runtime: send a message
result = await agent_bus.request(AgentMessage(
    sender="payment_request_agent",
    recipient="credentials_agent",
    intent="get_payment_token",
    payload={"processor": "stripe"},
))

# 3. result = {"token": "...", "source": "vault", "expires_in": 300, ...}
```

**What the bus does:**

```
AgentBus.request(message)
    │
    ├── 1. Look up handler for message.recipient
    ├── 2. Log: "dispatch [abc123] payment_request_agent → credentials_agent  intent=get_payment_token"
    ├── 3. result = await handler(message)
    └── 4. Log: "response [abc123] success=True"
         Return result
```

The logging is what makes the AgentBus valuable beyond a plain function call — every request-response pair appears in your log with a `correlation_id` that links them.

**Production upgrade: Redis pub/sub**

```python
# AgentBus (in-process, synchronous)
result = await agent_bus.request(message)

# Redis pub/sub (cross-process, same organization)
import aioredis
redis = aioredis.from_url("redis://localhost")
await redis.publish(f"agent:{message.recipient}", message.to_json())
result = await redis.subscribe(f"result:{message.correlation_id}")
```

---

## Section 3: Zero-Trust Credentials Model

**The problem:**

If every agent can call `get_secret()` directly:

```
payment_agent → get_secret("STRIPE_API_KEY") → raw key in memory
payment_agent → LLM call (raw key might appear in context)
payment_agent → logs (raw key might appear in log line)
```

A prompt injection, logging misconfiguration, or memory dump could expose the raw key.

**The zero-trust solution:**

```
payment_agent → AgentBus → CredentialsAgent → get_secret() → token (not raw key)
payment_agent → LLM call (token in context — useless if leaked, expires in 5 min)
payment_agent → logs (token fingerprint only)
```

**Security diagram:**

```
                    ┌─────────────────────────────┐
                    │      Secrets Vault          │
                    │  (Infisical / Doppler / KMS)│
                    └──────────────┬──────────────┘
                                   │ get_secret()
                                   │ (ONLY this agent calls vault)
                    ┌──────────────▼──────────────┐
                    │      CredentialsAgent       │
                    │  - Token cache (5 min TTL)  │
                    │  - Audit log every request  │
                    │  - Returns scoped tokens    │
                    └──────────────┬──────────────┘
                                   │ short-lived token
                         ┌─────────┴──────────┐
                         │                    │
              ┌──────────▼───────┐  ┌─────────▼────────┐
              │ PaymentRequest   │  │ PaymentHistory   │
              │    Agent         │  │    Agent         │
              │ (sees token)     │  │ (no credentials) │
              └──────────────────┘  └──────────────────┘
```

**Token vs raw key:**

| Property | Raw API key | Scoped token |
|---|---|---|
| Expiry | Never (until manually rotated) | 5 minutes (TTL) |
| Scope | Full account access | `["payment:create", "payment:read"]` only |
| Revocability | Must rotate everywhere | Revoke without touching the underlying key |
| If leaked | Full account compromise | Useless after TTL |
| In logs | Security incident | Safe (only fingerprint logged) |

---

## Section 4: Google A2A Protocol

Google's Agent-to-Agent (A2A) protocol (April 2025, Linux Foundation) extends the AgentBus concept to work across processes, organizations, and frameworks.

**Core concepts:**

```
AgentBus (our implementation)     Google A2A (production cross-org)
─────────────────────────────     ─────────────────────────────────
AgentMessage dataclass            Task JSON-RPC object
register() at startup             .well-known/agent.json (Agent Card)
Python function call              HTTP POST to agent's task endpoint
Same-process trust                OAuth 2.0 (client credentials flow)
correlation_id UUID               Task ID
dict result                       Artifact
```

**Agent Card** (A2A's discovery mechanism):

```json
// https://credentials-agent.example.com/.well-known/agent.json
{
  "name": "CredentialsAgent",
  "description": "Issues short-lived payment processor tokens",
  "url": "https://credentials-agent.example.com",
  "capabilities": {
    "streaming": false,
    "pushNotifications": false
  },
  "skills": [
    {
      "id": "get_payment_token",
      "name": "Get Payment Token",
      "description": "Issue a scoped, short-lived token for a payment processor",
      "inputModes": ["application/json"],
      "outputModes": ["application/json"]
    }
  ]
}
```

Our tool schema (`REQUEST_TOKEN_SCHEMA`) maps directly to an A2A Agent Card skill.

**Upgrade example:**

```python
# Our AgentBus (in-process)
result = await agent_bus.request(AgentMessage(
    sender="payment_request_agent",
    recipient="credentials_agent",
    intent="get_payment_token",
    payload={"processor": "stripe"},
))

# Google A2A (cross-process, cross-org)
# pip install a2a-sdk
from a2a.client import A2AClient, SendTaskRequest

client = A2AClient("https://credentials-agent.example.com")
response = await client.send_task(SendTaskRequest(
    message={
        "role": "user",
        "parts": [{"text": json.dumps({"processor": "stripe"})}],
    }
))
result = json.loads(response.result.parts[0].text)
```

---

## Section 5: When to Use What

| Protocol | Use when |
|---|---|
| **Tool call** (Features 7–9) | Agent calls an API, database, or function in the same process |
| **MCP** (Feature 9) | Agent calls tools on an MCP server (can be separate process, different team's tools) |
| **AgentBus** (Bonus F13) | Agent delegates to another agent in the same process and organization |
| **Google A2A** | Agent delegates to another agent in a different process, container, or organization |

**Decision tree:**

```
Do you need to call a tool or an agent?
│
├── Tool (function, API, database)
│   │
│   ├── Your tool, your process → TOOLS_REGISTRY (Feature 7)
│   └── Another team's tool, separate server → MCP (Feature 9)
│
└── Agent (has its own LLM, its own tools, its own context)
    │
    ├── Same process, same organization → AgentBus (Bonus Feature 13)
    └── Different process or organization → Google A2A
```

**MCP vs A2A — the key difference:**

- **MCP** connects an agent to *tools* (databases, APIs, file systems). The LLM is in your agent; the tool is on the MCP server.
- **A2A** connects an *agent* to another *agent*. Both sides have their own LLM, their own reasoning, their own tools. You're delegating intelligence, not just functionality.

A payment validation function is a tool (use MCP or TOOLS_REGISTRY).  
A payment validation sub-agent that validates, checks fraud, calls compliance APIs, and makes a recommendation — that's an agent (use A2A or AgentBus).
