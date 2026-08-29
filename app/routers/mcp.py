import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.dependencies import container
from app.mcp.client import MCPError
from app.schemas import MCPInvokeRequest, MCPInvokeResponse, MCPServerCreate

router = APIRouter(prefix="/v1/mcp", tags=["mcp"])


@router.post("/registry")
async def register_server(payload: MCPServerCreate, request: Request) -> dict:
    await container.auth.require_admin(request)
    return await container.mcp.register(payload.model_dump())


@router.get("/registry")
async def list_servers(request: Request) -> list[dict]:
    await container.auth.require_tenant(request)
    return await container.mcp.list_servers()


@router.delete("/registry/{name}")
async def delete_server(name: str, request: Request) -> dict:
    await container.auth.require_admin(request)
    await container.mcp.delete(name)
    return {"deleted": name}


@router.get("/health")
async def health_all(request: Request) -> list[dict]:
    await container.auth.require_tenant(request)
    return await container.mcp.health_all()


class HealthResponse(BaseModel):
    name: str
    ok: bool
    health: str
    error: str | None = None


@router.get("/health/{name}", response_model=HealthResponse)
async def health_one(name: str, request: Request) -> HealthResponse:
    await container.auth.require_tenant(request)
    return HealthResponse(**await container.mcp.health(name))


@router.post("/invoke", response_model=MCPInvokeResponse)
async def invoke_tool(payload: MCPInvokeRequest, request: Request) -> MCPInvokeResponse:
    await container.auth.require_tenant(request)
    agent_id = payload.agent_id or "default"
    run_id = payload.run_id or f"run_{agent_id[:24]}-{uuid.uuid4().hex[:10]}"
    await container.db.create_run(run_id, agent_id)

    started = time.perf_counter()
    try:
        result = await container.mcp.invoke(payload.server, payload.tool, payload.arguments)
    except MCPError as exc:
        result = {
            "ok": False,
            "server": payload.server,
            "tool": payload.tool,
            "content": "",
            "error": str(exc),
            "cost_inr": 0.0,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "retried": False,
        }

    step = {
        "step_no": 1,
        "step_type": "mcp",
        "provider": None,
        "model": None,
        "tool": payload.tool,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_inr": result.get("cost_inr", 0.0),
        "saved_cost_inr": 0.0,
        "latency_ms": result.get("latency_ms", 0),
        "cache_hit": False,
        "retried": bool(result.get("retried", False)),
        "error": result.get("error"),
    }
    await container.db.add_trace_step(run_id, step)
    await container.db.update_run_totals(run_id)

    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=step)

    return MCPInvokeResponse(
        ok=True,
        server=payload.server,
        tool=payload.tool,
        content=result.get("content", ""),
        error=None,
        cost_inr=result.get("cost_inr", 0.0),
        latency_ms=result.get("latency_ms", 0),
        retried=bool(result.get("retried", False)),
        run_id=run_id,
    )