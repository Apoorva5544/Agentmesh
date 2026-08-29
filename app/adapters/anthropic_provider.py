from typing import AsyncIterator

from anthropic import AsyncAnthropic

from app.adapters.base import LLMProvider, ProviderResponse


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str | None) -> None:
        self.client = AsyncAnthropic(api_key=api_key) if api_key else None

    async def complete(self, prompt: str, model: str, temperature: float, max_tokens: int) -> ProviderResponse:
        if self.client is None:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        response = await self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        content = "".join(block.text for block in response.content if block.type == "text")
        return ProviderResponse(content=content, completion_tokens=response.usage.output_tokens)

    async def stream(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        if self.client is None:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        async with self.client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for text in stream.text_stream:
                yield text