import time
import uuid
from typing import AsyncIterator

from fastapi import HTTPException

from app.costs import estimate_cost_inr, estimate_cost_usd
from app.dependencies import Container
from app.guardrails import redact_pii
from app.json_schema import SchemaValidationError, build_prompt, validate_output
from app.schemas import ChatCompletionRequest, ChatMessage
from app.sse import GatewayStreamError
from app.telemetry import span
from app.tokenizer import count_tokens


class BudgetBlocked(Exception):
    def __init__(self, verdict) -> None:
        self.verdict = verdict
        super().__init__("budget exhausted for agent")


class ChatGateway:
    """The unified /v1/chat/completions pipeline: budget guard -> language &
    complexity routing -> PII redaction -> prompt compression -> JSON schema
    enforcement -> semantic cache -> provider call -> cost attribution -> trace &
    ledger recording. Supports streaming (SSE) and non-streaming responses."""

    def __init__(self, container: Container) -> None:
        self.container = container

    def _new_run_id(self, agent_id: str) -> str:
        return f"run_{agent_id[:24]}-{uuid.uuid4().hex[:10]}"

    # ------------------------------------------------------------------ prepare
    async def _prepare(self, payload: ChatCompletionRequest, run_id: str, agent_id: str) -> dict:
        c = self.container
        verdict = await c.budgets.evaluate(agent_id)
        if verdict.status == "blocked":
            raise BudgetBlocked(verdict)
        if verdict.status == "warn" and verdict.fallback_model:
            payload = payload.model_copy(
                update={
                    "model": verdict.fallback_model,
                    "provider": c.router.provider_for_model(verdict.fallback_model),
                }
            )

        await c.db.create_run(run_id, agent_id)

        schema = None
        if payload.response_format and payload.response_format.type == "json_schema" and payload.response_format.json_schema:
            schema = payload.response_format.json_schema.value
            enriched = build_prompt(
                [m.model_dump() for m in payload.messages],
                schema,
            )
            payload = payload.model_copy(
                update={"messages": [ChatMessage(**m) for m in enriched]}
            )

        rendered = c.compressor.render_messages(payload.messages)
        provider, model, reason = c.router.choose(payload, rendered)

        if payload.compress_prompt:
            prompt, original_tokens, compressed_tokens = c.compressor.compress(payload.messages, model)
        else:
            prompt = rendered
            original_tokens = count_tokens(prompt, model)
            compressed_tokens = original_tokens

        return {
            "payload": payload,
            "provider": provider,
            "model": model,
            "reason": reason,
            "prompt": prompt,
            "original_tokens": original_tokens,
            "compressed_tokens": compressed_tokens,
            "schema": schema,
        }

    @staticmethod
    def _usage_base(prep: dict, run_id: str, agent_id: str) -> dict:
        return {
            "run_id": run_id,
            "agent_id": agent_id,
            "provider": prep["provider"],
            "model": prep["model"],
            "route": "/v1/chat/completions",
            "prompt_tokens": prep["original_tokens"],
            "compressed_prompt_tokens": prep["compressed_tokens"],
        }

    # --------------------------------------------------------------- non-stream
    async def complete(
        self,
        payload: ChatCompletionRequest,
        identity: str = "local",
    ) -> dict:
        c = self.container
        agent_id = payload.agent_id or "default"
        run_id = payload.run_id or self._new_run_id(agent_id)
        prep = await self._prepare(payload, run_id, agent_id)
        provider, model, reason = prep["provider"], prep["model"], prep["reason"]
        schema = prep["schema"]

        prompt, detections = redact_pii(prep["prompt"])
        pii_count = sum(d["count"] for d in detections)
        embedding = c.embeddings.embed(prompt)
        compression_saved = estimate_cost_inr(
            provider, model, prep["original_tokens"] - prep["compressed_tokens"], 0
        )
        started = time.perf_counter()

        if payload.use_cache:
            with span("cache.lookup", {"provider": provider, "model": model}):
                cached = await c.cache.lookup(embedding)
            if cached:
                cost_saved = estimate_cost_inr(
                    cached.provider, cached.model, cached.prompt_tokens, cached.completion_tokens
                )
                saved = cost_saved + compression_saved
                latency_ms = int((time.perf_counter() - started) * 1000)
                await c.db.add_trace_step(run_id, {
                    "step_no": 1, "step_type": "llm",
                    "provider": cached.provider, "model": cached.model, "tool": None,
                    "tokens_in": cached.prompt_tokens, "tokens_out": cached.completion_tokens,
                    "cost_inr": 0.0, "saved_cost_inr": round(saved, 6),
                    "latency_ms": latency_ms, "cache_hit": True, "retried": False, "error": None,
                })
                await c.db.record_usage({
                    **self._usage_base(prep, run_id, agent_id),
                    "cache_hit": True,
                    "completion_tokens": cached.completion_tokens,
                    "tokens_saved": prep["original_tokens"] + cached.completion_tokens,
                    "estimated_cost_inr": 0.0,
                    "estimated_cost_saved_inr": round(saved, 6),
                    "pii_redactions": pii_count,
                    "schema_requested": schema is not None,
                    "schema_passed": True if schema is not None else None,
                })
                await c.db.update_run_totals(run_id)
                return {
                    "id": f"chatcmpl-cache-{uuid.uuid4().hex}",
                    "provider": cached.provider,
                    "model": cached.model,
                    "content": cached.response,
                    "cache_hit": True,
                    "prompt_tokens": prep["original_tokens"],
                    "compressed_prompt_tokens": prep["compressed_tokens"],
                    "completion_tokens": cached.completion_tokens,
                    "tokens_saved": prep["original_tokens"] + cached.completion_tokens,
                    "routing_reason": reason,
                    "run_id": run_id,
                    "agent_id": agent_id,
                    "estimated_cost_inr": 0.0,
                    "estimated_cost_usd": 0.0,
                    "estimated_cost_saved_inr": round(saved, 6),
                }

        try:
            with span("llm.complete", {"provider": provider, "model": model}):
                provider_response = await c.providers[provider].complete(
                    prompt=prompt,
                    model=model,
                    temperature=payload.temperature,
                    max_tokens=payload.max_tokens,
                )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            await c.db.add_trace_step(run_id, {
                "step_no": 1, "step_type": "llm",
                "provider": provider, "model": model, "tool": None,
                "tokens_in": prep["compressed_tokens"], "tokens_out": 0,
                "cost_inr": 0.0, "saved_cost_inr": round(compression_saved, 6),
                "latency_ms": latency_ms, "cache_hit": False, "retried": False,
                "error": f"pii_redactions={pii_count}: {exc}",
            })
            await c.db.update_run_totals(run_id)
            provider_name = {"openai": "OPENAI", "anthropic": "ANTHROPIC", "sarvam": "SARVAM"}.get(provider, provider.upper())
            raise HTTPException(status_code=503, detail=f"{provider_name} provider error: {exc}")

        content = provider_response.content
        completion_tokens = provider_response.completion_tokens or count_tokens(content, model)
        schema_passed = None
        if schema is not None:
            try:
                validate_output(content, schema)
                schema_passed = True
            except SchemaValidationError:
                schema_passed = False
                await c.db.record_usage({
                    **self._usage_base(prep, run_id, agent_id),
                    "cache_hit": False,
                    "completion_tokens": completion_tokens,
                    "tokens_saved": max(prep["original_tokens"] - prep["compressed_tokens"], 0),
                    "estimated_cost_inr": estimate_cost_inr(provider, model, prep["compressed_tokens"], completion_tokens),
                    "estimated_cost_saved_inr": round(compression_saved, 6),
                    "pii_redactions": pii_count,
                    "schema_requested": True,
                    "schema_passed": False,
                })
                await c.db.add_trace_step(run_id, {
                    "step_no": 1, "step_type": "llm",
                    "provider": provider, "model": model, "tool": None,
                    "tokens_in": prep["compressed_tokens"], "tokens_out": completion_tokens,
                    "cost_inr": estimate_cost_inr(provider, model, prep["compressed_tokens"], completion_tokens),
                    "saved_cost_inr": round(compression_saved, 6),
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "cache_hit": False, "retried": False,
                    "error": "JSON schema validation failed - retry with a stricter system prompt",
                })
                await c.db.update_run_totals(run_id)
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "response failed JSON schema enforcement",
                        "suggestion": "retry with response_format.type=json_schema to enable gateway-level validation",
                    },
                )

        cost = estimate_cost_inr(provider, model, prep["compressed_tokens"], completion_tokens)
        tokens_saved = max(prep["original_tokens"] - prep["compressed_tokens"], 0)
        latency_ms = int((time.perf_counter() - started) * 1000)

        await c.cache.store(
            embedding=embedding,
            response=content,
            provider=provider,
            model=model,
            prompt_tokens=prep["compressed_tokens"],
            completion_tokens=completion_tokens,
        )
        await c.db.add_trace_step(run_id, {
            "step_no": 1, "step_type": "llm",
            "provider": provider, "model": model, "tool": None,
            "tokens_in": prep["compressed_tokens"], "tokens_out": completion_tokens,
            "cost_inr": cost, "saved_cost_inr": round(compression_saved, 6),
            "latency_ms": latency_ms, "cache_hit": False, "retried": False, "error": None,
        })
        await c.db.record_usage({
            **self._usage_base(prep, run_id, agent_id),
            "cache_hit": False,
            "completion_tokens": completion_tokens,
            "tokens_saved": tokens_saved,
            "estimated_cost_inr": cost,
            "estimated_cost_saved_inr": round(compression_saved, 6),
            "pii_redactions": pii_count,
            "schema_requested": schema is not None,
            "schema_passed": schema_passed,
        })
        await c.db.update_run_totals(run_id)

        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "provider": provider,
            "model": model,
            "content": content,
            "cache_hit": False,
            "prompt_tokens": prep["original_tokens"],
            "compressed_prompt_tokens": prep["compressed_tokens"],
            "completion_tokens": completion_tokens,
            "tokens_saved": tokens_saved,
            "routing_reason": reason,
            "run_id": run_id,
            "agent_id": agent_id,
            "estimated_cost_inr": cost,
            "estimated_cost_usd": estimate_cost_usd(provider, model, prep["compressed_tokens"], completion_tokens),
            "estimated_cost_saved_inr": round(compression_saved, 6),
        }

    # ------------------------------------------------------------------ stream
    async def stream(self, payload: ChatCompletionRequest) -> AsyncIterator[str]:
        c = self.container
        agent_id = payload.agent_id or "default"
        run_id = payload.run_id or self._new_run_id(agent_id)
        prep = await self._prepare(payload, run_id, agent_id)
        provider, model = prep["provider"], prep["model"]

        prompt, detections = redact_pii(prep["prompt"])
        pii_count = sum(d["count"] for d in detections)
        embedding = c.embeddings.embed(prompt)
        compression_saved = estimate_cost_inr(
            provider, model, prep["original_tokens"] - prep["compressed_tokens"], 0
        )
        started = time.perf_counter()

        if payload.use_cache:
            with span("cache.lookup", {"provider": provider, "model": model}):
                cached = await c.cache.lookup(embedding)
            if cached:
                cost_saved = estimate_cost_inr(
                    cached.provider, cached.model, cached.prompt_tokens, cached.completion_tokens
                )
                saved = cost_saved + compression_saved
                latency_ms = int((time.perf_counter() - started) * 1000)
                await c.db.add_trace_step(run_id, {
                    "step_no": 1, "step_type": "llm",
                    "provider": cached.provider, "model": cached.model, "tool": None,
                    "tokens_in": cached.prompt_tokens, "tokens_out": cached.completion_tokens,
                    "cost_inr": 0.0, "saved_cost_inr": round(saved, 6),
                    "latency_ms": latency_ms, "cache_hit": True, "retried": False, "error": None,
                })
                await c.db.record_usage({
                    **self._usage_base(prep, run_id, agent_id),
                    "cache_hit": True,
                    "completion_tokens": cached.completion_tokens,
                    "tokens_saved": prep["original_tokens"] + cached.completion_tokens,
                    "estimated_cost_inr": 0.0,
                    "estimated_cost_saved_inr": round(saved, 6),
                    "pii_redactions": pii_count,
                    "schema_requested": False,
                    "schema_passed": None,
                })
                await c.db.update_run_totals(run_id)
                yield cached.response
                return

        chunks: list[str] = []
        try:
            with span("llm.stream", {"provider": provider, "model": model}):
                async for delta in c.providers[provider].stream(
                    prompt=prompt,
                    model=model,
                    temperature=payload.temperature,
                    max_tokens=payload.max_tokens,
                ):
                    if delta:
                        chunks.append(delta)
                        yield delta
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            await c.db.add_trace_step(run_id, {
                "step_no": 1, "step_type": "llm",
                "provider": provider, "model": model, "tool": None,
                "tokens_in": prep["compressed_tokens"], "tokens_out": 0,
                "cost_inr": 0.0, "saved_cost_inr": round(compression_saved, 6),
                "latency_ms": latency_ms, "cache_hit": False, "retried": False,
                "error": str(exc),
            })
            await c.db.update_run_totals(run_id)
            provider_name = {"openai": "OPENAI", "anthropic": "ANTHROPIC", "sarvam": "SARVAM"}.get(provider, provider.upper())
            raise GatewayStreamError(f"{provider_name} provider error: {exc}") from exc

        content = "".join(chunks)
        completion_tokens = count_tokens(content, model)
        cost = estimate_cost_inr(provider, model, prep["compressed_tokens"], completion_tokens)
        latency_ms = int((time.perf_counter() - started) * 1000)

        await c.cache.store(
            embedding=embedding,
            response=content,
            provider=provider,
            model=model,
            prompt_tokens=prep["compressed_tokens"],
            completion_tokens=completion_tokens,
        )
        await c.db.add_trace_step(run_id, {
            "step_no": 1, "step_type": "llm",
            "provider": provider, "model": model, "tool": None,
            "tokens_in": prep["compressed_tokens"], "tokens_out": completion_tokens,
            "cost_inr": cost, "saved_cost_inr": round(compression_saved, 6),
            "latency_ms": latency_ms, "cache_hit": False, "retried": False, "error": None,
        })
        await c.db.record_usage({
            **self._usage_base(prep, run_id, agent_id),
            "cache_hit": False,
            "completion_tokens": completion_tokens,
            "tokens_saved": max(prep["original_tokens"] - prep["compressed_tokens"], 0),
            "estimated_cost_inr": cost,
            "estimated_cost_saved_inr": round(compression_saved, 6),
            "pii_redactions": pii_count,
            "schema_requested": False,
            "schema_passed": None,
        })
        await c.db.update_run_totals(run_id)