import json
from typing import AsyncIterator

import httpx

from app.adapters.base import LLMProvider, ProviderResponse
from app.tokenizer import count_tokens


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def complete(self, prompt: str, model: str, temperature: float, max_tokens: int) -> ProviderResponse:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                },
            )
            response.raise_for_status()
            data = response.json()
        content = data.get("response", "")
        return ProviderResponse(content=content, completion_tokens=count_tokens(content, model))

    async def stream(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": True,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    piece = payload.get("response")
                    if piece:
                        yield piece