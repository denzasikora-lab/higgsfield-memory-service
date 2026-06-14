from __future__ import annotations

from datetime import UTC, datetime

from src.db.repository import MemorySearchHit
from src.memory.embedding import EmbeddingProvider
from src.retrieval.service import RetrievalService
from src.schemas.requests import RecallRequest, SearchRequest
from src.schemas.responses import MemoryRecord


class MemoryListRepository:
    def __init__(self, memories: list[MemoryRecord]):
        self.memories = memories

    async def list_scoped_memories(
        self,
        user_id: str | None,
        session_id: str | None,
        include_inactive: bool = False,
        limit: int = 500,
    ) -> list[MemoryRecord]:
        _ = (user_id, session_id, include_inactive, limit)
        if include_inactive:
            return self.memories
        return [memory for memory in self.memories if memory.active]


class VectorRepository(MemoryListRepository):
    def __init__(self, memories: list[MemoryRecord], hits: list[MemorySearchHit]):
        super().__init__(memories)
        self.hits = hits
        self.query_embedding: list[float] | None = None

    async def search_scoped_memories_by_vector(
        self,
        query_embedding: list[float],
        user_id: str | None,
        session_id: str | None,
        include_inactive: bool = False,
        limit: int = 50,
    ) -> list[MemorySearchHit]:
        _ = (user_id, session_id, include_inactive, limit)
        self.query_embedding = query_embedding
        return self.hits


class FakeEmbeddingProvider(EmbeddingProvider):
    async def embed(self, text: str) -> list[float] | None:
        _ = text
        return [1.0, 0.0, 0.0]


def memory(
    key: str,
    value: str,
    active: bool = True,
    memory_type: str = "fact",
    turn_id: str = "turn_1",
    metadata: dict | None = None,
) -> MemoryRecord:
    now = datetime(2025, 3, 15, 10, 0, tzinfo=UTC)
    return MemoryRecord(
        id=f"mem_{key}_{active}",
        type=memory_type,
        key=key,
        value=value,
        confidence=0.92,
        source_session="s1",
        source_turn=turn_id,
        created_at=now,
        updated_at=now,
        supersedes=None,
        active=active,
        metadata=metadata or {"display_label": f"{key}: {value}"},
    )


async def test_recall_returns_prompt_ready_context_with_previous_fact() -> None:
    service = RetrievalService(
        MemoryListRepository(
            [
                memory("current_city", "Berlin", True, turn_id="turn_new"),
                memory("current_city", "NYC", False, turn_id="turn_old"),
                memory("employer", "Notion", True),
            ]
        )
    )

    response = await service.recall(
        RecallRequest(
            query="Where does this user live?",
            session_id="s1",
            user_id="u1",
            max_tokens=128,
        )
    )

    assert "current_city: Berlin" in response.context
    assert "previously NYC" in response.context
    assert "Notion" not in response.context
    assert response.citations[0].turn_id == "turn_new"


async def test_search_returns_structured_memory_results() -> None:
    service = RetrievalService(
        MemoryListRepository(
            [
                memory("current_city", "Berlin", True, turn_id="turn_city"),
                memory("employer", "Notion", True, turn_id="turn_work"),
            ]
        )
    )

    response = await service.search(
        SearchRequest(query="Berlin", session_id="s1", user_id="u1", limit=10)
    )

    assert response.results
    assert response.results[0].content == "fact:current_city: Berlin"
    assert response.results[0].metadata["source_turn"] == "turn_city"
    assert all("Notion" not in result.content for result in response.results)


async def test_recall_uses_vector_similarity_before_lexical_overlap() -> None:
    city = memory("current_city", "Berlin", True, turn_id="turn_city")
    employer = memory("employer", "Notion", True, turn_id="turn_work")
    repository = VectorRepository(
        [city, employer],
        [
            MemorySearchHit(city, 0.92),
            MemorySearchHit(employer, 0.05),
        ],
    )
    service = RetrievalService(repository, embedding_provider=FakeEmbeddingProvider())

    response = await service.recall(
        RecallRequest(query="residence", session_id="s1", user_id="u1", max_tokens=128)
    )

    assert repository.query_embedding == [1.0, 0.0, 0.0]
    assert "Berlin" in response.context
    assert "Notion" not in response.context
