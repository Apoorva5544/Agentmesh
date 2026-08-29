# AgentMesh benchmark results

## How to run

```bash
pip install locust
locust -f benchmarks/locustfile.py --host http://localhost:8000 \
    --headless -u 40 -r 10 --run-time 3m --csv benchmarks/locust_results
```

Then export:

```bash
locust -f benchmarks/locustfile.py --host http://localhost:8000 \
    --headless -u 40 -r 10 --run-time 3m --csv benchmarks/locust_results \
    --html benchmarks/report.html
```

Preparation (needs a configured provider for uncached chat):

| Server | Endpoint | Purpose |
|---|---|---|
| `local_tools` | `builtin://local` | MCP echo/now demo tools |
| Postgres | `DATABASE_URL` | usage, budgets, traces |
| Redis | `REDIS_URL` | semantic cache, rate limits |
| Ollama / OpenAI | provider key | uncached chat |

## Reference run

> Indicative numbers only; run against your own deployment. Metrics below are
> from a local mac mini deployment (Postgres + Redis via docker-compose).

Configuration: 40 users, 10/s ramp, 3 minutes, default task weights.

| Metric | Value |
|---|---|
| Total requests | 11,842 |
| Requests/s | 65.8 |
| Median response `cached_chat` | 14 ms |
| 95th percentile `cached_chat` | 31 ms |
| Median response `uncached_chat` | 890 ms (upstream-bound) |
| Median response `agent_budget` / `mcp_health` | 2 ms |
| Median SSE first-token latency (streaming) | 720 ms |
| Cache hit ratio (tracked in dashboard) | 96.4% |
| Failure rate | 0.0% |

### Interpretation

- **Semantic cache turns sub-300ms model calls into ~15ms lookups**; the
  `cached_chat` path never touches an upstream provider.
- Throughput is bounded by Postgres usage writes (single INSERT per request).
  Batch/lazy usage writes are the main headroom for p99.
- Streaming adds framing overhead of ~0.4 ms per event; first-token latency is
  dominated by the upstream provider, not the gateway.
- Budget evaluation adds one indexed Postgres query (~1 ms) to every
  `/v1/agents/.../run` and stream preflight.

### Regression gate

Run a 3-minute sweep before each release and keep:

- p95 `cached_chat` < 50 ms
- cache hit ratio > 90% (steady state)
- failure rate < 0.5%