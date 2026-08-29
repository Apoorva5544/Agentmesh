from typing import AsyncIterator

from openai import AsyncOpenAI

from app.adapters.base import LLMProvider, ProviderResponse


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str | None) -> None:
        self.client = AsyncOpenAI(api_key=api_key) if api_key else None

    async def complete(self, prompt: str, model: str, temperature: float, max_tokens: int) -> ProviderResponse:
        if self.client is None:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        response = await self.client.chat.completions.create(
            model=model,
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
            raise RuntimeError("OPENAI_API_KEY is not configured")
        stream = await self.client.chat.completions.create(
            model=model,
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