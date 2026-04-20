"""Retrieve router — similar-question hybrid search (§3.4, §6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Text, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import models
from app.db.session import session_scope
from app.services.embedding import build_dense_embedder
from app.services.llm_client import PromptLogContext
from app.services.llm_deps import get_llm_client
from app.services.retrieval_service import (
    SimilarQuery,
    similar_questions,
    similar_questions_multi_route,
)
from app.services.sparse_encoder import get_sparse_encoder
from app.services.vector_store import VectorStore, get_vector_store

router = APIRouter(prefix="/api/retrieve", tags=["retrieve"])
questions_list_router = APIRouter(prefix="/api/questions", tags=["retrieve"])


class SimilarFilters(BaseModel):
    subject: str | None = None
    grade_band: str | None = None
    difficulty_min: int | None = Field(default=None, ge=1, le=5)
    difficulty_max: int | None = Field(default=None, ge=1, le=5)
    excluded_ids: list[str] = Field(default_factory=list)


class SimilarRequest(BaseModel):
    mode: Literal["auto", "text", "kp", "pattern"] = "auto"
    query: str | None = None
    question_id: str | None = None
    solution_id: str | None = None
    kp_id: str | None = None
    pattern_id: str | None = None
    filters: SimilarFilters = Field(default_factory=SimilarFilters)
    k: int = Field(default=10, ge=1, le=50)


async def _session() -> AsyncSession:  # type: ignore[override]
    async with session_scope() as s:
        yield s


def _vs() -> VectorStore:
    return get_vector_store()


@router.post("/similar")
async def similar(
    req: SimilarRequest,
    session: AsyncSession = Depends(_session),
    llm=Depends(get_llm_client),
    vs: VectorStore = Depends(_vs),
) -> dict:
    q = SimilarQuery(
        mode=req.mode,
        query=req.query,
        question_id=req.question_id,
        solution_id=req.solution_id,
        kp_id=req.kp_id,
        pattern_id=req.pattern_id,
        subject=req.filters.subject,
        grade_band=req.filters.grade_band,
        difficulty_min=req.filters.difficulty_min,
        difficulty_max=req.filters.difficulty_max,
        excluded_ids=req.filters.excluded_ids,
        k=req.k,
    )
    dense = build_dense_embedder(
        llm,
        prompt_context=PromptLogContext(
            phase_description="相似题检索",
            question_id=str(req.question_id) if req.question_id else None,
            solution_id=str(req.solution_id) if req.solution_id else None,
            related={"mode": req.mode, "k": req.k},
        ),
    )
    if settings.retrieval.multi_route:
        hits = await similar_questions_multi_route(
            session, query=q, embedding=dense,
            sparse=get_sparse_encoder(), vector_store=vs,
        )
        strategy = "multi_route_rrf"
    else:
        hits = await similar_questions(
            session, query=q, embedding=dense, vector_store=vs,
        )
        strategy = "single_route"
    return {
        "hits": [h.__dict__ for h in hits],
        "mode": req.mode, "k": req.k, "strategy": strategy,
    }


# ── Library page helper: list recent questions with filters ─────────


def _norm_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _matches_text(needle: str, *haystacks: str) -> bool:
    if not needle:
        return True
    return any(needle in _norm_text(item) for item in haystacks if item)


def _contains_ci(expr, needle: str | None):
    value = _norm_text(needle)
    if not value:
        return None
    return func.lower(cast(expr, Text)).like(f"%{value}%")


def _current_solution_subquery():
    return (
        select(
            models.QuestionSolution.question_id.label("question_id"),
            models.QuestionSolution.id.label("current_solution_id"),
        )
        .where(models.QuestionSolution.is_current.is_(True))
        .subquery()
    )


def _pattern_name_subquery():
    return (
        select(
            models.QuestionPatternLink.question_id.label("question_id"),
            func.min(models.MethodPatternRow.name_cn).label("pattern_name"),
        )
        .join(
            models.MethodPatternRow,
            models.QuestionPatternLink.pattern_id == models.MethodPatternRow.id,
        )
        .group_by(models.QuestionPatternLink.question_id)
        .subquery()
    )


def _review_summary_subquery():
    return (
        select(
            models.QuestionStageReview.question_id.label("question_id"),
            func.sum(
                case(
                    (models.QuestionStageReview.review_status == "confirmed", 1),
                    else_=0,
                )
            ).label("confirmed_stage_count"),
            func.max(
                case(
                    (
                        and_(
                            models.QuestionStageReview.stage == "indexing",
                            models.QuestionStageReview.review_status == "confirmed",
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("indexed_confirmed"),
        )
        .group_by(models.QuestionStageReview.question_id)
        .subquery()
    )


_CURRENT_SOLUTION_SQ = _current_solution_subquery()
_PATTERN_NAME_SQ = _pattern_name_subquery()
_REVIEW_SUMMARY_SQ = _review_summary_subquery()


def _library_listing_stmt():
    profile_join = and_(
        models.QuestionRetrievalProfile.question_id == models.Question.id,
        or_(
            and_(
                _CURRENT_SOLUTION_SQ.c.current_solution_id.is_not(None),
                models.QuestionRetrievalProfile.solution_id == _CURRENT_SOLUTION_SQ.c.current_solution_id,
            ),
            and_(
                _CURRENT_SOLUTION_SQ.c.current_solution_id.is_(None),
                models.QuestionRetrievalProfile.solution_id.is_(None),
            ),
        ),
    )
    return (
        select(
            models.Question.id.label("question_id"),
            models.Question.subject,
            models.Question.grade_band,
            models.Question.difficulty,
            models.Question.status,
            models.Question.seen_count,
            models.Question.created_at,
            models.Question.parsed_json,
            _CURRENT_SOLUTION_SQ.c.current_solution_id,
            _PATTERN_NAME_SQ.c.pattern_name,
            models.QuestionRetrievalProfile.profile_json,
            func.coalesce(_REVIEW_SUMMARY_SQ.c.confirmed_stage_count, 0).label(
                "confirmed_stage_count",
            ),
            func.coalesce(_REVIEW_SUMMARY_SQ.c.indexed_confirmed, 0).label("indexed_confirmed"),
        )
        .select_from(models.Question)
        .outerjoin(
            _CURRENT_SOLUTION_SQ,
            _CURRENT_SOLUTION_SQ.c.question_id == models.Question.id,
        )
        .outerjoin(models.QuestionRetrievalProfile, profile_join)
        .outerjoin(
            _PATTERN_NAME_SQ,
            _PATTERN_NAME_SQ.c.question_id == models.Question.id,
        )
        .outerjoin(
            _REVIEW_SUMMARY_SQ,
            _REVIEW_SUMMARY_SQ.c.question_id == models.Question.id,
        )
    )


def _apply_list_question_filters(
    stmt,
    *,
    subject: str | None,
    grade_band: str | None,
    difficulty_min: int | None,
    difficulty_max: int | None,
    q: str | None,
    topic: str | None,
    method: str | None,
    target_type: str | None,
    novelty_flag: str | None,
    date_from: str | None,
    date_to: str | None,
    learning_ready: bool,
):
    if subject:
        stmt = stmt.where(models.Question.subject == subject)
    if grade_band:
        stmt = stmt.where(models.Question.grade_band == grade_band)
    if date_from:
        stmt = stmt.where(models.Question.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        stmt = stmt.where(models.Question.created_at <= datetime.fromisoformat(date_to))
    if difficulty_min is not None:
        stmt = stmt.where(models.Question.difficulty >= difficulty_min)
    if difficulty_max is not None:
        stmt = stmt.where(models.Question.difficulty <= difficulty_max)

    question_text_expr = models.Question.parsed_json["question_text"].astext
    topic_expr = models.QuestionRetrievalProfile.profile_json["topic_path"].astext
    method_expr = models.QuestionRetrievalProfile.profile_json["method_labels"].astext
    target_expr = models.QuestionRetrievalProfile.profile_json["target_types"].astext
    novelty_expr = models.QuestionRetrievalProfile.profile_json["novelty_flags"].astext
    lexical_expr = models.QuestionRetrievalProfile.profile_json["lexical_aliases"].astext

    if learning_ready:
        stmt = stmt.where(func.coalesce(_REVIEW_SUMMARY_SQ.c.indexed_confirmed, 0) == 1)
    if q_filter := _norm_text(q):
        stmt = stmt.where(or_(
            _contains_ci(question_text_expr, q_filter),
            _contains_ci(_PATTERN_NAME_SQ.c.pattern_name, q_filter),
            _contains_ci(topic_expr, q_filter),
            _contains_ci(method_expr, q_filter),
            _contains_ci(target_expr, q_filter),
            _contains_ci(novelty_expr, q_filter),
            _contains_ci(lexical_expr, q_filter),
        ))
    if method_filter := _norm_text(method):
        stmt = stmt.where(or_(
            _contains_ci(_PATTERN_NAME_SQ.c.pattern_name, method_filter),
            _contains_ci(method_expr, method_filter),
        ))
    if topic_filter := _norm_text(topic):
        stmt = stmt.where(_contains_ci(topic_expr, topic_filter))
    if target_filter := _norm_text(target_type):
        stmt = stmt.where(_contains_ci(target_expr, target_filter))
    if novelty_filter := _norm_text(novelty_flag):
        stmt = stmt.where(_contains_ci(novelty_expr, novelty_filter))
    return stmt


def _sort_columns(sort: Literal["recommended", "recent", "popular"]):
    confirmed_stage_count = func.coalesce(_REVIEW_SUMMARY_SQ.c.confirmed_stage_count, 0)
    indexed_confirmed = func.coalesce(_REVIEW_SUMMARY_SQ.c.indexed_confirmed, 0)
    if sort == "popular":
        return (
            models.Question.seen_count.desc(),
            models.Question.created_at.desc(),
        )
    if sort == "recommended":
        return (
            indexed_confirmed.desc(),
            confirmed_stage_count.desc(),
            models.Question.seen_count.desc(),
            models.Question.created_at.desc(),
        )
    return (models.Question.created_at.desc(),)


@questions_list_router.get("")
async def list_questions(
    subject: str | None = None,
    grade_band: str | None = None,
    difficulty_min: int | None = None,
    difficulty_max: int | None = None,
    q: str | None = None,
    topic: str | None = None,
    method: str | None = None,
    target_type: str | None = None,
    novelty_flag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    learning_ready: bool = True,
    sort: Literal["recommended", "recent", "popular"] = "recommended",
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(_session),
) -> dict:
    page_limit = max(1, min(limit, 200))
    page_offset = max(0, offset)
    base_stmt = _library_listing_stmt()
    filtered_stmt = _apply_list_question_filters(
        base_stmt,
        subject=subject,
        grade_band=grade_band,
        difficulty_min=difficulty_min,
        difficulty_max=difficulty_max,
        q=q,
        topic=topic,
        method=method,
        target_type=target_type,
        novelty_flag=novelty_flag,
        date_from=date_from,
        date_to=date_to,
        learning_ready=learning_ready,
    )
    total_count = int((await session.execute(
        select(func.count()).select_from(filtered_stmt.subquery())
    )).scalar_one() or 0)
    if total_count == 0:
        return {
            "items": [],
            "count": 0,
            "total_count": 0,
            "offset": page_offset,
            "limit": page_limit,
            "has_more": False,
            "next_offset": None,
            "facets": {"methods": [], "topics": [], "target_types": [], "novelty_flags": []},
        }

    sort_columns = _sort_columns(sort)
    page_rows = (await session.execute(
        filtered_stmt.order_by(*sort_columns).limit(page_limit).offset(page_offset)
    )).all()
    page_qids = [row.question_id for row in page_rows]

    facet_rows = (await session.execute(
        filtered_stmt.with_only_columns(
            models.Question.id.label("question_id"),
            _PATTERN_NAME_SQ.c.pattern_name,
            models.QuestionRetrievalProfile.profile_json,
        )
    )).all()

    review_rows = (await session.execute(
        select(
            models.QuestionStageReview.question_id,
            models.QuestionStageReview.stage,
            models.QuestionStageReview.review_status,
        )
        .where(models.QuestionStageReview.question_id.in_(page_qids))
    )).all()
    reviews_by_qid: dict[str, dict[str, str]] = {}
    for qid, stage, review_status in review_rows:
        reviews_by_qid.setdefault(str(qid), {})[str(stage)] = str(review_status)

    methods_facet: set[str] = set()
    topics_facet: set[str] = set()
    target_facet: set[str] = set()
    novelty_facet: set[str] = set()
    for row in facet_rows:
        pattern = str(row.pattern_name) if row.pattern_name else None
        profile = dict(row.profile_json or {})
        topic_path = [str(item) for item in (profile.get("topic_path") or [])]
        method_labels = [str(item) for item in profile.get("method_labels", [])]
        target_types = [str(item) for item in profile.get("target_types", [])]
        novelty_flags = [str(item) for item in profile.get("novelty_flags", [])]
        methods_facet.update(item for item in method_labels if item)
        if pattern:
            methods_facet.add(pattern)
        topics_facet.update(item for item in topic_path if item)
        target_facet.update(item for item in target_types if item)
        novelty_facet.update(item for item in novelty_flags if item)

    items: list[dict[str, Any]] = []
    for row in page_rows:
        qid = str(row.question_id)
        parsed = dict(row.parsed_json or {})
        profile = dict(row.profile_json or {})
        text = str(parsed.get("question_text") or "")
        pattern = str(row.pattern_name) if row.pattern_name else None
        topic_path = [
            str(item)
            for item in (profile.get("topic_path") or parsed.get("topic_path", []))
        ]
        method_labels = [str(item) for item in profile.get("method_labels", [])]
        target_types = [str(item) for item in profile.get("target_types", [])]
        novelty_flags = [str(item) for item in profile.get("novelty_flags", [])]
        review_statuses = reviews_by_qid.get(qid, {})

        items.append({
            "question_id": qid,
            "current_solution_id": str(row.current_solution_id) if row.current_solution_id else None,
            "subject": row.subject,
            "grade_band": row.grade_band,
            "difficulty": row.difficulty,
            "status": row.status,
            "question_text": text,
            "pattern_name": pattern,
            "seen_count": row.seen_count,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "learning_ready": bool(row.indexed_confirmed),
            "confirmed_stage_count": int(row.confirmed_stage_count or 0),
            "review_statuses": review_statuses,
            "topic_path": topic_path,
            "method_labels": method_labels,
            "target_types": target_types,
            "novelty_flags": novelty_flags,
            "textbook_stage": str(profile.get("textbook_stage") or ""),
        })
    return {
        "items": items,
        "count": len(items),
        "total_count": total_count,
        "offset": page_offset,
        "limit": page_limit,
        "has_more": page_offset + page_limit < total_count,
        "next_offset": page_offset + page_limit if page_offset + page_limit < total_count else None,
        "facets": {
            "methods": sorted(methods_facet),
            "topics": sorted(topics_facet),
            "target_types": sorted(target_facet),
            "novelty_flags": sorted(novelty_facet),
        },
    }
