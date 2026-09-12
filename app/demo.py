import datetime as dt
import random
import time

from app.costs import estimate_cost_inr

LOCAL_TOOLS_SERVER = {
    "name": "local_tools",
    "endpoint": "builtin://local",
    "description": "Zero-infra demo tools (echo, now) bundled with AgentMesh",
    "auth_type": "none",
    "auth_token": None,
    "rate_limit_rpm": 60,
    "cost_per_call_inr": 0.08,
    "timeout_ms": 15000,
    "enabled": True,
}

DEMO_AGENTS = [
    {
        "agent_id": "support_bot",
        "display_name": "Customer Support Agent",
        "monthly_budget_inr": 5000.0,
        "alert_thresholds": [0.5, 0.8, 0.95],
        "hard_limit": True,
        "fallback_model": "sarvam-1-lite",
    },
    {
        "agent_id": "research_agent",
        "display_name": "Research & Insights",
        "monthly_budget_inr": 2500.0,
        "alert_thresholds": [0.5, 0.8, 0.95],
        "hard_limit": False,
        "fallback_model": None,
    },
    {
        "agent_id": "billing_agent",
        "display_name": "Billing Admin",
        "monthly_budget_inr": 800.0,
        "alert_thresholds": [0.4, 0.7, 0.9],
        "hard_limit": True,
        "fallback_model": "sarvam-1-lite",
    },
]

DEMO_MODELS = [
    ("openai", "gpt-4o-mini"),
    ("openai", "gpt-4o"),
    ("anthropic", "claude-3-5-haiku-latest"),
    ("anthropic", "claude-3-5-sonnet-latest"),
    ("sarvam", "sarvam-1"),
    ("sarvam", "sarvam-1-lite"),
]

MCP_TOOLS = ("echo", "now")

# History window (days) and daily runs per agent.
DEMO_DAYS = 24
DAILY_RUN_MIN = 1
DAILY_RUN_MAX = 3


def _run_timestamp(day: dt.datetime, rng: random.Random) -> dt.datetime:
    return day.replace(
        hour=rng.randint(7, 22),
        minute=rng.randint(0, 59),
        second=rng.randint(0, 59),
        microsecond=0,
    )


def _llm_step(idx: int, rng: random.Random) -> tuple[dict, dict]:
    """Build (trace_step, usage_event) for a realistic LLM call."""
    provider, model = rng.choice(DEMO_MODELS)
    original = rng.randint(180, 1500)
    compressed = int(original * rng.uniform(0.7, 0.92))
    completion = rng.randint(60, 720)
    cache_hit = rng.random() < 0.30
    latency = rng.randint(180, 2400)

    if cache_hit:
        cost_inr = 0.0
        cost_saved = estimate_cost_inr(provider, model, original + completion, 0)
        tokens_saved = original + completion
    else:
        cost_inr = estimate_cost_inr(provider, model, compressed, completion)
        cost_saved = estimate_cost_inr(provider, model, original - compressed, 0)
        tokens_saved = original - compressed

    schema_requested = rng.random() < 0.35
    schema_passed = (rng.random() < 0.97) if schema_requested else None
    pii = rng.choices([0, 1, 2, 3], weights=[0.82, 0.12, 0.05, 0.01])[0]

    step = {
        "step_no": idx,
        "step_type": "llm",
        "provider": provider,
        "model": model,
        "tool": None,
        "tokens_in": compressed,
        "tokens_out": completion,
        "cost_inr": round(cost_inr, 6),
        "saved_cost_inr": round(cost_saved, 6),
        "latency_ms": latency,
        "cache_hit": cache_hit,
        "retried": rng.random() < 0.02,
        "error": None,
    }
    event = {
        "provider": provider,
        "model": model,
        "route": "/v1/chat/completions",
        "cache_hit": cache_hit,
        "prompt_tokens": original,
        "compressed_prompt_tokens": compressed,
        "completion_tokens": completion,
        "tokens_saved": tokens_saved,
        "estimated_cost_inr": round(cost_inr, 6),
        "estimated_cost_saved_inr": round(cost_saved, 6),
        "pii_redactions": pii,
        "schema_requested": schema_requested,
        "schema_passed": schema_passed,
    }
    return step, event


def _mcp_step(idx: int, rng: random.Random) -> dict:
    tool = rng.choice(MCP_TOOLS)
    return {
        "step_no": idx,
        "step_type": "mcp",
        "provider": None,
        "model": None,
        "tool": tool,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_inr": LOCAL_TOOLS_SERVER["cost_per_call_inr"],
        "saved_cost_inr": 0.0,
        "latency_ms": rng.randint(2, 60),
        "cache_hit": False,
        "retried": rng.random() < 0.05,
        "error": None,
    }


async def seed_demo_data(container) -> dict:
    """Populate a fresh install with a realistic slice of gateway activity:
    a zero-infra MCP server, budgeted agents, ~3 weeks of backdated traced
    runs / usage, plus a couple of live invokes so health shows 'ok'."""
    db = container.db
    settings = container.settings
    rng = random.Random(20260912)

    if not await db.get_server(LOCAL_TOOLS_SERVER["name"]):
        await container.mcp.register(LOCAL_TOOLS_SERVER)

    for agent in DEMO_AGENTS:
        await db.upsert_agent(
            agent_id=agent["agent_id"],
            display_name=agent["display_name"],
            budget_inr=agent["monthly_budget_inr"],
            alert_thresholds=agent["alert_thresholds"],
            hard_limit=agent["hard_limit"],
            fallback_model=agent["fallback_model"],
        )

    runs_created = 0
    usage_events = 0
    mcp_calls = 0
    now = dt.datetime.now(dt.UTC)
    seq = 0

    for offset in range(DEMO_DAYS, -1, -1):
        day = now - dt.timedelta(days=offset)
        for agent in DEMO_AGENTS:
            for _ in range(rng.randint(DAILY_RUN_MIN, DAILY_RUN_MAX)):
                seq += 1
                agent_id = agent["agent_id"]
                when = _run_timestamp(day, rng)
                run_id = f"demo_{agent_id[:12]}_{seq:04d}"
                await db.create_run(run_id, agent_id, created_at=when)

                steps = []
                for idx in range(1, rng.randint(2, 5) + 1):
                    if idx == 1 or rng.random() < 0.4:
                        step = _mcp_step(idx, rng)
                        steps.append(step)
                        mcp_calls += 1
                    else:
                        step, event = _llm_step(idx, rng)
                        steps.append(step)
                        await db.record_usage(
                            {**event, "run_id": run_id, "agent_id": agent_id},
                            created_at=when,
                        )
                        usage_events += 1

                for step in steps:
                    await db.add_trace_step(run_id, step)
                await db.update_run_totals(run_id)
                runs_created += 1

    live = []
    for tool, arguments in (("now", {}), ("echo", {"text": "hello from the AgentMesh demo"})):
        result = await container.mcp.invoke("local_tools", tool, arguments)
        live.append({"tool": tool, "ok": result.get("ok"), "latency_ms": result.get("latency_ms")})

    servers = await db.list_servers()
    agents = await db.list_agents()
    return {
        "seeded": True,
        "mode": "demo" if settings.demo_mode else "auto",
        "servers": [dict(s) for s in servers],
        "agents": [a["agent_id"] for a in agents],
        "runs_created": runs_created,
        "usage_events": usage_events,
        "mcp_calls": mcp_calls,
        "live_invokes": live,
        "seeded_at": time.time(),
    }