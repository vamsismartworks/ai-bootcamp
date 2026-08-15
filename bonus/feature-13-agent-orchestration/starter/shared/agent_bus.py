"""
In-process Agent-to-Agent (A2A) message bus for Bonus Feature 13.

AGENT COMMUNICATION PATTERN:
  This AgentBus implements local (in-process) A2A-inspired messaging.
  Google's A2A protocol (April 2025, Linux Foundation) extends this concept
  to work ACROSS organizations, frameworks, and clouds via HTTP + JSON-RPC 2.0
  + OAuth 2.0. See: pip install a2a-sdk

  Core concepts shared between AgentBus and Google A2A:

  | Concept       | AgentBus (this file)      | Google A2A             |
  |---------------|---------------------------|------------------------|
  | Message       | AgentMessage dataclass    | Task JSON object       |
  | Identity      | string name               | Agent Card (JSON)      |
  | Discovery     | register() at startup     | .well-known endpoint   |
  | Transport     | Python async call         | HTTP + JSON-RPC 2.0    |
  | Auth          | same-process trust        | OAuth 2.0              |
  | Correlation   | correlation_id UUID       | Task ID                |
  | Result        | dict                      | Artifact               |

YOUR TASK — TODO 1:
  Implement AgentBus.request() below.

  What it should do:
    1. Look up the handler for message.recipient in self._handlers
    2. If no handler: raise ValueError listing available agents
    3. Log the dispatch (use logger.info — correlation_id, sender, recipient, intent)
    4. Call the handler: result = await handler(message)
    5. Log the response (success = "error" not in result)
    6. Return the result dict

  See solution/shared/agent_bus.py for the full implementation.
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

    Fields mirror the core fields in Google A2A's Task object.
    """
    sender: str
    recipient: str
    intent: str
    payload: dict
    correlation_id: str = field(default_factory=lambda: str(uuid4()))


class AgentBus:
    """
    In-process agent message bus.

    Production upgrade path: replace with Redis pub/sub, AWS SQS,
    RabbitMQ, or Google's A2A protocol (pip install a2a-sdk).
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

        TODO 1: Implement this method.

        Steps:
          1. Look up: handler = self._handlers.get(message.recipient)
          2. If handler is None: raise ValueError with a helpful message
             listing self._handlers.keys()
          3. Log the dispatch (correlation_id, sender, recipient, intent)
          4. Call: result = await handler(message)
          5. Log the response (success = "error" not in result)
          6. Return result

        Why logging matters: in financial systems, every credential request
        must be traceable. The correlation_id links the request log line to
        the response log line — you can find the full audit trail for any
        payment by searching for correlation_id in your log aggregator.
        """
        # YOUR CODE HERE
        raise NotImplementedError("TODO 1: implement AgentBus.request()")

    @property
    def registered_agents(self) -> list[str]:
        return list(self._handlers.keys())


# Singleton bus — shared across all agents in this process.
agent_bus = AgentBus()
