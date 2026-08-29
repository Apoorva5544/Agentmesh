from fastapi import APIRouter, HTTPException, Request

from app.dependencies import container

router = APIRouter(prefix="/v1", tags=["runs"])


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    await container.auth.require_tenant(request)
    run = await container.db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    steps = await container.db.get_steps(run_id)
    usage_events = await container.db.get_usage_for_run(run_id)
    total_cost_inr = sum(float(s.get("cost_inr") or 0.0) for s in steps)
    return {
        "run": dict(run),
        "steps": [dict(s) for s in steps],
        "usage_events": [dict(e) for e in usage_events],
        "total_cost_inr": round(total_cost_inr, 4),
        "step_count": len(steps),
    }