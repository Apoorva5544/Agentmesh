from redis.asyncio import Redis

from app.adapters.anthropic_provider import AnthropicProvider
from app.adapters.base import LLMProvider
from app.adapters.ollama_provider import OllamaProvider
from app.adapters.openai_provider import OpenAIProvider
from app.adapters.sarvam_provider import SarvamProvider
from app.auth import AuthService
from app.budgets import CostGuard
from app.cache import SemanticCache
from app.compression import PromptCompressor
from app.config import Settings, get_settings
from app.db import Database
from app.embeddings import HashEmbeddingProvider
from app.mcp.registry import MCPRegistry
from app.router import ModelRouter


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.redis = Redis.from_url(settings.redis_url, decode_responses=False)
        self.db = Database(
            settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
        )
        self.cache = SemanticCache(
            self.redis,
            threshold=settings.cache_similarity_threshold,
            ttl_seconds=settings.cache_ttl_seconds,
        )
        self.compressor = PromptCompressor(settings.prompt_token_budget)
        self.embeddings = HashEmbeddingProvider()
        self.router = ModelRouter(
            settings.default_provider,
            settings.default_model,
            sarvam_enabled=bool(settings.sarvam_api_key),
        )
        self.auth = AuthService(self.db, settings)
        self.budgets = CostGuard(self.db)
        self.mcp = MCPRegistry(self.db, self.redis)
        self.providers: dict[str, LLMProvider] = {
            "openai": OpenAIProvider(settings.openai_api_key),
            "anthropic": AnthropicProvider(settings.anthropic_api_key),
            "sarvam": SarvamProvider(
                settings.sarvam_api_key,
                settings.sarvam_base_url,
                settings.sarvam_default_model,
            ),
            "ollama": OllamaProvider(settings.ollama_base_url),
        }

    async def startup(self) -> None:
        await self.db.init()

    async def shutdown(self) -> None:
        await self.db.close()
        await self.mcp.close()
        await self.redis.aclose()


container = Container(get_settings())