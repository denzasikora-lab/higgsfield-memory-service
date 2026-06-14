from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.config import Settings, get_settings
from src.db.repository import MemoryRepository
from src.db.session import async_session_maker
from src.memory.embedding import (
    DisabledEmbeddingProvider,
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from src.memory.extraction import (
    DeterministicExtractor,
    MemoryExtractor,
    OpenAIStructuredExtractor,
    SpacyMemoryExtractor,
    SpacyOpenAIFallbackExtractor,
)
from src.retrieval.service import RetrievalService


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session


async def get_embedding_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> EmbeddingProvider:
    return build_embedding_provider(
        settings.openai_api_key,
        settings.openai_base_url,
        settings.openai_embedding_model,
        settings.embedding_dimensions,
    )


async def get_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MemoryRepository:
    return MemoryRepository(
        session,
        embedding_provider=embedding_provider,
        memory_max_per_scope=settings.memory_max_per_scope,
    )


async def get_memory_extractor(
    settings: Annotated[Settings, Depends(get_settings)],
) -> MemoryExtractor:
    return build_memory_extractor(
        settings.extraction_provider,
        settings.openai_api_key,
        settings.openai_base_url,
        settings.openai_model,
        settings.spacy_model,
        settings.use_llm_extraction,
    )


async def get_retrieval_service(
    repository: Annotated[MemoryRepository, Depends(get_repository)],
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
) -> RetrievalService:
    return RetrievalService(repository, embedding_provider=embedding_provider)


@lru_cache
def build_embedding_provider(
    api_key: str | None,
    base_url: str | None,
    model: str,
    dimensions: int,
) -> EmbeddingProvider:
    if not api_key:
        return DisabledEmbeddingProvider()
    return OpenAIEmbeddingProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
        dimensions=dimensions,
    )


@lru_cache
def build_memory_extractor(
    provider: str,
    api_key: str | None,
    base_url: str | None,
    model: str,
    spacy_model: str,
    use_llm_extraction: bool,
) -> MemoryExtractor:
    normalized_provider = provider.strip().casefold()
    deterministic = DeterministicExtractor()
    openai_extractor: MemoryExtractor | None = None

    if api_key and (
        normalized_provider in {"openai", "llm", "spacy_openai_fallback"}
        or use_llm_extraction
    ):
        openai_extractor = OpenAIStructuredExtractor(
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

    if normalized_provider in {"deterministic", "regex"}:
        return deterministic
    if normalized_provider in {"openai", "llm"}:
        return openai_extractor or deterministic

    return SpacyOpenAIFallbackExtractor(
        spacy_extractor=SpacyMemoryExtractor(spacy_model),
        openai_extractor=openai_extractor,
        deterministic_extractor=deterministic,
    )
