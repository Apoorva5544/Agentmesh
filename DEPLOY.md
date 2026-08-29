# Deploying AgentMesh on Koyeb (free) + Neon + Upstash

A permanent HTTPS URL for **₹0/month, no card** — Koyeb free web services use
your Dockerfile and never sleep. This page is verified against the repo: the
image builds clean and boots purely from environment variables (no `.env` file
in the image).

> Why this stack: our app needs Postgres (`asyncpg`) + Redis (`redis.asyncio`) +
> a long-running web service. Render sleeps (bad cold starts), Railway dropped
> its free tier. Koyeb + Neon + Upstash are the combination that stays live.

---

## 1. Postgres — Neon (10 min)

1. Sign up at https://neon.tech with GitHub (no card).
2. New project → name `agentmesh` (region: nearest to you).
3. Go to **Connection Details** and toggle **"Direct connection (TCP)"** — this
   is the critical part: Neon's default *pooled* URL (`-pooler.` host) routes
   through PgBouncer, which breaks `asyncpg`'s prepared statements. Use the
   **direct** URL instead. It looks like:
   ```
   postgresql://user:password@ep-xxx.ca-central-1.aws.neon.tech/agentmesh?sslmode=require
   ```
   (the host has **no** `-pooler`), and keep `?sslmode=require`.
4. Copy it — everything else is automatic. On first boot the gateway creates
   its schema itself (`app/db.py` runs migrations at startup).

## 2. Redis — Upstash (5 min)

1. Sign up at https://console.upstash.com (GitHub, no card).
2. Create a **Redis** database → copy the **connection** string, not the REST
   API one. It uses TLS, so the scheme is **`rediss://`**:
   ```
   rediss://default:password@excited-snail-12345.upstash.io:6379
   ```
   Without `rediss://` TLS on, writes fail and Redis just disappears (the
   gateway fails open on cache/rate-limiter, so it degrades gracefully).

## 3. Push this repo to GitHub

See `README`/repo state — the directory is already a git repo with two commits.

## 4. Koyeb web service (10 min)

1. Sign up at https://app.koyeb.com (GitHub, no card).
2. **Create Web Service** → deploy from your GitHub repo.
3. Builder: **Dockerfile** (root must contain `Dockerfile` — it does).
4. Instance: **Free** (512 MB / single instance; free instances run in FRA).
5. Environment variables — copy from `scripts/deploy/koyeb.env.example`
   (they must come from env vars, not a `.env` file):
   | Key | Value |
   |---|---|
   | `DATABASE_URL` | Neon **direct** URL with `?sslmode=require` |
   | `REDIS_URL` | Upstash **`rediss://`** connection string |
   | `DEFAULT_PROVIDER` | `openai` |
   | `DEFAULT_MODEL` | `gpt-4o-mini` |
   | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `SARVAM_API_KEY` | your keys (blank = that provider stays unused) |
   | `ADMIN_API_KEY` | a long random string |
   | `ALLOW_NO_AUTH` | `false` |
   | `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE` | `1` / `5` (stay under Neon's connection limit) |
   | `OTEL_ENABLED` | `false` |
6. **Advanced** → Health-check path: `/health` (returns `{"status":"ok","database":"ok"}`).
7. Deploy. Build + boot takes ~3–5 min.

## 5. Verify (replace `agentmesh-<you>`)

```bash
curl https://agentmesh-<you>.koyeb.app/health
# {"status":"ok","database":"ok"}

# Issue a virtual key (admin key required because ALLOW_NO_AUTH=false)
curl -X POST https://agentmesh-<you>.koyeb.app/v1/admin/keys \
  -H "Authorization: Bearer $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"demo"}'
# -> {"id":1,"name":"demo","key":"sk-<prefix>-<secret>",...}  keep this key

# Chat through the gateway with the virtual key
curl -X POST https://agentmesh-<you>.koyeb.app/v1/chat/completions \
  -H "Authorization: Bearer sk-<prefix>-<secret>" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hello"}],"max_tokens":64}'

# Register a built-in MCP server, then invoke a tool
curl -X POST https://agentmesh-<you>.koyeb.app/v1/mcp/registry \
  -H "Authorization: Bearer $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"local_tools","endpoint":"builtin://local"}'
curl -X POST https://agentmesh-<you>.koyeb.app/v1/mcp/invoke \
  -H "Authorization: Bearer sk-<prefix>-<secret>" -H "Content-Type: application/json" \
  -d '{"server":"local_tools","tool":"now","arguments":{}}'
```

Open: dashboard `https://agentmesh-<you>.koyeb.app/dashboard`, docs `/docs`.

## Free-tier limits to know

- **Neon** suspends the compute after ~5 min idle; the first request then pays a
  few seconds of cold start (Koyeb health-checks help keep it warm). 0.5 GB
  storage. Keep `DB_POOL_MAX_SIZE` low.
- **Upstash** free = ~10K commands/day. Chat + cache easily fit a demo; a
  production workload should use the paid tier.
- **Ollama is NOT run here** — 512 MB can't hold a model. Use OpenAI/Anthropic/
  Sarvam keys (see below for a local-Ollama interview demo).
- Dashboard/static pages are **not** behind auth (`require_admin`/`require_tenant`
  only gate the API). Fine for a demo; put an auth proxy in front of `/dashboard*`
  if this becomes shared.

## Local Ollama demo (interview bonus)

Keep the full compose stack local and expose it with a temporary tunnel —
Ollama runs on your laptop, the gateway+PPI+cache all still work:

```bash
docker compose up -d --profile local-llm
docker compose exec ollama ollama pull qwen2:7b
brew install cloudflared
cloudflared tunnel --url http://localhost:8000   # prints a public https URL
```