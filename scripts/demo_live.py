#!/usr/bin/env python3
"""Real-agent demo against a deployed AgentMesh gateway.

This is what an actual integration looks like: an agent makes LLM calls,
invokes MCP tools, and reasons over tool output -- every step tagged with
agent_id + run_id so the live dashboard shows a full costed trace.

Run:
    AGENTMESH_BASE=https://agentmesh-ykug.onrender.com \
    AGENTMESH_ADMIN_KEY=<your-admin-key> \
    python3 scripts/demo_live.py
"""

import asyncio
import json
import os

from agentmesh import AgentMeshClient

BASE = os.environ.get("AGENTMESH_BASE", "https://agentmesh-ykug.onrender.com")
ADMIN_KEY = os.environ["AGENTMESH_ADMIN_KEY"]


async def main() -> None:
    # Control-plane side: one virtual key gates a "tenant" agent.
    admin = AgentMeshClient(BASE, api_key=ADMIN_KEY)
    key = await admin._post("/v1/admin/keys", {"name": "demo-robot"})["key"]
    print(f"virtual key minted: {key['key_prefix']}...")

    # Register an MCP server (admin-only) and an agent with a budget.
    await admin.register_server(
        "local_tools",
        "builtin://local",
        description="demo tools (echo, now)",
        cost_per_call_inr=0.25,
    )
    await admin.create_agent(
        "support_bot",
        "support automation demo",
        monthly_budget_inr=500,
        hard_limit_inr=600,
    )

    # Tenant side: a real agent workflow through the gateway.
    client = AgentMeshClient(BASE, api_key=key["key"])
    with client.run("support_bot") as run:
        reply = await run.chat(
            [{"role": "user", "content": "Order 10042 shows 'shipped' in the feed. Summarize its status and ask me what to check next."}],
            model="gpt-4o-mini",
        )
        print(f"LLM step  -> {reply['model']} ({reply['routing_reason']}) cost ₹{reply['estimated_cost_inr']:.2f}")
        print(f"           {reply['content']}\n")

        lookup = await run.invoke_tool("local_tools", "echo", {"text": "order 10042 status: delivered"})
        print(f"MCP step  -> local_tools.echo cost ₹{lookup['cost_inr']:.2f}: {lookup['output']}\n")

        followup = await run.chat(
            [{"role": "user", "content": f"Tool says: {lookup['output']}. Draft a short customer reply."}],
            model="gpt-4o-mini",
        )
        print(f"LLM step  -> {followup['model']} cost ₹{followup['estimated_cost_inr']:.2f}")
        print(f"           {followup['content']}\n")

        trace = await run.summary()
        print(json.dumps(trace, indent=2, default=str))

    print(f"\nOpen the live dashboard: {BASE}/dashboard")
    print(f"Run trace detail: {BASE}/dashboard/runs/{trace['run_id']}")


if __name__ == "__main__":
    asyncio.run(main())