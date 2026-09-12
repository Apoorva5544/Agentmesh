from fastapi import APIRouter, HTTPException

from app.dependencies import container
from app.demo import seed_demo_data

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/status")
async def demo_status() -> dict:
    db = container.db
    agents = await db.list_agents()
    servers = await db.list_servers()
    totals = await db.summary()
    return {
        "seeded": totals["total_requests"] > 0,
        "requests": totals["total_requests"],
        "runs": len(totals["recent_runs"]),
        "agents": len(agents),
        "servers": len(servers),
    }


@router.post("/seed")
async def demo_seed() -> dict:
    totals = await container.db.summary()
    if totals["total_requests"] > 0 and not container.settings.demo_mode:
        raise HTTPException(
            status_code=409,
            detail="Demo data already present. Set DEMO_MODE=true to reseed.",
        )
    return await seed_demo_data(container)