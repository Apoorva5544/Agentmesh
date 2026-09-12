"""Unit tests for the Gateway Console (sandbox) service with a fake container."""

from types import SimpleNamespace

import pytest

from app.gateway import BudgetBlocked
from app.routers.sandbox import SandboxService


class FakeGateway:
    def __init__(self, blocked=False):
        self.blocked = blocked
        self.last_payload = None

    async def complete(self, payload, identity="local"):
        self.last_payload = payload
        if self.blocked:
            verdict = SimpleNamespace(spend_inr=5200.0, budget_inr=5000.0)
            raise BudgetBlocked(verdict)
        return {
            "run_id": "run_support_bot-x",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "content": "A gateway.",
            "cache_hit": False,
            "estimated_cost_inr": 0.001234,
        }


class FakeExecutor:
    def __init__(self, fail=False):
        self.fail = fail
        self.last_req = None

    async def run(self, req):
        self.last_req = req
        if self.fail:
            raise RuntimeError("boom")
        return {"run_id": "run_support_bot-y", "agent_id": req.agent_id, "status": "completed", "steps": []}


class FakeDB:
    def __init__(self):
        self.runs = []
        self.steps = []
        self.totals = 0

    async def create_run(self, run_id, agent_id, created_at=None):
        self.runs.append(run_id)

    async def add_trace_step(self, run_id, step):
        self.steps.append({"run_id": run_id, "step": step})

    async def update_run_totals(self, run_id):
        self.totals += 1


class FakeMCP:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    async def invoke(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        return {"ok": self.ok, "content": "ok", "cost_inr": 0.08, "latency_ms": 5, "retried": False}


class FakeAuth:
    def __init__(self):
        self.issued = []

    async def issue_key(self, name):
        self.issued.append(name)
        return {"id": 1, "name": name, "key": "sk-xxxx", "key_prefix": "ab12"}


def make_service(blocked=False, mcp_ok=True, agent_fail=False):
    gateway = FakeGateway(blocked=blocked)
    executor = FakeExecutor(fail=agent_fail)
    db = FakeDB()
    mcp = FakeMCP(ok=mcp_ok)
    auth = FakeAuth()
    svc = SandboxService(gateway, executor, SimpleNamespace(db=db, mcp=mcp, auth=auth))
    return svc, gateway, executor, db, mcp, auth


async def test_chat_runs_through_real_gateway_request():
    svc, gateway, _, _, _, _ = make_service()
    result = await svc.run_chat("support_bot", {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]})

    assert result["ok"] is True
    assert result["run_id"] == "run_support_bot-x"
    assert gateway.last_payload.agent_id == "support_bot"
    assert gateway.last_payload.model == "gpt-4o-mini"
    assert gateway.last_payload.messages[0].content == "hi"


async def test_chat_returns_budget_blocked():
    svc, _, _, _, _, _ = make_service(blocked=True)
    result = await svc.run_chat("support_bot", {"messages": [{"role": "user", "content": "hi"}]})

    assert result["ok"] is False
    assert result["status"] == 402
    assert "Budget" in result["error"]


async def test_mcp_records_run_trace_and_invokes():
    svc, _, _, db, mcp, _ = make_service()
    result = await svc.run_mcp("support_bot", {"server": "local_tools", "tool": "echo", "arguments": {"text": "hi"}})

    assert result["ok"] is True
    assert result["run_id"].startswith("run_support_bot-")
    assert mcp.calls == [("local_tools", "echo", {"text": "hi"})]
    assert len(db.runs) == 1
    assert db.steps and db.steps[0]["step"]["step_type"] == "mcp"
    assert db.totals == 1


async def test_mcp_requires_tool():
    svc, _, _, _, _, _ = make_service()
    result = await svc.run_mcp("support_bot", {})

    assert result["ok"] is False
    assert result["status"] == 400


async def test_mcp_surfaces_invoke_failure_as_step_error():
    svc, _, _, _, _, _ = make_service(mcp_ok=False)
    result = await svc.run_mcp("support_bot", {"server": "local_tools", "tool": "echo"})

    assert result["ok"] is False
    assert result["status"] == 502


async def test_agent_run_dispatches_to_executor():
    svc, _, executor, _, _, _ = make_service()
    actions = [{"type": "mcp", "server": "local_tools", "tool": "echo", "arguments": {"text": "step"}}]
    result = await svc.run_agent("support_bot", {"actions": actions})

    assert result["ok"] is True
    assert result["run_id"] == "run_support_bot-y"
    assert executor.last_req.agent_id == "support_bot"
    assert len(executor.last_req.actions) == 1


async def test_agent_run_requires_actions():
    svc, _, _, _, _, _ = make_service()
    result = await svc.run_agent("support_bot", {})

    assert result["ok"] is False
    assert result["status"] == 400


async def test_issue_key_calls_auth_service():
    svc, _, _, _, _, auth = make_service()
    result = await svc.run_issue_key({"name": "console-agent"})

    assert result["ok"] is True
    assert auth.issued == ["console-agent"]
    assert result["result"]["key"].startswith("sk-")