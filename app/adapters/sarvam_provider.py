from typing import AsyncIterator

from openai import AsyncOpenAI

from app.adapters.base import LLMProvider, ProviderResponse
from app.costs import SARVAM_MODEL_ALIASES


class SarvamProvider(LLMProvider):
    """Sarvam-1 / Sarvam-1 Lite via the OpenAI-compatible /v1 endpoint."""

    def __init__(self, api_key: str | None, base_url: str, default_model: str) -> None:
        self.default_model = default_model
        self.client = (
            AsyncOpenAI(api_key=api_key, base_url=base_url) if api_key else None
        )

    def resolve_model(self, model: str) -> str:
        return SARVAM_MODEL_ALIASES.get(model, model)

    async def complete(self, prompt: str, model: str, temperature: float, max_tokens: int) -> ProviderResponse:
        if self.client is None:
            raise RuntimeError("SARVAM_API_KEY is not configured")
        response = await self.client.chat.completions.create(
            model=self.resolve_model(model),
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content or ""
        usage = response.usage
        completion_tokens = usage.completion_tokens if usage else 0
        return ProviderResponse(content=content, completion_tokens=completion_tokens)

    async def stream(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        if self.client is None:
            raise RuntimeError("SARVAM_API_KEY is not configured")
        stream = await self.client.chat.completions.create(
            model=self.resolve_model(model),
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = delta.content if delta and delta.content else ""
            if piece:
                yield piece