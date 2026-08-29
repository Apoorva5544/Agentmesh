from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class ProviderResponse:
    content: str
    completion_tokens: int


@dataclass
class ProviderStreamResult:
    content: str
    completion_tokens: int
    held_range: tuple[int, int] | None = None


class LLMProvider:
    async def complete(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> ProviderResponse:
        raise NotImplementedError

    async def stream(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        """Yield incremental content deltas. Default: no streaming support."""
        raise NotImplementedError