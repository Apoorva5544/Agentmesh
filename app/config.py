from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AgentMesh"
    database_url: str = "postgresql://agentmesh:agentmesh@localhost:5432/agentmesh"
    redis_url: str = "redis://localhost:6379/0"
    cache_similarity_threshold: float = Field(default=0.88, ge=0.0, le=1.0)
    cache_ttl_seconds: int = 86_400
    prompt_token_budget: int = 1800
    db_pool_min_size: int = Field(default=1, ge=0, le=20)
    db_pool_max_size: int = Field(default=5, ge=1, le=50)
    default_provider: str = "ollama"
    default_model: str = "llama3.1"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    sarvam_api_key: str | None = None
    sarvam_base_url: str = "https://api.sarvam.ai/v1"
    sarvam_default_model: str = "Sarvam 1"

    admin_api_key: str | None = None
    allow_no_auth: bool = True

    seed_demo_data: bool = False
    demo_mode: bool = False

    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4318/v1/traces"

    usd_to_inr_rate: float = Field(default=83.0, gt=0.0)


@lru_cache
def get_settings() -> Settings:
    return Settings()