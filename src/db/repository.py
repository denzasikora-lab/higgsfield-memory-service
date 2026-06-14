from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Memory, Turn
from src.memory.embedding import EmbeddingProvider, memory_embedding_text
from src.memory.types import ExtractedMemory
from src.schemas.requests import TurnCreateRequest
from src.schemas.responses import MemoryRecord

HARD_SUPERSEDE_KEYS = {
    "allergy",
    "answer_style",
    "country",
    "current_city",
    "current_project",
    "diet",
    "employer",
    "job_title",
    "pet",
    "preferred_language",
    "programming_language_preference",
    "relationship_status",
    "school",
}


@dataclass(frozen=True)
class MemorySearchHit:
    memory: MemoryRecord
    score: float


@dataclass(frozen=True)
class EvictionCandidate:
    id: str
    active: bool
    confidence: float
    created_at: datetime
    updated_at: datetime
    supersedes: str | None


@dataclass(frozen=True)
class MemoryOwnerScope:
    kind: str
    value: str


class MemoryRepository:
    def __init__(
        self,
        session: AsyncSession,
        embedding_provider: EmbeddingProvider | None = None,
        memory_max_per_scope: int = 200,
    ):
        self.session = session
        self.embedding_provider = embedding_provider
        self.memory_max_per_scope = memory_max_per_scope

    async def create_turn(self, payload: TurnCreateRequest) -> str:
        turn_id = f"turn_{uuid4().hex}"
        turn = Turn(
            id=turn_id,
            session_id=payload.session_id,
            user_id=payload.user_id,
            timestamp=payload.timestamp,
            messages_json=[message.model_dump(exclude_none=True) for message in payload.messages],
            metadata_json=payload.metadata,
        )
        self.session.add(turn)
        await self.session.flush()
        return turn_id

    async def store_extracted_memories(
        self,
        payload: TurnCreateRequest,
        turn_id: str,
        extracted_memories: Iterable[ExtractedMemory],
    ) -> list[MemoryRecord]:
        stored: list[Memory] = []
        extracted_list = list(extracted_memories)

        for extracted in extracted_list:
            memory = await self._build_memory(payload, turn_id, extracted)
            if memory is None:
                continue
            stored.append(memory)

        if stored:
            self.session.add_all(stored)
            await self.session.flush()

        deleted_ids: set[str] = set()
        if extracted_list:
            deleted_ids = await self._evict_over_scope_limit(payload.user_id, payload.session_id)
        await self.session.commit()

        kept_stored = [memory for memory in stored if memory.id not in deleted_ids]
        for memory in kept_stored:
            await self.session.refresh(memory)

        return [self._memory_to_record(memory) for memory in kept_stored]

    async def list_user_memories(self, user_id: str) -> list[MemoryRecord]:
        result = await self.session.execute(
            select(Memory).where(Memory.user_id == user_id).order_by(Memory.updated_at.desc())
        )
        return [self._memory_to_record(memory) for memory in result.scalars()]

    async def list_scoped_memories(
        self,
        user_id: str | None,
        session_id: str | None,
        include_inactive: bool = False,
        limit: int = 500,
    ) -> list[MemoryRecord]:
        statement = select(Memory)
        statement = self._apply_scope(statement, user_id, session_id)

        if not include_inactive:
            statement = statement.where(Memory.active.is_(True))

        result = await self.session.execute(
            statement.order_by(Memory.updated_at.desc(), Memory.created_at.desc()).limit(limit)
        )
        return [self._memory_to_record(memory) for memory in result.scalars()]

    async def search_scoped_memories_by_vector(
        self,
        query_embedding: list[float],
        user_id: str | None,
        session_id: str | None,
        include_inactive: bool = False,
        limit: int = 50,
    ) -> list[MemorySearchHit]:
        distance = Memory.embedding.cosine_distance(query_embedding)
        statement = select(Memory, distance.label("distance")).where(Memory.embedding.is_not(None))
        statement = self._apply_scope(statement, user_id, session_id)

        if not include_inactive:
            statement = statement.where(Memory.active.is_(True))

        result = await self.session.execute(statement.order_by(distance.asc()).limit(limit))
        hits: list[MemorySearchHit] = []
        for memory, distance_value in result.all():
            if distance_value is None:
                continue
            score = max(-1.0, min(1.0, 1.0 - float(distance_value)))
            hits.append(MemorySearchHit(self._memory_to_record(memory), score))
        return hits

    async def delete_session(self, session_id: str) -> None:
        await self.session.execute(
            delete(Memory).where(
                (Memory.session_id == session_id) | (Memory.source_session == session_id)
            )
        )
        await self.session.execute(delete(Turn).where(Turn.session_id == session_id))
        await self.session.commit()

    async def delete_user(self, user_id: str) -> None:
        await self.session.execute(delete(Memory).where(Memory.user_id == user_id))
        await self.session.execute(delete(Turn).where(Turn.user_id == user_id))
        await self.session.commit()

    async def _build_memory(
        self,
        payload: TurnCreateRequest,
        turn_id: str,
        extracted: ExtractedMemory,
    ) -> Memory | None:
        normalized_key = self._normalize_key(extracted.key)
        existing_active = await self._active_memories_for_key(
            payload.user_id,
            payload.session_id,
            normalized_key,
            extracted.type,
        )

        for existing in existing_active:
            if self._same_value(existing.value, extracted.value):
                existing.confidence = max(existing.confidence, extracted.confidence)
                existing.updated_at = payload.timestamp
                existing.metadata_json = merge_memory_metadata(
                    existing.metadata_json,
                    extracted.metadata,
                )
                if existing.embedding is None:
                    existing.embedding = await self._embed_memory(extracted)
                await self.session.flush()
                return None

        supersedes: str | None = None
        if self._should_supersede(extracted):
            supersedes = existing_active[0].id if existing_active else None
            for existing in existing_active:
                existing.active = False
                existing.updated_at = payload.timestamp
            await self.session.flush()
        elif extracted.type == "opinion" and existing_active:
            supersedes = existing_active[0].id
            extracted = self._merge_opinion(existing_active[0], extracted)
            for existing in existing_active:
                existing.active = False
                existing.updated_at = payload.timestamp
            await self.session.flush()

        return Memory(
            id=f"mem_{uuid4().hex}",
            user_id=payload.user_id,
            session_id=payload.session_id,
            type=extracted.type,
            key=extracted.key,
            value=extracted.value,
            normalized_key=normalized_key,
            confidence=extracted.confidence,
            source_turn=turn_id,
            source_session=payload.session_id,
            created_at=payload.timestamp,
            updated_at=payload.timestamp,
            supersedes=supersedes,
            active=True,
            metadata_json=extracted.metadata,
            embedding=await self._embed_memory(extracted),
        )

    async def _active_memories_for_key(
        self,
        user_id: str | None,
        session_id: str,
        normalized_key: str,
        memory_type: str,
    ) -> list[Memory]:
        statement = select(Memory).where(
            Memory.normalized_key == normalized_key,
            Memory.type == memory_type,
            Memory.active.is_(True),
        )

        if user_id:
            statement = statement.where(Memory.user_id == user_id)
        else:
            statement = statement.where(Memory.user_id.is_(None), Memory.session_id == session_id)

        result = await self.session.execute(statement.order_by(Memory.updated_at.desc()))
        return list(result.scalars())

    @staticmethod
    def _memory_to_record(memory: Memory) -> MemoryRecord:
        return MemoryRecord(
            id=memory.id,
            type=memory.type,
            key=memory.key,
            value=memory.value,
            confidence=memory.confidence,
            source_session=memory.source_session,
            source_turn=memory.source_turn,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
            supersedes=memory.supersedes,
            active=memory.active,
            metadata=memory.metadata_json or {},
            embedding=memory.embedding,
        )

    async def _embed_memory(self, memory: ExtractedMemory) -> list[float] | None:
        if self.embedding_provider is None:
            return None
        return await self.embedding_provider.embed(memory_embedding_text(memory))

    async def _evict_over_scope_limit(self, user_id: str | None, session_id: str) -> set[str]:
        if self.memory_max_per_scope < 1:
            return set()

        scope = memory_owner_scope(user_id, session_id)
        statement = select(Memory)
        if scope.kind == "user":
            statement = statement.where(Memory.user_id == scope.value)
        else:
            statement = statement.where(Memory.user_id.is_(None), Memory.session_id == scope.value)

        result = await self.session.execute(statement)
        candidates = list(result.scalars())
        victims = select_eviction_victims(candidates, self.memory_max_per_scope)
        for victim in victims:
            await self.session.delete(victim)

        if victims:
            await self.session.flush()
        return {victim.id for victim in victims}

    @staticmethod
    def _normalize_key(key: str) -> str:
        return key.strip().lower().replace(" ", "_").replace("-", "_")

    @staticmethod
    def _same_value(left: str, right: str) -> bool:
        return left.strip().casefold() == right.strip().casefold()

    @staticmethod
    def _should_supersede(extracted: ExtractedMemory) -> bool:
        if extracted.type == "fact":
            return extracted.key in HARD_SUPERSEDE_KEYS
        if extracted.type == "preference":
            return extracted.key in HARD_SUPERSEDE_KEYS
        return False

    @staticmethod
    def _merge_opinion(existing: Memory, extracted: ExtractedMemory) -> ExtractedMemory:
        if extracted.value.casefold() in existing.value.casefold():
            value = existing.value
        elif existing.value.casefold() in extracted.value.casefold():
            value = extracted.value
        else:
            value = f"{existing.value}; {extracted.value}"

        return ExtractedMemory(
            type=extracted.type,
            key=extracted.key,
            value=value,
            confidence=max(existing.confidence * 0.95, extracted.confidence),
            metadata=merge_memory_metadata(existing.metadata_json, extracted.metadata),
        )

    @staticmethod
    def _apply_scope(statement: Any, user_id: str | None, session_id: str | None) -> Any:
        if user_id:
            if session_id:
                return statement.where(
                    (Memory.user_id == user_id)
                    | ((Memory.user_id.is_(None)) & (Memory.session_id == session_id))
                )
            return statement.where(Memory.user_id == user_id)
        if session_id:
            return statement.where(Memory.session_id == session_id)
        return statement.where(Memory.id.is_(None))


def select_eviction_victims(
    candidates: Iterable[EvictionCandidate],
    max_count: int,
) -> list[EvictionCandidate]:
    candidates_list = list(candidates)
    excess = len(candidates_list) - max_count
    if excess <= 0:
        return []
    return sorted(candidates_list, key=_eviction_sort_key)[:excess]


def _eviction_sort_key(candidate: EvictionCandidate) -> tuple[int, float, datetime, datetime]:
    inactive_or_superseded = not candidate.active or candidate.supersedes is not None
    return (
        0 if inactive_or_superseded else 1,
        candidate.confidence,
        candidate.updated_at,
        candidate.created_at,
    )


def memory_owner_scope(user_id: str | None, session_id: str) -> MemoryOwnerScope:
    if user_id:
        return MemoryOwnerScope("user", user_id)
    return MemoryOwnerScope("session", session_id)


def merge_memory_metadata(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(left or {})
    merged.update(right or {})
    return merged
