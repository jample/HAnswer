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


@questions_list_router.get("")
async def list_questions(
    subject: str | None = None,
    grade_band: str | None = None,
    difficulty_min: int | None = None,
    difficulty_max: int | None = None,
    q: str | None = None,
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
    stmt = stmt.limit(max(1, min(limit, 200)))
    rows = (await session.execute(stmt)).scalars().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        text = (row.parsed_json or {}).get("question_text", "")
        if q and q.strip() and q not in text:
            continue
        # attach first linked pattern name if any
        pattern = (await session.execute(
            select(models.MethodPatternRow.name_cn)
            .join(
                models.QuestionPatternLink,
                models.QuestionPatternLink.pattern_id == models.MethodPatternRow.id,
            )
            .where(models.QuestionPatternLink.question_id == row.id)
            .limit(1)
        )).scalar_one_or_none()
        items.append({
            "question_id": str(row.id),
            "subject": row.subject,
            "grade_band": row.grade_band,
            "difficulty": row.difficulty,
            "status": row.status,
            "question_text": text,
            "pattern_name": pattern,
            "seen_count": row.seen_count,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })
    return {"items": items, "count": len(items)}
