from fastapi import APIRouter, Request

from app.dependencies import container
from app.schemas import AgentConfig, BudgetStatus, KeyCreateRequest, KeyCreateResponse

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.post("/keys", response_model=KeyCreateResponse)
async def create_key(payload: KeyCreateRequest, request: Request) -> KeyCreateResponse:
    await container.auth.require_admin(request)
    return KeyCreateResponse(**await container.auth.issue_key(payload.name))


@router.get("/keys")
async def list_keys(request: Request) -> list[dict]:
    await container.auth.require_admin(request)
    return [dict(r) for r in await container.db.list_keys()]


@router.post("/agents", response_model=AgentConfig)
async def upsert_agent(payload: AgentConfig, request: Request) -> AgentConfig:
    await container.auth.require_admin(request)
    await container.db.upsert_agent(
        agent_id=payload.agent_id,
        display_name=payload.display_name,
        budget_inr=payload.monthly_budget_inr,
        alert_thresholds=payload.alert_thresholds,
        hard_limit=payload.hard_limit,
        fallback_model=payload.fallback_model,
    )
    return payload


@router.get("/agents")
async def list_agents(request: Request) -> list[dict]:
    await container.auth.require_admin(request)
    return [dict(r) for r in await container.db.list_agents()]


@router.get("/budgets", response_model=list[BudgetStatus])
async def budget_status(request: Request) -> list[BudgetStatus]:
    await container.auth.require_admin(request)
    agents = await container.db.list_agents()
    statuses: list[BudgetStatus] = []
    for agent in agents:
        verdict = await container.budgets.evaluate(agent["agent_id"])
        statuses.append(
            BudgetStatus(
                agent_id=agent["agent_id"],
                monthly_spend_inr=verdict.spend_inr,
                monthly_budget_inr=verdict.budget_inr,
                ratio=verdict.ratio,
                status=verdict.status,  # type: ignore[arg-type]
                fallback_model=verdict.fallback_model,
            )
        )
    return statuses