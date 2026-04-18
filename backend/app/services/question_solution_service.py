"""Per-question alternative solution variants.

Question parsing remains question-scoped. From solving onward, each
question can have multiple solution variants. The currently worked-on
solution is mirrored back to the legacy question-level fields so older
features continue to function.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import Question, QuestionSolution
from app.services.stage_review_service import (
    REVIEW_CONFIRMED,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    review_question_status,
)

SOLUTION_STAGES = ("solving", "visualizing", "indexing")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _solution_title(ordinal: int) -> str:
    return f"解法 {ordinal}"


def _stage_review_payload(
    *,
    stage: str,
    review_status: str,
    artifact_version: int,
    run_count: int,
    summary: dict | None = None,
    refs: dict | None = None,
    review_note: str = "",
    reviewed_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> dict:
    return {
        "stage": stage,
        "review_status": review_status,
        "artifact_version": artifact_version,
        "run_count": run_count,
        "summary": summary or {},
        "refs": refs or {},
        "review_note": review_note,
        "reviewed_at": reviewed_at.isoformat() if reviewed_at else None,
        "updated_at": (updated_at or _utcnow()).isoformat(),
    }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def serialize_solution(row: QuestionSolution) -> dict:
    reviews = [
        dict(row.stage_reviews_json.get(stage) or {"stage": stage})
        for stage in SOLUTION_STAGES
        if row.stage_reviews_json.get(stage)
    ]
    return {
        "solution_id": str(row.id),
        "ordinal": row.ordinal,
        "title": row.title,
        "is_current": row.is_current,
        "status": row.status,
        "has_answer": row.answer_package_json is not None,
        "visualization_count": len(row.visualizations_json or []),
        "stage_reviews": reviews,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def list_solutions(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
) -> list[QuestionSolution]:
    return list((await session.execute(
        select(QuestionSolution)
        .where(QuestionSolution.question_id == question_id)
        .order_by(QuestionSolution.ordinal)
    )).scalars().all())


async def get_solution(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    solution_id: uuid.UUID,
) -> QuestionSolution | None:
    stmt = (
        select(QuestionSolution)
        .where(QuestionSolution.question_id == question_id)
        .where(QuestionSolution.id == solution_id)
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_current_solution(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
) -> QuestionSolution | None:
    stmt = (
        select(QuestionSolution)
        .where(QuestionSolution.question_id == question_id)
        .where(QuestionSolution.is_current.is_(True))
        .order_by(QuestionSolution.updated_at.desc(), QuestionSolution.created_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return row
    rows = await list_solutions(session, question_id=question_id)
    return rows[-1] if rows else None


async def create_solution(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    title: str | None = None,
    make_current: bool = True,
) -> QuestionSolution:
    rows = await list_solutions(session, question_id=question_id)
    ordinal = (rows[-1].ordinal + 1) if rows else 1
    if make_current:
        for row in rows:
            row.is_current = False
    row = QuestionSolution(
        question_id=question_id,
        ordinal=ordinal,
        title=(title or "").strip() or _solution_title(ordinal),
        is_current=make_current,
        status=review_question_status("parsed"),
        visualizations_json=[],
        stage_reviews_json={},
    )
    session.add(row)
    await session.flush()
    return row


async def ensure_current_solution(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
) -> QuestionSolution:
    row = await get_current_solution(session, question_id=question_id)
    if row is not None:
        return row
    question = await repo.get_question(session, question_id)
    if question is not None and (
        question.answer_package_json is not None
        or question.status in {"review_solve", "review_viz", "review_index", "answered"}
    ):
        return await bootstrap_solution_from_question(session, question=question)
    return await create_solution(session, question_id=question_id, make_current=True)


def get_solution_stage_review(
    solution: QuestionSolution,
    *,
    stage: str,
) -> dict | None:
    review = (solution.stage_reviews_json or {}).get(stage)
    return dict(review) if isinstance(review, dict) else None


def solution_stage_reviews(solution: QuestionSolution) -> list[dict]:
    reviews = solution.stage_reviews_json or {}
    out: list[dict] = []
    for stage in SOLUTION_STAGES:
        review = reviews.get(stage)
        if isinstance(review, dict):
            out.append(dict(review))
    return out


async def set_current_solution(
    session: AsyncSession,
    *,
    question: Question,
    solution: QuestionSolution,
) -> QuestionSolution:
    rows = await list_solutions(session, question_id=question.id)
    for row in rows:
        row.is_current = row.id == solution.id
    question.answer_package_json = solution.answer_package_json
    question.status = solution.status
    await session.flush()
    return solution


async def sync_solution_stage_reviews_to_question(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    solution: QuestionSolution,
) -> None:
    from app.services.stage_review_service import set_stage_review_status, record_stage_artifact

    for stage in SOLUTION_STAGES:
        review = get_solution_stage_review(solution, stage=stage)
        if review is None:
            continue
        if int(review.get("artifact_version") or 0) > 0:
            row = await record_stage_artifact(
                session,
                question_id=question_id,
                stage=stage,
                summary=dict(review.get("summary") or {}),
                refs=dict(review.get("refs") or {}),
                review_note=review.get("review_note"),
            )
            row.artifact_version = int(review.get("artifact_version") or row.artifact_version)
            row.run_count = int(review.get("run_count") or row.run_count)
            row.review_status = str(review.get("review_status") or row.review_status)
            row.reviewed_at = _parse_dt(review.get("reviewed_at"))
            row.updated_at = _parse_dt(review.get("updated_at")) or _utcnow()
        else:
            await set_stage_review_status(
                session,
                question_id=question_id,
                stage=stage,
                review_status=str(review.get("review_status") or REVIEW_PENDING),
                review_note=review.get("review_note"),
            )


async def record_solution_stage_artifact(
    session: AsyncSession,
    *,
    solution: QuestionSolution,
    stage: str,
    summary: dict,
    refs: dict | None = None,
    review_note: str | None = None,
) -> dict:
    reviews = dict(solution.stage_reviews_json or {})
    existing = dict(reviews.get(stage) or {})
    note = existing.get("review_note", "") if review_note is None else review_note
    payload = _stage_review_payload(
        stage=stage,
        review_status=REVIEW_PENDING,
        artifact_version=int(existing.get("artifact_version") or 0) + 1,
        run_count=int(existing.get("run_count") or 0) + 1,
        summary=summary,
        refs=refs or {},
        review_note=note,
    )
    reviews[stage] = payload
    solution.stage_reviews_json = reviews
    solution.status = review_question_status(stage)
    solution.updated_at = _utcnow()
    await session.flush()
    return payload


async def set_solution_stage_review_status(
    session: AsyncSession,
    *,
    solution: QuestionSolution,
    stage: str,
    review_status: str,
    review_note: str | None = None,
) -> dict:
    reviews = dict(solution.stage_reviews_json or {})
    existing = dict(reviews.get(stage) or {})
    payload = _stage_review_payload(
        stage=stage,
        review_status=review_status,
        artifact_version=int(existing.get("artifact_version") or 0),
        run_count=int(existing.get("run_count") or 0),
        summary=dict(existing.get("summary") or {}),
        refs=dict(existing.get("refs") or {}),
        review_note=(existing.get("review_note", "") if review_note is None else review_note),
        reviewed_at=_utcnow() if review_status == REVIEW_CONFIRMED else None,
        updated_at=_utcnow(),
    )
    reviews[stage] = payload
    solution.stage_reviews_json = reviews
    solution.updated_at = _utcnow()
    await session.flush()
    return payload


async def build_solution_stage_user_guidance(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    solution: QuestionSolution,
    target_stage: str,
) -> str:
    from app.services.stage_review_service import STAGE_ORDER, list_stage_reviews

    try:
        target_idx = STAGE_ORDER.index(target_stage)
    except ValueError:
        return ""

    notes: list[str] = []
    for row in await list_stage_reviews(session, question_id=question_id):
        note = row.review_note.strip()
        if not note:
            continue
        if row.stage == "parsed" and row.review_status == REVIEW_CONFIRMED:
            notes.append(f"[来自parsed阶段的用户要求]\n{note}")

    reviews = solution.stage_reviews_json or {}
    for stage in SOLUTION_STAGES:
        note = str((reviews.get(stage) or {}).get("review_note") or "").strip()
        if not note:
            continue
        stage_idx = STAGE_ORDER.index(stage)
        if stage_idx < target_idx and str((reviews.get(stage) or {}).get("review_status")) == REVIEW_CONFIRMED:
            notes.append(f"[来自{stage}阶段的用户要求]\n{note}")
        elif stage_idx == target_idx:
            notes.append(f"[本次{stage}阶段重跑要求]\n{note}")
    if not notes:
        return ""
    return (
        "请严格遵守以下用户补充要求。这些要求优先于默认表达风格，但不能违反事实、题意或 JSON Schema。\n\n"
        + "\n\n".join(notes)
    )


async def clear_solution_stage_outputs(
    session: AsyncSession,
    *,
    solution: QuestionSolution,
    stage: str,
) -> None:
    reviews = dict(solution.stage_reviews_json or {})
    if stage == "solving":
        solution.answer_package_json = None
        solution.visualizations_json = []
        solution.sediment_json = None
        for name in ("visualizing", "indexing"):
            reviews.pop(name, None)
        solution.status = review_question_status("parsed")
    elif stage == "visualizing":
        solution.visualizations_json = []
        solution.sediment_json = None
        reviews.pop("indexing", None)
        solution.status = review_question_status("solving")
    elif stage == "indexing":
        solution.sediment_json = None
        solution.status = review_question_status("visualizing")
    else:
        raise ValueError(f"unsupported solution stage: {stage}")
    solution.stage_reviews_json = reviews
    solution.updated_at = _utcnow()
    await session.flush()


async def update_solution_answer(
    session: AsyncSession,
    *,
    solution: QuestionSolution,
    answer_package_json: dict,
) -> None:
    solution.answer_package_json = answer_package_json
    solution.updated_at = _utcnow()
    await session.flush()


async def update_solution_visualizations(
    session: AsyncSession,
    *,
    solution: QuestionSolution,
    visualizations: list[dict],
) -> None:
    solution.visualizations_json = list(visualizations)
    solution.updated_at = _utcnow()
    await session.flush()


async def update_solution_indexing(
    session: AsyncSession,
    *,
    solution: QuestionSolution,
    payload: dict,
) -> None:
    solution.sediment_json = dict(payload)
    solution.updated_at = _utcnow()
    await session.flush()


async def get_solution_or_create(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    solution_id: uuid.UUID | None,
) -> QuestionSolution:
    if solution_id is not None:
        row = await get_solution(session, question_id=question_id, solution_id=solution_id)
        if row is None:
            raise KeyError(f"solution {solution_id} not found for question {question_id}")
        return row
    return await ensure_current_solution(session, question_id=question_id)


async def bootstrap_solution_from_question(
    session: AsyncSession,
    *,
    question: Question,
) -> QuestionSolution:
    from app.db.models import AnswerPackageSection, QuestionStageReview, VisualizationRow

    existing = await get_current_solution(session, question_id=question.id)
    if existing is not None:
        return existing

    stage_reviews: dict[str, dict] = {}
    review_rows = (await session.execute(
        select(QuestionStageReview)
        .where(QuestionStageReview.question_id == question.id)
        .where(QuestionStageReview.stage.in_(SOLUTION_STAGES))
    )).scalars().all()
    for row in review_rows:
        stage_reviews[row.stage] = _stage_review_payload(
            stage=row.stage,
            review_status=row.review_status,
            artifact_version=row.artifact_version,
            run_count=row.run_count,
            summary=dict(row.summary_json or {}),
            refs=dict(row.refs_json or {}),
            review_note=row.review_note,
            reviewed_at=row.reviewed_at,
            updated_at=row.updated_at,
        )

    viz_rows = (await session.execute(
        select(VisualizationRow)
        .where(VisualizationRow.question_id == question.id)
        .order_by(VisualizationRow.created_at)
    )).scalars().all()
    visualizations = [
        {
            "id": row.viz_ref,
            "title_cn": row.title,
            "caption_cn": row.caption,
            "learning_goal": row.learning_goal,
            "helpers_used": list(row.helpers_used_json or []),
            "jsx_code": row.jsx_code,
            "params": list(row.params_json or []),
            "animation": row.animation_json,
        }
        for row in viz_rows
    ]

    sediment_json = None
    sediment_section = (await session.execute(
        select(AnswerPackageSection)
        .where(AnswerPackageSection.question_id == question.id)
        .where(AnswerPackageSection.section == "sediment")
        .order_by(AnswerPackageSection.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if sediment_section is not None:
        sediment_json = dict(sediment_section.payload_json or {})

    row = await create_solution(session, question_id=question.id, make_current=True)
    row.status = question.status
    row.answer_package_json = question.answer_package_json
    row.visualizations_json = visualizations
    row.sediment_json = sediment_json
    row.stage_reviews_json = stage_reviews
    await session.flush()
    return row
