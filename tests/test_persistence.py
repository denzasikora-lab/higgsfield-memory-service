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


def test_embedding_migration_adds_cosine_index() -> None:
    migration = Path("alembic/versions/0002_embedding_cosine_index.py").read_text()

    assert "vector_cosine_ops" in migration
    assert "ix_memories_embedding_cosine" in migration
