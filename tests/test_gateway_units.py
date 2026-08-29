import pytest

from app.auth import generate_virtual_key, hash_secret
from app.budgets import CostGuard
from app.compression import PromptCompressor
from app.costs import SARVAM_MODEL_ALIASES, estimate_cost_inr
from app.embeddings import HashEmbeddingProvider, cosine_similarity
from app.language import detect_language, is_indic
from app.router import ModelRouter
from app.schemas import ChatCompletionRequest, ChatMessage


class FakeBudgetDB:
    def __init__(self, agent, spend, fired=None):
        self.agent = agent
        self.spend = spend
        self.fired = fired or []
        self.recorded = []

    async def get_agent(self, agent_id):
        return self.agent

    async def monthly_spend_inr(self, agent_id):
        return self.spend

    async def alert_fired_this_month(self, agent_id, threshold):
        return threshold in self.fired

    async def record_alert(self, agent_id, threshold, spend_inr, budget_inr):
        self.recorded.append((threshold, spend_inr, budget_inr))


BASE_AGENT = {
    "agent_id": "a",
    "monthly_budget_inr": 1000.0,
    "alert_thresholds": [0.5, 0.8, 0.95],
    "hard_limit": False,
    "fallback_model": None,
}


def test_hash_embeddings_are_semantically_reusable_for_similar_text() -> None:
    embedder = HashEmbeddingProvider(dimensions=128)
    first = embedder.embed("Explain semantic caching for large language model gateways")
    second = embedder.embed("Explain semantic cache for LLM gateway systems")

    assert cosine_similarity(first, second) > 0.35


def test_prompt_compressor_respects_budget() -> None:
    compressor = PromptCompressor(token_budget=30)
    messages = [
        ChatMessage(role="system", content="Keep the answer concise."),
        ChatMessage(
            role="user",
            content=(
                "This system must preserve API requirements. "
                "There is a long background paragraph with extra wording. "
                "The output should include examples and constraints."
            ),
        ),
    ]

    compressed, original_tokens, compressed_tokens = compressor.compress(messages)

    assert compressed_tokens <= 30
    assert compressed_tokens < original_tokens
    assert "system:" in compressed


def test_routes_by_complexity() -> None:
    router = ModelRouter("ollama", "llama3.1")
    simple = ChatCompletionRequest(messages=[ChatMessage(role="user", content="say hi")])
    assert router.choose(simple, "say hi")[0] == "ollama"

    complex_request = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Analyze architecture tradeoff and optimize this service")]
    )
    assert router.choose(complex_request, "Analyze architecture tradeoff and optimize this service")[0] == "anthropic"


def test_language_routing_to_sarvam() -> None:
    router = ModelRouter("ollama", "llama3.1", sarvam_enabled=True)
    hindi = "मौसम कैसा है?"
    request = ChatCompletionRequest(messages=[ChatMessage(role="user", content=hindi)])
    provider, model, reason = router.choose(request, hindi)
    assert provider == "sarvam"
    assert model == "sarvam-1"
    assert reason == "indic"

    router_disabled = ModelRouter("ollama", "llama3.1", sarvam_enabled=False)
    assert router_disabled.choose(request, hindi)[0] == "ollama"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("मौसम कैसा है?", "hi"),
        ("नमस्कार", "hi"),
        ("काय आहे?", "mr"),
        ("வணக்கம் என்னுடைய பெயர்", "ta"),
        ("మీ పేరు ఏమిటి", "te"),
        ("Hello world", "en"),
        ("?!!", "unknown"),
    ],
)
def test_language_detection(text: str, expected: str) -> None:
    assert detect_language(text) == expected


def test_indic_flag() -> None:
    assert is_indic("மணி என்ன?")
    assert not is_indic("what time is it?")


def test_inr_costs() -> None:
    assert estimate_cost_inr("openai", "gpt-4o-mini", 1_000_000, 0) == pytest.approx(12.5)
    assert estimate_cost_inr("ollama", "llama3.1", 10_000_000, 5_000_000) == 0.0
    assert estimate_cost_inr("unknown", "whatever", 1000, 1000) == 0.0


def test_sarvam_model_alias() -> None:
    assert SARVAM_MODEL_ALIASES["sarvam-1"] == "Sarvam 1"
    assert SARVAM_MODEL_ALIASES["sarvam-1-lite"] == "Sarvam 1 Lite"


def test_virtual_keys_are_hashed() -> None:
    plaintext, prefix, key_hash = generate_virtual_key("test-agent")
    assert plaintext.startswith(f"sk-{prefix}-")
    assert hash_secret(plaintext.split("-", 2)[2]) == key_hash
    assert key_hash == hash_secret(plaintext.split("-", 2)[2])


@pytest.mark.asyncio
async def test_budget_guard_warns_at_threshold() -> None:
    agent = {**BASE_AGENT, "monthly_budget_inr": 1000.0}
    db = FakeBudgetDB(agent, spend=900.0)
    verdict = await CostGuard(db).evaluate("agent_a")
    assert verdict.status == "warn"
    assert verdict.ratio == pytest.approx(0.9)
    assert db.recorded == [(0.5, 900.0, 1000.0), (0.8, 900.0, 1000.0)]


@pytest.mark.asyncio
async def test_budget_guard_blocks_on_hard_limit() -> None:
    agent = {**BASE_AGENT, "hard_limit": True}
    db = FakeBudgetDB(agent, spend=950.0)
    verdict = await CostGuard(db).evaluate("agent_a", projected_cost_inr=100.0)
    assert verdict.status == "blocked"


@pytest.mark.asyncio
async def test_budget_guard_unconfigured() -> None:
    db = FakeBudgetDB({}, spend=0.0)
    verdict = await CostGuard(db).evaluate("someone")
    assert verdict.status == "unconfigured"