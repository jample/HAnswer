"""Retrieval service (M5, §3.4).

Two retrieval strategies:

  1. `similar_questions()` — single-route hybrid. Runs dense ANN on
     `question_full_emb` and applies the weighted formula
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
    QuestionRetrievalProfile,
    QuestionSolution,
    RetrievalUnitRow,
)
from app.services.question_solution_service import get_current_solution
from app.services.embedding import DenseEmbedder
from app.services.rrf import fuse as rrf_fuse
from app.services.solution_ref_service import decode_solution_ref, encode_solution_ref
from app.services.sparse_encoder import SparseEncoder
from app.services.vector_store import VectorStore

Mode = Literal["auto", "text", "kp", "pattern"]


@dataclass
class Hit:
    question_id: str
    solution_id: str | None
    score: float
    cosine: float
    pattern_match: float       # 0 or 1
    kp_overlap: float          # 0..1
    subject: str
    grade_band: str
    difficulty: int
    question_text: str
    solution_title: str | None = None
    pattern_name: str | None = None
    shared_kp_names: list[str] | None = None
    rrf_score: float | None = None                  # multi-route only
    route_ranks: dict[str, int] | None = None       # multi-route only
    matched_unit_kinds: list[str] | None = None     # pedagogical-facet routes
    matched_unit_titles: list[str] | None = None


@dataclass
class SimilarQuery:
    mode: Mode = "auto"
    query: str | None = None
    question_id: str | None = None
    solution_id: str | None = None
    kp_id: str | None = None
    pattern_id: str | None = None
    subject: str | None = None
    grade_band: str | None = None
    difficulty_min: int | None = None
    difficulty_max: int | None = None
    excluded_ids: list[str] | None = None
    k: int = 10


async def _load_profile(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    solution_id: uuid.UUID | None,
) -> dict:
    stmt = select(QuestionRetrievalProfile.profile_json).where(
        QuestionRetrievalProfile.question_id == question_id
    )
    if solution_id is None:
        stmt = stmt.where(QuestionRetrievalProfile.solution_id.is_(None))
    else:
        stmt = stmt.where(QuestionRetrievalProfile.solution_id == solution_id)
    stmt = stmt.limit(1)
    row = (await session.execute(stmt)).scalar_one_or_none()
    return dict(row or {})


def _profile_context(profile: dict) -> tuple[set[str], set[str]]:
    patterns = {
        str(item).strip()
        for item in profile.get("method_labels", [])
        if str(item).strip()
    }
    kp_like = {
        str(item).strip()
        for group in (
            profile.get("topic_path", []),
            profile.get("target_types", []),
            profile.get("object_entities", []),
            profile.get("condition_signals", []),
        )
        for item in group
        if str(item).strip()
    }
    return patterns, kp_like


def _profile_pattern_name(profile: dict) -> str | None:
    labels = [str(item).strip() for item in profile.get("method_labels", []) if str(item).strip()]
    return labels[0] if labels else None


async def _lookup_text_context(
    session: AsyncSession,
    text: str,
    subject: str | None = None,
) -> tuple[set[str], set[str]]:
    """Find patterns and KPs whose names appear in *text*.

    This populates source_patterns/source_kps for text-mode queries so
    the structural route can participate in RRF fusion.
    """
    if not text:
        return set(), set()

    patterns: set[str] = set()
    kps: set[str] = set()

    pat_stmt = select(MethodPatternRow.name_cn)
    if subject:
        pat_stmt = pat_stmt.where(MethodPatternRow.subject == subject)
    for (name,) in (await session.execute(pat_stmt)).all():
        if name and name in text:
            patterns.add(name)

    kp_stmt = select(KnowledgePoint.name_cn, KnowledgePoint.path_cached)
    if subject:
        kp_stmt = kp_stmt.where(KnowledgePoint.subject == subject)
    for (name_cn, path_cached) in (await session.execute(kp_stmt)).all():
        # Match if name appears in query text or query text appears in path.
        if name_cn and name_cn in text:
            kps.add(name_cn)
        elif path_cached and any(seg in text for seg in path_cached.split("/") if len(seg) >= 2):
            kps.add(name_cn)

    return patterns, kps


async def _question_context(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    solution_id: uuid.UUID | None,
) -> tuple[set[str], set[str]]:
    profile = await _load_profile(session, question_id=question_id, solution_id=solution_id)
    return _profile_context(profile)


def _filter_solution_refs(raw_ids: list[str], excluded_questions: set[uuid.UUID]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ref_id in raw_ids:
        parsed = decode_solution_ref(ref_id)
        if parsed is None:
            continue
        qid, _sid = parsed
        if qid in excluded_questions or ref_id in seen:
            continue
        seen.add(ref_id)
        out.append(ref_id)
    return out


def _ref_matches_excluded_question(ref_id: str, excluded_questions: set[uuid.UUID]) -> bool:
    parsed = decode_solution_ref(ref_id)
    if parsed is None:
        return False
    qid, _sid = parsed
    return qid in excluded_questions


def _merge_unit_match_maps(
    target: dict[str, dict[str, set[str]]],
    source: dict[str, dict[str, set[str]]],
) -> None:
    for qid, meta in source.items():
        bucket = target.setdefault(qid, {"kinds": set(), "titles": set()})
        bucket["kinds"].update(meta["kinds"])
        bucket["titles"].update(meta["titles"])


async def _collapse_retrieval_unit_hits(
    session: AsyncSession,
    *,
    unit_ids: list[str],
    excluded: set[uuid.UUID],
) -> tuple[list[str], dict[str, dict[str, set[str]]]]:
    if not unit_ids:
        return [], {}
    parsed_ids: list[uuid.UUID] = []
    for ref_id in unit_ids:
        try:
            parsed_ids.append(uuid.UUID(ref_id))
        except ValueError:
            continue
    if not parsed_ids:
        return [], {}
    rows = (await session.execute(
        select(RetrievalUnitRow).where(RetrievalUnitRow.id.in_(parsed_ids))
    )).scalars().all()
    by_id = {str(row.id): row for row in rows}
    ordered_qids: list[str] = []
    seen_qids: set[str] = set()
    match_map: dict[str, dict[str, set[str]]] = {}
    for ref_id in unit_ids:
        row = by_id.get(ref_id)
        if row is None:
            continue
        qid = encode_solution_ref(question_id=row.question_id, solution_id=row.solution_id)
        if row.question_id in excluded:
            continue
        bucket = match_map.setdefault(qid, {"kinds": set(), "titles": set()})
        bucket["kinds"].add(row.unit_kind)
        if row.title:
            bucket["titles"].add(row.title)
        if qid in seen_qids:
            continue
        seen_qids.add(qid)
        ordered_qids.append(qid)
    return ordered_qids, match_map


async def _hydrate_hit(
    session: AsyncSession,
    *,
    ref_id: str,
    score: float,
    cosine: float = 0.0,
    source_patterns: set[str],
    source_kps: set[str],
    query: SimilarQuery,
    matched_units: dict[str, dict[str, set[str]]] | None = None,
    route_ranks: dict[str, int] | None = None,
) -> Hit | None:
    parsed = decode_solution_ref(ref_id)
    if parsed is None:
        return None
    qid, solution_id = parsed
    q = await session.get(Question, qid)
    if q is None:
        return None
    if query.difficulty_min is not None and q.difficulty < query.difficulty_min:
        return None
    if query.difficulty_max is not None and q.difficulty > query.difficulty_max:
        return None

    solution = None
    if solution_id is not None:
        solution = await session.get(QuestionSolution, solution_id)
    profile_patterns, profile_kps = await _question_context(
        session,
        question_id=qid,
        solution_id=solution_id,
    )
    profile = await _load_profile(session, question_id=qid, solution_id=solution_id)
    pattern_name = _profile_pattern_name(profile) or next(iter(profile_patterns), None)
    pattern_match = 1.0 if (source_patterns & profile_patterns) else 0.0
    kp_overlap = 0.0
    if source_kps:
        kp_overlap = len(source_kps & profile_kps) / max(len(source_kps | profile_kps), 1)

    shared_kp_names = sorted(source_kps & profile_kps) or None
    unit_meta = (matched_units or {}).get(ref_id, {})
    return Hit(
        question_id=str(qid),
        solution_id=str(solution_id) if solution_id else None,
        score=score,
        cosine=cosine,
        pattern_match=pattern_match,
        kp_overlap=kp_overlap,
        subject=q.subject,
        grade_band=q.grade_band,
        difficulty=q.difficulty,
        question_text=(q.parsed_json or {}).get("question_text", ""),
        solution_title=solution.title if solution is not None else None,
        pattern_name=pattern_name,
        shared_kp_names=shared_kp_names,
        rrf_score=score if route_ranks is not None else None,
        route_ranks=route_ranks,
        matched_unit_kinds=sorted(unit_meta.get("kinds", set())) or None,
        matched_unit_titles=sorted(unit_meta.get("titles", set())) or None,
    )


async def similar_questions(
    session: AsyncSession,
    *,
    query: SimilarQuery,
    embedding: DenseEmbedder,
    vector_store: VectorStore,
) -> list[Hit]:
    excluded = {uuid.UUID(x) for x in (query.excluded_ids or [])}
    source_patterns: set[str] = set()
    source_kps: set[str] = set()

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
        sid = uuid.UUID(query.solution_id) if query.solution_id else None
        if sid is None:
            current = await get_current_solution(session, question_id=qid)
            sid = current.id if current is not None else None
        text_for_embed = (q.parsed_json or {}).get("question_text", "")
        subject = query.subject or q.subject
        grade_band = query.grade_band or q.grade_band
        source_patterns, source_kps = await _question_context(
            session,
            question_id=qid,
            solution_id=sid,
        )
    elif query.mode == "text":
        if not query.query:
            return []
        text_for_embed = query.query
        subject = query.subject
        grade_band = query.grade_band
    elif query.mode == "kp":
        if not query.kp_id:
            return []
        node = await session.get(KnowledgePoint, uuid.UUID(query.kp_id))
        if node is None:
            return []
        source_kps = {node.name_cn, node.path_cached}
        subject = query.subject
        grade_band = query.grade_band
        text_for_embed = f"{node.name_cn}\n{node.path_cached}"
    elif query.mode == "pattern":
        if not query.pattern_id:
            return []
        mp = await session.get(MethodPatternRow, uuid.UUID(query.pattern_id))
        if mp is None:
            return []
        source_patterns = {mp.name_cn}
        subject = query.subject
        grade_band = query.grade_band
        text_for_embed = f"{mp.name_cn}\n{mp.when_to_use}"
    else:  # pragma: no cover - typed literal
        return []

    if not text_for_embed:
        return []

    vec = await embedding.embed_one(text_for_embed)
    raw_hits = await vector_store.search(
        "question_full_emb",
        vector=vec,
        k=max(query.k * 3, 30),
        subject=subject,
        grade_band=grade_band,
    )

    # ── Filter & hydrate ────────────────────────────────────────
    results: list[Hit] = []
    for h in raw_hits:
        parsed = decode_solution_ref(h.ref_id)
        if parsed is None:
            continue
        qid, solution_id = parsed
        if qid in excluded:
            continue
        profile_patterns, profile_kps = await _question_context(
            session,
            question_id=qid,
            solution_id=solution_id,
        )
        cos = max(0.0, min(1.0, (h.score + 1) / 2 if h.score < 0 else h.score))
        pattern_match = 1.0 if (source_patterns & profile_patterns) else 0.0
        kp_overlap = len(source_kps & profile_kps) / max(len(source_kps | profile_kps), 1) if source_kps else 0.0
        hit = await _hydrate_hit(
            session,
            ref_id=h.ref_id,
            score=(0.5 * cos + 0.3 * pattern_match + 0.2 * kp_overlap),
            cosine=cos,
            source_patterns=source_patterns,
            source_kps=source_kps,
            query=query,
        )
        if hit is None:
            continue
        hit.cosine = cos
        results.append(hit)

    results.sort(key=lambda h: h.score, reverse=True)
    return results[: query.k]


# ── Multi-route + RRF (§3.4) ────────────────────────────────────────


async def _structural_route(
    session: AsyncSession,
    *,
    source_patterns: set[str],
    source_kps: set[str],
    subject: str | None,
    grade_band: str | None,
    k: int,
    excluded: set[uuid.UUID],
) -> list[str]:
    """Rank candidate solutions by shared method labels + profile facets."""
    if not source_patterns and not source_kps:
        return []
    stmt = (
        select(QuestionRetrievalProfile, Question, QuestionSolution)
        .join(Question, Question.id == QuestionRetrievalProfile.question_id)
        .outerjoin(QuestionSolution, QuestionSolution.id == QuestionRetrievalProfile.solution_id)
    )
    if excluded:
        stmt = stmt.where(Question.id.notin_(list(excluded)))
    if subject:
        stmt = stmt.where(Question.subject == subject)
    if grade_band:
        stmt = stmt.where(Question.grade_band == grade_band)
    rows = (await session.execute(stmt)).all()
    scored: list[tuple[str, float]] = []
    for profile_row, question_row, solution_row in rows:
        profile = dict(profile_row.profile_json or {})
        patterns, kps = _profile_context(profile)
        pattern_score = 3.0 if (source_patterns & patterns) else 0.0
        kp_score = float(len(source_kps & kps))
        total = pattern_score + kp_score
        if total <= 0.0:
            continue
        scored.append((
            encode_solution_ref(question_id=question_row.id, solution_id=solution_row.id if solution_row is not None else None),
            total,
        ))
    scored.sort(key=lambda item: item[1], reverse=True)
    return [ref_id for ref_id, _score in scored[:k]]


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
      - dense:      ANN on question_full_emb / answer_full_emb / retrieval_unit_emb.
      - sparse:     BM25 / bge-m3 lexical weights on the *_sparse companions.
      - structural: PG counts of shared pattern + KP links (no ANN).

    Route weights + RRF damping constant come from
    `settings.retrieval.*`. Individual routes may return empty lists —
    RRF handles that gracefully.
    """
    rc = settings.retrieval
    wide_k = max(query.k * rc.wide_k_multiplier, 30)
    excluded = {uuid.UUID(x) for x in (query.excluded_ids or [])}
    source_patterns: set[str] = set()
    source_kps: set[str] = set()

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
        sid = uuid.UUID(query.solution_id) if query.solution_id else None
        if sid is None:
            current = await get_current_solution(session, question_id=qid)
            sid = current.id if current is not None else None
        source_patterns, source_kps = await _question_context(
            session,
            question_id=qid,
            solution_id=sid,
        )
    elif query.mode == "text":
        if not query.query:
            return []
        text_for_embed = query.query
        source_patterns, source_kps = await _lookup_text_context(
            session, text_for_embed, subject=subject,
        )
    elif query.mode == "kp":
        if not query.kp_id:
            return []
        node = await session.get(KnowledgePoint, uuid.UUID(query.kp_id))
        if node is None:
            return []
        source_kps = {node.name_cn, node.path_cached}
        text_for_embed = f"{node.name_cn}\n{node.path_cached}"
    elif query.mode == "pattern":
        if not query.pattern_id:
            return []
        mp = await session.get(MethodPatternRow, uuid.UUID(query.pattern_id))
        if mp is None:
            return []
        source_patterns = {mp.name_cn}
        text_for_embed = f"{mp.name_cn}\n{mp.when_to_use}"
    else:
        return []

    if not text_for_embed and not source_patterns and not source_kps:
        return []

    # ── Run three routes in parallel ─────────────────────────────
    matched_units: dict[str, dict[str, set[str]]] = {}

    async def _dense() -> tuple[list[str], dict[str, float]]:
        if not text_for_embed:
            return [], {}
        vec = await embedding.embed_one(text_for_embed)
        question_full_hits, answer_full_hits, unit_hits = await asyncio.gather(
            vector_store.search(
                "question_full_emb", vector=vec, k=wide_k,
                subject=subject, grade_band=grade_band,
            ),
            vector_store.search(
                "answer_full_emb", vector=vec, k=wide_k,
                subject=subject, grade_band=grade_band,
            ),
            vector_store.search(
                "retrieval_unit_emb", vector=vec, k=wide_k,
                subject=subject, grade_band=grade_band,
            ),
        )
        unit_qids, unit_match_map = await _collapse_retrieval_unit_hits(
            session,
            unit_ids=[h.ref_id for h in unit_hits],
            excluded=excluded,
        )
        _merge_unit_match_maps(matched_units, unit_match_map)

        # Collect best dense score per ref_id across all sub-collections.
        raw_dense_scores: dict[str, float] = {}
        for h in question_full_hits + answer_full_hits + unit_hits:
            if _ref_matches_excluded_question(h.ref_id, excluded):
                continue
            prev = raw_dense_scores.get(h.ref_id)
            if prev is None or h.score > prev:
                raw_dense_scores[h.ref_id] = h.score

        fused = rrf_fuse(
            routes={
                "question_full": _filter_solution_refs(
                    [h.ref_id for h in question_full_hits], excluded,
                ),
                "answer_full": _filter_solution_refs(
                    [h.ref_id for h in answer_full_hits], excluded,
                ),
                "retrieval_unit": unit_qids,
            },
            k=rc.rrf_k,
        )
        return [h.ref_id for h in fused], raw_dense_scores

    async def _sparse() -> list[str]:
        if not text_for_embed or not vector_store.supports_sparse:
            return []
        sv = await sparse.encode_one(text_for_embed)
        if not sv:
            return []
        question_full_hits, answer_full_hits, unit_hits = await asyncio.gather(
            vector_store.search_sparse(
                "question_full_emb", sparse=sv, k=wide_k,
                subject=subject, grade_band=grade_band,
            ),
            vector_store.search_sparse(
                "answer_full_emb", sparse=sv, k=wide_k,
                subject=subject, grade_band=grade_band,
            ),
            vector_store.search_sparse(
                "retrieval_unit_emb", sparse=sv, k=wide_k,
                subject=subject, grade_band=grade_band,
            ),
        )
        unit_qids, unit_match_map = await _collapse_retrieval_unit_hits(
            session,
            unit_ids=[h.ref_id for h in unit_hits],
            excluded=excluded,
        )
        _merge_unit_match_maps(matched_units, unit_match_map)
        fused = rrf_fuse(
            routes={
                "question_full_sparse": _filter_solution_refs(
                    [h.ref_id for h in question_full_hits], excluded,
                ),
                "answer_full_sparse": _filter_solution_refs(
                    [h.ref_id for h in answer_full_hits], excluded,
                ),
                "retrieval_unit_sparse": unit_qids,
            },
            k=rc.rrf_k,
        )
        return [h.ref_id for h in fused]

    async def _structural() -> list[str]:
        return await _structural_route(
            session,
            source_patterns=source_patterns,
            source_kps=source_kps,
            subject=subject, grade_band=grade_band,
            k=wide_k, excluded=excluded,
        )

    (dense_ids, raw_dense_scores), sparse_ids, struct_ids = await asyncio.gather(
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
        dense_ip = raw_dense_scores.get(fh.ref_id, 0.0)
        # Milvus HNSW with metric_type=IP returns inner product.
        # For normalized vectors IP ≈ cosine; clamp to [0, 1].
        cos = max(0.0, min(1.0, dense_ip))
        hit = await _hydrate_hit(
            session,
            ref_id=fh.ref_id,
            score=fh.score,
            cosine=cos,
            source_patterns=source_patterns,
            source_kps=source_kps,
            query=query,
            matched_units=matched_units,
            route_ranks=dict(fh.ranks),
        )
        if hit is None:
            continue
        hydrated.append(hit)
        if len(hydrated) >= query.k:
            break

    return hydrated
