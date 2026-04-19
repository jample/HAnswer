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
import hashlib
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
    RetrievalUnitRow,
)
from app.schemas import AnswerPackage, KnowledgePointRef, MethodPattern, ParsedQuestion
from app.services.embedding import DenseEmbedder, EmbedItem, TaskKind
from app.services.indexer_service import build_pedagogical_index, persist_pedagogical_index
from app.services.solution_ref_service import decode_solution_ref, encode_solution_ref
from app.services.sparse_encoder import SparseEncoder
from app.services.vector_store import VectorStore

log = logging.getLogger(__name__)


NEAR_DUP_THRESHOLD = 0.96  # §3.6.3

# Display labels used in v2 embedding ``title:`` slots.
SUBJECT_TITLE_CN = {"math": "数学", "physics": "物理"}
GRADE_BAND_TITLE_CN = {"junior": "初中", "senior": "高中"}


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _read_sig(owner, key: str) -> str | None:  # type: ignore[no-untyped-def]
    """Read the prior embedding signature off a row.

    ``Question.embedding_sigs`` is a JSONB dict keyed by surface
    (``qfull`` / ``afull``); every other table holds a single string
    in ``embedding_sig``.
    """
    if isinstance(owner, Question):
        sigs = owner.embedding_sigs or {}
        value = sigs.get(key)
        return str(value) if isinstance(value, str) else None
    value = getattr(owner, "embedding_sig", None)
    return str(value) if isinstance(value, str) else None


def _write_sig(owner, key: str, sig: str) -> None:  # type: ignore[no-untyped-def]
    if isinstance(owner, Question):
        sigs = dict(owner.embedding_sigs or {})
        sigs[key] = sig
        owner.embedding_sigs = sigs
        return
    owner.embedding_sig = sig


