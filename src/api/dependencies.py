from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repository import MemoryRepository
from src.db.session import async_session_maker
from src.memory.extraction import DeterministicExtractor, MemoryExtractor
from src.retrieval.service import RetrievalService


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session


async def get_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MemoryRepository:
    return MemoryRepository(session)


async def get_memory_extractor() -> MemoryExtractor:
    return DeterministicExtractor()


async def get_retrieval_service(
    repository: Annotated[MemoryRepository, Depends(get_repository)],
) -> RetrievalService:
    return RetrievalService(repository)
