from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_port: int = 8080
    database_url: str = "postgresql+asyncpg://memory:memory@postgres:5432/memory"
    memory_auth_token: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str = "gpt-4o-nano"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    extraction_provider: str = "spacy_openai_fallback"
    spacy_model: str = "en_core_web_sm"
    use_llm_extraction: bool = False
    memory_max_per_scope: int = 200
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
