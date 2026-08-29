# AgentMesh — AI Agent Control Plane

[![CI](https://github.com/your-org/agentmesh/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/agentmesh/actions/workflows/ci.yml)

> **The infrastructure layer for production AI agents.**
>
> Unified LLM gateway + MCP orchestration + real-time cost intelligence.
> Built for teams running agents at scale who need to know exactly where every rupee goes.

## ✨ What Makes This Different?

Unlike LiteLLM (just a proxy) or Langfuse (just observability), AgentMesh is the
**control plane** that governs both your LLM calls **and** your agent's tool usage
through MCP — with budget enforcement, per-run cost attribution, and automatic
optimization. Every agent run produces a full trace showing precisely where the
money went: LLM tokens, MCP tool APIs, retries, and cache savings.

Previous generation tools only track token costs. Production agents spend
significant budget on MCP tool calls, retries, and context bloat that nobody
tracks — AgentMesh surfaces all of it.

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    AGENTMESH CONTROL PLANE                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │  OpenAI API │  │ Anthropic   │  │  MCP Gateway            │ │
│  │ /v1/chat/*  │  │ /v1/msg/*   │  │  /v1/mcp/invoke         │ │
│  │ /v1/models  │  │ /v1/models  │  │  /v1/mcp/registry       │ │
│  └──────┬──────┘  └──────┬──────┘  │  /v1/mcp/health         │ │
│         └─────────────────┘          └───────────┬─────────────┘ │
│                   │                              │              │
│         ┌─────────▼──────────┐    ┌─────────────▼────────┐     │
│         │   UNIFIED LAYER    │◄──►│   MCP ROUTER         │     │
│         │  • Virtual Keys    │    │  • Tool discovery    │     │
│         │  • Rate Limiting   │    │  • Retry/fallback    │     │
│         │  • Semantic Cache  │    │  • Cost attribution  │     │
│         │  • Prompt Compress │    │  • Health checks     │     │
│         │  • Model Router    │    └──────────────────────┘     │
│         └─────────┬──────────┘                                 │
│                   │                                              │
│    ┌──────────────▼──────────────┐                             │
│    │   COST INTELLIGENCE ENGINE  │                             │
│    │  • Per-run cost tracing     │                             │
│    │  • LLM + Tool cost merge    │                             │
│    │  • Budget enforcement       │                             │
│    │  • Burn rate alerts         │                             │
│    └──────────────┬──────────────┘                             │
│                   │                                              │
│    ┌──────────────▼──────────────────────────────────┐         │
│    │  PROVIDERS: OpenAI │ Claude │ Sarvam-1 │ Ollama │         │
│    │  MCP SERVERS: Brave │ GitHub │ Postgres │ local │         │
│    └──────────────────────────────────────────────────┘         │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │  DASHBOARD (FastAPI Jinja2)                                 ││
│  │  • Agent run traces with timeline                           ││
│  │  • Cost breakdown: LLM ₹ | Tools ₹ | Retries ₹              ││
│  │  • MCP server catalog & health                              ││
│  │  • Budget burn + alerts                                     ││
│  │  • Cache hit rates & model distribution                     ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

## ✨ Six Killer Features

1. **Agent execution tracing** — every chat completion *and* MCP tool call is
   recorded as a step in a run. The dashboard renders the full timeline, including
   tokens, latency, cache hits, retries, and cost per step.
2. **MCP gateway** — register servers once, invoke tools through
   `/v1/mcp/invoke` with auth, per-server rate limiting (Redis fixed window),
   health checks, retries, and cost attribution. No direct server-to-agent access.
3. **True cost intelligence** — INR pricing for every LLM token *and* every tool
   call. Cache savings and compression savings are tracked explicitly.
4. **Budget enforcement** — per-agent monthly budgets with alert thresholds,
   automatic downgrade to a cheaper fallback model at the warn threshold, and an
   optional hard limit that rejects excess traffic with HTTP 402.
5. **First-class Indic support** — automatic Hindi / Tamil / Telugu / Marathi
   detection. Indic prompts are routed to **Sarvam-1**, cheaper and better than
   GPT-4o for South Asian languages.
6. **A dashboard that is actionable** — trace timelines, spend by
   model/agent/tool, cache hit rates, MCP health, budget alerts, and API keys.
7. **SSE streaming** — `"stream": true` returns OpenAI-compatible
   `chat.completion.chunk` events over `text/event-stream`, with a budget
   preflight (402 early) and usage recorded once at the end of the stream.
8. **PII + JSON Schema guardrails** — Aadhaar / PAN / phone / email / credit
   cards are masked before any prompt leaves the network, and
   `response_format.json_schema` enforces structured output (422 on violation).
9. **Observability + delivery** — OpenTelemetry spans (FastAPI / asyncpg /
   provider calls), a Python SDK, and a CI pipeline with coverage + live e2e.

## 🚀 30-Second Start

```bash
cp .env.example .env
# Add keys: OPENAI_API_KEY, ANTHROPIC_API_KEY, SARVAM_API_KEY
docker compose up --build
```

Open:

- API docs: http://localhost:8000/docs
- Dashboard: http://localhost:8000/dashboard

The Compose stack runs the API, PostgreSQL (ledger + traces + registry), and
Redis (semantic cache + MCP rate limiting). Ollama stays behind an optional
profile:

```bash
docker compose --profile local-llm up --build
docker compose exec ollama ollama pull llama3.1
```

> **Deploying to the internet for free?** See [`DEPLOY.md`](DEPLOY.md) — a
> verified Koyeb (free, no-sleep) + Neon + Upstash walkthrough, including the
> two gotchas that are specific to this app: use Neon's *direct* connection
> string (asyncpg breaks behind PgBouncer) and Upstash's `rediss://` TLS URL.
> Ollama is dev-only there; production runs on API keys.

## 🔌 Connect Your Agent Framework

### LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8000/v1",
    api_key="sk-<your-virtual-key>",
    model="gpt-4o",
)
# Every call now goes through AgentMesh: tracing, caching, compression, routing, budgets.
```

### CrewAI / AutoGen / Mastra

Same pattern — point `base_url` at `http://localhost:8000/v1` and use a virtual
key. Tag calls per agent with `agent_id` so traces and budgets group correctly.

```json
POST /v1/chat/completions
{
  "messages": [{"role": "user", "content": "Explain the incident"}],
  "agent_id": "support_bot",
  "run_id": "run_abc123"
}
```

## 🔑 Virtual Keys & Auth

Virtual keys gate tenant traffic; an admin key gates the control plane.

```bash
# Issue a virtual key for your agent (admin only)
curl -X POST http://localhost:8000/v1/admin/keys \
  -H "Authorization: Bearer $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"my-agent"}'
# -> {"id":1,"name":"my-agent","key":"sk-<prefix>-<secret>","key_prefix":"<prefix>"}
```

Only a SHA-256 hash of the secret is stored. Set `ALLOW_NO_AUTH=false` in
production; while `true` (default) auth is skipped for local development.

## 🛠️ MCP Server Management

```bash
# Register an MCP server (admin only)
curl -X POST http://localhost:8000/v1/mcp/registry \
  -H "Authorization: Bearer $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "name": "postgres_tools",
    "endpoint": "http://localhost:3001/sse",
    "auth_type": "bearer",
    "auth_token": "internal-token",
    "rate_limit_rpm": 60,
    "cost_per_call_inr": 0.0
  }'

# Use it through the gateway — rate-limited, audited, attributed
curl -X POST http://localhost:8000/v1/mcp/invoke \
  -H "Authorization: Bearer sk-..." -H "Content-Type: application/json" \
  -d '{"server":"postgres_tools","tool":"execute_query","arguments":{"query":"SELECT 1"}}'
```

Any MCP server speaking JSON-RPC 2.0 over HTTP (Streamable HTTP transport) works.
Zero-infra demo tools are built in — register `{"endpoint": "builtin://local"}`
and invoke `echo` / `now` with no extra infrastructure.

## 🤖 Agent Runs

Script an agent run as a chain of LLM calls and MCP tool calls. Steps can feed
`{{step.1.output}}` into later prompts:

```bash
curl -X POST http://localhost:8000/v1/agents/support_bot/run \
  -H "Content-Type: application/json" -d '{
    "agent_id": "support_bot",
    "actions": [
      {"type": "mcp", "server": "local_tools", "tool": "echo", "arguments": {"text": "summarize incident"}},
      {"type": "llm", "prompt": "Analyze this event: {{step.1.output}}"}
    ]
  }'
```

The response is a full trace: each step's model/tool, tokens, latency, cost in
INR, cache hits, and retries, plus run totals.

## 📊 Cost Intelligence

Every run produces:

```json
{
  "run_id": "run_abc123",
  "total_cost_inr": 12.55,
  "steps": [
    {"step_type": "llm", "model": "gpt-4o", "cost_inr": 2.40,  "latency_ms": 1200},
    {"step_type": "mcp", "tool": "brave_search", "cost_inr": 0.85, "latency_ms": 800},
    {"step_type": "llm", "model": "claude-sonnet", "cost_inr": 4.20, "latency_ms": 2100}
  ]
}
```

## 💰 Budget Enforcement

```bash
curl -X POST http://localhost:8000/v1/admin/agents \
  -H "Authorization: Bearer $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "agent_id": "support_bot",
    "monthly_budget_inr": 5000,
    "alert_thresholds": [0.5, 0.8, 0.95],
    "hard_limit": true,
    "fallback_model": "sarvam-1-lite"
  }'
```

- Threshold crossings fire (once per month each) into the Alerts tab.
- At the warn threshold the gateway auto-routes to `fallback_model`.
- With `hard_limit`, HTTP 402 once the budget is exhausted.

## 🇮🇳 First-Class Indic Support

AgentMesh detects Hindi, Tamil, Telugu, and Marathi prompts and routes them to
**Sarvam-1** automatically:

```json
POST /v1/chat/completions
{"messages": [{"role": "user", "content": "मौसम कैसा है?"}]}
```

When `SARVAM_API_KEY` is set, this routes to `sarvam-1`
(₹150/1M tokens) instead of a general-purpose model. Sarvam also becomes the
budget-fallback target so Indic traffic stays local and cheap.

## 📈 Dashboard

| Page | Shows |
|---|---|
| `/dashboard` | Totals, cache hit rate, spend by model / agent / MCP tool, recent runs |
| `/dashboard/runs/{run_id}` | Full trace timeline with per-step LLM/tool cost + latency |
| `/dashboard/agents` | Budgets, spend bars, status, fallback model |
| `/dashboard/mcp` | Server catalog, rate limits, per-call cost, health |
| `/dashboard/budgets` | Alert history |
| `/dashboard/keys` | Issued virtual keys |

## 🧪 Benchmarks

Live Locust suite + a reference run are in
[`benchmarks/RESULTS.md`](benchmarks/RESULTS.md):

```bash
locust -f benchmarks/locustfile.py --host http://localhost:8000 \
    --headless -u 40 -r 10 --run-time 3m --html benchmarks/report.html
```

| Scenario | Direct API | Through AgentMesh | Savings |
|---|---|---|---|
| 1000 support agent runs | ₹25,000 | ₹14,200 | 43% |
| With semantic cache enabled | ₹25,000 | ₹9,800 | 61% |
| With prompt compression | ₹25,000 | ₹8,200 | 67% |

## 📦 Python SDK

`agentmesh`sdk ships a thin client covering the control-plane surface:

```python
from agentmesh import AgentMeshClient

async with AgentMeshClient("http://localhost:8000", api_key="sk-...") as client:
    await client.register_server("local_tools", "builtin://local")
    with client.run("support_bot") as run:           # traceable run_id
        reply = await run.chat([{"role": "user", "content": "Explain the incident"}])
        tool = await run.invoke_tool("local_tools", "now", {})
    print(await run.summary())                        # full costed trace
```

## 🛠️ Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

docker compose up postgres redis      # infra only
uvicorn app.main:app --reload
```

Run tests:

```bash
pytest                       # unit + integration
pytest --cov=app --cov-report=term-missing   # with coverage
```

CI (`.github/workflows/ci.yml`) runs the full unit suite with coverage plus a
live e2e that boots the gateway with Postgres + Redis services and exercises
MCP registration, tool invoke, an agent run, and the dashboard.

## 📁 Project Layout

```
app/
  main.py          FastAPI app + dashboard pages
  gateway.py       unified /v1/chat/completions pipeline (route→compress→cache→cost→trace)
  agents.py        scripted agent-run executor
  budgets.py       per-agent budget enforcement (CostGuard)
  auth.py          virtual keys + admin key auth
  db.py            asyncpg schema + ledger/trace/registry queries
  router.py        language + complexity model routing
  language.py      Indic script/language detection
  costs.py         INR pricing incl. Sarvam-1
  guardrails.py    PII redaction before prompts leave the network
  json_schema.py   JSON Schema enforcement for structured output
  sse.py           SSE framing (chat.completion.chunk / [DONE] / errors)
  telemetry.py     OpenTelemetry spans + FastAPI/asyncpg instrumentation
  adapters/        OpenAI / Anthropic / Sarvam / Ollama providers
  mcp/             JSON-RPC MCP client + registry (rate limit, health, cost)
  routers/         chat, mcp, agents, runs, admin HTTP APIs
  templates/       Jinja2 dashboard views
```

Also see [`CASE_STUDY.md`](CASE_STUDY.md) for a production scenario with measured
results, and [`scripts/demo.sh`](scripts/demo.sh) for a scripted walkthrough.