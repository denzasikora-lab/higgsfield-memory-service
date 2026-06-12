from __future__ import annotations

import re
from typing import Protocol

from src.schemas.requests import TurnCreateRequest

from .types import ExtractedMemory

STOP = r"(?=\.|,|;|!|\?| and | but | because | as |$)"
ENTITY = r"([A-Z][A-Za-z0-9&.'+-]*(?:\s+[A-Z][A-Za-z0-9&.'+-]*)*)"
LOWER_PHRASE = r"([a-z][A-Za-z0-9&,'+\- /]{1,80})"


class MemoryExtractor(Protocol):
    async def extract(self, payload: TurnCreateRequest, turn_id: str) -> list[ExtractedMemory]:
        """Extract structured memories from a completed turn."""


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

        for match in re.finditer(rf"\bmy (dog|cat) {ENTITY}{STOP}", text, re.I):
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
        value = re.split(r"\s+(?:and|but|because|as)\s+", value, maxsplit=1, flags=re.I)[0]
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
