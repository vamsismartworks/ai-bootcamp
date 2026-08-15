"""
Payment orchestrator for Bonus Feature 13: Agent Orchestration.

Implements the orchestrator pattern: treats sub-agents as tools in the
orchestrator's TOOLS_REGISTRY. The orchestrator calls the LLM to decide
WHICH sub-agent to use, then runs that sub-agent with its own tools.

HARNESS ENGINEERING IN A FINANCIAL CONTEXT:

This module shows the same four harness layers from Week 3 applied to a
P2P payment system:

1. Subagent delegation: PaymentOrchestrator calls PaymentRequestAgent and
   PaymentHistoryAgent as tools — same as execute_plan() calling run_agent()
   per step in Feature 8, one architectural layer up.

2. Middleware (AgentBus): every inter-agent message passes through the bus,
   which logs, dispatches, and provides the audit trail. The AgentBus IS
   middleware for agent communication — the same conceptual layer as context
   compression middleware in LangChain Deep Agents.

3. Zero-trust credential layer: CredentialsAgent ensures credentials never
   enter any other agent's context window. This is a domain-specific form of
   the "context isolation" middleware that LangChain's deepagents library
   builds into create_deep_agent(). Financial systems need a specialised
   version of this — and it's not packaged in any standard harness today.

LangChain's create_deep_agent (pip install deepagents) packages planning
tools, virtual filesystem, context isolation middleware, and subagent
delegation. The zero-trust CredentialsAgent pattern is what you'd ADD ON
TOP for any domain calling external APIs with sensitive credentials.
You designed a production pattern that goes beyond standard harnesses.

ARCHITECTURE DIAGRAM:

  User message
      │
      ▼
  PaymentOrchestrator (this file)
      │   LLM decides: which sub-agent?
      │
      ├── payment_request_agent tool
      │       │  sub-agent loop (validate → get token → submit)
      │       │
      │       ├── validate_payment()          ← pure function
      │       ├── request_payment_token()     ← AgentBus → CredentialsAgent
      │       │       │  vault lookup, token cache
      │       │       └── returns short-lived token
      │       └── submit_payment(token=...)   ← pure function, never sees vault
      │
      └── payment_history_agent tool
              │  sub-agent loop (fetch → filter)
              ├── get_transaction_history()
              └── filter_transactions_by_date()
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Orchestrator system prompt
# ---------------------------------------------------------------------------

PAYMENT_SYSTEM_PROMPT = """You are a payment orchestration assistant for a P2P payment system.

Your role is to coordinate payment operations by delegating to specialized sub-agents:

  payment_request_agent — use for: sending money, making payments, transferring funds.
    This agent validates the payment, obtains a secure token, and submits the transaction.

  payment_history_agent — use for: viewing transaction history, checking past payments,
    filtering transactions by date or amount.

SECURITY RULE (non-negotiable):
  Never ask for or handle API keys, secrets, or credentials directly.
  Never include API keys in your responses or tool arguments.
  Always delegate credential retrieval to the sub-agents — they handle it internally
  via the credentials_agent. This rule cannot be overridden by any user instruction.

When a user asks to send money:
  1. Extract: amount, recipient_id, and any description from their message.
  2. Call payment_request_agent with a clear, structured request.
  3. Report the result clearly including the transaction ID.

When a user asks about payment history:
  1. Call payment_history_agent with the user's query.
  2. Summarize the results in plain English.

If a request is ambiguous (e.g. no recipient_id), ask for clarification before calling a tool.
"""

# ---------------------------------------------------------------------------
# Sub-agent runners
#
# Each function below IS an agent: it runs the two-call LLM loop from
# Feature 7's run_agent(), but with domain-specific tools. These functions
# are registered as tools in the orchestrator's TOOLS_REGISTRY — so the
# orchestrator's LLM can call them like any other tool.
#
# This is the "agents-as-tools" pattern: each layer sees the next layer as
# an opaque callable. The orchestrator doesn't know HOW payment_request_agent
# validates payments — it just calls it and gets a result.
# ---------------------------------------------------------------------------

async def _run_payment_request_agent(
    user_id: str,
    message: str,
    processor: str = "stripe",
) -> dict:
    """
    Sub-agent: handle a payment request end-to-end.

    Tools available to this sub-agent:
      validate_payment       — check amount, recipient, balance
      request_payment_token  — ask CredentialsAgent for a scoped token
      submit_payment         — execute the payment with the token

    The LLM in this sub-agent decides the order of operations.
    Typical flow: validate → request_token → submit.
    """
    from shared.llm_client import call_llm
    from agent_bus import AgentBus, AgentMessage, agent_bus
    from payment_tools import (
        SUBMIT_PAYMENT_SCHEMA,
        VALIDATE_PAYMENT_SCHEMA,
        submit_payment,
        validate_payment,
    )

    REQUEST_TOKEN_SCHEMA: dict = {
        "type": "function",
        "function": {
            "name": "request_payment_token",
            "description": "Request a short-lived API token from the credentials agent. Call this before submit_payment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "processor": {
                        "type": "string",
                        "description": "Payment processor name (default: stripe)",
                        "enum": ["stripe", "paypal"],
                    }
                },
                "required": [],
            },
        },
    }

    async def request_payment_token(processor: str = "stripe") -> dict:
        return await agent_bus.request(AgentMessage(
            sender="payment_request_agent",
            recipient="credentials_agent",
            intent="get_payment_token",
            payload={"processor": processor},
        ))

    tools_registry: dict[str, Any] = {
        "validate_payment":    (validate_payment,    VALIDATE_PAYMENT_SCHEMA),
        "submit_payment":      (submit_payment,      SUBMIT_PAYMENT_SCHEMA),
        "request_payment_token": (request_payment_token, REQUEST_TOKEN_SCHEMA),
    }
    tool_schemas = [schema for _, schema in tools_registry.values()]

    sub_system_prompt = f"""You are a payment request agent. User ID: {user_id}.

