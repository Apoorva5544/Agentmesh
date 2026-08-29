from app.language import is_indic
from app.schemas import ChatCompletionRequest
from app.tokenizer import count_tokens

HIGH_COMPLEXITY = ("anthropic", "claude-3-5-sonnet-latest")
MID_COMPLEXITY = ("openai", "gpt-4o-mini")

# (provider, model) -> cheaper (provider, model) used when a budget is running low.
DOWNGRADE_PATHS: dict[tuple[str, str], tuple[str, str]] = {
    ("openai", "gpt-4o"): ("openai", "gpt-4o-mini"),
    ("anthropic", "claude-3-5-sonnet-latest"): ("anthropic", "claude-3-5-haiku-latest"),
    ("sarvam", "sarvam-1"): ("sarvam", "sarvam-1-lite"),
}


class ModelRouter:
    def __init__(self, default_provider: str, default_model: str, sarvam_enabled: bool = False) -> None:
        self.default_provider = default_provider
        self.default_model = default_model
        self.sarvam_enabled = sarvam_enabled

    def choose(self, request: ChatCompletionRequest, prompt: str) -> tuple[str, str, str]:
        indic = is_indic(prompt)
        if indic and self.sarvam_enabled:
            return "sarvam", "sarvam-1", "indic"

        if request.provider and request.model:
            return request.provider, request.model, "explicit"
        if request.provider:
            return request.provider, self._default_model_for_provider(request.provider), "explicit"
        if request.model:
            return self._provider_for_model(request.model), request.model, "explicit"

        complexity = self._complexity(prompt)
        if complexity >= 4:
            return HIGH_COMPLEXITY[0], HIGH_COMPLEXITY[1], "complexity"
        if complexity >= 2:
            return MID_COMPLEXITY[0], MID_COMPLEXITY[1], "complexity"
        return self.default_provider, self.default_model, "complexity"

    def downgrade(self, provider: str, model: str) -> tuple[str, str]:
        return DOWNGRADE_PATHS.get((provider, model), (provider, model))

    def _complexity(self, prompt: str) -> int:
        score = 0
        tokens = count_tokens(prompt)
        lowered = prompt.lower()
        if tokens > 2500:
            score += 3
        elif tokens > 900:
            score += 1
        for marker in ("analyze", "debug", "architecture", "tradeoff", "prove", "optimize", "brainstorm"):
            if marker in lowered:
                score += 1
        return score

    def _default_model_for_provider(self, provider: str) -> str:
        return {
            "openai": "gpt-4o-mini",
            "anthropic": "claude-3-5-haiku-latest",
            "sarvam": "sarvam-1",
            "ollama": self.default_model,
        }[provider]

    def _provider_for_model(self, model: str) -> str:
        lowered = model.lower()
        if lowered.startswith("claude"):
            return "anthropic"
        if lowered.startswith(("gpt", "o1", "o3")):
            return "openai"
        if "sarvam" in lowered:
            return "sarvam"
        return "ollama"

    def provider_for_model(self, model: str) -> str:
        return self._provider_for_model(model)