@dataclass
class _Surface:
    collection: str
    ref_id: str
    text: str
    task_kind: TaskKind
    title: str
    sig_owner: object
    sig_key: str
    subject: str
    grade_band: str
    difficulty: int = 0
    unit_kind: str = ""
    new_sig: str = ""
    prior_sig: str | None = None
    needs_embed: bool = True
    dense_vec: list[float] | None = None


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

    # 3. Build the embedding plan ────────────────────────────────────
    #
    # One ``_Surface`` per (collection, ref_id). Each surface carries:
    #   - the text to embed,
    #   - its task_kind / title (so v2 picks the right embedding head),
    #   - the SHA256 signature we need to compare against the row's
    #     ``embedding_sig(s)`` to decide whether to skip the call.
    #
    # We then split surfaces into "to_embed" (sig changed or first run)
    # and "skipped" (sig unchanged → vector already in Milvus). Only
    # to_embed surfaces incur a Gemini call + a Milvus upsert.

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

    solution_ref = encode_solution_ref(question_id=question_id, solution_id=solution_id)
    subject_label = SUBJECT_TITLE_CN.get(q.subject, q.subject)
    band_label = GRADE_BAND_TITLE_CN.get(q.grade_band, q.grade_band)
    kp_path_title = " / ".join(
        sorted({r.path_cached for r in kp_rows if r.path_cached})
    ) or "未分类"
    pattern_title = package.method_pattern.name_cn or "未命名方法"

    surfaces: list[_Surface] = [
        _Surface(
            collection="question_full_emb",
            ref_id=solution_ref,
            text=question_full_text,
            task_kind="RETRIEVAL_DOCUMENT",
            title=f"{subject_label} · {band_label} · {kp_path_title}",
            sig_owner=q,
            sig_key="qfull",
            subject=q.subject,
            grade_band=q.grade_band,
            difficulty=q.difficulty,
        ),
        _Surface(
            collection="answer_full_emb",
            ref_id=solution_ref,
            text=answer_full_text,
            # Answers serve QA-style queries (the question text). Gemini
            # docs explicitly recommend QUESTION_ANSWERING for the
            # corpus side of QA retrieval.
            task_kind="QUESTION_ANSWERING",
            title=f"{subject_label} · 答 · {pattern_title}",
            sig_owner=q,
            sig_key="afull",
            subject=q.subject,
            grade_band=q.grade_band,
            difficulty=q.difficulty,
        ),
        _Surface(
            collection="pattern_emb",
            ref_id=str(pattern_row.id),
            text=pattern_summary,
            # Pattern vectors are clustered by similarity, not retrieved
            # against arbitrary queries → CLUSTERING gives a tighter
            # neighborhood geometry on the same corpus.
            task_kind="CLUSTERING",
            title=pattern_title,
            sig_owner=pattern_row,
            sig_key="main",
            subject=q.subject,
            grade_band=q.grade_band,
        ),
    ]
    for kp_row in kp_rows:
        surfaces.append(_Surface(
            collection="kp_emb",
            ref_id=str(kp_row.id),
            text=f"{kp_row.name_cn}\n{kp_row.path_cached}",
            task_kind="CLUSTERING",
            title=kp_row.path_cached or kp_row.name_cn,
            sig_owner=kp_row,
            sig_key="main",
            subject=kp_row.subject,
            grade_band=kp_row.grade_band,
        ))
    for unit_row in retrieval_unit_rows:
        surfaces.append(_Surface(
            collection="retrieval_unit_emb",
            ref_id=str(unit_row.id),
            text=unit_row.text,
            task_kind="RETRIEVAL_DOCUMENT",
            title=f"{unit_row.unit_kind} · {unit_row.title}".strip(" ·"),
            sig_owner=unit_row,
            sig_key="main",
            subject=q.subject,
            grade_band=q.grade_band,
            difficulty=q.difficulty,
            unit_kind=unit_row.unit_kind,
        ))

    # Compute new sigs and decide which surfaces are stale.
    for s in surfaces:
        s.new_sig = _hash_text(s.text)
        s.prior_sig = _read_sig(s.sig_owner, s.sig_key)
        s.needs_embed = s.prior_sig != s.new_sig

    to_embed = [s for s in surfaces if s.needs_embed]
    skipped_count = len(surfaces) - len(to_embed)

    # 4. Embed only stale surfaces ────────────────────────────────────
    if to_embed:
        await _maybe_report(
            progress,
            f"调用 Gemini Embedding 生成稠密向量 ("
            f"{len(to_embed)} 段文本{f', {skipped_count} 段命中签名缓存' if skipped_count else ''})…",
        )
        items = [
            EmbedItem(text=s.text, task_kind=s.task_kind, title=s.title)
            for s in to_embed
        ]
        vectors = await embedding.embed_documents(items)
        for s, vec in zip(to_embed, vectors, strict=True):
            s.dense_vec = vec
    elif skipped_count:
        await _maybe_report(
            progress,
            f"全部 {skipped_count} 段文本命中签名缓存,跳过 Gemini Embedding 调用",
        )

    # 5. Near-dup check ───────────────────────────────────────────────
    #
    # Uses ``question_full_emb`` (canonical question vector) so the
    # legacy ``q_emb`` collection can be retired. We only run the probe
    # when we *just* re-embedded the question — if the question's
    # signature was unchanged the dedupe verdict is unchanged too.
    near_dup_of: uuid.UUID | None = None
    qfull_surface = surfaces[0]  # question_full_emb is always position 0
    if qfull_surface.dense_vec is not None:
        hits = await vector_store.search(
            "question_full_emb",
            vector=qfull_surface.dense_vec,
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

    # 6. Upserts (dense + sparse) for stale surfaces only ─────────────
    if to_embed:
        await _maybe_report(
            progress,
            f"写入向量数据库 (稠密 {len(to_embed)} 条)…",
        )
        dense_jobs: list[Awaitable[None]] = []
        for s in to_embed:
            dense_jobs.append(vector_store.upsert(
                s.collection,
                ref_id=s.ref_id,
                vector=s.dense_vec or [],
                subject=s.subject,
                grade_band=s.grade_band,
                difficulty=s.difficulty,
                unit_kind=s.unit_kind,
            ))
        await asyncio.gather(*dense_jobs)

        if sparse_encoder is not None and getattr(vector_store, "supports_sparse", False):
            await _maybe_report(
                progress,
                f"写入稀疏向量索引 (BM25 / bge-m3, {len(to_embed)} 条)…",
            )
            sparse_vecs = await sparse_encoder.encode([s.text for s in to_embed])
            sparse_jobs: list[Awaitable[None]] = []
            for s, sp in zip(to_embed, sparse_vecs, strict=True):
                sparse_jobs.append(vector_store.upsert_sparse(
                    s.collection,
                    ref_id=s.ref_id,
                    sparse=sp,
                    subject=s.subject,
                    grade_band=s.grade_band,
                    difficulty=s.difficulty,
                    unit_kind=s.unit_kind,
                ))
            await asyncio.gather(*sparse_jobs)

        # Persist new sigs + cross-table embedding refs.
        for s in to_embed:
            _write_sig(s.sig_owner, s.sig_key, s.new_sig)

    # Maintain the legacy embedding_ref columns the UI still inspects.
    pattern_row.embedding_ref = str(pattern_row.id)
    for kp_row in kp_rows:
        kp_row.embedding_ref = str(kp_row.id)

    # 7. Near-dup: bump evidence on the canonical question.
    if near_dup_of is not None:
        canonical = await session.get(Question, near_dup_of)
        if canonical is not None:
            canonical.seen_count += 1

    await session.flush()
    return SedimentResult(
        pattern_id=pattern_row.id, kp_ids=kp_ids, near_dup_of=near_dup_of,
    )
