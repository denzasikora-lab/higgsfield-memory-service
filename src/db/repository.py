from __future__ import annotations

from collections.abc import Iterable
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Memory, Turn
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


class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

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

        for extracted in extracted_memories:
            memory = await self._build_memory(payload, turn_id, extracted)
            if memory is None:
                continue
            stored.append(memory)

        if stored:
            self.session.add_all(stored)

        await self.session.commit()

        for memory in stored:
            await self.session.refresh(memory)

        return [self._memory_to_record(memory) for memory in stored]

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

        if user_id:
            if session_id:
                statement = statement.where(
                    (Memory.user_id == user_id)
                    | ((Memory.user_id.is_(None)) & (Memory.session_id == session_id))
                )
            else:
                statement = statement.where(Memory.user_id == user_id)
        elif session_id:
            statement = statement.where(Memory.session_id == session_id)
        else:
            statement = statement.where(Memory.id.is_(None))

        if not include_inactive:
            statement = statement.where(Memory.active.is_(True))

        result = await self.session.execute(
            statement.order_by(Memory.updated_at.desc(), Memory.created_at.desc()).limit(limit)
        )
        return [self._memory_to_record(memory) for memory in result.scalars()]

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
        )

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
            metadata=extracted.metadata,
        )
