"""anchor dialog sessions to a concrete solution

Revision ID: 0009_dialog_solution_anchor
Revises: 0008_embedding_sigs
Create Date: 2026-04-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_dialog_solution_anchor"
down_revision = "0008_embedding_sigs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_sessions",
        sa.Column(
            "solution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("question_solutions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_conversation_sessions_solution_id",
        "conversation_sessions",
        ["solution_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_sessions_solution_id", table_name="conversation_sessions")
    op.drop_column("conversation_sessions", "solution_id")