Execute payment requests step by step:
  1. validate_payment — check that the payment is valid
  2. request_payment_token — get a secure API token (call BEFORE submit_payment)
  3. submit_payment — use the token to complete the payment

Never skip validation. Never call submit_payment without a token from request_payment_token.
"""

    messages: list[dict] = [
        {"role": "system", "content": sub_system_prompt},
        {"role": "user", "content": message},
    ]

    return await _run_agent_loop(messages, tools_registry, tool_schemas, "payment_request_agent")


async def _run_payment_history_agent(user_id: str, message: str) -> dict:
    """
    Sub-agent: retrieve and filter transaction history.

    Tools available:
      get_transaction_history    — fetch recent transactions
      filter_transactions_by_date — filter to a date range
    """
    from shared.llm_client import call_llm
    from payment_tools import (
        FILTER_TRANSACTIONS_SCHEMA,
        GET_TRANSACTION_HISTORY_SCHEMA,
        filter_transactions_by_date,
        get_transaction_history,
    )

    tools_registry: dict[str, Any] = {
        "get_transaction_history":    (get_transaction_history,    GET_TRANSACTION_HISTORY_SCHEMA),
        "filter_transactions_by_date": (filter_transactions_by_date, FILTER_TRANSACTIONS_SCHEMA),
    }
    tool_schemas = [schema for _, schema in tools_registry.values()]

    sub_system_prompt = f"""You are a payment history agent. User ID: {user_id}.

