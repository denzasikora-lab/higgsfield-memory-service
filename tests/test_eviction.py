from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.db.repository import EvictionCandidate, memory_owner_scope, select_eviction_victims


def candidate(
    memory_id: str,
    *,
    active: bool = True,
    confidence: float = 0.9,
    age_days: int = 0,
    supersedes: str | None = None,
) -> EvictionCandidate:
    now = datetime(2025, 3, 20, 10, 0, tzinfo=UTC)
    timestamp = now - timedelta(days=age_days)
    return EvictionCandidate(
        id=memory_id,
        active=active,
        confidence=confidence,
        created_at=timestamp,
        updated_at=timestamp,
        supersedes=supersedes,
    )


def test_memory_owner_scope_prefers_user_id_over_session_id() -> None:
    assert memory_owner_scope("u1", "s1").kind == "user"
    assert memory_owner_scope("u1", "s1").value == "u1"
    assert memory_owner_scope(None, "s1").kind == "session"
    assert memory_owner_scope(None, "s1").value == "s1"


def test_eviction_deletes_inactive_superseded_and_low_confidence_first() -> None:
    inactive = candidate("inactive", active=False, confidence=0.99, age_days=1)
    superseded = candidate("superseded", supersedes="old", confidence=0.95, age_days=1)
    active_low = candidate("active_low", confidence=0.2, age_days=10)
    active_high = candidate("active_high", confidence=0.95, age_days=30)

    victims = select_eviction_victims(
        [active_high, active_low, superseded, inactive],
        max_count=2,
    )

    assert [victim.id for victim in victims] == ["superseded", "inactive"]


def test_eviction_deletes_oldest_low_confidence_active_memory_when_needed() -> None:
    active_old_low = candidate("active_old_low", confidence=0.2, age_days=10)
    active_new_low = candidate("active_new_low", confidence=0.2, age_days=1)
    active_high = candidate("active_high", confidence=0.9, age_days=20)

    victims = select_eviction_victims(
        [active_high, active_new_low, active_old_low],
        max_count=2,
    )

    assert [victim.id for victim in victims] == ["active_old_low"]
