from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: Literal["ok"]


class TurnCreateResponse(BaseModel):
    id: str


class Citation(BaseModel):
    turn_id: str
    score: float
    snippet: str


class RecallResponse(BaseModel):
    context: str
    citations: list[Citation]


class SearchResult(BaseModel):
    content: str
    score: float
    session_id: str
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    results: list[SearchResult]


class MemoryRecord(BaseModel):
    id: str
    type: Literal["fact", "preference", "opinion", "event"]
    key: str
    value: str
    confidence: float
    source_session: str
    source_turn: str
    created_at: datetime
    updated_at: datetime
    supersedes: str | None = None
    active: bool
    metadata: dict[str, Any] = Field(default_factory=dict, exclude=True)
    embedding: list[float] | None = Field(default=None, exclude=True)

    model_config = ConfigDict(from_attributes=True)


class UserMemoriesResponse(BaseModel):
    memories: list[MemoryRecord]
