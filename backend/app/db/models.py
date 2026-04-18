"""ORM models matching §5.5.1.

Design notes:
  - UUID PKs; `uuid_generate_v4()` from the `uuid-ossp` Postgres extension.
  - JSONB for LLM payloads (parsed + answer_package).
  - `seen_count` counters updated on dedup hits.
  - status enums (pending|live) for taxonomy nodes.
  - Unique index on `questions.dedup_hash` for exact-match dedup.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)


class IngestImage(Base):
    __tablename__ = "ingest_images"

    id: Mapped[uuid.UUID] = _uuid_pk()
    path: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = _created_at()


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    image_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingest_images.id", ondelete="SET NULL"), nullable=True,
    )
    parsed_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    answer_package_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    subject: Mapped[str] = mapped_column(String(16), nullable=False)
    grade_band: Mapped[str] = mapped_column(String(16), nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    dedup_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        UniqueConstraint("dedup_hash", name="uq_questions_dedup_hash"),
        Index("ix_questions_subject_grade_diff", "subject", "grade_band", "difficulty"),
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="ck_questions_difficulty"),
        CheckConstraint("subject IN ('math','physics')", name="ck_questions_subject"),
        CheckConstraint("grade_band IN ('junior','senior')", name="ck_questions_grade_band"),
    )


class AnswerPackageSection(Base):
    """Streamed section storage; enables resume-after-refresh."""
    __tablename__ = "answer_packages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False,
    )
    section: Mapped[str] = mapped_column(String(48), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index("ix_answer_packages_question_id", "question_id"),
    )


class SolutionStepRow(Base):
    __tablename__ = "solution_steps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    formula: Mapped[str] = mapped_column(Text, nullable=False, default="")
    why_this_step: Mapped[str] = mapped_column(Text, nullable=False)
    viz_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")


class VisualizationRow(Base):
    __tablename__ = "visualizations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False,
    )
    viz_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    learning_goal: Mapped[str] = mapped_column(Text, nullable=False)
    helpers_used_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    jsx_code: Mapped[str] = mapped_column(Text, nullable=False)
    params_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    animation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = _created_at()


_STATUS = Enum("pending", "live", name="taxonomy_status")


class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"

    id: Mapped[uuid.UUID] = _uuid_pk()
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_points.id", ondelete="SET NULL"), nullable=True,
    )
    name_cn: Mapped[str] = mapped_column(String(128), nullable=False)
    path_cached: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(String(16), nullable=False)
    grade_band: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index("ix_kp_status_parent", "status", "parent_id"),
        UniqueConstraint("subject", "grade_band", "path_cached", name="uq_kp_path"),
    )


class MethodPatternRow(Base):
    __tablename__ = "method_patterns"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name_cn: Mapped[str] = mapped_column(String(128), nullable=False)
    subject: Mapped[str] = mapped_column(String(16), nullable=False)
    grade_band: Mapped[str] = mapped_column(String(16), nullable=False)
    when_to_use: Mapped[str] = mapped_column(Text, nullable=False)
    procedure_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    pitfalls_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index("ix_pattern_status", "status"),
        UniqueConstraint("subject", "grade_band", "name_cn", name="uq_pattern_name"),
    )


class Pitfall(Base):
    __tablename__ = "pitfalls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name_cn: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    pattern_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("method_patterns.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = _created_at()


class QuestionKPLink(Base):
    __tablename__ = "question_kp_link"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True,
    )
    kp_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_points.id", ondelete="CASCADE"), primary_key=True,
    )
    weight: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=1.0)


class QuestionPatternLink(Base):
    __tablename__ = "question_pattern_link"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True,
    )
    pattern_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("method_patterns.id", ondelete="CASCADE"), primary_key=True,
    )
    weight: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=1.0)


class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created_at()


class ExamItem(Base):
    __tablename__ = "exam_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL"), nullable=True,
    )
    synthesized_payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    answer_outline: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rubric: Mapped[str] = mapped_column(Text, nullable=False, default="")


class LLMCall(Base):
    """Cost & quality ledger (§7.1.3)."""
    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    task: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index("ix_llm_calls_task_version", "task", "prompt_version"),
    )
