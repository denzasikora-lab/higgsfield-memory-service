from __future__ import annotations

import spacy

from src.memory.extraction import (
    DeterministicExtractor,
    SpacyMemoryExtractor,
    SpacyOpenAIFallbackExtractor,
)
from src.memory.types import ExtractedMemory
from src.schemas.requests import TurnCreateRequest


class FakeOpenAIExtractor:
    def __init__(self, memories: list[ExtractedMemory]):
        self.memories = memories
        self.calls = 0

    async def extract(self, payload: TurnCreateRequest, turn_id: str) -> list[ExtractedMemory]:
        _ = (payload, turn_id)
        self.calls += 1
        return self.memories


def entity_nlp():
    nlp = spacy.blank("en")
    ruler = nlp.add_pipe("entity_ruler")
    ruler.add_patterns(
        [
            {"label": "GPE", "pattern": "Berlin"},
            {"label": "GPE", "pattern": "NYC"},
            {"label": "ORG", "pattern": "Stripe"},
            {"label": "ORG", "pattern": "Notion"},
        ]
    )
    nlp.add_pipe("sentencizer")
    return nlp


def payload_for(content: str) -> TurnCreateRequest:
    return TurnCreateRequest.model_validate(
        {
            "session_id": "s1",
            "user_id": "u1",
            "messages": [{"role": "user", "content": content}],
            "timestamp": "2025-03-01T10:00:00Z",
            "metadata": {},
        }
    )


async def test_spacy_extractor_adds_entity_labels_and_display_labels() -> None:
    extractor = SpacyMemoryExtractor("unused", nlp=entity_nlp())

    memories = extractor.extract_from_text("I live in Berlin and work at Stripe.", "user", None)
    by_key = {(memory.type, memory.key): memory for memory in memories}

    city = by_key[("fact", "current_city")]
    employer = by_key[("fact", "employer")]
    labels = city.metadata["labels"]

    assert city.value == "Berlin"
    assert employer.value == "Stripe"
    assert {"text": "Berlin", "label": "GPE"} in labels
    assert {"text": "Stripe", "label": "ORG"} in labels
    assert city.metadata["display_label"] == "current_city: Berlin"


async def test_spacy_openai_extractor_uses_openai_for_preferences() -> None:
    openai = FakeOpenAIExtractor(
        [
            ExtractedMemory(
                "preference",
                "answer_style",
                "Prefers concise answers",
                0.93,
                metadata={
                    "source": "openai",
                    "labels": [{"text": "concise answers", "label": "PREFERENCE"}],
                    "display_label": "answer_style: Prefers concise answers",
                },
            )
        ]
    )
    extractor = SpacyOpenAIFallbackExtractor(
        SpacyMemoryExtractor("unused", nlp=entity_nlp()),
        openai_extractor=openai,
    )

    memories = await extractor.extract(payload_for("I prefer concise answers."), "turn_1")

    assert openai.calls == 1
    assert memories[0].metadata["source"] == "openai"
    assert memories[0].metadata["display_label"] == "answer_style: Prefers concise answers"


async def test_spacy_openai_extractor_dedupes_and_prefers_openai_metadata() -> None:
    openai = FakeOpenAIExtractor(
        [
            ExtractedMemory(
                "fact",
                "employer",
                "Stripe",
                0.96,
                metadata={
                    "source": "openai",
                    "labels": [{"text": "Stripe", "label": "ORG"}],
                    "display_label": "employer: Stripe",
                },
            )
        ]
    )
    extractor = SpacyOpenAIFallbackExtractor(
        SpacyMemoryExtractor("unused", nlp=entity_nlp()),
        openai_extractor=openai,
    )

    memories = await extractor.extract(payload_for("Actually, I work at Stripe."), "turn_1")
    employer_memories = [memory for memory in memories if memory.key == "employer"]

    assert len(employer_memories) == 1
    assert employer_memories[0].metadata["source"] == "openai"


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


async def test_extractor_does_not_duplicate_named_pet_memories() -> None:
    extractor = DeterministicExtractor()
    payload = TurnCreateRequest.model_validate(
        {
            "session_id": "s3",
            "user_id": "u1",
            "messages": [
                {
                    "role": "user",
                    "content": "My dog is named Biscuit.",
                }
            ],
            "timestamp": "2025-03-16T10:05:00Z",
            "metadata": {},
        }
    )

    memories = await extractor.extract(payload, "turn_3")
    pet_memories = [memory for memory in memories if memory.key == "pet"]

    assert len(pet_memories) == 1
    assert pet_memories[0].value == "Has a dog named Biscuit"
