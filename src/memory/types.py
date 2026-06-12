from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MemoryType = Literal["fact", "preference", "opinion", "event"]


@dataclass(frozen=True)
class ExtractedMemory:
    type: MemoryType
    key: str
    value: str
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)
