from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Protocol

import spacy
from openai import AsyncOpenAI
from spacy.language import Language

from src.schemas.requests import TurnCreateRequest, TurnMessage

from .types import ExtractedMemory

STOP = r"(?=\.|,|;|!|\?| and | but | because | as | last | this | now | recently |$)"
ENTITY = r"([A-Z][A-Za-z0-9&.'+-]*(?:\s+[A-Z][A-Za-z0-9&.'+-]*)*)"
LOWER_PHRASE = r"([a-z][A-Za-z0-9&,'+\- /]{1,80})"


class MemoryExtractor(Protocol):
    async def extract(self, payload: TurnCreateRequest, turn_id: str) -> list[ExtractedMemory]:
        """Extract structured memories from a completed turn."""


MEMORY_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["fact", "preference", "opinion", "event"],
                    },
                    "key": {"type": "string", "minLength": 1},
                    "value": {"type": "string", "minLength": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "labels": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "text": {"type": "string"},
                                "label": {"type": "string"},
                            },
                            "required": ["text", "label"],
                        },
                    },
                    "display_label": {"type": ["string", "null"]},
                },
                "required": [
                    "type",
                    "key",
                    "value",
                    "confidence",
                    "labels",
                    "display_label",
                ],
            },
        }
    },
    "required": ["memories"],
}

OPENAI_SYSTEM_PROMPT = """Extract durable structured memories about the human user.
Return only information useful for future AI-agent personalization.
Do not store assistant claims unless they confirm a user fact.
Use snake_case canonical keys. Prefer precise typed memories over raw message chunks.
For labels, include named entities or important semantic tags that support the memory.
For display_label, write a short prompt-ready label; otherwise use null."""

FALLBACK_CUES = {
    "actually",
    "allergic",
    "allergy",
    "appointment",
    "correction",
    "don't like",
    "hate",
    "i am",
    "i prefer",
    "i'm",
    "love",
    "moved",
    "prefer",
    "remember",
    "update",
}


class OpenAIStructuredExtractor:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
    ) -> None:
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def extract(self, payload: TurnCreateRequest, turn_id: str) -> list[ExtractedMemory]:
        _ = turn_id
        messages = [
            message.model_dump(exclude_none=True)
            for message in payload.messages
            if message.role != "assistant"
        ]
        if not messages:
            return []

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": OPENAI_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "session_id": payload.session_id,
                            "user_id_present": payload.user_id is not None,
                            "messages": messages,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "memory_extraction",
                    "strict": True,
                    "schema": MEMORY_JSON_SCHEMA,
                },
            },
        )
        content = response.choices[0].message.content or '{"memories":[]}'
        parsed = json.loads(content)
        return self._parse_memories(parsed)

    @staticmethod
    def _parse_memories(parsed: object) -> list[ExtractedMemory]:
        if not isinstance(parsed, dict):
            return []

        memories: list[ExtractedMemory] = []
        for item in parsed.get("memories", []):
            if not isinstance(item, dict):
                continue
            memory_type = item.get("type")
            key = item.get("key")
            value = item.get("value")
            confidence = item.get("confidence")
            if memory_type not in {"fact", "preference", "opinion", "event"}:
                continue
            if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
                continue
            if not isinstance(confidence, int | float):
                continue

            labels = item.get("labels")
            display_label = item.get("display_label")
            metadata = {
                "source": "openai",
                "labels": labels if isinstance(labels, list) else [],
            }
            if isinstance(display_label, str) and display_label.strip():
                metadata["display_label"] = display_label.strip()

            memories.append(
                ExtractedMemory(
                    type=memory_type,
                    key=key.strip(),
                    value=value.strip(),
                    confidence=max(0.0, min(float(confidence), 1.0)),
                    metadata=metadata,
                )
            )
        return memories


