import uuid

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.dependencies import container
from app.routers import admin, agents, chat, mcp, runs
from app.telemetry import setup_telemetry

app = FastAPI(title=container.settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(chat.router)
app.include_router(mcp.router)
app.include_router(agents.router)
app.include_router(admin.router)
app.include_router(runs.router)


@app.on_event("startup")
async def startup() -> None:
    await container.startup()
    setup_telemetry(app, container.settings)


@app.on_event("shutdown")
async def shutdown() -> None:
    await container.shutdown()


@app.get("/health")
async def health() -> dict[str, str]:
    db_ok = await container.db.healthcheck()
    return {"status": "ok" if db_ok else "degraded", "database": "ok" if db_ok else "unreachable"}


@app.get("/metrics")
async def metrics() -> dict:
    return await container.db.summary()


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    data = await container.db.summary()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"data": data, "active": "overview"},
    )


@app.get("/dashboard/runs/{run_id}", response_class=HTMLResponse)
async def run_detail(run_id: str, request: Request) -> HTMLResponse:
    run = await container.db.get_run(run_id)
    if run is None:
        return templates.TemplateResponse(
            request=request, name="not_found.html", context={"active": "overview"}, status_code=404
        )
    steps = await container.db.get_steps(run_id)
    return templates.TemplateResponse(
        request=request,
        name="run_detail.html",
        context={"run": dict(run), "steps": [dict(s) for s in steps], "active": "overview"},
    )


@app.get("/dashboard/agents", response_class=HTMLResponse)
async def agents_page(request: Request) -> HTMLResponse:
    rows = await container.db.list_agents()
    agents_view = []
    for agent in rows:
        verdict = await container.budgets.evaluate(agent["agent_id"])
        agents_view.append({**dict(agent), **dict(verdict.__dict__)})
    return templates.TemplateResponse(
        request=request,
        name="agents.html",
        context={"agents": agents_view, "active": "agents"},
    )


@app.get("/dashboard/mcp", response_class=HTMLResponse)
async def mcp_page(request: Request) -> HTMLResponse:
    servers = await container.mcp.list_servers()
    health = await container.mcp.health_all()
    health_by_name = {item["name"]: item for item in health}
    view = [{**server, **health_by_name.get(server["name"], {})} for server in servers]
    return templates.TemplateResponse(
        request=request,
        name="mcp.html",
        context={"servers": view, "active": "mcp"},
    )


@app.get("/dashboard/budgets", response_class=HTMLResponse)
async def budgets_page(request: Request) -> HTMLResponse:
    agents = await container.db.list_agents()
    alerts = await container.db.list_alerts()
    view = []
    for agent in agents:
        verdict = await container.budgets.evaluate(agent["agent_id"])
        view.append({**dict(agent), **dict(verdict.__dict__)})
    return templates.TemplateResponse(
        request=request,
        name="budgets.html",
        context={"agents": view, "alerts": [dict(a) for a in alerts], "active": "budgets"},
    )


@app.get("/dashboard/keys", response_class=HTMLResponse)
async def keys_page(request: Request) -> HTMLResponse:
    keys = await container.db.list_keys()
    return templates.TemplateResponse(
        request=request,
        name="keys.html",
        context={"keys": [dict(k) for k in keys], "active": "keys"},
    )


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": container.settings.app_name, "docs": "/docs", "dashboard": "/dashboard"}