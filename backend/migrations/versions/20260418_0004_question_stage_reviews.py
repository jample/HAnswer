"""question stage review state

Revision ID: 0004_question_stage_reviews
Revises: 0003_dialog_memory
Create Date: 2026-04-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_question_stage_reviews"
down_revision = "0003_dialog_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question_stage_reviews",
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
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("review_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("artifact_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("summary_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("refs_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
            "stage",
            name="uq_question_stage_reviews_question_stage",
        ),
    )
    op.create_index(
        "ix_question_stage_reviews_question_id",
        "question_stage_reviews",
        ["question_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_question_stage_reviews_question_id",
        table_name="question_stage_reviews",
    )
    op.drop_table("question_stage_reviews")