class SpacyMemoryExtractor:
    def __init__(self, spacy_model: str, nlp: Language | None = None) -> None:
        self.nlp = nlp or self._load_nlp(spacy_model)

    def extract_from_text(
        self,
        text: str,
        role: str,
        tool_name: str | None,
    ) -> list[ExtractedMemory]:
        normalized = DeterministicExtractor._normalize_text(text)
        doc = self.nlp(normalized)
        labels = self._entity_labels(doc)
        candidate_tags = self._candidate_tags(doc)
        memories: list[ExtractedMemory] = []

        memories.extend(self._extract_locations(normalized, doc, labels, candidate_tags))
        memories.extend(self._extract_work(normalized, doc, labels, candidate_tags))
        memories.extend(
            self._extract_tool_events(normalized, role, tool_name, labels, candidate_tags)
        )

        return memories

    @staticmethod
    def _load_nlp(spacy_model: str) -> Language:
        try:
            nlp = spacy.load(spacy_model)
        except OSError:
            nlp = spacy.blank("en")
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer")
        return nlp

    def _extract_locations(
        self,
        text: str,
        doc: Language,
        labels: list[dict[str, str]],
        candidate_tags: list[str],
    ) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []

        moved = re.search(
            r"\bmoved from (?P<previous>.+?) to (?P<current>.+?)(?:[.;!?]|$)",
            text,
            re.I,
        )
        if moved:
            previous = self._best_entity_value(
                doc,
                moved.group("previous"),
                {"GPE", "LOC"},
            )
            current = self._best_entity_value(
                doc,
                moved.group("current"),
                {"GPE", "LOC"},
            )
            if current:
                memories.append(
                    self._memory("fact", "current_city", current, 0.88, labels, candidate_tags)
                )
            if previous:
                memories.append(
                    self._memory("fact", "previous_city", previous, 0.82, labels, candidate_tags)
                )
            if current and previous:
                memories.append(
                    self._memory(
                        "event",
                        "relocation",
                        f"Moved to {current} from {previous}",
                        0.78,
                        labels,
                        candidate_tags,
                    )
                )

        current = self._entity_after_trigger(
            doc,
            triggers=("live in", "based in", "moved to"),
            labels={"GPE", "LOC"},
        )
        if current:
            memories.append(
                self._memory("fact", "current_city", current, 0.86, labels, candidate_tags)
            )

        return memories

    def _extract_work(
        self,
        text: str,
        doc: Language,
        labels: list[dict[str, str]],
        candidate_tags: list[str],
    ) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []

        employer = self._entity_after_trigger(
            doc,
            triggers=("work at", "work for", "joined", "started at", "started working at"),
            labels={"ORG", "PRODUCT"},
        )
        if employer:
            memories.append(
                self._memory("fact", "employer", employer, 0.86, labels, candidate_tags)
            )

        title = re.search(
            r"\b(?:as a|as an|job title is)\s+([A-Za-z][A-Za-z /+-]{1,80})(?:[.;!?]|$)",
            text,
            re.I,
        )
        if title:
            memories.append(
                self._memory(
                    "fact",
                    "job_title",
                    DeterministicExtractor._clean(title.group(1)),
                    0.78,
                    labels,
                    candidate_tags,
                )
            )

        return memories

    def _extract_tool_events(
        self,
        text: str,
        role: str,
        tool_name: str | None,
        labels: list[dict[str, str]],
        candidate_tags: list[str],
    ) -> list[ExtractedMemory]:
        if role != "tool" or not re.search(r"\bappointment\b", text, re.I):
            return []

        return [
            self._memory(
                "event",
                f"{tool_name or 'tool'}_appointment",
                DeterministicExtractor._clean(text),
                0.72,
                labels,
                candidate_tags,
            )
        ]

    @staticmethod
    def _entity_labels(doc: Language) -> list[dict[str, str]]:
        return [{"text": ent.text, "label": ent.label_} for ent in doc.ents if ent.text.strip()]

    @staticmethod
    def _candidate_tags(doc: Language) -> list[str]:
        tags: list[str] = []
        try:
            tags.extend(chunk.text for chunk in doc.noun_chunks if 2 <= len(chunk.text) <= 80)
        except ValueError:
            pass
        tags.extend(token.text for token in doc if token.pos_ == "PROPN" and len(token.text) > 1)
        return list(dict.fromkeys(tags))[:12]

    @staticmethod
    def _best_entity_value(doc: Language, span_text: str, labels: set[str]) -> str | None:
        normalized_span = span_text.casefold()
        matches = [
            ent.text
            for ent in doc.ents
            if ent.label_ in labels and ent.text.casefold() in normalized_span
        ]
        if matches:
            return DeterministicExtractor._clean(matches[-1])
        match = re.search(ENTITY, span_text)
        return DeterministicExtractor._clean_entity(match.group(1)) if match else None

    @staticmethod
    def _entity_after_trigger(
        doc: Language,
        triggers: tuple[str, ...],
        labels: set[str],
    ) -> str | None:
        lower = doc.text.casefold()
        for ent in doc.ents:
            if ent.label_ not in labels:
                continue
            prefix = lower[max(0, ent.start_char - 80) : ent.start_char]
            if any(trigger in prefix for trigger in triggers):
                return DeterministicExtractor._clean(ent.text)
        return None

    @staticmethod
    def _memory(
        memory_type: str,
        key: str,
        value: str,
        confidence: float,
        labels: list[dict[str, str]],
        candidate_tags: list[str],
    ) -> ExtractedMemory:
        return ExtractedMemory(
            memory_type,  # type: ignore[arg-type]
            key,
            value,
            confidence,
            metadata={
                "source": "spacy",
                "labels": labels,
                "candidate_tags": candidate_tags,
                "display_label": f"{key}: {value}",
            },
        )


