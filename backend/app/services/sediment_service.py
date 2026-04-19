"""Knowledge sediment service (M6, §3.6.2 + §3.6.3).

Runs after an AnswerPackage is persisted. For each question it:
  1. Resolves or creates a MethodPattern row (pending by default).
  2. Resolves or creates KnowledgePoint rows along the proposed path.
  3. Inserts question↔pattern / question↔kp link rows with weight.
  4. Embeds question text, pattern summary, new kp paths into Milvus.
  5. Bumps seen_count on pattern + kps.
  6. Near-duplicate check on q_emb (≥ 0.96 cosine) — if found, returns
     the existing question's id so the caller can merge evidence.

Resolution rules:
  - MethodPattern: (subject, grade_band, name_cn) is the uniqueness key.
    The LLM may propose `pattern_id_suggested = "new:<name>"` or supply
    the exact name of an existing pattern.
  - KnowledgePoint: `node_ref` is either an existing UUID or
    `"new:A>B>C"`. For the `new:` form we walk the path and create any
    missing parent with status="pending".
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo
from app.db.models import (
    KnowledgePoint,
    MethodPatternRow,
    Question,
    QuestionKPLink,
    QuestionPatternLink,
)
from app.schemas import AnswerPackage, KnowledgePointRef, MethodPattern, ParsedQuestion
from app.services.embedding import DenseEmbedder
from app.services.indexer_service import build_pedagogical_index, persist_pedagogical_index
from app.services.solution_ref_service import decode_solution_ref, encode_solution_ref
from app.services.sparse_encoder import SparseEncoder
from app.services.vector_store import VectorStore

log = logging.getLogger(__name__)


NEAR_DUP_THRESHOLD = 0.96  # §3.6.3


@dataclass
class SedimentResult:
    pattern_id: uuid.UUID
    kp_ids: list[uuid.UUID] = field(default_factory=list)
    near_dup_of: uuid.UUID | None = None  # another question_id if cosine ≥ threshold


# ── pattern resolution ──────────────────────────────────────────────


async def _resolve_pattern(
    session: AsyncSession,
    *,
    mp: MethodPattern,
    subject: str,
    grade_band: str,
) -> tuple[MethodPatternRow, bool]:
    """Return (pattern_row, created)."""
    # Try existing by (subject, grade_band, name_cn).
    stmt = select(MethodPatternRow).where(
        MethodPatternRow.subject == subject,
        MethodPatternRow.grade_band == grade_band,
        MethodPatternRow.name_cn == mp.name_cn,
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        existing.seen_count += 1
        return existing, False

    row = MethodPatternRow(
        name_cn=mp.name_cn,
        subject=subject,
        grade_band=grade_band,
        when_to_use=mp.when_to_use,
        procedure_json=list(mp.general_procedure),
        pitfalls_json=list(mp.pitfalls),
        status="pending",
        seen_count=1,
    )
    session.add(row)
    await session.flush()
    return row, True


# ── kp resolution (path walk) ───────────────────────────────────────


def _split_new_path(ref: str) -> list[str] | None:
    if not ref.startswith("new:"):
        return None
    rest = ref.removeprefix("new:").strip()
    if not rest:
        return None
    parts = [seg.strip() for seg in rest.split(">")]
    return [p for p in parts if p]


async def _resolve_kp_by_path(
    session: AsyncSession,
    *,
    path_cached: str,
    subject: str,
    grade_band: str,
) -> KnowledgePoint | None:
    stmt = select(KnowledgePoint).where(
        KnowledgePoint.subject == subject,
        KnowledgePoint.grade_band == grade_band,
        KnowledgePoint.path_cached == path_cached,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _ensure_kp_path(
    session: AsyncSession,
    *,
    parts: list[str],
    subject: str,
    grade_band: str,
) -> tuple[KnowledgePoint, list[KnowledgePoint]]:
    """Walk/ create every node along `parts`; return (leaf, newly_created)."""
    parent: KnowledgePoint | None = None
    created: list[KnowledgePoint] = []
    for i in range(len(parts)):
        path = ">".join(parts[: i + 1])
        node = await _resolve_kp_by_path(
            session, path_cached=path, subject=subject, grade_band=grade_band,
        )
        if node is None:
            node = KnowledgePoint(
                parent_id=parent.id if parent else None,
                name_cn=parts[i],
                path_cached=path,
                subject=subject,
                grade_band=grade_band,
                status="pending",
                seen_count=0,
            )
            session.add(node)
            await session.flush()
            created.append(node)
        parent = node
    assert parent is not None
    return parent, created


async def _resolve_kp(
    session: AsyncSession,
    *,
    ref: KnowledgePointRef,
    subject: str,
    grade_band: str,
) -> tuple[KnowledgePoint, bool]:
    """Return (kp_row, newly_created)."""
    # Existing id path.
    try:
        uid = uuid.UUID(ref.node_ref)
    except (ValueError, TypeError):
        uid = None
    if uid is not None:
        existing = await session.get(KnowledgePoint, uid)
        if existing is not None:
            existing.seen_count += 1
            return existing, False
        # fall through to new-path handling; treat as missing.

    parts = _split_new_path(ref.node_ref) or [ref.node_ref]
    leaf, created = await _ensure_kp_path(
        session, parts=parts, subject=subject, grade_band=grade_band,
    )
    leaf.seen_count += 1
    return leaf, bool(created)


# ── link rows ───────────────────────────────────────────────────────


async def _upsert_pattern_link(
    session: AsyncSession, question_id: uuid.UUID, pattern_id: uuid.UUID, weight: float,
) -> None:
    existing = await session.get(QuestionPatternLink, (question_id, pattern_id))
    if existing is not None:
        existing.weight = float(weight)
        return
    session.add(QuestionPatternLink(
        question_id=question_id, pattern_id=pattern_id, weight=float(weight),
    ))


async def _upsert_kp_link(
    session: AsyncSession, question_id: uuid.UUID, kp_id: uuid.UUID, weight: float,
) -> None:
    existing = await session.get(QuestionKPLink, (question_id, kp_id))
    if existing is not None:
        existing.weight = float(weight)
        return
    session.add(QuestionKPLink(
        question_id=question_id, kp_id=kp_id, weight=float(weight),
    ))


# ── main entry point ────────────────────────────────────────────────


ProgressCallback = Callable[[str], Awaitable[None]]


async def _maybe_report(progress: ProgressCallback | None, message: str) -> None:
    if progress is None:
        return
    try:
        await progress(message)
    except Exception:  # noqa: BLE001
        log.exception("sediment progress callback failed")


async def sediment(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    solution_id: uuid.UUID | None = None,
    package: AnswerPackage,
    embedding: DenseEmbedder,
    vector_store: VectorStore,
    sparse_encoder: SparseEncoder | None = None,
    progress: ProgressCallback | None = None,
) -> SedimentResult:
    """Persist pattern / kps / embeddings for a freshly answered question.

    Idempotent on pattern/kp links (update-or-insert). Embeddings are
    re-upserted with the same ref_id so a re-run replaces the vector.
    If `sparse_encoder` is provided and the vector store supports
    sparse, each dense upsert is paired with a BM25/bge-m3 sparse
    upsert so the multi-route retrieval path can search lexically.
    """
    q = await repo.get_question(session, question_id)
    if q is None:
        raise KeyError(f"question {question_id} not found")

    # 1. Pattern
    await _maybe_report(progress, "解析方法模式与知识点…")
    pattern_row, _ = await _resolve_pattern(
        session, mp=package.method_pattern, subject=q.subject, grade_band=q.grade_band,
    )
    await _upsert_pattern_link(session, question_id, pattern_row.id, weight=1.0)

    # 2. KPs
    kp_ids: list[uuid.UUID] = []
    for ref in package.knowledge_points:
        kp_row, _ = await _resolve_kp(
            session, ref=ref, subject=q.subject, grade_band=q.grade_band,
        )
        kp_ids.append(kp_row.id)
        await _upsert_kp_link(session, question_id, kp_row.id, weight=ref.weight)

    await _maybe_report(progress, "构建检索单元 (步骤 / 公式 / 易错点)…")
    parsed = ParsedQuestion.model_validate(q.parsed_json or {})
    index = build_pedagogical_index(parsed=parsed, package=package)
    retrieval_unit_rows = await persist_pedagogical_index(
        session,
        question_id=question_id,
        solution_id=solution_id,
        profile=index.profile,
        units=index.units,
    )

    # 3. Embeddings — batched single call to save tokens.
    q_text = q.parsed_json.get("question_text", "") if q.parsed_json else ""
    question_full_text = index.profile.query_texts.question_full_text
    answer_full_text = index.profile.query_texts.answer_full_text
    pattern_summary = "\n".join([
        package.method_pattern.name_cn,
        package.method_pattern.when_to_use,
        *package.method_pattern.general_procedure,
    ])
    kp_rows: list[KnowledgePoint] = []
    for kpid in kp_ids:
        node = await session.get(KnowledgePoint, kpid)
        if node is not None:
            kp_rows.append(node)
    kp_texts = [f"{r.name_cn}\n{r.path_cached}" for r in kp_rows]

    retrieval_unit_texts = [row.text for row in retrieval_unit_rows]

    texts_to_embed = [
        q_text,
        question_full_text,
        answer_full_text,
        pattern_summary,
        *kp_texts,
        *retrieval_unit_texts,
    ]
    await _maybe_report(
        progress,
        f"调用 Gemini Embedding 生成稠密向量 ({len(texts_to_embed)} 段文本)…",
    )
    vectors = await embedding.embed_many(texts_to_embed)
    q_vec = vectors[0]
    question_full_vec = vectors[1]
    answer_full_vec = vectors[2]
    pattern_vec = vectors[3]
    kp_start = 4
    kp_end = kp_start + len(kp_texts)
    kp_vecs = vectors[kp_start:kp_end]
    retrieval_unit_vecs = vectors[kp_end:]

    solution_ref = encode_solution_ref(question_id=question_id, solution_id=solution_id)

    # 4. Near-dup check before writing q_emb so self-match doesn't trigger.
    near_dup_of: uuid.UUID | None = None
    hits = await vector_store.search(
        "q_emb",
        vector=q_vec,
        k=3,
        subject=q.subject,
        grade_band=q.grade_band,
    )
    for h in hits:
        parsed_ref = decode_solution_ref(h.ref_id)
        if parsed_ref is None:
            continue
        hit_question_id, _hit_solution_id = parsed_ref
        if hit_question_id == question_id:
            continue
        if h.score >= NEAR_DUP_THRESHOLD:
            near_dup_of = hit_question_id
            break

    # 5. Upserts — fan out concurrently to Milvus.
    total_dense = 4 + len(kp_rows) + len(retrieval_unit_rows)
    await _maybe_report(
        progress,
        f"写入向量数据库 (稠密 {total_dense} 条)…",
    )
    dense_jobs: list[Awaitable[None]] = [
        vector_store.upsert(
            "q_emb", ref_id=solution_ref, vector=q_vec,
            subject=q.subject, grade_band=q.grade_band, difficulty=q.difficulty,
        ),
        vector_store.upsert(
            "question_full_emb", ref_id=solution_ref, vector=question_full_vec,
            subject=q.subject, grade_band=q.grade_band, difficulty=q.difficulty,
        ),
        vector_store.upsert(
            "answer_full_emb", ref_id=solution_ref, vector=answer_full_vec,
            subject=q.subject, grade_band=q.grade_band, difficulty=q.difficulty,
        ),
        vector_store.upsert(
            "pattern_emb", ref_id=str(pattern_row.id), vector=pattern_vec,
            subject=q.subject, grade_band=q.grade_band,
        ),
    ]
    for row, vec in zip(kp_rows, kp_vecs):
        dense_jobs.append(vector_store.upsert(
            "kp_emb", ref_id=str(row.id), vector=vec,
            subject=row.subject, grade_band=row.grade_band,
        ))
        row.embedding_ref = str(row.id)
    pattern_row.embedding_ref = str(pattern_row.id)
    for row, vec in zip(retrieval_unit_rows, retrieval_unit_vecs):
        dense_jobs.append(vector_store.upsert(
            "retrieval_unit_emb", ref_id=str(row.id), vector=vec,
            subject=q.subject, grade_band=q.grade_band,
            difficulty=q.difficulty, unit_kind=row.unit_kind,
        ))
    await asyncio.gather(*dense_jobs)

    # 5b. Sparse lexical upserts (M5 multi-route). The sparse encoder
    # accumulates corpus statistics here so future queries get real
    # IDF weighting.
    if sparse_encoder is not None and getattr(vector_store, "supports_sparse", False):
        await _maybe_report(
            progress,
            f"写入稀疏向量索引 (BM25 / bge-m3, {len(texts_to_embed)} 条)…",
        )
        sparse_vecs = await sparse_encoder.encode(texts_to_embed)
        sp_q = sparse_vecs[0]
        sp_question_full = sparse_vecs[1]
        sp_answer_full = sparse_vecs[2]
        sp_pat = sparse_vecs[3]
        sp_kps = sparse_vecs[kp_start:kp_end]
        sp_units = sparse_vecs[kp_end:]
        sparse_jobs: list[Awaitable[None]] = [
            vector_store.upsert_sparse(
                "q_emb", ref_id=solution_ref, sparse=sp_q,
                subject=q.subject, grade_band=q.grade_band, difficulty=q.difficulty,
            ),
            vector_store.upsert_sparse(
                "question_full_emb", ref_id=solution_ref, sparse=sp_question_full,
                subject=q.subject, grade_band=q.grade_band, difficulty=q.difficulty,
            ),
            vector_store.upsert_sparse(
                "answer_full_emb", ref_id=solution_ref, sparse=sp_answer_full,
                subject=q.subject, grade_band=q.grade_band, difficulty=q.difficulty,
            ),
            vector_store.upsert_sparse(
                "pattern_emb", ref_id=str(pattern_row.id), sparse=sp_pat,
                subject=q.subject, grade_band=q.grade_band,
            ),
        ]
        for row, sp in zip(kp_rows, sp_kps):
            sparse_jobs.append(vector_store.upsert_sparse(
                "kp_emb", ref_id=str(row.id), sparse=sp,
                subject=row.subject, grade_band=row.grade_band,
            ))
        for row, sp in zip(retrieval_unit_rows, sp_units):
            sparse_jobs.append(vector_store.upsert_sparse(
                "retrieval_unit_emb", ref_id=str(row.id), sparse=sp,
                subject=q.subject, grade_band=q.grade_band,
                difficulty=q.difficulty, unit_kind=row.unit_kind,
            ))
        await asyncio.gather(*sparse_jobs)

    # 6. Near-dup: bump evidence on the canonical question.
    if near_dup_of is not None:
        canonical = await session.get(Question, near_dup_of)
        if canonical is not None:
            canonical.seen_count += 1

    await session.flush()
    return SedimentResult(
        pattern_id=pattern_row.id, kp_ids=kp_ids, near_dup_of=near_dup_of,
    )
