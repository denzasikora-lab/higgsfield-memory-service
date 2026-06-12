from __future__ import annotations

from pathlib import Path


def test_compose_uses_named_postgres_volume() -> None:
    compose = Path("docker-compose.yml").read_text()

    assert "memory_pgdata:" in compose
    assert "/var/lib/postgresql/data" in compose


def test_initial_migration_enables_pgvector() -> None:
    migration = Path("alembic/versions/0001_initial_schema.py").read_text()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in migration
    assert "Vector(1536)" in migration
