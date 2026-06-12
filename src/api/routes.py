from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from src.api.auth import require_auth
from src.api.dependencies import get_memory_extractor, get_repository, get_retrieval_service
from src.db.repository import MemoryRepository
from src.memory.extraction import MemoryExtractor
from src.retrieval.service import RetrievalService
from src.schemas.requests import RecallRequest, SearchRequest, TurnCreateRequest
from src.schemas.responses import (
    HealthResponse,
    RecallResponse,
    SearchResponse,
    TurnCreateResponse,
    UserMemoriesResponse,
)

public_router = APIRouter()
memory_router = APIRouter(dependencies=[Depends(require_auth)])


@public_router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@memory_router.post(
    "/turns",
    response_model=TurnCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_turn(
    payload: TurnCreateRequest,
    repository: Annotated[MemoryRepository, Depends(get_repository)],
    extractor: Annotated[MemoryExtractor, Depends(get_memory_extractor)],
) -> TurnCreateResponse:
    turn_id = await repository.create_turn(payload)
    extracted_memories = await extractor.extract(payload, turn_id)
    await repository.store_extracted_memories(payload, turn_id, extracted_memories)
    return TurnCreateResponse(id=turn_id)


@memory_router.post("/recall", response_model=RecallResponse)
async def recall(
    payload: RecallRequest,
    retrieval: Annotated[RetrievalService, Depends(get_retrieval_service)],
) -> RecallResponse:
    return await retrieval.recall(payload)


@memory_router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    retrieval: Annotated[RetrievalService, Depends(get_retrieval_service)],
) -> SearchResponse:
    return await retrieval.search(payload)


@memory_router.get("/users/{user_id}/memories", response_model=UserMemoriesResponse)
async def list_user_memories(
    user_id: str,
    repository: Annotated[MemoryRepository, Depends(get_repository)],
) -> UserMemoriesResponse:
    memories = await repository.list_user_memories(user_id)
    return UserMemoriesResponse(memories=memories)


@memory_router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    repository: Annotated[MemoryRepository, Depends(get_repository)],
) -> Response:
    await repository.delete_session(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@memory_router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    repository: Annotated[MemoryRepository, Depends(get_repository)],
) -> Response:
    await repository.delete_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
