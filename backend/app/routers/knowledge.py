"""Knowledge router — taxonomy browse + promote/merge/reject (§3.6, §6)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.db.session import session_scope

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class PromoteRequest(BaseModel):
    kind: Literal["kp", "pattern"]
    id: str


class MergeRequest(BaseModel):
    kind: Literal["kp", "pattern"]
    from_id: str
    into_id: str


async def _session() -> AsyncSession:  # type: ignore[override]
    async with session_scope() as s:
        yield s


# ── Tree / detail ──────────────────────────────────────────────────


@router.get("/tree")
async def tree(
    subject: str | None = None,
    grade_band: str | None = None,
    status: Literal["all", "live", "pending"] = "all",
    session: AsyncSession = Depends(_session),
) -> dict:
    stmt = select(models.KnowledgePoint)
    if subject:
        stmt = stmt.where(models.KnowledgePoint.subject == subject)
    if grade_band:
        stmt = stmt.where(models.KnowledgePoint.grade_band == grade_band)
    if status != "all":
        stmt = stmt.where(models.KnowledgePoint.status == status)
    stmt = stmt.order_by(models.KnowledgePoint.path_cached.asc())
    rows = (await session.execute(stmt)).scalars().all()
    nodes = [
        {
            "id": str(r.id),
            "parent_id": str(r.parent_id) if r.parent_id else None,
            "name_cn": r.name_cn,
            "path_cached": r.path_cached,
            "subject": r.subject,
            "grade_band": r.grade_band,
            "status": r.status,
            "seen_count": r.seen_count,
        }
        for r in rows
    ]
    return {"nodes": nodes, "count": len(nodes)}


@router.get("/pending")
async def pending(session: AsyncSession = Depends(_session)) -> dict:
    kps = (await session.execute(
        select(models.KnowledgePoint)
        .where(models.KnowledgePoint.status == "pending")
        .order_by(models.KnowledgePoint.seen_count.desc())
    )).scalars().all()
    patterns = (await session.execute(
        select(models.MethodPatternRow)
        .where(models.MethodPatternRow.status == "pending")
        .order_by(models.MethodPatternRow.seen_count.desc())
    )).scalars().all()
    return {
        "kps": [
            {
                "id": str(k.id), "name_cn": k.name_cn,
                "path_cached": k.path_cached, "subject": k.subject,
                "grade_band": k.grade_band, "seen_count": k.seen_count,
            } for k in kps
        ],
        "patterns": [
            {
                "id": str(p.id), "name_cn": p.name_cn, "subject": p.subject,
                "grade_band": p.grade_band, "when_to_use": p.when_to_use,
                "seen_count": p.seen_count,
            } for p in patterns
        ],
    }


# ── Node detail (§9.5 right panel) ─────────────────────────────────


def _q_row(q: models.Question, weight: float | None = None) -> dict:
    return {
        "question_id": str(q.id),
        "subject": q.subject,
        "grade_band": q.grade_band,
        "difficulty": q.difficulty,
        "status": q.status,
        "question_text": (q.parsed_json or {}).get("question_text", "") if q.parsed_json else "",
        "weight": float(weight) if weight is not None else None,
    }


@router.get("/kp/{kp_id}/detail")
async def kp_detail(
    kp_id: uuid.UUID, session: AsyncSession = Depends(_session),
) -> dict:
    """Related questions + co-occurring method patterns for a KnowledgePoint."""
    kp = await session.get(models.KnowledgePoint, kp_id)
    if kp is None:
        raise HTTPException(404, "kp not found")

    q_rows = (await session.execute(
        select(models.Question, models.QuestionKPLink.weight)
        .join(models.QuestionKPLink, models.QuestionKPLink.question_id == models.Question.id)
        .where(models.QuestionKPLink.kp_id == kp_id)
        .order_by(models.QuestionKPLink.weight.desc(), models.Question.created_at.desc())
        .limit(50)
    )).all()
    questions = [_q_row(q, w) for (q, w) in q_rows]

    # Patterns co-occurring on those questions.
    pat_rows = (await session.execute(
        select(
            models.MethodPatternRow,
            models.QuestionPatternLink.weight,
        )
        .join(
            models.QuestionPatternLink,
            models.QuestionPatternLink.pattern_id == models.MethodPatternRow.id,
        )
        .where(models.QuestionPatternLink.question_id.in_([q.id for q, _ in q_rows]))
    )).all()
    pattern_agg: dict[str, dict] = {}
    for pat, w in pat_rows:
        key = str(pat.id)
        row = pattern_agg.setdefault(key, {
            "id": key, "name_cn": pat.name_cn, "subject": pat.subject,
            "grade_band": pat.grade_band, "status": pat.status,
            "co_occurrence": 0, "weight_sum": 0.0,
        })
        row["co_occurrence"] += 1
        row["weight_sum"] += float(w)
    patterns = sorted(
        pattern_agg.values(), key=lambda r: r["co_occurrence"], reverse=True,
    )

    return {
        "kp": {
            "id": str(kp.id), "name_cn": kp.name_cn,
            "path_cached": kp.path_cached, "subject": kp.subject,
            "grade_band": kp.grade_band, "status": kp.status,
            "seen_count": kp.seen_count,
        },
        "questions": questions,
        "patterns": patterns,
    }


@router.get("/pattern/{pattern_id}/detail")
async def pattern_detail(
    pattern_id: uuid.UUID, session: AsyncSession = Depends(_session),
) -> dict:
    """Related questions + pitfalls for a MethodPattern."""
    pat = await session.get(models.MethodPatternRow, pattern_id)
    if pat is None:
        raise HTTPException(404, "pattern not found")

    q_rows = (await session.execute(
        select(models.Question, models.QuestionPatternLink.weight)
        .join(
            models.QuestionPatternLink,
            models.QuestionPatternLink.question_id == models.Question.id,
        )
        .where(models.QuestionPatternLink.pattern_id == pattern_id)
        .order_by(models.QuestionPatternLink.weight.desc(), models.Question.created_at.desc())
        .limit(50)
    )).all()
    questions = [_q_row(q, w) for (q, w) in q_rows]

    pitfall_rows = (await session.execute(
        select(models.Pitfall).where(models.Pitfall.pattern_id == pattern_id)
    )).scalars().all()

    return {
        "pattern": {
            "id": str(pat.id), "name_cn": pat.name_cn,
            "subject": pat.subject, "grade_band": pat.grade_band,
            "when_to_use": pat.when_to_use,
            "procedure": pat.procedure_json,
            "pitfalls": pat.pitfalls_json,
            "status": pat.status, "seen_count": pat.seen_count,
        },
        "questions": questions,
        "pitfalls_linked": [
            {"id": str(p.id), "name_cn": p.name_cn, "description": p.description}
            for p in pitfall_rows
        ],
    }


# ── Mutations ──────────────────────────────────────────────────────


@router.post("/promote")
async def promote(
    req: PromoteRequest, session: AsyncSession = Depends(_session),
) -> dict:
    model_cls = models.KnowledgePoint if req.kind == "kp" else models.MethodPatternRow
    row = await session.get(model_cls, uuid.UUID(req.id))
    if row is None:
        raise HTTPException(404, f"{req.kind} {req.id} not found")
    row.status = "live"
    await session.flush()
    return {"promoted": req.model_dump(), "status": row.status}


@router.post("/reject")
async def reject(
    req: PromoteRequest, session: AsyncSession = Depends(_session),
) -> dict:
    """Soft-delete: pending → 'rejected' via cascade-delete link rows.

    Stage-1 implementation: actually delete the row plus its link rows,
    since we have no `rejected` status enum value. Downstream features
    that want a full audit trail can introduce a `rejected` status later.
    """
    kind = req.kind
    rid = uuid.UUID(req.id)
    if kind == "kp":
        await session.execute(
            delete(models.QuestionKPLink).where(models.QuestionKPLink.kp_id == rid)
        )
        await session.execute(
            delete(models.KnowledgePoint).where(models.KnowledgePoint.id == rid)
        )
    else:
        await session.execute(
            delete(models.QuestionPatternLink).where(
                models.QuestionPatternLink.pattern_id == rid
            )
        )
        await session.execute(
            delete(models.MethodPatternRow).where(models.MethodPatternRow.id == rid)
        )
    await session.flush()
    return {"rejected": req.model_dump()}


@router.post("/merge")
async def merge(req: MergeRequest, session: AsyncSession = Depends(_session)) -> dict:
    """Rewrite all links from `from_id` → `into_id`, then delete `from_id`.

    `into_id` is expected to be a live node; `from_id` is typically pending.
    """
    if req.from_id == req.into_id:
        raise HTTPException(400, "from_id == into_id")

    from_id = uuid.UUID(req.from_id)
    into_id = uuid.UUID(req.into_id)

    if req.kind == "kp":
        model_cls = models.KnowledgePoint
        link_cls = models.QuestionKPLink
        link_col = link_cls.kp_id
    else:
        model_cls = models.MethodPatternRow
        link_cls = models.QuestionPatternLink
        link_col = link_cls.pattern_id

    src = await session.get(model_cls, from_id)
    dst = await session.get(model_cls, into_id)
    if src is None or dst is None:
        raise HTTPException(404, "from_id or into_id not found")

    # Move every link, de-duplicating on (question, target).
    existing_questions = set((await session.execute(
        select(link_cls.question_id).where(link_col == into_id)
    )).scalars().all())
    incoming = (await session.execute(
        select(link_cls).where(link_col == from_id)
    )).scalars().all()
    for link in incoming:
        if link.question_id in existing_questions:
            # into already has this question; drop the src side
            await session.delete(link)
        else:
            # Repoint the link to the destination id.
            if req.kind == "kp":
                link.kp_id = into_id
            else:
                link.pattern_id = into_id
    # Transfer seen_count, then delete the source node.
    dst.seen_count += src.seen_count
    await session.delete(src)
    await session.flush()
    return {
        "merged": req.model_dump(),
        "into": {"id": str(dst.id), "seen_count": dst.seen_count},
    }
