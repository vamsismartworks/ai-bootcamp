"""
In-process Agent-to-Agent (A2A) message bus for Bonus Feature 13.

AGENT COMMUNICATION PATTERN:
  This AgentBus implements local (in-process) A2A-inspired messaging.
  It lets one agent send a structured message to another and await a response,
  with full audit logging of every request-response pair.

  Google's A2A protocol (April 2025, Linux Foundation) extends this concept
  to work ACROSS organizations, frameworks, and clouds via HTTP + JSON-RPC 2.0
  + OAuth 2.0. See: pip install a2a-sdk

  Core concepts shared between AgentBus and Google A2A:

  | Concept          | AgentBus (this file)        | Google A2A           |
  |------------------|-----------------------------|----------------------|
  | Message envelope | AgentMessage dataclass      | Task JSON object     |
  | Agent identity   | string name in registry     | Agent Card (JSON)    |
  | Agent discovery  | register() call at startup  | .well-known endpoint |
  | Transport        | Python async function call  | HTTP + JSON-RPC 2.0  |
  | Authentication   | same-process trust          | OAuth 2.0            |
  | Correlation      | correlation_id UUID         | Task ID              |
  | Result           | dict                        | Artifact             |

  When to upgrade to Google A2A:
  - Your agents run in different processes or containers
  - Your agents are written in different frameworks (LangGraph + CrewAI)
  - You need to delegate to agents operated by another organization
  - You need OAuth-level authentication between agents

AUDIT TRAIL:
  Every message and response is logged with correlation_id. In a financial
  system, this log is your compliance record — it proves which agent requested
  credentials, when, and what the response was. Never disable this logging.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    """
    Structured envelope for inter-agent communication.

    Fields mirror the core fields in Google A2A's Task object:
      sender         → task.metadata["sender_agent"]
      recipient      → routing key (which agent receives this)
      intent         → task.type (what kind of work is requested)
      payload        → task.input (the data for the request)
      correlation_id → task.id (trace request ↔ response)
    """
    sender: str
    recipient: str
    intent: str
    payload: dict
    correlation_id: str = field(default_factory=lambda: str(uuid4()))


class AgentBus:
    """
    In-process agent message bus.

    Each registered agent handles messages sent to its name.
    Every message-response pair is logged for auditing — essential
    in financial systems where credential requests must be traceable.

    Production upgrade path: replace with Redis pub/sub, AWS SQS,
    RabbitMQ, or Google's A2A protocol (pip install a2a-sdk) for
    cross-process or cross-organization agent communication.

    Usage:
        bus = AgentBus()
        bus.register("credentials_agent", cred_agent.handle)

        result = await bus.request(AgentMessage(
            sender="payment_request_agent",
            recipient="credentials_agent",
            intent="get_payment_token",
            payload={"processor": "stripe"},
        ))
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable] = {}

    def register(self, agent_name: str, handler: Callable) -> None:
        """Register an agent to handle messages sent to agent_name."""
        self._handlers[agent_name] = handler
        logger.info("AgentBus: registered agent '%s'", agent_name)

    async def request(self, message: AgentMessage) -> dict:
        """
        Send a message to the named agent and await its response.

        Logs the message before dispatch and the outcome after — every
        credential request, every inter-agent call, appears in the log
        with a correlation_id that links the request to the response.

        Raises:
            ValueError: if no agent is registered for message.recipient
        """
        handler = self._handlers.get(message.recipient)
        if not handler:
            registered = list(self._handlers.keys())
            raise ValueError(
                f"No agent registered for '{message.recipient}'. "
                f"Registered agents: {registered}. "
                f"Call agent_bus.register('{message.recipient}', handler) at startup."
            )

        logger.info(
            "AgentBus dispatch  [%s] %s → %s  intent=%s",
            message.correlation_id[:8],
            message.sender,
            message.recipient,
            message.intent,
        )

        result = await handler(message)

        logger.info(
            "AgentBus response  [%s] success=%s",
            message.correlation_id[:8],
            "error" not in result,
        )

        return result

    @property
    def registered_agents(self) -> list[str]:
        return list(self._handlers.keys())


# ---------------------------------------------------------------------------
# Singleton bus — shared across all agents registered at startup.
#
# In multi-process deployments: replace this with a message queue client
# (Redis pub/sub, RabbitMQ) or the Google A2A SDK.
# ---------------------------------------------------------------------------
agent_bus = AgentBus()
