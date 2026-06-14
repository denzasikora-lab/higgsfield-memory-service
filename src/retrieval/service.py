from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from math import sqrt

from src.db.repository import MemoryRepository
from src.memory.embedding import EmbeddingProvider
from src.schemas.requests import RecallRequest, SearchRequest
from src.schemas.responses import (
    Citation,
    MemoryRecord,
    RecallResponse,
    SearchResponse,
    SearchResult,
)

HISTORY_WORDS = {"previous", "formerly", "before", "history", "old", "past", "used to"}
MIN_RETRIEVAL_SCORE = 0.28

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
    def __init__(
        self,
        repository: MemoryRepository,
        embedding_provider: EmbeddingProvider | None = None,
    ):
        self.repository = repository
        self.embedding_provider = embedding_provider

    async def recall(self, payload: RecallRequest) -> RecallResponse:
        memories = await self.repository.list_scoped_memories(
            payload.user_id,
            payload.session_id,
            include_inactive=True,
        )
        if not memories:
            return RecallResponse(context="", citations=[])

        scored = await self._rank(
            payload.query,
            memories,
            payload.user_id,
            payload.session_id,
            include_inactive=True,
            limit=50,
        )
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
        include_inactive = self._wants_history(payload.query)
        memories = await self.repository.list_scoped_memories(
            payload.user_id,
            payload.session_id,
            include_inactive=include_inactive,
        )
        scored = await self._rank(
            payload.query,
            memories,
            payload.user_id,
            payload.session_id,
            include_inactive=include_inactive,
            limit=max(payload.limit * 4, 50),
        )
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
                    "labels": item.memory.metadata.get("labels", []),
                    "display_label": item.memory.metadata.get("display_label"),
                },
            )
            for item in scored[: payload.limit]
            if item.score >= MIN_RETRIEVAL_SCORE
        ]
        return SearchResponse(results=results)

    async def _rank(
        self,
        query: str,
        memories: list[MemoryRecord],
        user_id: str | None,
        session_id: str | None,
        include_inactive: bool,
        limit: int,
    ) -> list[ScoredMemory]:
        query_embedding = await self._embed_query(query)
        if query_embedding is not None:
            vector_scored = await self._rank_by_vector(
                query,
                query_embedding,
                memories,
                user_id,
                session_id,
                include_inactive,
                limit,
            )
            if vector_scored:
                return vector_scored

        return self._rank_lexical(query, memories)

    async def _rank_by_vector(
        self,
        query: str,
        query_embedding: list[float],
        memories: list[MemoryRecord],
        user_id: str | None,
        session_id: str | None,
        include_inactive: bool,
        limit: int,
    ) -> list[ScoredMemory]:
        search_by_vector = getattr(self.repository, "search_scoped_memories_by_vector", None)
        if callable(search_by_vector):
            hits = await search_by_vector(
                query_embedding,
                user_id,
                session_id,
                include_inactive=include_inactive,
                limit=limit,
            )
            return self._score_vector_hits(query, hits)

        embedded_memories = [memory for memory in memories if memory.embedding]
        hits = [
            ScoredMemory(memory, self._cosine_similarity(query_embedding, memory.embedding or []))
            for memory in embedded_memories
        ]
        return self._score_vector_hits(query, hits)

    async def _embed_query(self, query: str) -> list[float] | None:
        if self.embedding_provider is None:
            return None
        try:
            return await self.embedding_provider.embed(query)
        except Exception:
            return None

    def _score_vector_hits(self, query: str, hits: list[object]) -> list[ScoredMemory]:
        inferred_keys = self._infer_keys(query)
        scored: list[ScoredMemory] = []
        for hit in hits:
            memory = hit.memory
            semantic = max(-1.0, min(1.0, hit.score))
            key_match = 1.0 if memory.key in inferred_keys else 0.0
            value_match = 1.0 if memory.value.lower() in query.lower() else 0.0
            active_bonus = 1.0 if memory.active else 0.25
            score = (
                0.86 * semantic
                + 0.05 * key_match
                + 0.04 * min(memory.confidence, 1.0)
                + 0.03 * value_match
                + 0.02 * active_bonus
            )
            scored.append(ScoredMemory(memory, score))
        return sorted(scored, key=lambda item: item.score, reverse=True)

    def _rank_lexical(self, query: str, memories: list[MemoryRecord]) -> list[ScoredMemory]:
        query_tokens = self._tokens(query)
        inferred_keys = self._infer_keys(query)
        recency_scores = self._recency_scores(memories)
        scored: list[ScoredMemory] = []

        for memory in memories:
            content = f"{memory.type} {memory.key} {memory.value}"
            content_tokens = self._tokens(content)
            overlap = len(query_tokens & content_tokens)
            lexical = overlap / max(len(query_tokens), 1)
            key_match = 1.0 if memory.key in inferred_keys else 0.0
            value_match = 1.0 if memory.value.lower() in query.lower() else 0.0
            active_bonus = 1.0 if memory.active else 0.25
            recency = recency_scores.get(memory.id, 1.0)
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

    @staticmethod
    def _recency_scores(memories: list[MemoryRecord]) -> dict[str, float]:
        if not memories:
            return {}

        timestamps = [memory.updated_at.timestamp() for memory in memories]
        oldest = min(timestamps)
        newest = max(timestamps)
        if newest == oldest:
            return {memory.id: 1.0 for memory in memories}

        return {
            memory.id: (memory.updated_at.timestamp() - oldest) / (newest - oldest)
            for memory in memories
        }

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
            elif memory.key in inferred_keys or item.score >= MIN_RETRIEVAL_SCORE:
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
        display_label = memory.metadata.get("display_label")
        if isinstance(display_label, str) and display_label.strip():
            return display_label.strip()
        return f"{memory.key}: {memory.value}"

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

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        numerator = sum(
            left_value * right_value for left_value, right_value in zip(left, right, strict=True)
        )
        left_norm = sqrt(sum(value * value for value in left))
        right_norm = sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return numerator / (left_norm * right_norm)
