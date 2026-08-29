"""Minimal Python client for the AgentMesh control plane.

Uses httpx against the gateway's OpenAI-compatible `/v1` surface.
"""

import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import httpx


class AgentMeshError(Exception):
    def __init__(self, status_code: int, message: Any):
        self.status_code = status_code
        if isinstance(message, dict):
            self.message = message.get("detail") or message.get("message") or str(message)
        else:
            self.message = str(message)
        super().__init__(self.message)


class AgentMeshClient:
    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers(api_key),
            timeout=timeout,
        )

    @staticmethod
    def _headers(api_key: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AgentMeshClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def chat(self, messages: list[dict], *, model: str | None = None,
                   agent_id: str | None = None, use_cache: bool = True,
                   run_id: str | None = None, stream: bool = False,
                   response_format: dict | None = None, max_tokens: int | None = None,
                   temperature: float | None = None) -> dict:
        payload: dict[str, Any] = {
            "messages": messages,
            "use_cache": use_cache,
            "stream": stream,
        }
        if model:
            payload["model"] = model
        if agent_id:
            payload["agent_id"] = agent_id
        if run_id:
            payload["run_id"] = run_id
        if response_format:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        return await self._post("/v1/chat/completions", payload)

    async def stream_chat(self, messages: list[dict], **kwargs: Any) -> list[str]:
        """Stream a completion over SSE and return the concatenated deltas."""
        payload = {"messages": messages, "stream": True}
        payload.update({k: v for k, v in kwargs.items() if v is not None})
        response = await self._client.post("/v1/chat/completions", json=payload)
        response.raise_for_status()
        chunks: list[str] = []
        async for line in response.aiter_lines():
            if line.startswith("data: ") and line != "data: ":
                data = line[6:]
                if data == "[DONE]":
                    continue
                import json as _json

                chunk = _json.loads(data)
                if "error" in chunk:
                    raise AgentMeshError(response.status_code, chunk["error"])
                choices = chunk.get("choices") or []
                for choice in choices:
                    content = (choice.get("delta") or {}).get("content")
                    if content:
                        chunks.append(content)
        return chunks

    async def invoke_tool(self, server: str, tool: str, arguments: dict | None = None,
                          agent_id: str | None = None, run_id: str | None = None) -> dict:
        payload: dict[str, Any] = {"server": server, "tool": tool}
        if arguments is not None:
            payload["arguments"] = arguments
        if agent_id:
            payload["agent_id"] = agent_id
        if run_id:
            payload["run_id"] = run_id
        return await self._post("/v1/mcp/invoke", payload)

    async def register_server(self, name: str, endpoint: str, *, description: str | None = None,
                              cost_per_call_inr: float = 0.0, rate_limit_rpm: int = 60) -> dict:
        payload: dict[str, Any] = {
            "name": name,
            "endpoint": endpoint,
            "cost_per_call_inr": cost_per_call_inr,
            "rate_limit_rpm": rate_limit_rpm,
        }
        if description:
            payload["description"] = description
        return await self._post("/v1/mcp/registry", payload)

    async def create_agent(self, agent_id: str, description: str = "", *,
                           monthly_budget_inr: float = 0.0, hard_limit_inr: float = 0.0,
                           notify_email: str | None = None) -> dict:
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "description": description,
            "monthly_budget_inr": monthly_budget_inr,
            "hard_limit_inr": hard_limit_inr,
            "notify_email": notify_email,
        }
        return await self._post("/v1/agents", payload)

    async def run_agent(self, agent_id: str, actions: list[dict], run_id: str | None = None) -> dict:
        payload: dict[str, Any] = {"agent_id": agent_id, "actions": actions}
        if run_id:
            payload["run_id"] = run_id
        return await self._post(f"/v1/agents/{agent_id}/run", payload)

    async def get_run(self, run_id: str) -> dict:
        response = await self._client.get(f"/v1/runs/{run_id}")
        self._raise_for_status(response)
        return response.json()

    @contextmanager
    def run(self, agent_id: str = "default") -> Iterator["RunContext"]:
        """Attach a traceable run_id to every call inside the block."""
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        ctx = RunContext(client=self, run_id=run_id, agent_id=agent_id)
        try:
            yield ctx
        finally:
            pass  # run_id is recorded lazily by the gateway on first action

    async def _post(self, path: str, payload: dict) -> dict:
        response = await self._client.post(path, json=payload)
        self._raise_for_status(response)
        return response.json()

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise AgentMeshError(response.status_code, detail)


class RunContext:
    def __init__(self, client: AgentMeshClient, run_id: str, agent_id: str):
        self.client = client
        self.run_id = run_id
        self.agent_id = agent_id
        self.calls: int = 0

    async def chat(self, messages: list[dict], **kwargs: Any) -> dict:
        self.calls += 1
        return await self.client.chat(messages, agent_id=self.agent_id,
                                      run_id=self.run_id, **kwargs)

    async def invoke_tool(self, server: str, tool: str, arguments: dict | None = None) -> dict:
        self.calls += 1
        return await self.client.invoke_tool(server, tool, arguments,
                                             agent_id=self.agent_id, run_id=self.run_id)

    async def summary(self) -> dict:
        return await self.client.get_run(self.run_id)