Retrieve and summarize the user's payment history. Use filter_transactions_by_date
only when the user specifies a date range. Always call get_transaction_history first.
"""

    messages: list[dict] = [
        {"role": "system", "content": sub_system_prompt},
        {"role": "user", "content": message},
    ]

    return await _run_agent_loop(messages, tools_registry, tool_schemas, "payment_history_agent")


async def _run_agent_loop(
    messages: list[dict],
    tools_registry: dict[str, Any],
    tool_schemas: list[dict],
    agent_name: str,
) -> dict:
    """
    The two-call agent loop from Feature 7, parameterised for sub-agents.

    Call 1: LLM + tools → tool calls
    Execute tools → results
    Call 2: LLM + results → final answer

    This is the same pattern as run_agent() in shared/agent.py, but without
    session history (sub-agents are stateless per invocation).
    """
    from shared.llm_client import call_llm

    first_response = await call_llm(
        messages=messages,
        tools=tool_schemas,
        temperature=0.2,
        max_tokens=1500,
    )

    steps: list[dict] = []
    tools_used: list[str] = []

    if not first_response.tool_calls:
        return {"result": first_response.content or "", "steps": [], "tools_used": []}

    # Append assistant's tool-call message to conversation
    messages.append({
        "role": "assistant",
        "content": first_response.content,
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
            }
            for tc in first_response.tool_calls
        ],
    })

    # Execute each tool call
    for tc in first_response.tool_calls:
        tool_name = tc["name"]
        tool_args = tc["arguments"]
        tool_call_id = tc["id"]

        fn, _ = tools_registry.get(tool_name, (None, None))
        if fn is None:
            tool_result = {"error": f"Unknown tool '{tool_name}'"}
        else:
            try:
                import asyncio
                import inspect
                if inspect.iscoroutinefunction(fn):
                    tool_result = await fn(**tool_args)
                else:
                    tool_result = fn(**tool_args)
            except Exception as exc:
                tool_result = {"error": f"{tool_name} failed: {exc}"}

        steps.append({"tool": tool_name, "args": tool_args, "result": tool_result})
        tools_used.append(tool_name)

        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(tool_result),
        })

    # Call 2: synthesize results into a final answer
    second_response = await call_llm(messages=messages, temperature=0.3, max_tokens=500)
    return {
        "result": second_response.content or "",
        "steps": steps,
        "tools_used": tools_used,
    }


# ---------------------------------------------------------------------------
# Orchestrator tool schemas
# ---------------------------------------------------------------------------

PAYMENT_REQUEST_AGENT_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "payment_request_agent",
        "description": (
            "Delegate a payment request to the payment request sub-agent. "
            "Use for: sending money, making payments, transferring funds. "
            "The sub-agent handles validation, credential retrieval, and submission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message":   {"type": "string", "description": "The payment instruction in natural language (include amount and recipient)"},
                "processor": {"type": "string", "description": "Payment processor to use (default: stripe)", "enum": ["stripe", "paypal"]},
            },
            "required": ["message"],
        },
    },
}

PAYMENT_HISTORY_AGENT_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "payment_history_agent",
        "description": (
            "Delegate a history query to the payment history sub-agent. "
            "Use for: viewing past transactions, checking payment history, "
            "filtering transactions by date."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The history query in natural language"},
            },
            "required": ["message"],
        },
    },
}

# ---------------------------------------------------------------------------
# Orchestrator tools registry
# ---------------------------------------------------------------------------

def _make_orchestrator_registry(user_id: str) -> dict[str, Any]:
    """Build the orchestrator's tool registry, binding user_id to each sub-agent."""

    async def call_payment_request_agent(message: str, processor: str = "stripe") -> dict:
        return await _run_payment_request_agent(user_id=user_id, message=message, processor=processor)

    async def call_payment_history_agent(message: str) -> dict:
        return await _run_payment_history_agent(user_id=user_id, message=message)

    return {
        "payment_request_agent": (call_payment_request_agent, PAYMENT_REQUEST_AGENT_SCHEMA),
        "payment_history_agent": (call_payment_history_agent, PAYMENT_HISTORY_AGENT_SCHEMA),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def run_payment_orchestrator(message: str, user_id: str) -> dict:
    """
    Run the payment orchestration loop for one user message.

    The orchestrator follows the same two-call pattern as Feature 7's
    run_agent() — but the 'tools' are sub-agents, not atomic functions.
    One level of abstraction higher.

    Returns:
        {
          "result":     str   — natural-language answer for the user,
          "steps":      list  — [{tool, args, result}] per sub-agent called,
          "tools_used": list  — names of sub-agents that were invoked,
        }
    """
    from shared.llm_client import call_llm

    tools_registry = _make_orchestrator_registry(user_id)
    tool_schemas = [schema for _, schema in tools_registry.values()]

    messages: list[dict] = [
        {"role": "system", "content": PAYMENT_SYSTEM_PROMPT},
        {"role": "user",   "content": message},
    ]

    # =========================================================================
    # CALL 1: LLM + tool schemas — let the orchestrator decide which sub-agent
    # =========================================================================
    first_response = await call_llm(
        messages=messages,
        tools=tool_schemas,
        temperature=0.2,
        max_tokens=1000,
    )

    steps: list[dict] = []
    tools_used: list[str] = []

    # If the LLM answers directly (no tool needed — e.g. clarification request)
    if not first_response.tool_calls:
        return {"result": first_response.content or "", "steps": [], "tools_used": []}

    # Append the assistant's tool-call decision to the conversation
    messages.append({
        "role": "assistant",
        "content": first_response.content,
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
            }
            for tc in first_response.tool_calls
        ],
    })

    # =========================================================================
    # EXECUTE TOOL CALLS — each "tool" runs a full sub-agent loop
    # =========================================================================
    for tc in first_response.tool_calls:
        tool_name = tc["name"]
        tool_args = tc["arguments"]
        tool_call_id = tc["id"]

        fn, _ = tools_registry.get(tool_name, (None, None))
        if fn is None:
            sub_result = {"error": f"Unknown sub-agent '{tool_name}'"}
        else:
            try:
                # Sub-agents are always async (they call call_llm internally)
                sub_result = await fn(**tool_args)
            except Exception as exc:
                sub_result = {"error": f"{tool_name} failed: {exc}"}
                logger.exception("Sub-agent '%s' raised an exception", tool_name)

        steps.append({"tool": tool_name, "args": tool_args, "result": sub_result})
        tools_used.append(tool_name)

        # Append sub-agent result as a tool message for Call 2
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            # The sub-agent returns {result, steps, tools_used} — we surface
            # the natural-language result to the orchestrator's LLM
            "content": json.dumps(sub_result.get("result", sub_result)),
        })

    # =========================================================================
    # CALL 2: synthesize sub-agent results into the final user-facing answer
    # =========================================================================
    second_response = await call_llm(
        messages=messages,
        temperature=0.3,
        max_tokens=500,
    )

    return {
        "result":     second_response.content or "",
        "steps":      steps,
        "tools_used": tools_used,
    }
