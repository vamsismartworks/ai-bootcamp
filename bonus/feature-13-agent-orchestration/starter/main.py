"""
Bonus Feature 13: Agent Orchestration — starter

Your task is in the shared/ files, not here. This file is complete.

Fill in the three TODOs in:
  shared/agent_bus.py          — TODO 1: AgentBus.request()
  shared/credentials_agent.py  — TODO 2: CredentialsAgent._get_payment_token()
  shared/payment_orchestrator.py — TODO 3: run_payment_orchestrator()

Once you've implemented all three, run:
    cd bonus/feature-13-agent-orchestration/starter/
    uvicorn main:app --reload --port 8001

Test the full flow:
    curl -X POST http://localhost:8001/api/payment/execute \
      -H "Content-Type: application/json" \
      -d '{"user_id": "user_123", "message": "Send $25 to user_456 for coffee"}'
"""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))

_BONUS_SHARED = Path(__file__).resolve().parents[0] / "shared"
sys.path.insert(0, str(_BONUS_SHARED))

from shared.logging_config import setup_logging
from shared.provider_check import check_provider_config

from agent_bus import agent_bus
from credentials_agent import CredentialsAgent
from payment_orchestrator import run_payment_orchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await check_provider_config()
    credentials_agent = CredentialsAgent(cache_ttl_seconds=300)
    agent_bus.register("credentials_agent", credentials_agent.handle)
    yield


app = FastAPI(
    title="Payment Orchestration — Bonus Feature 13 (Starter)",
    description=(
        "Starter app — implement the 3 TODOs in shared/ to make this work.\n\n"
        "  TODO 1: shared/agent_bus.py         — AgentBus.request()\n"
        "  TODO 2: shared/credentials_agent.py — CredentialsAgent._get_payment_token()\n"
        "  TODO 3: shared/payment_orchestrator.py — run_payment_orchestrator()"
    ),
    version="13.0.0",
    lifespan=lifespan,
)


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


@app.post("/api/payment/execute", response_model=OrchestratorResponse)
async def payment_execute(request: PaymentExecuteRequest) -> OrchestratorResponse:
    """Execute a P2P payment via the orchestrator."""
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
    """Retrieve payment history via the orchestrator."""
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
