import re
import time
import uuid

from app.dependencies import Container
from app.gateway import ChatGateway
from app.mcp.client import MCPError
from app.schemas import AgentRunRequest, ChatCompletionRequest, ChatMessage

STEP_REF_RE = re.compile(r"\{\{\s*step\.(\d+)\.output\s*\}\}")


class AgentRunExecutor:
    """Executes a scripted agent run, chaining LLM calls and MCP tool calls into
    a single costed, latency-tracked trace."""

    def __init__(self, gateway: ChatGateway) -> None:
        self.gateway = gateway

    @staticmethod
    def _render(template: str, outputs: dict[int, str]) -> str:
        def substitute(match: re.Match) -> str:
            index = int(match.group(1))
            return outputs.get(index, "")

        return STEP_REF_RE.sub(substitute, template)

    async def run(self, request: AgentRunRequest) -> dict:
        container = self.gateway.container
        run_id = request.run_id or f"run_{request.agent_id[:24]}-{uuid.uuid4().hex[:10]}"
        await container.db.create_run(run_id, request.agent_id)

        outputs: dict[int, str] = {}
        try:
            for index, action in enumerate(request.actions):
                step_no = index + 1
                if action.type == "llm":
                    await self._run_llm_action(run_id, request.agent_id, step_no, action, outputs)
                else:
                    await self._run_mcp_action(run_id, step_no, action, outputs)
            await container.db.update_run_totals(run_id)
            await container.db.update_run_status(run_id, "completed")
        except Exception:
            await container.db.update_run_totals(run_id)
            await container.db.update_run_status(run_id, "failed")
            raise

        return await self._collect(run_id)

    async def _run_llm_action(self, run_id: str, agent_id: str, step_no: int, action, outputs: dict[int, str]) -> None:
        prompt = self._render(action.prompt or "", outputs)
        payload = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            model=action.model,
            use_cache=action.use_cache,
            compress_prompt=action.compress_prompt,
            agent_id=agent_id,
            run_id=run_id,
        )
        result = await self.gateway.complete(payload)
        outputs[step_no] = result["content"]

    async def _run_mcp_action(self, run_id: str, step_no: int, action, outputs: dict[int, str]) -> None:
        container = self.gateway.container
        started = time.perf_counter()
        try:
            result = await container.mcp.invoke(action.server, action.tool, action.arguments)
        except MCPError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            result = {
                "ok": False,
                "server": action.server,
                "tool": action.tool,
                "content": "",
                "error": str(exc),
                "cost_inr": 0.0,
                "latency_ms": latency_ms,
                "retried": False,
            }
        step = {
            "step_no": step_no,
            "step_type": "mcp",
            "provider": None,
            "model": None,
            "tool": action.tool,
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
        outputs[step_no] = result.get("content") or (f"<error: {result.get('error')}>" if result.get("error") else "")

    async def _collect(self, run_id: str) -> dict:
        container = self.gateway.container
        run = await container.db.get_run(run_id)
        steps = await container.db.get_steps(run_id)
        return {
            "run_id": run_id,
            "agent_id": run["agent_id"],
            "status": run["status"],
            "total_cost_inr": round(float(run["total_cost_inr"]), 4),
            "total_latency_ms": int(run["total_latency_ms"]),
            "cache_hits": int(run["cache_hits"]),
            "retry_count": int(run["retry_count"]),
            "steps": [
                {
                    "step_no": int(s["step_no"]),
                    "step_type": s["step_type"],
                    "provider": s["provider"],
                    "model": s["model"],
                    "tool": s["tool"],
                    "tokens_in": int(s["tokens_in"]),
                    "tokens_out": int(s["tokens_out"]),
                    "cost_inr": round(float(s["cost_inr"]), 6),
                    "saved_cost_inr": round(float(s["saved_cost_inr"]), 6),
                    "latency_ms": int(s["latency_ms"]),
                    "cache_hit": bool(s["cache_hit"]),
                    "retried": bool(s["retried"]),
                    "error": s["error"],
                }
                for s in steps
            ],
        }