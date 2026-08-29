import datetime as dt

import httpx


class MCPError(Exception):
    pass


class MCPTimeout(MCPError):
    pass


class MCPClient:
    """Minimal MCP client speaking JSON-RPC 2.0 over HTTP (Streamable HTTP transport).

    Supports the `tools/list` and `tools/call` methods used by the AgentMesh
    gateway. Custom transports (local tool handlers) are handled upstream.
    """

    def __init__(
        self,
        endpoint: str,
        auth_type: str = "none",
        auth_token: str | None = None,
        timeout_ms: int = 15000,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout_ms / 1000
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if auth_type == "bearer" and auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        elif auth_type == "basic" and auth_token:
            headers["Authorization"] = f"Basic {auth_token}"
        self._client = httpx.AsyncClient(headers=headers, timeout=self.timeout)

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        request_id = 1
        try:
            response = await self._client.post(
                self.endpoint,
                json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
            )
            response.raise_for_status()
            if response.status_code == 202:
                raise MCPTimeout("MCP server requested a long-running session")
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise MCPTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise MCPError(f"MCP transport error: {exc}") from exc
        except ValueError as exc:
            raise MCPError(f"MCP server returned non-JSON response: {exc}") from exc
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if payload.get("error"):
            raise MCPError(payload["error"].get("message", "unknown MCP error"))
        return payload.get("result") or {}

    async def list_tools(self) -> list[dict]:
        result = await self._rpc("tools/list")
        return result.get("tools", [])

    async def call(self, name: str, arguments: dict | None = None) -> dict:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise MCPError(result.get("content", "tool error"))
        content = result.get("content", [])
        return {"content": self._render_content(content), "raw": result}

    @staticmethod
    def _render_content(content: list[dict]) -> str:
        parts: list[str] = []
        for item in content:
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif item.get("type") == "resource":
                resource = item.get("resource", {})
                blob = resource.get("text") or resource.get("blob")
                if blob:
                    parts.append(str(blob))
        return "\n".join(parts)

    async def close(self) -> None:
        await self._client.aclose()


async def register_local_tools(client) -> None:
    """Placeholder for custom transports (kept for parity with the base client)."""


LOCAL_TOOL_HANDLERS: dict[str, callable] = {
    "echo": lambda args: {"text": args.get("text", "")},
    "now": lambda args: {"iso8601": dt.datetime.now(dt.UTC).isoformat()},
}


class LocalExecutor:
    """Runs tools registered in code (endpoint `builtin://local`) with zero infra."""

    async def list_tools(self) -> list[dict]:
        return [
            {"name": name, "description": f"Local tool: {name}", "inputSchema": {"type": "object"}}
            for name in LOCAL_TOOL_HANDLERS
        ]

    async def call(self, name: str, arguments: dict | None = None) -> dict:
        handler = LOCAL_TOOL_HANDLERS.get(name)
        if handler is None:
            raise MCPError(f"unknown local tool: {name}")
        return {"content": "ok", "ok": handler(arguments or {})}