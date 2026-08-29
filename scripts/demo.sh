#!/usr/bin/env bash
# AgentMesh demo: register an MCP server, run a multi-step agent, stream a
# completion, and show the traced run + dashboard numbers.
set -euo pipefail

BASE="${BASE:-http://localhost:8000}"
ADMIN="${ADMIN_KEY:-}"

auth_args=()
if [[ -n "$ADMIN" ]]; then
  auth_args=(-H "Authorization: Bearer $ADMIN")
fi

echo "== 1. Register local MCP server =="
curl -fsS "${auth_args[@]}" -X POST "$BASE/v1/mcp/registry" \
  -H 'Content-Type: application/json' \
  -d '{"name":"local_tools","endpoint":"builtin://local","description":"demo tools","cost_per_call_inr":0.25}'
echo

echo "== 2. Configure the support agent + budget =="
curl -fsS "${auth_args[@]}" -X POST "$BASE/v1/agents" \
  -H 'Content-Type: application/json' \
  -d '{"agent_id":"support_bot","display_name":"Support Bot","monthly_budget_inr":100,"alert_thresholds":[0.5,0.8],"hard_limit":true}'
echo

echo "== 3. Scripted agent run (2 MCP steps, costed trace) =="
RUN=$(curl -fsS -X POST "$BASE/v1/agents/support_bot/run" \
  -H 'Content-Type: application/json' \
  -d '{"actions":[{"type":"mcp","server":"local_tools","tool":"echo","arguments":{"text":"order 10042 status: shipped"}},{"type":"mcp","server":"local_tools","tool":"now","arguments":{}}]}')
echo "$RUN" | python3 -m json.tool
RUN_ID=$(echo "$RUN" | python3 -c 'import sys,json;print(json.load(sys.stdin)["run_id"])')

echo "== 4. Full trace (JSON) =="
curl -fsS -X GET "$BASE/v1/runs/$RUN_ID" | python3 -m json.tool

echo "== 5. Streaming chat (SSE) =="
curl -fsS -N -X POST "$BASE/v1/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Count from one to five, one per line."}],"stream":true,"max_tokens":64}' |
  grep -E '^data: ' | head -n 8

echo "== 6. Cost dashboard =="
curl -fsS "$BASE/metrics" | python3 -m json.tool
echo "Open http://localhost:8000/dashboard for the full UI."