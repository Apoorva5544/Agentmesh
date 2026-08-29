import datetime as dt

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS virtual_keys (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    monthly_budget_inr DOUBLE PRECISION NOT NULL DEFAULT 0,
    alert_thresholds DOUBLE PRECISION[] NOT NULL DEFAULT '{0.5,0.8,0.95}',
    hard_limit BOOLEAN NOT NULL DEFAULT FALSE,
    fallback_model TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'completed',
    total_cost_inr DOUBLE PRECISION NOT NULL DEFAULT 0,
    total_latency_ms BIGINT NOT NULL DEFAULT 0,
    step_count INTEGER NOT NULL DEFAULT 0,
    cache_hits INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trace_steps (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    step_no INTEGER NOT NULL,
    step_type TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    tool TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_inr DOUBLE PRECISION NOT NULL DEFAULT 0,
    saved_cost_inr DOUBLE PRECISION NOT NULL DEFAULT 0,
    latency_ms BIGINT NOT NULL DEFAULT 0,
    cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    retried BOOLEAN NOT NULL DEFAULT FALSE,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS usage_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    run_id TEXT,
    agent_id TEXT NOT NULL DEFAULT 'default',
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    route TEXT NOT NULL,
    cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    compressed_prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    tokens_saved INTEGER NOT NULL DEFAULT 0,
    estimated_cost_inr DOUBLE PRECISION NOT NULL DEFAULT 0,
    estimated_cost_saved_inr DOUBLE PRECISION NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_events_created ON usage_events (created_at);
CREATE INDEX IF NOT EXISTS idx_usage_events_agent ON usage_events (agent_id);
CREATE INDEX IF NOT EXISTS idx_trace_steps_run ON trace_steps (run_id);

CREATE TABLE IF NOT EXISTS mcp_servers (
    name TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    auth_type TEXT NOT NULL DEFAULT 'none',
    auth_token TEXT,
    rate_limit_rpm INTEGER NOT NULL DEFAULT 60,
    cost_per_call_inr DOUBLE PRECISION NOT NULL DEFAULT 0,
    timeout_ms INTEGER NOT NULL DEFAULT 15000,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_health TEXT,
    last_checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS budget_alerts (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    threshold DOUBLE PRECISION NOT NULL,
    spend_inr DOUBLE PRECISION NOT NULL,
    budget_inr DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _ts(value: dt.datetime) -> str:
    return value.isoformat()


def _num(value) -> float:
    if value is None:
        return 0.0
    return float(value)


class Database:
    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 10) -> None:
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        self.pool = await asyncpg.create_pool(
            self.dsn, min_size=self.min_size, max_size=self.max_size
        )
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            await conn.execute(SCHEMA)
            await conn.execute(
                "ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS pii_redactions INTEGER NOT NULL DEFAULT 0"
            )
            await conn.execute(
                "ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS schema_requested BOOLEAN NOT NULL DEFAULT FALSE"
            )
            await conn.execute(
                "ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS schema_passed BOOLEAN DEFAULT NULL"
            )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def _conn(self) -> asyncpg.Connection:
        assert self.pool is not None, "database not initialised"
        return self.pool

    async def healthcheck(self) -> bool:
        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ keys
    async def create_key(self, name: str, key_prefix: str, key_hash: str) -> asyncpg.Record:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "INSERT INTO virtual_keys (name, key_prefix, key_hash) VALUES ($1, $2, $3)"
                " RETURNING id, name, key_prefix, created_at",
                name,
                key_prefix,
                key_hash,
            )

    async def get_key(self, key_prefix: str, key_hash: str) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT id, name, key_prefix FROM virtual_keys WHERE key_prefix = $1 AND key_hash = $2",
                key_prefix,
                key_hash,
            )

    async def list_keys(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT id, name, key_prefix, created_at FROM virtual_keys ORDER BY id"
            )

    # ---------------------------------------------------------------- agents
    async def upsert_agent(
        self,
        agent_id: str,
        display_name: str,
        budget_inr: float,
        alert_thresholds: list[float],
        hard_limit: bool,
        fallback_model: str | None,
    ) -> asyncpg.Record:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                INSERT INTO agents (agent_id, display_name, monthly_budget_inr, alert_thresholds,
                                    hard_limit, fallback_model, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, now())
                ON CONFLICT (agent_id) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    monthly_budget_inr = EXCLUDED.monthly_budget_inr,
                    alert_thresholds = EXCLUDED.alert_thresholds,
                    hard_limit = EXCLUDED.hard_limit,
                    fallback_model = EXCLUDED.fallback_model,
                    updated_at = now()
                RETURNING agent_id, display_name, monthly_budget_inr, alert_thresholds,
                          hard_limit, fallback_model
                """,
                agent_id,
                display_name,
                budget_inr,
                alert_thresholds,
                hard_limit,
                fallback_model,
            )

    async def get_agent(self, agent_id: str) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT * FROM agents WHERE agent_id = $1", agent_id
            )

    async def list_agents(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM agents ORDER BY agent_id")

    # ------------------------------------------------------------------- runs
    async def create_run(self, run_id: str, agent_id: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO runs (run_id, agent_id) VALUES ($1, $2) ON CONFLICT (run_id) DO NOTHING",
                run_id,
                agent_id,
            )

    async def add_trace_step(self, run_id: str, step: dict) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO trace_steps (
                    run_id, step_no, step_type, provider, model, tool,
                    tokens_in, tokens_out, cost_inr, saved_cost_inr, latency_ms,
                    cache_hit, retried, error
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                """,
                run_id,
                step["step_no"],
                step["step_type"],
                step.get("provider"),
                step.get("model"),
                step.get("tool"),
                step.get("tokens_in", 0),
                step.get("tokens_out", 0),
                step.get("cost_inr", 0.0),
                step.get("saved_cost_inr", 0.0),
                step.get("latency_ms", 0),
                bool(step.get("cache_hit", False)),
                bool(step.get("retried", False)),
                step.get("error"),
            )

    async def update_run_totals(self, run_id: str) -> None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(cost_inr), 0)::float8 AS cost,
                       COALESCE(SUM(latency_ms), 0)::bigint AS latency,
                       COUNT(*) AS steps,
                       COALESCE(SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END), 0) AS hits,
                       COALESCE(SUM(CASE WHEN retried THEN 1 ELSE 0 END), 0) AS retries
                FROM trace_steps WHERE run_id = $1
                """,
                run_id,
            )
            await conn.execute(
                """
                UPDATE runs SET total_cost_inr = $2, total_latency_ms = $3,
                                step_count = $4, cache_hits = $5, retry_count = $6
                WHERE run_id = $1
                """,
                run_id,
                round(_num(row["cost"]), 2),
                row["latency"],
                row["steps"],
                row["hits"],
                row["retries"],
            )

    async def update_run_status(self, run_id: str, status: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE runs SET status = $2 WHERE run_id = $1", run_id, status
            )

    async def get_run(self, run_id: str) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT * FROM runs WHERE run_id = $1", run_id
            )

    async def get_steps(self, run_id: str) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM trace_steps WHERE run_id = $1 ORDER BY step_no", run_id
            )

    async def get_usage_for_run(self, run_id: str) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM usage_events WHERE run_id = $1 ORDER BY id", run_id
            )

    async def list_runs(self, agent_id: str | None = None, limit: int = 50) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            if agent_id:
                return await conn.fetch(
                    "SELECT * FROM runs WHERE agent_id = $1 ORDER BY created_at DESC LIMIT $2",
                    agent_id,
                    limit,
                )
            return await conn.fetch(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT $1", limit
            )

    # ----------------------------------------------------------------- usage
    async def record_usage(self, event: dict) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO usage_events (
                    run_id, agent_id, provider, model, route, cache_hit,
                    prompt_tokens, compressed_prompt_tokens, completion_tokens,
                    tokens_saved, estimated_cost_inr, estimated_cost_saved_inr,
                    pii_redactions, schema_requested, schema_passed
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                """,
                event.get("run_id"),
                event.get("agent_id", "default"),
                event["provider"],
                event["model"],
                event["route"],
                bool(event.get("cache_hit", False)),
                event.get("prompt_tokens", 0),
                event.get("compressed_prompt_tokens", 0),
                event.get("completion_tokens", 0),
                event.get("tokens_saved", 0),
                event.get("estimated_cost_inr", 0.0),
                event.get("estimated_cost_saved_inr", 0.0),
                event.get("pii_redactions", 0),
                bool(event.get("schema_requested", False)),
                event.get("schema_passed"),
            )

    async def monthly_spend_inr(self, agent_id: str) -> float:
        start = dt.datetime.now(dt.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        async with self.pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COALESCE(SUM(estimated_cost_inr), 0)::float8 FROM usage_events"
                " WHERE agent_id = $1 AND created_at >= $2",
                agent_id,
                start,
            )
        return float(value)

    async def summary(self) -> dict:
        now = dt.datetime.now(dt.UTC)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        async with self.pool.acquire() as conn:
            totals = await conn.fetchrow(
                """
                SELECT COUNT(*) AS requests,
                       COALESCE(SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END), 0) AS cache_hits,
                       COALESCE(SUM(tokens_saved), 0) AS tokens_saved,
                       COALESCE(SUM(estimated_cost_saved_inr), 0) AS cost_saved,
                       COALESCE(SUM(estimated_cost_inr), 0) AS cost,
                       COALESCE(SUM(pii_redactions), 0) AS pii_redactions,
                       COALESCE(SUM(CASE WHEN schema_requested THEN 1 ELSE 0 END), 0) AS schema_requests,
                       COALESCE(SUM(CASE WHEN schema_requested AND schema_passed THEN 1 ELSE 0 END), 0) AS schema_passed
                FROM usage_events
                """
            )
            month_totals = await conn.fetchrow(
                """
                SELECT COALESCE(SUM(estimated_cost_inr), 0) AS cost,
                       COALESCE(SUM(estimated_cost_saved_inr), 0) AS cost_saved
                FROM usage_events WHERE created_at >= $1
                """,
                start,
            )
            by_model = await conn.fetch(
                """
                SELECT provider, model, COUNT(*) AS requests,
                       COALESCE(SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END), 0) AS cache_hits,
                       COALESCE(SUM(tokens_saved), 0) AS tokens_saved,
                       COALESCE(SUM(estimated_cost_inr), 0) AS cost_inr,
                       COALESCE(SUM(estimated_cost_saved_inr), 0) AS cost_saved
                FROM usage_events GROUP BY provider, model ORDER BY requests DESC
                """
            )
            by_agent = await conn.fetch(
                """
                SELECT agent_id, COUNT(*) AS requests,
                       COALESCE(SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END), 0) AS cache_hits,
                       COALESCE(SUM(estimated_cost_inr), 0) AS cost_inr,
                       COALESCE(SUM(estimated_cost_saved_inr), 0) AS cost_saved
                FROM usage_events GROUP BY agent_id ORDER BY cost_inr DESC
                """
            )
            by_tool = await conn.fetch(
                """
                SELECT tool, COUNT(*) AS calls, COALESCE(SUM(cost_inr), 0) AS cost_inr
                FROM trace_steps WHERE step_type = 'mcp' AND tool IS NOT NULL
                GROUP BY tool ORDER BY calls DESC
                """
            )
            by_lang = await conn.fetch(
                "SELECT COUNT(*) AS requests FROM usage_events"
            )
            recent = await conn.fetch(
                """
                SELECT created_at, provider, model, cache_hit, tokens_saved,
                       estimated_cost_inr, estimated_cost_saved_inr
                FROM usage_events ORDER BY id DESC LIMIT 20
                """
            )
            recent_runs = await conn.fetch(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT 10"
            )
        row = dict(totals)
        requests = row.get("requests") or 0
        cache_hits = row.get("cache_hits") or 0
        schema_requests = row.get("schema_requests") or 0
        row["cache_hit_rate"] = round(cache_hits / requests * 100, 2) if requests else 0
        row["schema_compliance_rate"] = round((row.get("schema_passed") or 0) / schema_requests * 100, 2) if schema_requests else None
        return {
            "totals": {
                **row,
                "cost_saved": round(_num(row["cost_saved"]), 2),
                "cost": round(_num(row["cost"]), 2),
                "pii_redactions": row.get("pii_redactions") or 0,
            },
            "month_totals": {
                "cost": round(_num(month_totals["cost"]), 2),
                "cost_saved": round(_num(month_totals["cost_saved"]), 2),
            },
            "total_requests": int(request_count := requests),
            "cache_hit_rate": row["cache_hit_rate"],
            "by_model": [dict(r) for r in by_model],
            "by_agent": [dict(r) for r in by_agent],
            "by_tool": [dict(r) for r in by_tool],
            "recent_runs": [dict(r) for r in recent_runs],
            "recent": [dict(r) for r in recent],
        }

    # ---------------------------------------------------------- budget alerts
    async def alert_fired_this_month(self, agent_id: str, threshold: float) -> bool:
        start = dt.datetime.now(dt.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM budget_alerts WHERE agent_id = $1 AND threshold = $2 AND created_at >= $3 LIMIT 1",
                agent_id,
                threshold,
                start,
            )
        return row is not None

    async def record_alert(self, agent_id: str, threshold: float, spend_inr: float, budget_inr: float) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO budget_alerts (agent_id, threshold, spend_inr, budget_inr)
                VALUES ($1, $2, $3, $4)
                """,
                agent_id,
                threshold,
                spend_inr,
                budget_inr,
            )

    async def list_alerts(self, agent_id: str | None = None, limit: int = 50) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            if agent_id:
                return await conn.fetch(
                    "SELECT * FROM budget_alerts WHERE agent_id = $1 ORDER BY created_at DESC LIMIT $2",
                    agent_id,
                    limit,
                )
            return await conn.fetch(
                "SELECT * FROM budget_alerts ORDER BY created_at DESC LIMIT $1", limit
            )

    # ---------------------------------------------------------- mcp servers
    async def upsert_server(
        self,
        name: str,
        endpoint: str,
        description: str,
        auth_type: str,
        auth_token: str | None,
        rate_limit_rpm: int,
        cost_per_call_inr: float,
        timeout_ms: int,
        enabled: bool,
    ) -> asyncpg.Record:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """
                INSERT INTO mcp_servers (name, endpoint, description, auth_type, auth_token,
                                         rate_limit_rpm, cost_per_call_inr, timeout_ms, enabled)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (name) DO UPDATE SET
                    endpoint = EXCLUDED.endpoint,
                    description = EXCLUDED.description,
                    auth_type = EXCLUDED.auth_type,
                    auth_token = EXCLUDED.auth_token,
                    rate_limit_rpm = EXCLUDED.rate_limit_rpm,
                    cost_per_call_inr = EXCLUDED.cost_per_call_inr,
                    timeout_ms = EXCLUDED.timeout_ms,
                    enabled = EXCLUDED.enabled
                RETURNING name, endpoint, description, rate_limit_rpm, cost_per_call_inr, enabled
                """,
                name,
                endpoint,
                description,
                auth_type,
                auth_token,
                rate_limit_rpm,
                cost_per_call_inr,
                timeout_ms,
                enabled,
            )

    async def get_server(self, name: str) -> asyncpg.Record | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM mcp_servers WHERE name = $1", name)

    async def list_servers(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM mcp_servers ORDER BY name")

    async def mark_health(self, name: str, health: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE mcp_servers SET last_health = $2, last_checked_at = now() WHERE name = $1",
                name,
                health,
            )

    async def delete_server(self, name: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM mcp_servers WHERE name = $1", name)


def as_record_dict(record) -> dict:
    return dict(record) if record is not None else {}