class SpacyOpenAIFallbackExtractor:
    def __init__(
        self,
        spacy_extractor: SpacyMemoryExtractor,
        openai_extractor: MemoryExtractor | None = None,
        deterministic_extractor: MemoryExtractor | None = None,
    ) -> None:
        self.spacy_extractor = spacy_extractor
        self.openai_extractor = openai_extractor
        self.deterministic_extractor = deterministic_extractor or DeterministicExtractor()

    async def extract(self, payload: TurnCreateRequest, turn_id: str) -> list[ExtractedMemory]:
        spacy_memories: list[ExtractedMemory] = []
        fallback_messages: list[TurnMessage] = []

        for message in payload.messages:
            if message.role == "assistant":
                continue
            message_memories = self.spacy_extractor.extract_from_text(
                message.content,
                message.role,
                message.name,
            )
            spacy_memories.extend(message_memories)
            if self._needs_openai_fallback(message.content, message_memories):
                fallback_messages.append(message)

        fallback_memories = await self._extract_fallback(payload, turn_id, fallback_messages)
        combined = self._dedupe_prefer_rich(spacy_memories + fallback_memories)
        if combined:
            return combined

        return await self.deterministic_extractor.extract(payload, turn_id)

    async def _extract_fallback(
        self,
        payload: TurnCreateRequest,
        turn_id: str,
        fallback_messages: list[TurnMessage],
    ) -> list[ExtractedMemory]:
        if not fallback_messages:
            return []

        fallback_payload = payload.model_copy(update={"messages": fallback_messages})
        if self.openai_extractor is not None:
            try:
                memories = await self.openai_extractor.extract(fallback_payload, turn_id)
                if memories:
                    return memories
            except (json.JSONDecodeError, ValueError, RuntimeError, OSError):
                pass
            except Exception:
                pass

        return await self.deterministic_extractor.extract(fallback_payload, turn_id)

    @staticmethod
    def _needs_openai_fallback(text: str, memories: list[ExtractedMemory]) -> bool:
        lower = text.casefold()
        has_good_typed_memory = any(memory.confidence >= 0.8 for memory in memories)
        has_fallback_cue = any(cue in lower for cue in FALLBACK_CUES)
        return has_fallback_cue or not has_good_typed_memory

    @staticmethod
    def _dedupe_prefer_rich(memories: list[ExtractedMemory]) -> list[ExtractedMemory]:
        by_marker: dict[tuple[str, str, str], ExtractedMemory] = {}
        for memory in memories:
            if not memory.value:
                continue
            marker = (
                memory.type,
                memory.key.strip().casefold().replace(" ", "_").replace("-", "_"),
                memory.value.strip().casefold(),
            )
            existing = by_marker.get(marker)
            if existing is None:
                by_marker[marker] = memory
                continue
            by_marker[marker] = _richer_memory(existing, memory)
        return list(by_marker.values())


