"""Locust benchmark suite for AgentMesh.

Target the gateway (typically behind a registered MCP server + Redis cache):

    locust -f benchmarks/locustfile.py --host http://localhost:8000 \
        --headless -u 40 -r 10 --run-time 3m --csv benchmarks/locust_results

Task mix:
  - cached_chat (5x): hits the semantic cache, no upstream LLM call.
  - uncached_chat (2x): passes through to an upstream provider (configure one).
  - agent_budget (1x): exercises cost control + DB usage recording.
  - mcp_invoke (1x): MCP tool round-trip against a registered server.
"""

import uuid

from locust import HttpUser, between, task

CACHEABLE = "Describe semantic caching in one short paragraph."


class GatewayUser(HttpUser):
    wait_time = between(0.05, 0.3)

    @task(5)
    def cached_chat(self):
        self.client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": CACHEABLE}],
                "provider": "ollama",
                "model": "llama3.1",
                "max_tokens": 256,
                "use_cache": True,
            },
        )

    @task(2)
    def uncached_chat(self):
        self.client.post(
            "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "user", "content": f"Unique probe {uuid.uuid4().hex}: summarise in 10 words."}
                ],
                "provider": "ollama",
                "model": "llama3.1",
                "max_tokens": 128,
                "use_cache": True,
            },
        )

    @task(1)
    def agent_budget(self):
        self.client.post(
            "/v1/agents/bench_bot/run",
            json={
                "agent_id": "bench_bot",
                "actions": [
                    {"type": "mcp", "server": "local_tools", "tool": "echo", "arguments": {"text": "tick"}}
                ],
            },
        )

    @task(1)
    def mcp_health(self):
        self.client.get("/v1/mcp/health")


class StreamUser(HttpUser):
    """Streaming chat: exercises SSE framing + adapter stream parsing."""

    wait_time = between(0.2, 0.8)

    @task(1)
    def stream_chat(self):
        self.client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Count from one to three."}],
                "provider": "ollama",
                "model": "llama3.1",
                "max_tokens": 64,
                "stream": True,
            },
        )