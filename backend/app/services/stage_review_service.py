"""Stage review state and artifact reuse helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import (
    AnswerPackageSection,
    Question,
    QuestionKPLink,
    QuestionPatternLink,
    QuestionRetrievalProfile,
    QuestionStageReview,
    RetrievalUnitRow,
    SolutionStepRow,
    VisualizationRow,
)
from app.schemas import AnswerPackage, ParsedQuestion
from app.services.solution_ref_service import encode_solution_ref
from app.services.vector_store import VectorStore

STAGE_ORDER = ("parsed", "solving", "visualizing", "indexing")
REVIEW_PENDING = "pending"
REVIEW_CONFIRMED = "confirmed"
REVIEW_REJECTED = "rejected"
QUESTION_STATUS_BY_REVIEW_STAGE = {
    "parsed": "review_parse",
    "solving": "review_solve",
    "visualizing": "review_viz",
    "indexing": "review_index",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def review_question_status(stage: str) -> str:
    return QUESTION_STATUS_BY_REVIEW_STAGE[stage]


def next_stage(stage: str) -> str | None:
    try:
        idx = STAGE_ORDER.index(stage)
    except ValueError:
        return None
    if idx + 1 >= len(STAGE_ORDER):
        return None
    return STAGE_ORDER[idx + 1]


def summarize_parsed(parsed_json: dict | None) -> dict:
    parsed = ParsedQuestion.model_validate(parsed_json or {})
    return {
        "question_text": parsed.question_text,
        "topic_path": list(parsed.topic_path),
        "given_count": len(parsed.given),
        "find_count": len(parsed.find),
        "tags": list(parsed.tags),
        "difficulty": parsed.difficulty,
    }


def summarize_answer(answer_package_json: dict | None) -> dict:
    pkg = AnswerPackage.model_validate(answer_package_json or {})
    return {
        "method_pattern": pkg.method_pattern.name_cn,
        "solution_step_count": len(pkg.solution_steps),
        "knowledge_point_count": len(pkg.knowledge_points),
        "similar_question_count": len(pkg.similar_questions),
        "self_check_count": len(pkg.self_check),
    }


def summarize_visualizations(rows: list[VisualizationRow]) -> dict:
    return {
        "visualization_count": len(rows),
        "viz_refs": [row.viz_ref for row in rows],
        "titles": [row.title for row in rows],
    }


def summarize_indexing(
    *,
    pattern_id: str | None,
    kp_ids: list[str],
    retrieval_unit_ids: list[str],
    near_dup_of: str | None,
) -> dict:
    return {
        "pattern_id": pattern_id,
        "kp_count": len(kp_ids),
        "retrieval_unit_count": len(retrieval_unit_ids),
        "near_dup_of": near_dup_of,
    }


async def get_stage_review(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    stage: str,
) -> QuestionStageReview | None:
    stmt = (
        select(QuestionStageReview)
        .where(QuestionStageReview.question_id == question_id)
        .where(QuestionStageReview.stage == stage)
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_stage_reviews(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
) -> list[QuestionStageReview]:
    return list((await session.execute(
        select(QuestionStageReview)
        .where(QuestionStageReview.question_id == question_id)
        .order_by(QuestionStageReview.created_at, QuestionStageReview.stage)
    )).scalars().all())


async def record_stage_artifact(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    stage: str,
    summary: dict,
    refs: dict | None = None,
    review_note: str | None = None,
) -> QuestionStageReview:
    row = await get_stage_review(session, question_id=question_id, stage=stage)
    if row is None:
        row = QuestionStageReview(
            question_id=question_id,
            stage=stage,
            review_status=REVIEW_PENDING,
            artifact_version=1,
            run_count=1,
            summary_json=summary,
            refs_json=refs or {},
            review_note=review_note or "",
        )
        session.add(row)
    else:
        preserved_note = row.review_note if review_note is None else review_note
        row.review_status = REVIEW_PENDING
        row.artifact_version += 1
        row.run_count += 1
        row.summary_json = summary
        row.refs_json = refs or {}
        row.review_note = preserved_note
        row.reviewed_at = None
        row.updated_at = _utcnow()
    await session.flush()
    return row


async def set_stage_review_status(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    stage: str,
    review_status: str,
    review_note: str | None = None,
) -> QuestionStageReview:
    row = await get_stage_review(session, question_id=question_id, stage=stage)
    if row is None:
        row = QuestionStageReview(
            question_id=question_id,
            stage=stage,
            review_status=review_status,
            artifact_version=0,
            run_count=0,
            summary_json={},
            refs_json={},
            review_note=review_note or "",
        )
        session.add(row)
    else:
        row.review_status = review_status
        if review_note is not None:
            row.review_note = review_note
        row.updated_at = _utcnow()
    row.reviewed_at = _utcnow() if review_status == REVIEW_CONFIRMED else None
    await session.flush()
    return row


def serialize_stage_review(row: QuestionStageReview) -> dict:
    return {
        "stage": row.stage,
        "review_status": row.review_status,
        "artifact_version": row.artifact_version,
        "run_count": row.run_count,
        "summary": row.summary_json,
        "refs": row.refs_json,
        "review_note": row.review_note,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def ensure_parsed_stage_review(
    session: AsyncSession,
    *,
    question: Question,
    review_note: str | None = None,
) -> None:
    existing = await get_stage_review(session, question_id=question.id, stage="parsed")
    if existing is not None:
        if review_note is not None:
            existing.review_note = review_note
            existing.updated_at = _utcnow()
            await session.flush()
        return
    row = await record_stage_artifact(
        session,
        question_id=question.id,
        stage="parsed",
        summary=summarize_parsed(question.parsed_json),
        refs={"question_id": str(question.id), "image_id": str(question.image_id) if question.image_id else None},
        review_note=review_note or "",
    )
    if question.status in {"answered", "review_solve", "review_viz", "review_index", "solving", "visualizing", "indexing"}:
        row.review_status = REVIEW_CONFIRMED
        row.reviewed_at = _utcnow()
        row.updated_at = _utcnow()
    else:
        question.status = review_question_status("parsed")
    await session.flush()


async def build_stage_user_guidance(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    target_stage: str,
) -> str:
    try:
        target_idx = STAGE_ORDER.index(target_stage)
    except ValueError:
        return ""

    rows = await list_stage_reviews(session, question_id=question_id)
    notes: list[str] = []
    for row in rows:
        note = row.review_note.strip()
        if not note:
            continue
        try:
            stage_idx = STAGE_ORDER.index(row.stage)
        except ValueError:
            continue
        if stage_idx < target_idx and row.review_status == REVIEW_CONFIRMED:
            notes.append(f"[来自{row.stage}阶段的用户要求]\n{note}")
        elif stage_idx == target_idx:
            notes.append(f"[本次{row.stage}阶段重跑要求]\n{note}")
    if not notes:
        return ""
    return (
        "请严格遵守以下用户补充要求。这些要求优先于默认表达风格，但不能违反事实、题意或 JSON Schema。\n\n"
        + "\n\n".join(notes)
    )


async def _delete_question_level_vectors(
    *,
    vector_store: VectorStore | None,
    question_id: uuid.UUID,
    solution_id: uuid.UUID | None = None,
) -> None:
    if vector_store is None:
        return
    ref_id = encode_solution_ref(question_id=question_id, solution_id=solution_id)
    for collection in ("question_full_emb", "answer_full_emb"):
        await vector_store.delete(collection, ref_id=ref_id)


async def clear_stage_outputs(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    stage: str,
    vector_store: VectorStore | None = None,
    solution_id: uuid.UUID | None = None,
) -> None:
    q = await repo.get_question(session, question_id)
    if q is None:
        raise KeyError(f"question {question_id} not found")

    retrieval_units = list((await session.execute(
        select(RetrievalUnitRow)
        .where(RetrievalUnitRow.question_id == question_id)
        .where(RetrievalUnitRow.solution_id == solution_id)
    )).scalars().all())

    if stage in {"parsed", "solving"}:
        await session.execute(
            delete(AnswerPackageSection).where(AnswerPackageSection.question_id == question_id)
        )
        await session.execute(
            delete(SolutionStepRow).where(SolutionStepRow.question_id == question_id)
        )
        q.answer_package_json = None
    else:
        await session.execute(
            delete(AnswerPackageSection)
            .where(AnswerPackageSection.question_id == question_id)
            .where(AnswerPackageSection.section.in_(["status", "error", "sediment"]))
        )

    if stage in {"parsed", "solving", "visualizing"}:
        await session.execute(
            delete(VisualizationRow).where(VisualizationRow.question_id == question_id)
        )

    if stage in {"parsed", "solving", "indexing"}:
        await session.execute(
            delete(RetrievalUnitRow)
            .where(RetrievalUnitRow.question_id == question_id)
            .where(RetrievalUnitRow.solution_id == solution_id)
        )
        await session.execute(
            delete(QuestionRetrievalProfile)
            .where(QuestionRetrievalProfile.question_id == question_id)
            .where(QuestionRetrievalProfile.solution_id == solution_id)
        )
        if solution_id is None:
            await session.execute(
                delete(QuestionKPLink).where(QuestionKPLink.question_id == question_id)
            )
            await session.execute(
                delete(QuestionPatternLink).where(QuestionPatternLink.question_id == question_id)
            )
        await _delete_question_level_vectors(
            vector_store=vector_store,
            question_id=question_id,
            solution_id=solution_id,
        )
        if vector_store is not None:
            for row in retrieval_units:
                await vector_store.delete("retrieval_unit_emb", ref_id=str(row.id))

    stages_to_delete: list[str] = []
    if stage == "parsed":
        stages_to_delete.extend(["solving", "visualizing", "indexing"])
    elif stage == "solving":
        stages_to_delete.extend(["visualizing", "indexing"])
    elif stage == "indexing":
        stages_to_delete.extend([])

    await session.execute(
        delete(QuestionStageReview)
        .where(QuestionStageReview.question_id == question_id)
        .where(QuestionStageReview.stage.in_(stages_to_delete))
    )

    if stage == "parsed":
        q.status = review_question_status("parsed")
    elif stage == "solving":
        q.status = "parsed"
    elif stage == "visualizing":
        q.status = review_question_status("solving")
    elif stage == "indexing":
        q.status = review_question_status("visualizing")
    await session.flush()
