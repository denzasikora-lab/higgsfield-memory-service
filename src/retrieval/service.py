from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from src.db.repository import MemoryRepository
from src.schemas.requests import RecallRequest, SearchRequest
from src.schemas.responses import (
    Citation,
    MemoryRecord,
    RecallResponse,
    SearchResponse,
    SearchResult,
)

HISTORY_WORDS = {"previous", "formerly", "before", "history", "old", "past", "used to"}

QUERY_KEYWORDS = {
    "current_city": {"live", "city", "based", "location", "moved"},
    "previous_city": {"previous", "formerly", "before", "moved", "from"},
    "employer": {"work", "company", "employer", "job", "joined"},
    "job_title": {"role", "title", "job", "position"},
    "answer_style": {"answer", "respond", "style", "concise", "direct"},
    "explanation_style": {"explain", "explanation", "abstract"},
    "allergy": {"allergy", "allergic", "food", "eat"},
    "diet": {"diet", "vegetarian", "vegan", "food", "eat"},
    "pet": {"pet", "dog", "cat"},
    "programming_language_preference": {"language", "python", "typescript", "script", "code"},
    "current_project": {"project", "building", "working"},
    "relocation": {"moved", "relocation", "live", "city"},
}


@dataclass(frozen=True)
class ScoredMemory:
    memory: MemoryRecord
    score: float


