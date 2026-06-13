from __future__ import annotations

from src.memory.extraction import DeterministicExtractor
from src.schemas.requests import TurnCreateRequest


async def test_extractor_finds_structured_location_and_employer_memories() -> None:
    extractor = DeterministicExtractor()
    payload = TurnCreateRequest.model_validate(
        {
            "session_id": "s1",
            "user_id": "u1",
            "messages": [
                {
                    "role": "user",
                    "content": "I live in NYC and work at Stripe.",
                }
            ],
            "timestamp": "2025-03-01T10:00:00Z",
            "metadata": {},
        }
    )

    memories = await extractor.extract(payload, "turn_1")
    by_key = {(memory.type, memory.key): memory.value for memory in memories}

    assert by_key[("fact", "current_city")] == "NYC"
    assert by_key[("fact", "employer")] == "Stripe"


async def test_extractor_finds_preferences_pets_and_tool_events() -> None:
    extractor = DeterministicExtractor()
    payload = TurnCreateRequest.model_validate(
        {
            "session_id": "s1",
            "user_id": "u1",
            "messages": [
                {
                    "role": "user",
                    "content": "I prefer concise answers. I was walking Biscuit this morning.",
                },
                {
                    "role": "tool",
                    "name": "calendar",
                    "content": "User has a dentist appointment on Friday",
                },
            ],
            "timestamp": "2025-03-01T10:00:00Z",
            "metadata": {},
        }
    )

    memories = await extractor.extract(payload, "turn_1")
    values = {memory.value for memory in memories}

    assert "Prefers concise, direct answers" in values
    assert "Likely has a pet named Biscuit" in values
    assert "User has a dentist appointment on Friday" in values


async def test_extractor_handles_natural_evolution_phrasing() -> None:
    extractor = DeterministicExtractor()
    payload = TurnCreateRequest.model_validate(
        {
            "session_id": "s2",
            "user_id": "u1",
            "messages": [
                {
                    "role": "user",
                    "content": "I moved from NYC to Berlin last month. I joined Notion as a PM.",
                }
            ],
            "timestamp": "2025-03-15T10:00:00Z",
            "metadata": {},
        }
    )

    memories = await extractor.extract(payload, "turn_2")
    by_key = {(memory.type, memory.key): memory.value for memory in memories}

    assert by_key[("fact", "current_city")] == "Berlin"
    assert by_key[("fact", "previous_city")] == "NYC"
    assert by_key[("fact", "employer")] == "Notion"
    assert by_key[("fact", "job_title")] == "PM"
