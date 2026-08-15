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

YOUR TASK — TODO 3:
  Implement run_payment_orchestrator() below.

  It follows the same two-call loop as Feature 7's run_agent().
  The difference: the "tools" here are sub-agents, not atomic functions.

  Steps:
    1. Build tools_registry = _make_orchestrator_registry(user_id)
    2. Build tool_schemas = [schema for _, schema in tools_registry.values()]
    3. Build messages list: system prompt + user message
    4. CALL 1: call_llm(messages, tools=tool_schemas, temperature=0.2)
    5. If no tool_calls: return {"result": content, "steps": [], "tools_used": []}
    6. Append the assistant's tool-call message to messages (see agent.py pattern)
    7. For each tool call:
         a. Look up fn from tools_registry
         b. Call: sub_result = await fn(**tool_args)
         c. steps.append({"tool": tool_name, "args": tool_args, "result": sub_result})
         d. Append tool message: role="tool", tool_call_id=..., content=json.dumps(sub_result.get("result", sub_result))
    8. CALL 2: call_llm(messages, temperature=0.3) — synthesize final answer
    9. Return {"result": second_response.content, "steps": steps, "tools_used": tools_used}

  Reference: shared/agent.py run_agent() — same pattern, one level up.
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
# Sub-agent runners (provided complete — no TODOs in these)
# ---------------------------------------------------------------------------

async def _run_payment_request_agent(user_id: str, message: str, processor: str = "stripe") -> dict:
    """Sub-agent: validate → get token → submit payment."""
    from shared.llm_client import call_llm
    from agent_bus import AgentBus, AgentMessage, agent_bus
    from payment_tools import (
        SUBMIT_PAYMENT_SCHEMA, VALIDATE_PAYMENT_SCHEMA,
        submit_payment, validate_payment,
    )

    REQUEST_TOKEN_SCHEMA: dict = {
        "type": "function",
        "function": {
            "name": "request_payment_token",
            "description": "Request a short-lived API token from the credentials agent. Call this before submit_payment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "processor": {"type": "string", "description": "Payment processor name (default: stripe)", "enum": ["stripe", "paypal"]},
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
        "validate_payment":      (validate_payment,      VALIDATE_PAYMENT_SCHEMA),
        "submit_payment":        (submit_payment,        SUBMIT_PAYMENT_SCHEMA),
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
        {"role": "user",   "content": message},
    ]
    return await _run_agent_loop(messages, tools_registry, tool_schemas, "payment_request_agent")


async def _run_payment_history_agent(user_id: str, message: str) -> dict:
    """Sub-agent: fetch and filter transaction history."""
    from shared.llm_client import call_llm
    from payment_tools import (
        FILTER_TRANSACTIONS_SCHEMA, GET_TRANSACTION_HISTORY_SCHEMA,
        filter_transactions_by_date, get_transaction_history,
    )

    tools_registry: dict[str, Any] = {
        "get_transaction_history":    (get_transaction_history,    GET_TRANSACTION_HISTORY_SCHEMA),
        "filter_transactions_by_date": (filter_transactions_by_date, FILTER_TRANSACTIONS_SCHEMA),
    }
    tool_schemas = [schema for _, schema in tools_registry.values()]

    sub_system_prompt = f"""You are a payment history agent. User ID: {user_id}.
Retrieve and summarize the user's payment history.
Use filter_transactions_by_date only when the user specifies a date range.
Always call get_transaction_history first.
"""
    messages: list[dict] = [
        {"role": "system", "content": sub_system_prompt},
        {"role": "user",   "content": message},
    ]
    return await _run_agent_loop(messages, tools_registry, tool_schemas, "payment_history_agent")


async def _run_agent_loop(
    messages: list[dict],
    tools_registry: dict[str, Any],
    tool_schemas: list[dict],
    agent_name: str,
) -> dict:
    """Two-call agent loop (Feature 7 pattern) for sub-agents."""
    import inspect
    from shared.llm_client import call_llm

    first_response = await call_llm(messages=messages, tools=tool_schemas, temperature=0.2, max_tokens=1500)
    steps: list[dict] = []
    tools_used: list[str] = []

    if not first_response.tool_calls:
        return {"result": first_response.content or "", "steps": [], "tools_used": []}

    messages.append({
        "role": "assistant",
        "content": first_response.content,
        "tool_calls": [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}}
            for tc in first_response.tool_calls
        ],
    })

    for tc in first_response.tool_calls:
        tool_name, tool_args, tool_call_id = tc["name"], tc["arguments"], tc["id"]
        fn, _ = tools_registry.get(tool_name, (None, None))
        if fn is None:
            tool_result = {"error": f"Unknown tool '{tool_name}'"}
        else:
            try:
                tool_result = await fn(**tool_args) if inspect.iscoroutinefunction(fn) else fn(**tool_args)
            except Exception as exc:
                tool_result = {"error": f"{tool_name} failed: {exc}"}

        steps.append({"tool": tool_name, "args": tool_args, "result": tool_result})
        tools_used.append(tool_name)
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": json.dumps(tool_result)})

    second_response = await call_llm(messages=messages, temperature=0.3, max_tokens=500)
    return {"result": second_response.content or "", "steps": steps, "tools_used": tools_used}


# ---------------------------------------------------------------------------
# Orchestrator tool schemas (provided complete)
# ---------------------------------------------------------------------------

PAYMENT_REQUEST_AGENT_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "payment_request_agent",
        "description": "Delegate a payment request to the payment request sub-agent. Use for: sending money, making payments.",
        "parameters": {
            "type": "object",
            "properties": {
                "message":   {"type": "string", "description": "The payment instruction (include amount and recipient)"},
                "processor": {"type": "string", "description": "Payment processor (default: stripe)", "enum": ["stripe", "paypal"]},
            },
            "required": ["message"],
        },
    },
}

PAYMENT_HISTORY_AGENT_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "payment_history_agent",
        "description": "Delegate a history query to the payment history sub-agent. Use for: past transactions, payment history.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The history query in natural language"},
            },
            "required": ["message"],
        },
    },
}


def _make_orchestrator_registry(user_id: str) -> dict[str, Any]:
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

    TODO 3: Implement this function.

    It follows the same two-call pattern as Feature 7's run_agent()
    in shared/agent.py — but the 'tools' are sub-agents, not atomic functions.

    Steps:
      1. tools_registry = _make_orchestrator_registry(user_id)
      2. tool_schemas = [schema for _, schema in tools_registry.values()]
      3. messages = [system_prompt, user_message]
      4. CALL 1: first_response = await call_llm(messages, tools=tool_schemas, temperature=0.2)
      5. If not first_response.tool_calls: return direct answer
      6. Append assistant tool-call message to messages
      7. For each tc in first_response.tool_calls:
           fn, _ = tools_registry[tc["name"]]
           sub_result = await fn(**tc["arguments"])
           Append tool message with role="tool", content=json.dumps(sub_result.get("result", sub_result))
      8. CALL 2: second_response = await call_llm(messages, temperature=0.3)
      9. Return {result, steps, tools_used}

    Returns:
        {"result": str, "steps": list, "tools_used": list}
    """
    from shared.llm_client import call_llm

    # YOUR CODE HERE
    raise NotImplementedError("TODO 3: implement run_payment_orchestrator()")
