from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TurnMessage(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
    name: str | None = None

    model_config = ConfigDict(extra="forbid")


class TurnCreateRequest(BaseModel):
    session_id: NonEmptyString
    user_id: str | None = None
    messages: list[TurnMessage] = Field(min_length=1)
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class RecallRequest(BaseModel):
    query: NonEmptyString
    session_id: NonEmptyString
    user_id: str | None = None
    max_tokens: int = Field(default=1024, ge=1, le=8192)

    model_config = ConfigDict(extra="forbid")


class SearchRequest(BaseModel):
    query: NonEmptyString
    session_id: str | None = None
    user_id: str | None = None
    limit: int = Field(default=10, ge=1, le=100)

    model_config = ConfigDict(extra="forbid")
