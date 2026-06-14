from __future__ import annotations

from typing import Protocol

from openai import AsyncOpenAI

from src.memory.types import ExtractedMemory
from src.schemas.responses import MemoryRecord


class EmbeddingProvider(Protocol):
    async def embed(self, text: str) -> list[float] | None:
        """Return an embedding vector for semantic retrieval."""


class DisabledEmbeddingProvider:
    async def embed(self, text: str) -> list[float] | None:
        _ = text
        return None


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        dimensions: int,
        base_url: str | None = None,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def embed(self, text: str) -> list[float] | None:
        response = await self.client.embeddings.create(
            model=self.model,
            input=text,
            dimensions=self.dimensions,
        )
        return list(response.data[0].embedding)


def memory_embedding_text(memory: ExtractedMemory | MemoryRecord) -> str:
    metadata = memory.metadata if isinstance(memory.metadata, dict) else {}
    labels = metadata.get("labels", [])
    label_text = " ".join(
        f"{item.get('label', '')}:{item.get('text', '')}"
        for item in labels
        if isinstance(item, dict)
    )
    display_label = metadata.get("display_label")
    candidate_tags = metadata.get("candidate_tags", [])
    tag_text = " ".join(str(tag) for tag in candidate_tags if tag)

    parts = [
        memory.type,
        memory.key,
        memory.value,
        str(display_label or ""),
        label_text,
        tag_text,
    ]
    return " ".join(part for part in parts if part).strip()