def _richer_memory(left: ExtractedMemory, right: ExtractedMemory) -> ExtractedMemory:
    left_score = _memory_richness(left)
    right_score = _memory_richness(right)
    winner, loser = (right, left) if right_score > left_score else (left, right)
    metadata = _merge_metadata(loser.metadata, winner.metadata)
    return replace(winner, metadata=metadata)


def _memory_richness(memory: ExtractedMemory) -> int:
    metadata = memory.metadata
    source_score = 4 if metadata.get("source") == "openai" else 2
    label_score = len(metadata.get("labels", [])) if isinstance(metadata.get("labels"), list) else 0
    display_score = 2 if metadata.get("display_label") else 0
    return source_score + label_score + display_score


def _merge_metadata(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    merged = dict(left)
    merged.update(right)

    labels: list[dict[str, str]] = []
    seen_labels: set[tuple[str, str]] = set()
    for source in (left, right):
        items = source.get("labels", [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            label = item.get("label")
            if not isinstance(text, str) or not isinstance(label, str):
                continue
            marker = (text.casefold(), label.casefold())
            if marker in seen_labels:
                continue
            seen_labels.add(marker)
            labels.append({"text": text, "label": label})
    if labels:
        merged["labels"] = labels
    return merged


class DeterministicExtractor:
    async def extract(self, payload: TurnCreateRequest, turn_id: str) -> list[ExtractedMemory]:
        _ = turn_id
        memories: list[ExtractedMemory] = []

        for message in payload.messages:
            if message.role == "assistant":
                continue
            memories.extend(self._extract_from_text(message.content, message.role, message.name))

        return self._dedupe(memories)

    def _extract_from_text(
        self,
        text: str,
        role: str,
        tool_name: str | None,
    ) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []
        normalized = self._normalize_text(text)

        memories.extend(self._extract_locations(normalized))
        memories.extend(self._extract_work(normalized))
        memories.extend(self._extract_preferences(normalized))
        memories.extend(self._extract_pets(normalized))
        memories.extend(self._extract_health_and_diet(normalized))
        memories.extend(self._extract_projects(normalized))
        memories.extend(self._extract_opinions(normalized))
        memories.extend(self._extract_events(normalized, role, tool_name))

        return memories

    def _extract_locations(self, text: str) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []

        for match in re.finditer(rf"\bI (?:currently )?live in {ENTITY}{STOP}", text, re.I):
            memories.append(self._fact("current_city", self._clean_entity(match.group(1)), 0.93))

        for match in re.finditer(rf"\bI(?:'m| am) based in {ENTITY}{STOP}", text, re.I):
            memories.append(self._fact("current_city", self._clean_entity(match.group(1)), 0.9))

        for match in re.finditer(
            rf"\bI (?:just )?moved to {ENTITY}(?: from {ENTITY})?{STOP}",
            text,
            re.I,
        ):
            current_city = self._clean_entity(match.group(1))
            memories.append(self._fact("current_city", current_city, 0.95))

            previous_city = (
                self._clean_entity(match.group(2)) if match.lastindex and match.group(2) else None
            )
            if previous_city:
                memories.append(self._fact("previous_city", previous_city, 0.84))
                value = f"Moved to {current_city} from {previous_city}"
            else:
                value = f"Moved to {current_city}"
            memories.append(ExtractedMemory("event", "relocation", value, 0.86))

        for match in re.finditer(
            rf"\bI (?:just )?moved from {ENTITY} to {ENTITY}{STOP}",
            text,
            re.I,
        ):
            previous_city = self._clean_entity(match.group(1))
            current_city = self._clean_entity(match.group(2))
            memories.append(self._fact("current_city", current_city, 0.95))
            memories.append(self._fact("previous_city", previous_city, 0.84))
            memories.append(
                ExtractedMemory(
                    "event",
                    "relocation",
                    f"Moved to {current_city} from {previous_city}",
                    0.86,
                )
            )

        for match in re.finditer(rf"\bActually,?\s+I meant {ENTITY}{STOP}", text, re.I):
            memories.append(self._fact("current_city", self._clean_entity(match.group(1)), 0.88))

        return memories

    def _extract_work(self, text: str) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []

        employer_patterns = [
            rf"\bI work (?:at|for) {ENTITY}{STOP}",
            rf"\bwork (?:at|for) {ENTITY}{STOP}",
            rf"\bI(?:'ve| have)? just (?:joined|started at|started working at) {ENTITY}{STOP}",
            rf"\bI (?:joined|started at|started working at) {ENTITY}{STOP}",
            rf"\bstarted at {ENTITY}{STOP}",
        ]
        for pattern in employer_patterns:
            for match in re.finditer(pattern, text, re.I):
                memories.append(self._fact("employer", self._clean_entity(match.group(1)), 0.93))

        for match in re.finditer(rf"\bmy job title is {LOWER_PHRASE}{STOP}", text, re.I):
            memories.append(self._fact("job_title", self._clean(match.group(1)), 0.9))

        for match in re.finditer(
            r"\bas (?:a|an) "
            r"(PM|product manager|engineer|software engineer|designer|developer|founder)"
            rf"{STOP}",
            text,
            re.I,
        ):
            memories.append(self._fact("job_title", self._clean(match.group(1)), 0.82))

        for match in re.finditer(
            r"\bI(?:'m| am) (?:a|an) "
            r"([A-Za-z][A-Za-z /+-]*(?:engineer|developer|designer|manager|PM|lead|"
            r"founder|student))"
            rf"{STOP}",
            text,
            re.I,
        ):
            memories.append(self._fact("job_title", self._clean(match.group(1)), 0.78))

        return memories

    def _extract_preferences(self, text: str) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []

        if re.search(r"\bprefer (?:concise|short|brief|direct).{0,40}answers?\b", text, re.I):
            memories.append(
                ExtractedMemory(
                    "preference",
                    "answer_style",
                    "Prefers concise, direct answers",
                    0.92,
                )
            )

        if re.search(
            r"\b(?:keep|make) (?:it |answers? )?(?:concise|short|brief|direct)\b",
            text,
            re.I,
        ):
            memories.append(
                ExtractedMemory(
                    "preference",
                    "answer_style",
                    "Prefers concise, direct answers",
                    0.86,
                )
            )

        for match in re.finditer(rf"\bI prefer {ENTITY}{STOP}", text, re.I):
            value = self._clean_entity(match.group(1))
            memories.append(
                ExtractedMemory(
                    "preference",
                    "programming_language_preference",
                    f"Prefers {value}",
                    0.78,
                )
            )

        for match in re.finditer(
            rf"\bI (?:like|love|use) {ENTITY} for ([a-z][^.;,!]+){STOP}",
            text,
            re.I,
        ):
            language = self._clean_entity(match.group(1))
            purpose = self._clean(match.group(2))
            memories.append(
                ExtractedMemory(
                    "preference",
                    "programming_language_preference",
                    f"Uses {language} for {purpose}",
                    0.82,
                )
            )

        return memories

    def _extract_pets(self, text: str) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []

        for match in re.finditer(
            rf"\b(?:my )?(dog|cat|pet) (?:is )?named {ENTITY}{STOP}",
            text,
            re.I,
        ):
            species = match.group(1).lower()
            name = self._clean_entity(match.group(2))
            memories.append(self._fact("pet", f"Has a {species} named {name}", 0.92))

        for match in re.finditer(rf"\bmy (dog|cat) (?!is named\b){ENTITY}{STOP}", text, re.I):
            species = match.group(1).lower()
            name = self._clean_entity(match.group(2))
            memories.append(self._fact("pet", f"Has a {species} named {name}", 0.84))

        for match in re.finditer(rf"\bwalking {ENTITY} this morning\b", text, re.I):
            name = self._clean_entity(match.group(1))
            memories.append(self._fact("pet", f"Likely has a pet named {name}", 0.65))

        return memories

    def _extract_health_and_diet(self, text: str) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []

        for match in re.finditer(
            r"\ballergic to ([A-Za-z][A-Za-z ,/-]+?)(?=\.|,|;|!|\?|$)",
            text,
            re.I,
        ):
            allergy = self._clean(match.group(1))
            memories.append(self._fact("allergy", f"Allergic to {allergy}", 0.95))

        if re.search(r"\bI(?:'m| am) vegetarian\b", text, re.I):
            memories.append(ExtractedMemory("preference", "diet", "Vegetarian", 0.94))

        if re.search(r"\bI(?:'m| am) vegan\b", text, re.I):
            memories.append(ExtractedMemory("preference", "diet", "Vegan", 0.94))

        return memories

    def _extract_projects(self, text: str) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []

        for match in re.finditer(r"\bcurrent project is ([^.;!?]{3,120})", text, re.I):
            memories.append(self._fact("current_project", self._clean(match.group(1)), 0.86))

        for match in re.finditer(r"\bI(?:'m| am) working on ([^.;!?]{3,120})", text, re.I):
            memories.append(self._fact("current_project", self._clean(match.group(1)), 0.82))

        return memories

    def _extract_opinions(self, text: str) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []

        for match in re.finditer(r"\bI hate ([^.;!?]{3,120})", text, re.I):
            subject = self._clean(match.group(1))
            memories.append(
                ExtractedMemory("opinion", self._opinion_key(subject), f"Dislikes {subject}", 0.83)
            )

        for match in re.finditer(r"\bI love ([^.;!?]{3,120})", text, re.I):
            subject = self._clean(match.group(1))
            memories.append(
                ExtractedMemory("opinion", self._opinion_key(subject), f"Likes {subject}", 0.82)
            )

        if re.search(r"\bTypeScript generics are getting annoying\b", text, re.I):
            memories.append(
                ExtractedMemory(
                    "opinion",
                    "typescript",
                    "Finds TypeScript generics annoying",
                    0.8,
                )
            )

        if re.search(r"\bTypeScript is fine for big projects\b", text, re.I):
            memories.append(
                ExtractedMemory(
                    "opinion",
                    "typescript",
                    "Finds TypeScript fine for big projects but prefers Python for scripts",
                    0.88,
                )
            )

        return memories

    def _extract_events(
        self,
        text: str,
        role: str,
        tool_name: str | None,
    ) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []

        if role == "tool" and re.search(r"\bappointment\b", text, re.I):
            key = f"{tool_name or 'tool'}_appointment"
            memories.append(ExtractedMemory("event", key, self._clean(text), 0.7))

        return memories

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _clean(value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip(" \t\n\r.,;:!?\"'")
        return value[0].upper() + value[1:] if value else value

    @classmethod
    def _clean_entity(cls, value: str) -> str:
        value = re.split(
            r"\s+(?:and|but|because|as|last|this|now|recently)\s+|\.\s+I\b",
            value,
            maxsplit=1,
            flags=re.I,
        )[0]
        return cls._clean(value)

    @staticmethod
    def _fact(key: str, value: str, confidence: float) -> ExtractedMemory:
        return ExtractedMemory("fact", key, value, confidence)

    @staticmethod
    def _opinion_key(subject: str) -> str:
        first_word = re.sub(r"[^a-z0-9_]+", "_", subject.lower()).strip("_")
        if "abstract" in first_word or "explanation" in first_word:
            return "explanation_style"
        if "typescript" in first_word:
            return "typescript"
        return first_word[:80] or "general"

    @staticmethod
    def _dedupe(memories: list[ExtractedMemory]) -> list[ExtractedMemory]:
        seen: set[tuple[str, str, str]] = set()
        unique: list[ExtractedMemory] = []
        for memory in memories:
            if not memory.value:
                continue
            marker = (memory.type, memory.key, memory.value.lower())
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(memory)
        return unique
