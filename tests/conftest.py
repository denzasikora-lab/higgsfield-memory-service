from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_repository
from src.db.config import get_settings
from src.main import create_app
from src.memory.types import ExtractedMemory
from src.schemas.requests import TurnCreateRequest
from src.schemas.responses import MemoryRecord


class FakeRepository:
    def __init__(self) -> None:
        self.turns: list[TurnCreateRequest] = []
        self.memories: list[MemoryRecord] = []
        self.deleted_sessions: list[str] = []
        self.deleted_users: list[str] = []

    async def create_turn(self, payload: TurnCreateRequest) -> str:
        self.turns.append(payload)
        return f"turn_test_{len(self.turns)}"

    async def store_extracted_memories(
        self,
        payload: TurnCreateRequest,
        turn_id: str,
        extracted_memories: list[ExtractedMemory],
    ) -> list[MemoryRecord]:
        _ = (payload, turn_id, extracted_memories)
        return []

    async def list_user_memories(self, user_id: str) -> list[MemoryRecord]:
        _ = user_id
        return self.memories

    async def list_scoped_memories(
        self,
        user_id: str | None,
        session_id: str | None,
        include_inactive: bool = False,
        limit: int = 500,
    ) -> list[MemoryRecord]:
        _ = (user_id, session_id, include_inactive, limit)
        return self.memories

    async def delete_session(self, session_id: str) -> None:
        self.deleted_sessions.append(session_id)

    async def delete_user(self, user_id: str) -> None:
        self.deleted_users.append(user_id)


@pytest.fixture
def fake_repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    fake_repository: FakeRepository,
) -> Iterator[TestClient]:
    monkeypatch.delenv("MEMORY_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("EXTRACTION_PROVIDER", "deterministic")
    get_settings.cache_clear()
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: fake_repository
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()
