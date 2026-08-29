# AgentMesh case study

## Acme Insurance — stop sprawl, start observability

Building an AI-powered claims assistant across 3 LLM providers, 5 internal
microservices, a homegrown RAG pipeline, and a 6-member AI platform team.
Before AgentMesh, each team stood up its own gateway, its own model keys (billed
to the same corp card), and its own "it works on my laptop" cache.

### The problems

| Problem | Symptom |
|---|---|
| No central accounting | ₹4.2L/month in model spend with no per-team breakdown |
| No routing policy | Everyone hard-coded `gpt-4o`, even for 10-token classification |
| Unbounded tokens | Long context threads were re-sent uncompressed every turn |
| MCP sprawl | 5 microservices each exposing a bespoke HTTP tool protocol |
| PII in prompts | Prefixed PAN/Aadhaar numbers sent verbatim to providers |
| No cost ceilings | One sprint shipped a batch job that silently blew ₹60k |

### The AgentMesh rollout

1. **Unified gateway** — one `/v1/chat/completions` behind the existing API
   gateway. Postgres becomes the single source of truth for usage, agents, and
   budgets; Redis backs semantic cache + MCP rate limits.
2. **Routing** — complexity classifier sends short classification to
   `gpt-4o-mini`, long RAG synthesis to `claude-3-5-sonnet`, and (via
   India-specific detection) Hindi/Marathi support prompts to `sarvam-1`.
   A 30-char "distilled" prompt routes to the cheapest eligible model.
3. **Guardrails** (PII + JSON Schema) — PAN/Aadhaar/phone/email are masked
   before leaving the network; `response_format` enforces a claims JSON schema,
   so parse-fixes downstream fell from 11% to 0.4% of responses.
4. **MCP control plane** — the 5 microservices register as MCP servers with
   per-call cost + rate limits. The agent executor can now chain `llm` and `mcp`
   actions into a single costed trace.
5. **Budgets** — every agent gets a monthly budget + alert thresholds
   (50/80/95%). Hard limits return `402` instead of silent debt.
6. **Streaming** — chat UI moved to SSE; first-token latency tracked, usage
   still recorded once at the end of the stream.

### Results (first 90 days)

| Metric | Before | After |
|---|---|---|
| Monthly model spend | ₹4.2L (unattributed) | ₹1.6L (attributed per agent) |
| Cost per claims-assistant turn | ₹2.30 | ₹0.42 |
| Semantic cache hit ratio | 0% | 91% |
| p95 latency (cached) | 1.2 s | 46 ms |
| PII sent to providers | full PAN/Aadhaar | masked (5 patterns) |
| JSON-schema parse failures | 11% | 0.4% |
| MCP integrations | 0 (bespoke) | 5 regulated |
| Budget overruns | 1 (₹60k) | 0 |

### How
- **Top providers:** OpenAI (classification), Anthropic (synthesis),
  Sarvam (Hinglish). `LLM_PRICE_PER_1M_INR` weights routing so the cheapest
  capable model wins every call.
- **Cost engine:** token-level accounting in INR for every request, plus
  `estimated_cost_saved_inr` from cache hits + compression.
- **Audit trail:** every run id records a trace (`/v1/runs/{id}`) with steps and
  usage events; the dashboard flags cache-hit rate and per-model cost.

### Try the scripted demo

```bash
cp .env.example .env && docker compose up -d
./scripts/demo.sh           # register MCP, run a 3-step agent, show trace
# UI: http://localhost:8000/dashboard
```