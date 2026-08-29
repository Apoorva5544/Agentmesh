import re

from app.schemas import ChatMessage
from app.tokenizer import count_tokens


SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


class PromptCompressor:
    def __init__(self, token_budget: int) -> None:
        self.token_budget = token_budget

    def render_messages(self, messages: list[ChatMessage]) -> str:
        return "\n".join(f"{message.role}: {message.content}" for message in messages)

    def compress(self, messages: list[ChatMessage], model: str | None = None) -> tuple[str, int, int]:
        rendered = self.render_messages(messages)
        original_tokens = count_tokens(rendered, model)
        if original_tokens <= self.token_budget:
            return rendered, original_tokens, original_tokens

        system_lines = [m.content for m in messages if m.role == "system"]
        user_text = "\n".join(m.content for m in messages if m.role != "system")
        sentences = [s.strip() for s in SENTENCE_RE.split(user_text) if s.strip()]
        if not sentences:
            sentences = [user_text]

        scored = sorted(
            sentences,
            key=lambda s: (self._signal_score(s), len(s)),
            reverse=True,
        )

        kept: list[str] = []
        prefix = "\n".join(f"system: {line}" for line in system_lines)
        for sentence in scored:
            candidate = "\n".join(part for part in [prefix, *kept, sentence] if part)
            if count_tokens(candidate, model) <= self.token_budget:
                kept.append(sentence)

        compressed = "\n".join(part for part in [prefix, " ".join(kept)] if part)
        compressed_tokens = count_tokens(compressed, model)
        return compressed, original_tokens, compressed_tokens

    def _signal_score(self, sentence: str) -> int:
        keywords = (
            "must",
            "should",
            "require",
            "error",
            "bug",
            "api",
            "schema",
            "deadline",
            "constraint",
            "example",
            "output",
        )
        lowered = sentence.lower()
        return sum(keyword in lowered for keyword in keywords)

