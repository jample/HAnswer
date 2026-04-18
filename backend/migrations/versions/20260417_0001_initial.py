"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    op.create_table(
        "ingest_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("mime", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False, index=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("image_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("ingest_images.id", ondelete="SET NULL"), nullable=True),
        sa.Column("parsed_json", postgresql.JSONB(), nullable=False),
        sa.Column("answer_package_json", postgresql.JSONB(), nullable=True),
        sa.Column("subject", sa.String(16), nullable=False),
        sa.Column("grade_band", sa.String(16), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("dedup_hash", sa.String(64), nullable=False),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("dedup_hash", name="uq_questions_dedup_hash"),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name="ck_questions_difficulty"),
        sa.CheckConstraint("subject IN ('math','physics')", name="ck_questions_subject"),
        sa.CheckConstraint("grade_band IN ('junior','senior')", name="ck_questions_grade_band"),
    )
    op.create_index("ix_questions_subject_grade_diff", "questions",
                    ["subject", "grade_band", "difficulty"])

    op.create_table(
        "answer_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("question_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section", sa.String(48), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_answer_packages_question_id", "answer_packages", ["question_id"])

    op.create_table(
        "solution_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("question_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("formula", sa.Text(), nullable=False, server_default=""),
        sa.Column("why_this_step", sa.Text(), nullable=False),
        sa.Column("viz_ref", sa.Text(), nullable=False, server_default=""),
    )

    op.create_table(
        "visualizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("question_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("viz_ref", sa.String(128), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("learning_goal", sa.Text(), nullable=False),
        sa.Column("helpers_used_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("jsx_code", sa.Text(), nullable=False),
        sa.Column("params_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("animation_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "knowledge_points",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("knowledge_points.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name_cn", sa.String(128), nullable=False),
        sa.Column("path_cached", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(16), nullable=False),
        sa.Column("grade_band", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_ref", sa.String(64), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("subject", "grade_band", "path_cached", name="uq_kp_path"),
    )
    op.create_index("ix_kp_status_parent", "knowledge_points", ["status", "parent_id"])

    op.create_table(
        "method_patterns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("name_cn", sa.String(128), nullable=False),
        sa.Column("subject", sa.String(16), nullable=False),
        sa.Column("grade_band", sa.String(16), nullable=False),
        sa.Column("when_to_use", sa.Text(), nullable=False),
        sa.Column("procedure_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("pitfalls_json", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding_ref", sa.String(64), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("subject", "grade_band", "name_cn", name="uq_pattern_name"),
    )
    op.create_index("ix_pattern_status", "method_patterns", ["status"])

    op.create_table(
        "pitfalls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("name_cn", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("pattern_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("method_patterns.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "question_kp_link",
        sa.Column("question_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("kp_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("knowledge_points.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("weight", sa.Numeric(4, 3), nullable=False, server_default="1.0"),
    )
    op.create_table(
        "question_pattern_link",
        sa.Column("question_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("pattern_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("method_patterns.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("weight", sa.Numeric(4, 3), nullable=False, server_default="1.0"),
    )

    op.create_table(
        "exams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("config_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "exam_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("exam_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("exams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source_question_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("questions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("synthesized_payload_json", postgresql.JSONB(), nullable=True),
        sa.Column("answer_outline", sa.Text(), nullable=False, server_default=""),
        sa.Column("rubric", sa.Text(), nullable=False, server_default=""),
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("uuid_generate_v4()")),
        sa.Column("task", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_llm_calls_task_version", "llm_calls", ["task", "prompt_version"])


def downgrade() -> None:
    for t in [
        "llm_calls", "exam_items", "exams",
        "question_pattern_link", "question_kp_link",
        "pitfalls", "method_patterns", "knowledge_points",
        "visualizations", "solution_steps", "answer_packages",
        "questions", "ingest_images",
    ]:
        op.drop_table(t)
