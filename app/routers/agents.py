from fastapi import APIRouter, HTTPException, Request

from app.agents import AgentRunExecutor
from app.dependencies import container
from app.routers.chat import gateway
from app.schemas import AgentConfig, AgentRunRequest, AgentRunResponse

executor = AgentRunExecutor(gateway)
router = APIRouter(prefix="/v1/agents", tags=["agents"])


@router.post("")
async def create_agent(payload: AgentConfig, request: Request) -> dict:
    await container.auth.require_tenant(request)
    row = await container.db.upsert_agent(
        agent_id=payload.agent_id,
        display_name=payload.display_name or payload.agent_id,
        budget_inr=payload.monthly_budget_inr,
        alert_thresholds=payload.alert_thresholds,
        hard_limit=payload.hard_limit,
        fallback_model=payload.fallback_model,
    )
    return dict(row)


@router.post("/{agent_id}/run", response_model=AgentRunResponse)
async def run_agent(agent_id: str, payload: AgentRunRequest, request: Request) -> AgentRunResponse:
    await container.auth.require_tenant(request)
    payload.agent_id = agent_id
    try:
        return AgentRunResponse(**await executor.run(payload))
    except HTTPException as exc:
        raise exc


@router.get("/{agent_id}/traces")
async def agent_traces(agent_id: str, request: Request) -> dict:
    await container.auth.require_tenant(request)
    runs = await container.db.list_runs(agent_id, limit=50)
    return {"agent_id": agent_id, "runs": [dict(r) for r in runs]}