"""pedagogical retrieval profile tables

Revision ID: 0002_pedagogical_retrieval
Revises: 0001_initial
Create Date: 2026-04-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_pedagogical_retrieval"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question_retrieval_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("question_id", name="uq_question_retrieval_profiles_question_id"),
    )

    op.create_table(
        "retrieval_units",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("unit_kind", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("keywords_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("weight", sa.Numeric(4, 3), nullable=False, server_default="1.0"),
        sa.Column("source_section", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_retrieval_units_question_kind",
        "retrieval_units",
        ["question_id", "unit_kind"],
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_units_question_kind", table_name="retrieval_units")
    op.drop_table("retrieval_units")
    op.drop_table("question_retrieval_profiles")
