"""End-to-end unit tests for the ChatGateway pipeline with a fake container."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.compression import PromptCompressor
from app.embeddings import HashEmbeddingProvider
from app.gateway import BudgetBlocked, ChatGateway
from app.router import ModelRouter
from app.schemas import ChatCompletionRequest, ChatMessage
from app.sse import GatewayStreamError


class FakeBudget:
    def __init__(self, verdict):
        self.verdict = verdict

    async def evaluate(self, agent_id):
        return self.verdict


class FakeDB:
    def __init__(self):
        self.steps = []
        self.usage = []
        self.totals_updated = 0
        self.runs = []

    async def create_run(self, run_id, agent_id):
        self.runs.append(run_id)

    async def add_trace_step(self, run_id, step):
        self.steps.append(step)

    async def record_usage(self, event):
        self.usage.append(event)

    async def update_run_totals(self, run_id):
        self.totals_updated += 1


class FakeProvider:
    def __init__(self, content):
        self.content = content
        self.last_prompt = None
        self.stream_chunks = []
        self.stream_error = None

    async def complete(self, prompt, model, temperature, max_tokens):
        self.last_prompt = prompt
        return SimpleNamespace(content=self.content, completion_tokens=None)

    async def stream(self, prompt, model, temperature, max_tokens):
        self.last_prompt = prompt
        if self.stream_error is not None:
            raise self.stream_error
        for chunk in self.stream_chunks:
            yield chunk


class FakeCache:
    def __init__(self, hit=None):
        self.hit = hit
        self.stores = []

    async def lookup(self, embedding):
        return self.hit

    async def store(self, **kwargs):
        self.stores.append(kwargs)


def _make_container(verdict=None, cache_hit=None):
    verdict = verdict or SimpleNamespace(
        status="ok", spend_inr=0.0, budget_inr=0.0, ratio=0.0, fallback_model=None
    )
    providers = {
        "ollama": FakeProvider("Hello from Ollama."),
        "openai": FakeProvider("Hello from OpenAI."),
    }
    container = SimpleNamespace(
        budgets=FakeBudget(verdict),
        db=FakeDB(),
        router=ModelRouter("ollama", "llama3.1"),
        compressor=PromptCompressor(token_budget=4000),
        embeddings=HashEmbeddingProvider(dimensions=64),
        providers=providers,
        cache=FakeCache(hit=cache_hit),
    )
    return container, providers


def _request(content: str, **kwargs):
    return ChatCompletionRequest(
        messages=[ChatMessage(role="user", content=content)], **kwargs
    )


CHEM_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "chem",
        "schema": {
            "type": "object",
            "properties": {
                "compound": {"type": "string"},
                "formula": {"type": "string"},
                "toxicity": {"type": "number"},
            },
            "required": ["compound", "formula", "toxicity"],
        },
    },
}


async def test_complete_non_stream_records_pipeline() -> None:
    container, providers = _make_container()
    payload = _request("say hi", provider="ollama", model="llama3.1", use_cache=False)
    result = await ChatGateway(container).complete(payload)
    assert result["content"] == "Hello from Ollama."
    assert result["cache_hit"] is False
    assert result["model"] == "llama3.1"
    assert providers["ollama"].last_prompt is not None
    assert container.db.steps[0]["tool"] is None
    assert container.db.usage[0]["pii_redactions"] == 0
    assert container.db.usage[0]["schema_requested"] is False
    assert container.db.usage[0]["schema_passed"] is None


async def test_complete_cache_hit_skips_provider() -> None:
    hit = SimpleNamespace(
        provider="ollama",
        model="llama3.1",
        prompt_tokens=10,
        completion_tokens=5,
        response="cached reply",
    )
    container, providers = _make_container(cache_hit=hit)
    payload = _request("same cached prompt", use_cache=True)
    result = await ChatGateway(container).complete(payload)
    assert result["cache_hit"] is True
    assert result["content"] == "cached reply"
    assert providers["ollama"].last_prompt is None
    assert container.db.usage[0]["cache_hit"] is True


async def test_complete_redacts_pii_before_provider() -> None:
    container, providers = _make_container()
    payload = _request(
        "PAN ABCDE1234F and aadhaar 1234 5678 9012 for the customer",
        provider="ollama",
        model="llama3.1",
        use_cache=False,
    )
    await ChatGateway(container).complete(payload)
    sent = providers["ollama"].last_prompt
    assert "ABCDE1234F" not in sent
    assert "1234 5678 9012" not in sent
    assert "[REDACTED_PAN]" in sent
    assert container.db.usage[0]["pii_redactions"] >= 2


async def test_complete_json_schema_valid() -> None:
    container, providers = _make_container()
    providers["ollama"].content = (
        '{"compound": "aspirin", "formula": "C9H8O4", "toxicity": 3.2}'
    )
    payload = _request(
        "analyze aspirin", provider="ollama", model="llama3.1", use_cache=False,
        response_format=CHEM_SCHEMA,
    )
    result = await ChatGateway(container).complete(payload)
    assert result["content"].startswith("{")
    assert container.db.usage[0]["schema_requested"] is True
    assert container.db.usage[0]["schema_passed"] is True


async def test_complete_json_schema_failure_is_422() -> None:
    container, providers = _make_container()
    providers["ollama"].content = "sorry, I cannot comply"
    payload = _request(
        "analyze aspirin", provider="ollama", model="llama3.1", use_cache=False,
        response_format=CHEM_SCHEMA,
    )
    with pytest.raises(HTTPException) as exc:
        await ChatGateway(container).complete(payload)
    assert exc.value.status_code == 422
    assert container.db.usage[0]["schema_requested"] is True
    assert container.db.usage[0]["schema_passed"] is False


async def test_complete_budget_blocked_short_circuits() -> None:
    blocked = SimpleNamespace(
        status="blocked", spend_inr=150.0, budget_inr=100.0, ratio=1.5, fallback_model=None
    )
    container, providers = _make_container(verdict=blocked)
    payload = _request("hi", provider="ollama", model="llama3.1", use_cache=False)
    with pytest.raises(BudgetBlocked):
        await ChatGateway(container).complete(payload)
    assert providers["ollama"].last_prompt is None


async def test_complete_warn_uses_fallback_model() -> None:
    warn = SimpleNamespace(
        status="warn",
        spend_inr=80.0,
        budget_inr=100.0,
        ratio=0.8,
        fallback_model="gpt-4o-mini",
    )
    container, providers = _make_container(verdict=warn)
    payload = _request("hi", provider="ollama", model="llama3.1", use_cache=False)
    result = await ChatGateway(container).complete(payload)
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-4o-mini"
    assert providers["openai"].last_prompt is not None


async def test_provider_error_becomes_503() -> None:
    class Boom(Exception):
        pass

    async def fail(*args, **kwargs):
        raise Boom("credentials expired")

    container, providers = _make_container()
    providers["openai"].complete = fail
    payload = _request(
        "analyze the architecture tradeoffs here", provider="openai", use_cache=False
    )
    with pytest.raises(HTTPException) as exc:
        await ChatGateway(container).complete(payload)
    assert exc.value.status_code == 503
    assert "OPENAI provider error" in exc.value.detail


async def test_stream_yields_deltas_and_records_usage() -> None:
    container, providers = _make_container()
    providers["ollama"].stream_chunks = ["Hel", "lo, ", "world!"]
    payload = _request("say hello", provider="ollama", model="llama3.1", use_cache=False)
    collected = []
    async for delta in ChatGateway(container).stream(payload):
        collected.append(delta)
    assert collected == ["Hel", "lo, ", "world!"]
    assert container.db.usage[0]["cache_hit"] is False
    assert container.db.steps[0]["tokens_out"] >= 1
    assert providers["ollama"].last_prompt is not None


async def test_stream_cache_hit_yields_single_delta() -> None:
    hit = SimpleNamespace(
        provider="ollama",
        model="llama3.1",
        prompt_tokens=5,
        completion_tokens=5,
        response="cached",
    )
    container, providers = _make_container(cache_hit=hit)
    payload = _request("same prompt", use_cache=True)
    collected = []
    async for delta in ChatGateway(container).stream(payload):
        collected.append(delta)
    assert collected == ["cached"]
    assert container.db.usage[0]["cache_hit"] is True


async def test_stream_provider_error_raises_gateway_stream_error() -> None:
    container, providers = _make_container()
    providers["ollama"].stream_error = RuntimeError("connection reset")
    payload = _request("hi", provider="ollama", model="llama3.1", use_cache=False)
    with pytest.raises(GatewayStreamError) as exc:
        async for _ in ChatGateway(container).stream(payload):
            pass
    assert "OLLAMA provider error" in str(exc.value)