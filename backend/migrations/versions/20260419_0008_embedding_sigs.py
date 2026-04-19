"""embedding signature columns for hash-skip on re-runs

Adds nullable hash columns so the sediment pipeline can detect which
texts already have a fresh embedding in Milvus and skip the redundant
Gemini call + Milvus upsert.

  * ``questions.embedding_sigs`` JSONB — per-surface SHA256
      ({"qfull": "...", "afull": "..."})
  * ``method_patterns.embedding_sig`` VARCHAR(64) — single text/hash
  * ``knowledge_points.embedding_sig`` VARCHAR(64)
  * ``retrieval_units.embedding_sig`` VARCHAR(64)

Existing rows have NULL signatures, which forces a re-embed on the
next sediment run — equivalent to the previous unconditional behavior
so this migration is non-destructive.

Note: a sibling change retires the Milvus ``q_emb`` and
``q_emb_sparse`` collections in favor of ``question_full_emb`` for
near-duplicate detection. Drop those Milvus collections manually (or
via Attu) once after deploying — Postgres is not affected.

Revision ID: 0008_embedding_sigs
Revises: 0007_visualization_engines
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_embedding_sigs"
down_revision = "0007_visualization_engines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "questions",
        sa.Column(
            "embedding_sigs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "method_patterns",
        sa.Column("embedding_sig", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "knowledge_points",
        sa.Column("embedding_sig", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "retrieval_units",
        sa.Column("embedding_sig", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("retrieval_units", "embedding_sig")
    op.drop_column("knowledge_points", "embedding_sig")
    op.drop_column("method_patterns", "embedding_sig")
    op.drop_column("questions", "embedding_sigs")
