"""Retrieval service (M5, §3.4).

Two retrieval strategies:

  1. `similar_questions()` — single-route hybrid. Runs dense ANN on
     `q_emb` and applies the weighted formula
        score = 0.5·cos + 0.3·pattern_match + 0.2·kp_overlap
     in a post-hoc rerank. Kept for backward compatibility and for
     deployments that don't need the sparse/structural routes.

  2. `similar_questions_multi_route()` — three independent routes
     (dense, sparse lexical, structural pattern+kp overlap) fused with
     Reciprocal-Rank Fusion (§3.4, RRF). Recommended for production —
     materially better recall on Chinese math/physics where decisive
     rare tokens would otherwise be washed out by a single dense
     embedding. Enabled via `settings.retrieval.multi_route`.

All DB queries are async; unknown questions or empty queries return
`[]` rather than raising.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    KnowledgePoint,
    MethodPatternRow,
    Question,
    QuestionKPLink,
    QuestionPatternLink,
)
from app.services.embedding import DenseEmbedder
from app.services.rrf import fuse as rrf_fuse
from app.services.sparse_encoder import SparseEncoder
from app.services.vector_store import VectorStore

Mode = Literal["auto", "text", "kp", "pattern"]


@dataclass
class Hit:
    question_id: str
    score: float
    cosine: float
    pattern_match: float       # 0 or 1
    kp_overlap: float          # 0..1
    subject: str
    grade_band: str
    difficulty: int
    question_text: str
    pattern_name: str | None = None
    shared_kp_names: list[str] | None = None
    rrf_score: float | None = None                  # multi-route only
    route_ranks: dict[str, int] | None = None       # multi-route only


@dataclass
class SimilarQuery:
    mode: Mode = "auto"
    query: str | None = None
    question_id: str | None = None
    kp_id: str | None = None
    pattern_id: str | None = None
    subject: str | None = None
    grade_band: str | None = None
    difficulty_min: int | None = None
    difficulty_max: int | None = None
    excluded_ids: list[str] | None = None
    k: int = 10


async def _question_context(
    session: AsyncSession, question_id: uuid.UUID,
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """Return (pattern_ids, kp_ids) linked to a question."""
    pat = (await session.execute(
        select(QuestionPatternLink.pattern_id).where(
            QuestionPatternLink.question_id == question_id,
        )
    )).scalars().all()
    kps = (await session.execute(
        select(QuestionKPLink.kp_id).where(QuestionKPLink.question_id == question_id)
    )).scalars().all()
    return set(pat), set(kps)


async def similar_questions(
    session: AsyncSession,
    *,
    query: SimilarQuery,
    embedding: DenseEmbedder,
    vector_store: VectorStore,
) -> list[Hit]:
    excluded = {uuid.UUID(x) for x in (query.excluded_ids or [])}
    source_patterns: set[uuid.UUID] = set()
    source_kps: set[uuid.UUID] = set()

    # ── Build query vector + context ─────────────────────────────
    text_for_embed: str | None = None
    if query.mode == "auto":
        if not query.question_id:
            return []
        qid = uuid.UUID(query.question_id)
        excluded.add(qid)
        q = await session.get(Question, qid)
        if q is None:
            return []
        text_for_embed = (q.parsed_json or {}).get("question_text", "")
        subject = query.subject or q.subject
        grade_band = query.grade_band or q.grade_band
        source_patterns, source_kps = await _question_context(session, qid)
    elif query.mode == "text":
        if not query.query:
            return []
        text_for_embed = query.query
        subject = query.subject
        grade_band = query.grade_band
    elif query.mode == "kp":
        if not query.kp_id:
            return []
        source_kps = {uuid.UUID(query.kp_id)}
        subject = query.subject
        grade_band = query.grade_band
        # Use kp name+path embedding as the search anchor.
        node = await session.get(KnowledgePoint, uuid.UUID(query.kp_id))
        if node is None:
            return []
        text_for_embed = f"{node.name_cn}\n{node.path_cached}"
    elif query.mode == "pattern":
        if not query.pattern_id:
            return []
        source_patterns = {uuid.UUID(query.pattern_id)}
        subject = query.subject
        grade_band = query.grade_band
        mp = await session.get(MethodPatternRow, uuid.UUID(query.pattern_id))
        if mp is None:
            return []
        text_for_embed = f"{mp.name_cn}\n{mp.when_to_use}"
    else:  # pragma: no cover - typed literal
        return []

    if not text_for_embed:
        return []

    vec = await embedding.embed_one(text_for_embed)
    raw_hits = await vector_store.search(
        "q_emb",
        vector=vec,
        k=max(query.k * 3, 30),
        subject=subject,
        grade_band=grade_band,
    )

    # ── Filter & hydrate ────────────────────────────────────────
    results: list[Hit] = []
    for h in raw_hits:
        try:
            qid = uuid.UUID(h.ref_id)
        except ValueError:
            continue
        if qid in excluded:
            continue
        q = await session.get(Question, qid)
        if q is None:
            continue
        if query.difficulty_min is not None and q.difficulty < query.difficulty_min:
            continue
        if query.difficulty_max is not None and q.difficulty > query.difficulty_max:
            continue

        patterns, kps = await _question_context(session, qid)
        pattern_match = 1.0 if (source_patterns & patterns) else 0.0
        kp_overlap = 0.0
        if source_kps:
            kp_overlap = len(source_kps & kps) / max(len(source_kps | kps), 1)

        # cosine ∈ [-1, 1]; rerank assumes [0, 1].
        cos = max(0.0, min(1.0, (h.score + 1) / 2 if h.score < 0 else h.score))
        score = 0.5 * cos + 0.3 * pattern_match + 0.2 * kp_overlap

        pattern_name = None
        if patterns:
            p_row = await session.get(MethodPatternRow, next(iter(patterns)))
            if p_row is not None:
                pattern_name = p_row.name_cn

        shared_kp_names: list[str] = []
        for kpid in (source_kps & kps):
            node = await session.get(KnowledgePoint, kpid)
            if node is not None:
                shared_kp_names.append(node.name_cn)

        results.append(Hit(
            question_id=str(qid),
            score=score,
            cosine=cos,
            pattern_match=pattern_match,
            kp_overlap=kp_overlap,
            subject=q.subject,
            grade_band=q.grade_band,
            difficulty=q.difficulty,
            question_text=(q.parsed_json or {}).get("question_text", ""),
            pattern_name=pattern_name,
            shared_kp_names=shared_kp_names or None,
        ))

    results.sort(key=lambda h: h.score, reverse=True)
    return results[: query.k]


# ── Multi-route + RRF (§3.4) ────────────────────────────────────────


async def _structural_route(
    session: AsyncSession,
    *,
    source_patterns: set[uuid.UUID],
    source_kps: set[uuid.UUID],
    subject: str | None,
    grade_band: str | None,
    k: int,
    excluded: set[uuid.UUID],
) -> list[str]:
    """Rank candidate questions by shared pattern + KP count (PG only)."""
    if not source_patterns and not source_kps:
        return []
    scores: dict[uuid.UUID, float] = {}
    if source_patterns:
        rows = (await session.execute(
            select(QuestionPatternLink.question_id, QuestionPatternLink.pattern_id)
            .where(QuestionPatternLink.pattern_id.in_(source_patterns))
        )).all()
        for qid, _pid in rows:
            scores[qid] = scores.get(qid, 0.0) + 3.0   # pattern weight
    if source_kps:
        rows = (await session.execute(
            select(QuestionKPLink.question_id, QuestionKPLink.kp_id,
                   QuestionKPLink.weight)
            .where(QuestionKPLink.kp_id.in_(source_kps))
        )).all()
        for qid, _kid, weight in rows:
            scores[qid] = scores.get(qid, 0.0) + float(weight or 1.0)
    if not scores:
        return []

    # Optional filter: subject/grade_band must match.
    if subject or grade_band:
        ids = list(scores.keys())
        stmt = select(Question.id, Question.subject, Question.grade_band).where(
            Question.id.in_(ids)
        )
        rows = (await session.execute(stmt)).all()
        allowed = {
            qid for qid, s, g in rows
            if (not subject or s == subject)
            and (not grade_band or g == grade_band)
        }
        scores = {qid: sc for qid, sc in scores.items() if qid in allowed}

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [str(qid) for qid, _ in ordered if qid not in excluded][:k]


async def similar_questions_multi_route(
    session: AsyncSession,
    *,
    query: SimilarQuery,
    embedding: DenseEmbedder,
    sparse: SparseEncoder,
    vector_store: VectorStore,
) -> list[Hit]:
    """Three-route hybrid retrieval with RRF fusion (§3.4).

    Routes:
      - dense:      ANN on q_emb with the dense embedding.
      - sparse:     BM25 / bge-m3 lexical weights on q_emb_sparse.
      - structural: PG counts of shared pattern + KP links (no ANN).

    Route weights + RRF damping constant come from
    `settings.retrieval.*`. Individual routes may return empty lists —
    RRF handles that gracefully.
    """
    rc = settings.retrieval
    wide_k = max(query.k * rc.wide_k_multiplier, 30)
    excluded = {uuid.UUID(x) for x in (query.excluded_ids or [])}
    source_patterns: set[uuid.UUID] = set()
    source_kps: set[uuid.UUID] = set()

    # ── Resolve query text + context (mirrors single-route path) ─
    text_for_embed: str | None = None
    subject = query.subject
    grade_band = query.grade_band
    if query.mode == "auto":
        if not query.question_id:
            return []
        qid = uuid.UUID(query.question_id)
        excluded.add(qid)
        q = await session.get(Question, qid)
        if q is None:
            return []
        text_for_embed = (q.parsed_json or {}).get("question_text", "")
        subject = subject or q.subject
        grade_band = grade_band or q.grade_band
        source_patterns, source_kps = await _question_context(session, qid)
    elif query.mode == "text":
        if not query.query:
            return []
        text_for_embed = query.query
    elif query.mode == "kp":
        if not query.kp_id:
            return []
        source_kps = {uuid.UUID(query.kp_id)}
        node = await session.get(KnowledgePoint, uuid.UUID(query.kp_id))
        if node is None:
            return []
        text_for_embed = f"{node.name_cn}\n{node.path_cached}"
    elif query.mode == "pattern":
        if not query.pattern_id:
            return []
        source_patterns = {uuid.UUID(query.pattern_id)}
        mp = await session.get(MethodPatternRow, uuid.UUID(query.pattern_id))
        if mp is None:
            return []
        text_for_embed = f"{mp.name_cn}\n{mp.when_to_use}"
    else:
        return []

    if not text_for_embed and not source_patterns and not source_kps:
        return []

    # ── Run three routes in parallel ─────────────────────────────
    async def _dense() -> list[str]:
        if not text_for_embed:
            return []
        vec = await embedding.embed_one(text_for_embed)
        hits = await vector_store.search(
            "q_emb", vector=vec, k=wide_k,
            subject=subject, grade_band=grade_band,
        )
        return [h.ref_id for h in hits if uuid.UUID(h.ref_id) not in excluded]

    async def _sparse() -> list[str]:
        if not text_for_embed or not vector_store.supports_sparse:
            return []
        sv = await sparse.encode_one(text_for_embed)
        if not sv:
            return []
        hits = await vector_store.search_sparse(
            "q_emb", sparse=sv, k=wide_k,
            subject=subject, grade_band=grade_band,
        )
        return [h.ref_id for h in hits if uuid.UUID(h.ref_id) not in excluded]

    async def _structural() -> list[str]:
        return await _structural_route(
            session,
            source_patterns=source_patterns,
            source_kps=source_kps,
            subject=subject, grade_band=grade_band,
            k=wide_k, excluded=excluded,
        )

    dense_ids, sparse_ids, struct_ids = await asyncio.gather(
        _dense(), _sparse(), _structural()
    )

    fused = rrf_fuse(
        routes={"dense": dense_ids, "sparse": sparse_ids, "structural": struct_ids},
        k=rc.rrf_k,
        weights={
            "dense": rc.route_weights_dense,
            "sparse": rc.route_weights_sparse,
            "structural": rc.route_weights_structural,
        },
    )
    if not fused:
        return []

    # ── Hydrate + apply final PG filters (difficulty, etc.) ──────
    hydrated: list[Hit] = []
    for fh in fused:
        try:
            qid = uuid.UUID(fh.ref_id)
        except ValueError:
            continue
        q = await session.get(Question, qid)
        if q is None:
            continue
        if query.difficulty_min is not None and q.difficulty < query.difficulty_min:
            continue
        if query.difficulty_max is not None and q.difficulty > query.difficulty_max:
            continue

        patterns, kps = await _question_context(session, qid)
        pattern_match = 1.0 if (source_patterns & patterns) else 0.0
        kp_overlap = 0.0
        if source_kps:
            kp_overlap = len(source_kps & kps) / max(len(source_kps | kps), 1)

        pattern_name = None
        if patterns:
            p_row = await session.get(MethodPatternRow, next(iter(patterns)))
            if p_row is not None:
                pattern_name = p_row.name_cn
        shared_kp_names: list[str] = []
        for kpid in (source_kps & kps):
            node = await session.get(KnowledgePoint, kpid)
            if node is not None:
                shared_kp_names.append(node.name_cn)

        hydrated.append(Hit(
            question_id=str(qid),
            score=fh.score,                      # RRF score as canonical
            cosine=0.0,                          # not computed in multi-route
            pattern_match=pattern_match,
            kp_overlap=kp_overlap,
            subject=q.subject,
            grade_band=q.grade_band,
            difficulty=q.difficulty,
            question_text=(q.parsed_json or {}).get("question_text", ""),
            pattern_name=pattern_name,
            shared_kp_names=shared_kp_names or None,
            rrf_score=fh.score,
            route_ranks=dict(fh.ranks),
        ))
        if len(hydrated) >= query.k:
            break

    return hydrated
