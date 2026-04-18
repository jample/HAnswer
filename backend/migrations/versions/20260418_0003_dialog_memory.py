"""dialog sessions and rolling memory

Revision ID: 0003_dialog_memory
Revises: 0002_pedagogical_retrieval
Create Date: 2026-04-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_dialog_memory"
down_revision = "0002_pedagogical_retrieval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(256), nullable=False, server_default="新对话"),
        sa.Column("latest_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("key_facts_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("open_questions_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "last_message_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_conversation_sessions_last_message_at",
        "conversation_sessions",
        ["last_message_at"],
    )
    op.create_index(
        "ix_conversation_sessions_question_id",
        "conversation_sessions",
        ["question_id"],
    )

    op.create_table(
        "conversation_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('user','assistant','system')",
            name="ck_conversation_messages_role",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_conversation_messages_sequence_no",
        ),
    )
    op.create_index(
        "ix_conversation_messages_conversation",
        "conversation_messages",
        ["conversation_id", "sequence_no"],
    )

    op.create_table(
        "conversation_memory_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("key_facts_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("open_questions_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_conversation_memory_snapshots_conversation",
        "conversation_memory_snapshots",
        ["conversation_id", "sequence_no"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_memory_snapshots_conversation",
        table_name="conversation_memory_snapshots",
    )
    op.drop_table("conversation_memory_snapshots")
    op.drop_index("ix_conversation_messages_conversation", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversation_sessions_question_id", table_name="conversation_sessions")
    op.drop_index(
        "ix_conversation_sessions_last_message_at",
        table_name="conversation_sessions",
    )
    op.drop_table("conversation_sessions")
