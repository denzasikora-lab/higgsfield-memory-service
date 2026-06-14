"""Add cosine index for memory embeddings.

Revision ID: 0002_embedding_cosine_index
Revises: 0001_initial_schema
Create Date: 2026-06-14 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "0002_embedding_cosine_index"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memories_embedding_cosine "
        "ON memories USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_memories_embedding_cosine")
