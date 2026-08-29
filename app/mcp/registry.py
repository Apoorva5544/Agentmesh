import time

import httpx
from redis.asyncio import Redis

from app.db import Database
from app.mcp.client import LocalExecutor, MCPClient, MCPError, MCPTimeout
from app.telemetry import span


class RateLimited(MCPError):
    pass


class MCPRegistry:
    def __init__(self, db: Database, redis: Redis) -> None:
        self.db = db
        self.redis = redis
        self._clients: dict[str, MCPClient | LocalExecutor] = {}

    def _client_for(self, row: dict) -> MCPClient | LocalExecutor:
        name = row["name"]
        cached = self._clients.get(name)
        if cached is not None:
            return cached
        if row["endpoint"].startswith("builtin://"):
            client: MCPClient | LocalExecutor = LocalExecutor()
        else:
            client = MCPClient(
                endpoint=row["endpoint"],
                auth_type=row.get("auth_type") or "none",
                auth_token=row.get("auth_token"),
                timeout_ms=row.get("timeout_ms") or 15000,
            )
        self._clients[name] = client
        return client

    async def _rate_limit_or_raise(self, name: str, rpm: int) -> None:
        if rpm <= 0:
            return
        window = int(time.time()) // 60
        key = f"mcp:ratelimit:{name}:{window}"
        try:
            count = await self.redis.incr(key)
        except Exception:
            return  # fail-open if Redis is unavailable (local dev)
        if count == 1:
            await self.redis.expire(key, 120)
        if count > rpm:
            raise RateLimited(f"server '{name}' exceeded {rpm} requests/minute")

    async def register(self, fields: dict) -> dict:
        row = await self.db.upsert_server(
            name=fields["name"],
            endpoint=fields["endpoint"],
            description=fields.get("description", ""),
            auth_type=fields.get("auth_type", "none"),
            auth_token=fields.get("auth_token"),
            rate_limit_rpm=int(fields.get("rate_limit_rpm", 60)),
            cost_per_call_inr=float(fields.get("cost_per_call_inr", 0.0)),
            timeout_ms=int(fields.get("timeout_ms", 15000)),
            enabled=bool(fields.get("enabled", True)),
        )
        self._clients.pop(fields["name"], None)
        await self.db.mark_health(fields["name"], "registered")
        return dict(row)

    async def delete(self, name: str) -> None:
        await self.db.delete_server(name)
        client = self._clients.pop(name, None)
        if isinstance(client, MCPClient):
            await client.close()

    async def list_servers(self) -> list[dict]:
        rows = await self.db.list_servers()
        return [dict(row) for row in rows]

    async def get(self, name: str) -> dict | None:
        row = await self.db.get_server(name)
        return dict(row) if row else None

    async def health(self, name: str) -> dict:
        row = await self.db.get_server(name)
        if row is None:
            return {"name": name, "ok": False, "health": "unknown", "error": "not registered"}
        info = dict(row)
        client = self._client_for(info)
        try:
            tools = await client.list_tools()
            await self.db.mark_health(name, "ok")
            return {"name": name, "ok": True, "health": "ok", "tools": len(tools), "checked_at": time.time()}
        except (MCPError, MCPTimeout, httpx.HTTPError) as exc:
            await self.db.mark_health(name, f"error: {exc}")
            return {"name": name, "ok": False, "health": "error", "error": str(exc)}

    async def health_all(self) -> list[dict]:
        servers = await self.list_servers()
        return [await self.health(s["name"]) for s in servers]

    async def invoke(self, name: str, tool: str, arguments: dict | None) -> dict:
        row = await self.db.get_server(name)
        if row is None:
            raise MCPError(f"MCP server '{name}' is not registered")
        info = dict(row)
        if not info.get("enabled", True):
            raise MCPError(f"MCP server '{name}' is disabled")

        await self._rate_limit_or_raise(name, int(info.get("rate_limit_rpm", 60)))

        cost_inr = float(info.get("cost_per_call_inr") or 0.0)
        started = time.perf_counter()
        content = ""
        error: str | None = None
        retried = False
        with span("mcp.invoke", {"server": name, "tool": tool}):
            try:
                client = self._client_for(info)
                result = await client.call(tool, arguments or {})
                content = result.get("content", "")
            except MCPTimeout as exc:
                error = str(exc)
            except MCPError as exc:
                error = str(exc)
            except Exception as exc:  # transport-level failure can be retried once
                retried = True
                try:
                    client = self._client_for(info)
                    result = await client.call(tool, arguments or {})
                    content = result.get("content", "")
                except Exception as exc2:
                    error = str(exc2)

        latency_ms = int((time.perf_counter() - started) * 1000)
        if error:
            await self.db.mark_health(name, f"error: {error[:120]}")
            return {
                "ok": False,
                "server": name,
                "tool": tool,
                "content": "",
                "error": error,
                "cost_inr": cost_inr,
                "latency_ms": latency_ms,
                "retried": retried,
            }
        await self.db.mark_health(name, "ok")
        return {
            "ok": True,
            "server": name,
            "tool": tool,
            "content": content,
            "error": None,
            "cost_inr": cost_inr,
            "latency_ms": latency_ms,
            "retried": retried,
        }

    async def close(self) -> None:
        for name, client in list(self._clients.items()):
            if isinstance(client, MCPClient):
                await client.close()
        self._clients.clear()