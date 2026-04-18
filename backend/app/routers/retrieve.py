"""Retrieve router — similar-question hybrid search (§3.4, §6)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.db.session import session_scope
from app.config import settings
from app.services.embedding import build_dense_embedder
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
    dense = build_dense_embedder(llm)
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
    learning_ready: bool = True,
    sort: Literal["recommended", "recent", "popular"] = "recommended",
    limit: int = 50,
    session: AsyncSession = Depends(_session),
) -> dict:
    stmt = select(models.Question).order_by(models.Question.created_at.desc())
    if subject:
        stmt = stmt.where(models.Question.subject == subject)
    if grade_band:
        stmt = stmt.where(models.Question.grade_band == grade_band)
    if difficulty_min is not None:
        stmt = stmt.where(models.Question.difficulty >= difficulty_min)
    if difficulty_max is not None:
        stmt = stmt.where(models.Question.difficulty <= difficulty_max)
    candidate_limit = max(100, min(limit * 5, 500))
    stmt = stmt.limit(candidate_limit)
    rows = (await session.execute(stmt)).scalars().all()
    qids = [row.id for row in rows]
    if not qids:
        return {
            "items": [],
            "count": 0,
            "facets": {"methods": [], "topics": [], "target_types": [], "novelty_flags": []},
        }

    pattern_rows = (await session.execute(
        select(models.QuestionPatternLink.question_id, models.MethodPatternRow.name_cn)
        .join(
            models.MethodPatternRow,
            models.QuestionPatternLink.pattern_id == models.MethodPatternRow.id,
        )
        .where(models.QuestionPatternLink.question_id.in_(qids))
    )).all()
    pattern_by_qid: dict[str, str] = {}
    for qid, name_cn in pattern_rows:
        pattern_by_qid.setdefault(str(qid), str(name_cn))

    solution_rows = (await session.execute(
        select(models.QuestionSolution.question_id, models.QuestionSolution.id)
        .where(models.QuestionSolution.question_id.in_(qids))
        .where(models.QuestionSolution.is_current.is_(True))
    )).all()
    current_solution_by_qid = {str(qid): sid for qid, sid in solution_rows}

    profile_rows = (await session.execute(
        select(
            models.QuestionRetrievalProfile.question_id,
            models.QuestionRetrievalProfile.solution_id,
            models.QuestionRetrievalProfile.profile_json,
        )
        .where(models.QuestionRetrievalProfile.question_id.in_(qids))
    )).all()
    profile_by_qid: dict[str, dict] = {}
    for qid, solution_id, profile_json in profile_rows:
        key = str(qid)
        current_sid = current_solution_by_qid.get(key)
        if current_sid is not None and solution_id == current_sid:
            profile_by_qid[key] = profile_json or {}
        elif current_sid is None and solution_id is None:
            profile_by_qid.setdefault(key, profile_json or {})

    review_rows = (await session.execute(
        select(
            models.QuestionStageReview.question_id,
            models.QuestionStageReview.stage,
            models.QuestionStageReview.review_status,
        )
        .where(models.QuestionStageReview.question_id.in_(qids))
    )).all()
    reviews_by_qid: dict[str, dict[str, str]] = {}
    for qid, stage, review_status in review_rows:
        reviews_by_qid.setdefault(str(qid), {})[str(stage)] = str(review_status)

    items: list[dict[str, Any]] = []
    methods_facet: set[str] = set()
    topics_facet: set[str] = set()
    target_facet: set[str] = set()
    novelty_facet: set[str] = set()
    q_norm = _norm_text(q)
    method_norm = _norm_text(method)
    topic_norm = _norm_text(topic)
    target_norm = _norm_text(target_type)
    novelty_norm = _norm_text(novelty_flag)
    for row in rows:
        qid = str(row.id)
        text = (row.parsed_json or {}).get("question_text", "")
        pattern = pattern_by_qid.get(qid)
        profile = profile_by_qid.get(qid, {})
        topic_path = [str(item) for item in (profile.get("topic_path") or (row.parsed_json or {}).get("topic_path", []))]
        method_labels = [str(item) for item in profile.get("method_labels", [])]
        target_types = [str(item) for item in profile.get("target_types", [])]
        novelty_flags = [str(item) for item in profile.get("novelty_flags", [])]
        lexical_aliases = [str(item) for item in profile.get("lexical_aliases", [])]
        review_statuses = reviews_by_qid.get(qid, {})
        stage_confirmed = sum(1 for v in review_statuses.values() if v == "confirmed")
        indexed_confirmed = review_statuses.get("indexing") == "confirmed"

        if learning_ready and not indexed_confirmed:
            continue
        if q_norm and not _matches_text(
            q_norm,
            text,
            pattern,
            " ".join(topic_path),
            " ".join(method_labels),
            " ".join(target_types),
            " ".join(novelty_flags),
            " ".join(lexical_aliases),
        ):
            continue
        if method_norm and not _matches_text(method_norm, pattern, " ".join(method_labels)):
            continue
        if topic_norm and not _matches_text(topic_norm, " ".join(topic_path)):
            continue
        if target_norm and not _matches_text(target_norm, " ".join(target_types)):
            continue
        if novelty_norm and not _matches_text(novelty_norm, " ".join(novelty_flags)):
            continue

        methods_facet.update(item for item in method_labels if item)
        if pattern:
            methods_facet.add(pattern)
        topics_facet.update(item for item in topic_path if item)
        target_facet.update(item for item in target_types if item)
        novelty_facet.update(item for item in novelty_flags if item)

        items.append({
            "question_id": qid,
            "current_solution_id": str(current_solution_by_qid.get(qid)) if current_solution_by_qid.get(qid) else None,
            "subject": row.subject,
            "grade_band": row.grade_band,
            "difficulty": row.difficulty,
            "status": row.status,
            "question_text": text,
            "pattern_name": pattern,
            "seen_count": row.seen_count,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "learning_ready": indexed_confirmed,
            "confirmed_stage_count": stage_confirmed,
            "review_statuses": review_statuses,
            "topic_path": topic_path,
            "method_labels": method_labels,
            "target_types": target_types,
            "novelty_flags": novelty_flags,
            "textbook_stage": str(profile.get("textbook_stage") or ""),
        })

    if sort == "popular":
        items.sort(
            key=lambda item: (
                int(item["seen_count"]),
                str(item["created_at"] or ""),
            ),
            reverse=True,
        )
    elif sort == "recommended":
        items.sort(
            key=lambda item: (
                int(bool(item["learning_ready"])),
                int(item["confirmed_stage_count"]),
                int(item["seen_count"]),
                str(item["created_at"] or ""),
            ),
            reverse=True,
        )
    else:
        items.sort(key=lambda item: str(item["created_at"] or ""), reverse=True)

    items = items[: max(1, min(limit, 200))]
    return {
        "items": items,
        "count": len(items),
        "facets": {
            "methods": sorted(methods_facet),
            "topics": sorted(topics_facet),
            "target_types": sorted(target_facet),
            "novelty_flags": sorted(novelty_facet),
        },
    }
