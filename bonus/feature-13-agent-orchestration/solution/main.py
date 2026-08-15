"""
Bonus Feature 13: Agent Orchestration — solution

Standalone FastAPI app with two payment endpoints.
Builds on the orchestrator pattern introduced in this bonus module.

New endpoints (bonus-only, not part of the core 12-feature arc):
  POST /api/payment/execute  — execute a P2P payment via the orchestrator
  POST /api/payment/history  — retrieve payment history via the orchestrator
  GET  /api/agents           — list registered agents on the AgentBus
  GET  /api/health           — health check

Run:
    cd bonus/feature-13-agent-orchestration/solution/
    uvicorn main:app --reload --port 8001

(Use port 8001 to avoid conflicting with the main app on 8000.)
"""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Add repo root → gives us shared.llm_client, shared.secrets, etc.
_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))

# Add the bonus feature's shared/ directory directly → gives us
# payment_tools, agent_bus, credentials_agent, payment_orchestrator
# as top-level imports (no namespace collision with the main shared/).
_BONUS_SHARED = Path(__file__).resolve().parents[1] / "shared"
sys.path.insert(0, str(_BONUS_SHARED))

from shared.logging_config import setup_logging
from shared.provider_check import check_provider_config

from agent_bus import agent_bus
from credentials_agent import CredentialsAgent
from payment_orchestrator import run_payment_orchestrator

# ---------------------------------------------------------------------------
# Lifespan: register agents on the bus at startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await check_provider_config()

    # CredentialsAgent: the ONLY agent that touches the secrets vault.
    # Registered first so sub-agents can call it immediately on their first
    # credential request.
    credentials_agent = CredentialsAgent(cache_ttl_seconds=300)
    agent_bus.register("credentials_agent", credentials_agent.handle)

    yield


app = FastAPI(
    title="Payment Orchestration — Bonus Feature 13",
    description=(
        "Multi-agent P2P payment system demonstrating:\n"
        "  - Agents-as-tools (orchestrator pattern)\n"
        "  - Agent-to-Agent communication via AgentBus\n"
        "  - Zero-trust CredentialsAgent (vault isolation)\n\n"
        "This is a BONUS module. The main 12-feature app runs on port 8000."
    ),
    version="13.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class PaymentExecuteRequest(BaseModel):
    user_id: str
    message: str

class PaymentHistoryRequest(BaseModel):
    user_id: str
    message: str = "Show my recent transactions"

class OrchestratorResponse(BaseModel):
    result: str
    tools_used: list[str]
    steps: list[dict]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/payment/execute", response_model=OrchestratorResponse)
async def payment_execute(request: PaymentExecuteRequest) -> OrchestratorResponse:
    """
    Execute a P2P payment via the orchestrator.

    The orchestrator decides which sub-agents to call (validation,
    credential retrieval, submission) and returns a natural-language
    confirmation with the transaction ID.

    Example request:
        {"user_id": "user_123", "message": "Send $25 to user_456 for coffee"}
    """
    if not request.user_id.strip():
        raise HTTPException(status_code=400, detail="user_id is required")
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    result = await run_payment_orchestrator(
        message=request.message,
        user_id=request.user_id,
    )
    return OrchestratorResponse(
        result=result["result"],
        tools_used=result["tools_used"],
        steps=result["steps"],
    )


@app.post("/api/payment/history", response_model=OrchestratorResponse)
async def payment_history(request: PaymentHistoryRequest) -> OrchestratorResponse:
    """
    Retrieve payment history via the orchestrator.

    The orchestrator delegates to the payment_history_agent sub-agent,
    which calls get_transaction_history and optionally filters by date.

    Example requests:
        {"user_id": "user_123", "message": "Show my last 5 transactions"}
        {"user_id": "user_123", "message": "Show payments from July 2026"}
    """
    if not request.user_id.strip():
        raise HTTPException(status_code=400, detail="user_id is required")

    result = await run_payment_orchestrator(
        message=request.message,
        user_id=request.user_id,
    )
    return OrchestratorResponse(
        result=result["result"],
        tools_used=result["tools_used"],
        steps=result["steps"],
    )


@app.get("/api/agents")
async def list_agents() -> dict:
    """List all agents currently registered on the AgentBus."""
    return {
        "registered_agents": agent_bus.registered_agents,
        "agent_count": len(agent_bus.registered_agents),
    }


@app.get("/api/health")
async def health() -> dict:
    from shared.config import settings
    return {
        "status": "ok",
        "version": "13.0.0",
        "provider": settings.llm_provider,
        "registered_agents": agent_bus.registered_agents,
    }
