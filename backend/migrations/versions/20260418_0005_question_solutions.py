"""question solution variants

Revision ID: 0005_question_solutions
Revises: 0004_question_stage_reviews
Create Date: 2026-04-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_question_solutions"
down_revision = "0004_question_stage_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question_solutions",
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
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(64), nullable=False, server_default=""),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("answer_package_json", postgresql.JSONB(), nullable=True),
        sa.Column("visualizations_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("sediment_json", postgresql.JSONB(), nullable=True),
        sa.Column("stage_reviews_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "question_id",
            "ordinal",
            name="uq_question_solutions_question_ordinal",
        ),
    )
    op.create_index(
        "ix_question_solutions_question_current",
        "question_solutions",
        ["question_id", "is_current"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_question_solutions_question_current",
        table_name="question_solutions",
    )
    op.drop_table("question_solutions")
