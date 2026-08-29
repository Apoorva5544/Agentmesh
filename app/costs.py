USD_TO_INR_RATE = 83.0

# Input/output price per 1M tokens in INR. Falls back to 0 for unknown models.
LLM_PRICE_PER_1M_INR: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", "gpt-4o-mini"): (12.5, 50.0),
    ("openai", "gpt-4o"): (415.0, 1245.0),
    ("anthropic", "claude-3-5-haiku-latest"): (66.0, 332.0),
    ("anthropic", "claude-3-5-sonnet-latest"): (249.0, 1245.0),
    ("sarvam", "sarvam-1"): (150.0, 600.0),
    ("sarvam", "sarvam-1-lite"): (50.0, 150.0),
    ("ollama", "llama3.1"): (0.0, 0.0),
}

# Model name aliases that map user-facing ids onto a real served model.
SARVAM_MODEL_ALIASES: dict[str, str] = {
    "sarvam-1": "Sarvam 1",
    "sarvam-1-lite": "Sarvam 1 Lite",
}


def estimate_cost_inr(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    input_price, output_price = LLM_PRICE_PER_1M_INR.get((provider, model), (0.0, 0.0))
    cost = (prompt_tokens / 1_000_000 * input_price) + (completion_tokens / 1_000_000 * output_price)
    return round(cost, 6)


def estimate_cost_usd(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    return round(estimate_cost_inr(provider, model, prompt_tokens, completion_tokens) / USD_TO_INR_RATE, 8)


def provider_model_cost_for(model_routing: tuple[str, str]) -> tuple[float, float]:
    return LLM_PRICE_PER_1M_INR.get(model_routing, (0.0, 0.0))