class RetrievalService:
    def __init__(self, repository: MemoryRepository):
        self.repository = repository

    async def recall(self, payload: RecallRequest) -> RecallResponse:
        memories = await self.repository.list_scoped_memories(
            payload.user_id,
            payload.session_id,
            include_inactive=True,
        )
        if not memories:
            return RecallResponse(context="", citations=[])

        scored = self._rank(payload.query, memories)
        selected = self._select_for_recall(payload.query, scored)
        if not selected:
            return RecallResponse(context="", citations=[])

        context = self._assemble_context(selected, memories, payload.max_tokens)
        citations = [
            Citation(
                turn_id=item.memory.source_turn,
                score=round(item.score, 4),
                snippet=self._label(item.memory),
            )
            for item in selected[:8]
        ]
        return RecallResponse(context=context, citations=citations)

    async def search(self, payload: SearchRequest) -> SearchResponse:
        memories = await self.repository.list_scoped_memories(
            payload.user_id,
            payload.session_id,
            include_inactive=self._wants_history(payload.query),
        )
        scored = self._rank(payload.query, memories)
        results = [
            SearchResult(
                content=f"{item.memory.type}:{item.memory.key}: {item.memory.value}",
                score=round(item.score, 4),
                session_id=item.memory.source_session,
                timestamp=item.memory.updated_at,
                metadata={
                    "key": item.memory.key,
                    "type": item.memory.type,
                    "active": item.memory.active,
                    "source_turn": item.memory.source_turn,
                    "supersedes": item.memory.supersedes,
                },
            )
            for item in scored[: payload.limit]
            if item.score >= 0.32
        ]
        return SearchResponse(results=results)

    def _rank(self, query: str, memories: list[MemoryRecord]) -> list[ScoredMemory]:
        query_tokens = self._tokens(query)
        inferred_keys = self._infer_keys(query)
        scored: list[ScoredMemory] = []

        for memory in memories:
            content = f"{memory.type} {memory.key} {memory.value}"
            content_tokens = self._tokens(content)
            overlap = len(query_tokens & content_tokens)
            lexical = overlap / max(len(query_tokens), 1)
            key_match = 1.0 if memory.key in inferred_keys else 0.0
            value_match = 1.0 if memory.value.lower() in query.lower() else 0.0
            active_bonus = 1.0 if memory.active else 0.25
            recency = 1.0
            score = (
                0.42 * lexical
                + 0.24 * key_match
                + 0.16 * min(memory.confidence, 1.0)
                + 0.10 * active_bonus
                + 0.08 * value_match
                + 0.02 * recency
            )
            scored.append(ScoredMemory(memory, score))

        return sorted(scored, key=lambda item: item.score, reverse=True)

    def _select_for_recall(
        self,
        query: str,
        scored: list[ScoredMemory],
    ) -> list[ScoredMemory]:
        inferred_keys = self._infer_keys(query)
        general_query = self._is_general_recall_query(query)
        selected: list[ScoredMemory] = []

        for item in scored:
            memory = item.memory
            if not memory.active and not self._wants_history(query):
                continue
            if general_query:
                if memory.active and memory.type in {"fact", "preference", "opinion", "event"}:
                    selected.append(item)
            elif memory.key in inferred_keys or item.score >= 0.32:
                selected.append(item)

        return selected[:12]

    def _assemble_context(
        self,
        selected: list[ScoredMemory],
        all_memories: list[MemoryRecord],
        max_tokens: int,
    ) -> str:
        max_chars = max_tokens * 4
        previous_values = self._previous_values_by_key(all_memories)

        sections: list[tuple[str, list[str]]] = [
            ("Known facts about this user", []),
            ("Preferences and opinions", []),
            ("Relevant recent context", []),
        ]

        seen: set[str] = set()
        for item in selected:
            memory = item.memory
            marker = f"{memory.type}:{memory.key}:{memory.value.lower()}"
            if marker in seen:
                continue
            seen.add(marker)

            label = self._label(memory)
            previous = previous_values.get(memory.key)
            if memory.active and previous and memory.type == "fact":
                label = f"{label} (updated {memory.updated_at.date()}; previously {previous})"
            elif memory.active:
                label = f"{label} (updated {memory.updated_at.date()})"

            line = f"- {label}"
            if memory.type == "fact":
                sections[0][1].append(line)
            elif memory.type in {"preference", "opinion"}:
                sections[1][1].append(line)
            else:
                sections[2][1].append(line)

        parts: list[str] = []
        current_len = 0
        for title, lines in sections:
            if not lines:
                continue
            block_lines = [f"## {title}"]
            for line in lines:
                projected = current_len + len("\n".join(block_lines + [line])) + 2
                if projected <= max_chars:
                    block_lines.append(line)
            if len(block_lines) > 1:
                block = "\n".join(block_lines)
                current_len += len(block) + 2
                parts.append(block)

        return "\n\n".join(parts)

    @staticmethod
    def _label(memory: MemoryRecord) -> str:
        if memory.key == "current_city":
            return f"Currently lives in {memory.value}"
        if memory.key == "previous_city":
            return f"Previously lived in {memory.value}"
        if memory.key == "employer":
            return f"Works at {memory.value}"
        if memory.key == "job_title":
            return f"Job title: {memory.value}"
        if memory.key == "current_project":
            return f"Current project: {memory.value}"
        if memory.key == "allergy":
            return memory.value
        if memory.key == "diet":
            return f"Diet: {memory.value}"
        if memory.key == "pet":
            return memory.value
        return memory.value

    @staticmethod
    def _previous_values_by_key(memories: list[MemoryRecord]) -> dict[str, str]:
        values: dict[str, list[str]] = defaultdict(list)
        for memory in memories:
            if memory.active:
                continue
            values[memory.key].append(memory.value)
        return {key: ", ".join(items[:2]) for key, items in values.items()}

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 1}

    @staticmethod
    def _wants_history(query: str) -> bool:
        lower = query.lower()
        return any(word in lower for word in HISTORY_WORDS)

    @staticmethod
    def _is_general_recall_query(query: str) -> bool:
        lower = query.lower()
        return any(
            phrase in lower
            for phrase in (
                "what should i know",
                "what do you know",
                "context",
                "about this user",
                "how to answer",
                "how should i respond",
            )
        )

    @staticmethod
    def _infer_keys(query: str) -> set[str]:
        tokens = {token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) > 1}
        keys = {
            key
            for key, keywords in QUERY_KEYWORDS.items()
            if tokens & keywords or any(keyword in query.lower() for keyword in keywords)
        }
        if "where" in tokens and not keys:
            keys.add("current_city")
        return keys
