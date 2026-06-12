from __future__ import annotations

from datetime import UTC, datetime

from src.memory.extraction import DeterministicExtractor
from src.retrieval.service import RetrievalService
from src.schemas.requests import RecallRequest, TurnCreateRequest
from src.schemas.responses import MemoryRecord


class FixtureRepository:
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
        return self.memories


async def test_fixture_location_and_employer_recall_quality() -> None:
    extractor = DeterministicExtractor()
    payload = TurnCreateRequest.model_validate(
        {
            "session_id": "s2",
            "user_id": "u1",
            "messages": [
                {
                    "role": "user",
                    "content": "I just moved to Berlin and started at Notion.",
                }
            ],
            "timestamp": "2025-03-15T10:00:00Z",
            "metadata": {},
        }
    )
    extracted = await extractor.extract(payload, "turn_1")
    now = datetime(2025, 3, 15, 10, 0, tzinfo=UTC)
    records = [
        MemoryRecord(
            id=f"mem_{index}",
            type=memory.type,
            key=memory.key,
            value=memory.value,
            confidence=memory.confidence,
            source_session="s2",
            source_turn="turn_1",
            created_at=now,
            updated_at=now,
            active=True,
        )
        for index, memory in enumerate(extracted)
    ]
    service = RetrievalService(FixtureRepository(records))

    location = await service.recall(
        RecallRequest(query="Where does the user live now?", session_id="s2", user_id="u1")
    )
    employer = await service.recall(
        RecallRequest(query="Where does the user work now?", session_id="s2", user_id="u1")
    )

    assert "Berlin" in location.context
    assert "Notion" in employer.context
