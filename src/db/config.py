from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_port: int = 8080
    database_url: str = "postgresql+asyncpg://memory:memory@postgres:5432/memory"
    memory_auth_token: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    use_llm_extraction: bool = False
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
