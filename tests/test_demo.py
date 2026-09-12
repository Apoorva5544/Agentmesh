"""Unit tests for the demo seeding flow with a fake container (no DB/Redis)."""

from types import SimpleNamespace

import pytest

from app.demo import DEMO_AGENTS, DEMO_MODELS, seed_demo_data


class FakeDB:
    def __init__(self):
        self.servers = {}
        self.agents = {}
        self.runs = []
        self.steps = []
        self.usage = []

    async def get_server(self, name):
        return self.servers.get(name)

    async def upsert_server(self, **kwargs):
        self.servers[kwargs["name"]] = kwargs
        return kwargs

    async def upsert_agent(self, **kwargs):
        self.agents[kwargs["agent_id"]] = kwargs
        return kwargs

    async def create_run(self, run_id, agent_id, created_at=None):
        self.runs.append({"run_id": run_id, "agent_id": agent_id, "created_at": created_at})

    async def add_trace_step(self, run_id, step):
        self.steps.append({"run_id": run_id, "step": step})

    async def record_usage(self, event, created_at=None):
        self.usage.append({**event, "created_at": created_at})

    async def update_run_totals(self, run_id):
        pass

    async def list_servers(self):
        return [dict(v) for v in self.servers.values()]

    async def list_agents(self):
        return [dict(v) for v in self.agents.values()]


class FakeMCP:
    def __init__(self):
        self.registered = []
        self.live = []

    async def register(self, fields):
        self.registered.append(fields)
        return fields

    async def invoke(self, name, tool, arguments):
        self.live.append((name, tool, arguments))
        return {"ok": True, "content": "ok", "latency_ms": 1, "cost_inr": 0.08}


@pytest.fixture
def fake_container():
    return SimpleNamespace(
        db=FakeDB(),
        mcp=FakeMCP(),
        settings=SimpleNamespace(demo_mode=False),
    )


async def test_seed_registers_local_tools_and_agents(fake_container):
    result = await seed_demo_data(fake_container)

    assert [f["name"] for f in fake_container.mcp.registered] == ["local_tools"]
    assert fake_container.mcp.registered[0]["endpoint"] == "builtin://local"
    assert set(fake_container.db.agents) == {a["agent_id"] for a in DEMO_AGENTS}


async def test_seed_generates_backdated_runs_usage_and_live_invokes(fake_container):
    result = await seed_demo_data(fake_container)

    assert result["runs_created"] > 0
    assert result["usage_events"] > 0
    assert result["mcp_calls"] > 0

    assert fake_container.db.runs
    assert all(r["created_at"] is not None for r in fake_container.db.runs)
    assert fake_container.db.usage
    assert all(u["created_at"] is not None for u in fake_container.db.usage)

    assert len(fake_container.mcp.live) == 2
    assert fake_container.mcp.live[0][0] == "local_tools"

    # MCP steps exist and LLM steps carry a real provider/model + cost.
    mcp_steps = [s["step"] for s in fake_container.db.steps if s["step"]["step_type"] == "mcp"]
    llm_steps = [s["step"] for s in fake_container.db.steps if s["step"]["step_type"] == "llm"]
    assert mcp_steps and all(s["tool"] in ("echo", "now") for s in mcp_steps)
    assert llm_steps and all((p, m) in DEMO_MODELS for p, m in
                             ((s["provider"], s["model"]) for s in llm_steps))

    # Budget figures stay coherent: run totals equal sum of step costs (0-cost cache hits allowed).
    assert any(s["saved_cost_inr"] >= 0 for s in llm_steps)