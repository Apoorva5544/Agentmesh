import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.agents import AgentRunExecutor
from app.dependencies import container
from app.gateway import BudgetBlocked, ChatGateway
from app.mcp.client import MCPError
from app.routers.agents import executor
from app.routers.chat import gateway
from app.schemas import AgentRunRequest, ChatCompletionRequest, ChatMessage, RunAction

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


class SandboxRunRequest(BaseModel):
    kind: Literal["chat", "mcp", "agent", "issue_key"]
    agent_id: str = "support_bot"
    body: dict | None = None


class SandboxService:
    """Executes a console recipe through the real gateway pipeline so every call
    is rate-limited, audited into a trace, and attributed to the given agent."""

    def __init__(self, gateway: ChatGateway, executor: AgentRunExecutor, container) -> None:
        self.gateway = gateway
        self.executor = executor
        self.container = container

    async def run_chat(self, agent_id: str, body: dict) -> dict:
        messages = [
            ChatMessage(role=m.get("role", "user"), content=str(m.get("content", "")))
            for m in body.get("messages") or []
        ]
        req = ChatCompletionRequest(
            messages=messages or [ChatMessage(role="user", content="Say hi")],
            model=body.get("model"),
            agent_id=agent_id,
            stream=False,
        )
        try:
            result = await self.gateway.complete(req)
        except BudgetBlocked as exc:
            v = exc.verdict
            return {
                "ok": False,
                "status": 402,
                "error": f"Budget exhausted: ₹{v.spend_inr:.2f} / ₹{v.budget_inr:.0f} for '{agent_id}'",
            }
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            return {"ok": False, "status": 503, "error": str(detail)}
        return {"ok": True, "status": 200, "run_id": result.get("run_id"), "result": result}

    async def run_mcp(self, agent_id: str, body: dict) -> dict:
        tool = body.get("tool")
        if not tool:
            return {"ok": False, "status": 400, "error": "mcp recipe needs a 'tool'"}
        server = body.get("server", "local_tools")
        arguments = body.get("arguments") or {}

        run_id = f"run_{agent_id[:24]}-{uuid.uuid4().hex[:10]}"
        await self.container.db.create_run(run_id, agent_id)
        started = time.perf_counter()
        try:
            result = await self.container.mcp.invoke(server, tool, arguments)
        except MCPError as exc:
            result = {
                "ok": False,
                "server": server,
                "tool": tool,
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
            "tool": tool,
            "tokens_in": 0,
            "tokens_out": 0,
            "cost_inr": result.get("cost_inr", 0.0),
            "saved_cost_inr": 0.0,
            "latency_ms": result.get("latency_ms", 0),
            "cache_hit": False,
            "retried": bool(result.get("retried", False)),
            "error": result.get("error"),
        }
        await self.container.db.add_trace_step(run_id, step)
        await self.container.db.update_run_totals(run_id)
        return {
            "ok": bool(result.get("ok", False)),
            "status": 200 if result.get("ok") else 502,
            "run_id": run_id,
            "error": result.get("error"),
            "result": result,
        }

    async def run_agent(self, agent_id: str, body: dict) -> dict:
        actions = [RunAction(**a) for a in body.get("actions") or []]
        if not actions:
            return {"ok": False, "status": 400, "error": "agent recipe needs 'actions'"}
        req = AgentRunRequest(actions=actions, agent_id=agent_id)
        try:
            result = await self.executor.run(req)
        except Exception as exc:
            return {"ok": False, "status": 500, "error": str(exc)}
        return {"ok": True, "status": 200, "run_id": result["run_id"], "result": result}

    async def run_issue_key(self, body: dict) -> dict:
        name = body.get("name") or "console-agent"
        result = await self.container.auth.issue_key(name)
        return {"ok": True, "status": 200, "result": result}


sandbox = SandboxService(gateway, executor, container)


@router.post("/run")
async def sandbox_run(payload: SandboxRunRequest, request: Request) -> dict:
    body = payload.body or {}
    if payload.kind == "issue_key":
        await container.auth.require_admin(request)
    else:
        await container.auth.require_tenant(request)

    if payload.kind == "chat":
        return await sandbox.run_chat(payload.agent_id, body)
    if payload.kind == "mcp":
        return await sandbox.run_mcp(payload.agent_id, body)
    if payload.kind == "agent":
        return await sandbox.run_agent(payload.agent_id, body)
    return await sandbox.run_issue_key(body)


SAVED_PROMPTS = [
    {
        "name": "Echo — local tool",
        "tag": "zero-infra",
        "kind": "mcp",
        "agent_id": "support_bot",
        "description": "Invokes the bundled echo tool (works with no external infra)",
        "body": {
            "server": "local_tools",
            "tool": "echo",
            "arguments": {"text": "Hello from the gateway console"},
        },
    },
    {
        "name": "Now — server time",
        "tag": "zero-infra",
        "kind": "mcp",
        "agent_id": "support_bot",
        "description": "Invokes the bundled now tool (works with no external infra)",
        "body": {"server": "local_tools", "tool": "now", "arguments": {}},
    },
    {
        "name": "Scripted agent run",
        "tag": "zero-infra",
        "kind": "agent",
        "agent_id": "support_bot",
        "description": "A traced multi-step agent run chaining two local tool calls",
        "body": {
            "actions": [
                {"type": "mcp", "server": "local_tools", "tool": "echo", "arguments": {"text": "step 1"}},
                {"type": "mcp", "server": "local_tools", "tool": "now", "arguments": {}},
            ]
        },
    },
    {
        "name": "Chat — what is the gateway?",
        "tag": "needs llm key",
        "kind": "chat",
        "agent_id": "support_bot",
        "description": "A real LLM completion through the gateway (requires a provider key)",
        "body": {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Tell me what this gateway does in one sentence."}],
        },
    },
    {
        "name": "Chat — Hindi routing",
        "tag": "needs llm key",
        "kind": "chat",
        "agent_id": "support_bot",
        "description": "Tests Indic-language routing to Sarvam-1 (requires a Sarvam key)",
        "body": {
            "model": "sarvam-1",
            "messages": [{"role": "user", "content": "इस गेटवे के बारे में एक लाइन में बताओ।"}],
        },
    },
    {
        "name": "Issue a virtual key",
        "tag": "admin",
        "kind": "issue_key",
        "agent_id": "support_bot",
        "description": "Issues a scoped tenant key (admin action)",
        "body": {"name": "console-agent"},
    },
]