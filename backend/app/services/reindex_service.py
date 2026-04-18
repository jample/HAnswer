"""Rebuild Milvus retrieval indexes from PostgreSQL source of truth."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    KnowledgePoint,
    MethodPatternRow,
    Question,
    QuestionKPLink,
    QuestionPatternLink,
    QuestionSolution,
)
from app.schemas import AnswerPackage, ParsedQuestion
from app.services.embedding import DenseEmbedder
from app.services.indexer_service import build_pedagogical_index, persist_pedagogical_index
from app.services.sparse_encoder import SparseEncoder
from app.services.vector_store import VectorStore


@dataclass
class ReindexStats:
    indexed_questions: int = 0
    indexed_patterns: int = 0
    indexed_kps: int = 0


async def _load_pattern(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
) -> MethodPatternRow | None:
    stmt = (
        select(MethodPatternRow)
        .join(QuestionPatternLink, QuestionPatternLink.pattern_id == MethodPatternRow.id)
        .where(QuestionPatternLink.question_id == question_id)
        .order_by(QuestionPatternLink.weight.desc(), MethodPatternRow.created_at)
    )
    return (await session.execute(stmt)).scalars().first()


async def _load_kps(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
) -> list[KnowledgePoint]:
    stmt = (
        select(KnowledgePoint)
        .join(QuestionKPLink, QuestionKPLink.kp_id == KnowledgePoint.id)
        .where(QuestionKPLink.question_id == question_id)
        .order_by(QuestionKPLink.weight.desc(), KnowledgePoint.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def rebuild_retrieval_indexes(
    session: AsyncSession,
    *,
    embedding: DenseEmbedder,
    vector_store: VectorStore,
    sparse_encoder: SparseEncoder | None = None,
    question_ids: list[uuid.UUID] | None = None,
) -> ReindexStats:
    stats = ReindexStats()
    stmt = select(QuestionSolution).where(QuestionSolution.answer_package_json.is_not(None)).order_by(QuestionSolution.created_at)
    if question_ids:
        stmt = stmt.where(QuestionSolution.question_id.in_(question_ids))
    solutions = list((await session.execute(stmt)).scalars().all())

    seen_pattern_ids: set[uuid.UUID] = set()
    seen_kp_ids: set[uuid.UUID] = set()

    for solution in solutions:
        q = await session.get(Question, solution.question_id)
        if q is None:
            continue
        parsed = ParsedQuestion.model_validate(q.parsed_json or {})
        package = AnswerPackage.model_validate(solution.answer_package_json or {})
        index = build_pedagogical_index(parsed=parsed, package=package)
        retrieval_unit_rows = await persist_pedagogical_index(
            session,
            question_id=q.id,
            solution_id=solution.id,
            profile=index.profile,
            units=index.units,
        )
        pattern_row = await _load_pattern(session, question_id=q.id)
        kp_rows = await _load_kps(session, question_id=q.id)

        q_text = parsed.question_text
        question_full_text = index.profile.query_texts.question_full_text
        answer_full_text = index.profile.query_texts.answer_full_text

        embed_plan: list[tuple[str, object, str]] = [
            ("q_emb", f"{q.id}::{solution.id}", q_text),
            ("question_full_emb", f"{q.id}::{solution.id}", question_full_text),
            ("answer_full_emb", f"{q.id}::{solution.id}", answer_full_text),
        ]
        if pattern_row is not None:
            pattern_summary = "\n".join([
                pattern_row.name_cn,
                pattern_row.when_to_use,
                *list(pattern_row.procedure_json or []),
            ])
            embed_plan.append(("pattern_emb", pattern_row, pattern_summary))
        for row in kp_rows:
            embed_plan.append(("kp_emb", row, f"{row.name_cn}\n{row.path_cached}"))
        for row in retrieval_unit_rows:
            embed_plan.append(("retrieval_unit_emb", row, row.text))

        texts = [text for _collection, _row, text in embed_plan]
        dense_vecs = await embedding.embed_many(texts)
        sparse_vecs = (
            await sparse_encoder.encode(texts)
            if sparse_encoder is not None and getattr(vector_store, "supports_sparse", False)
            else []
        )

        for idx, (collection, row, _text) in enumerate(embed_plan):
            dense = dense_vecs[idx]
            subject = q.subject
            grade_band = q.grade_band
            difficulty = q.difficulty
            unit_kind = ""
            ref_id = ""
            if collection in {"q_emb", "question_full_emb", "answer_full_emb"}:
                ref_id = str(row)
            elif collection == "pattern_emb":
                assert isinstance(row, MethodPatternRow)
                ref_id = str(row.id)
                row.embedding_ref = ref_id
            elif collection == "kp_emb":
                assert isinstance(row, KnowledgePoint)
                ref_id = str(row.id)
                subject = row.subject
                grade_band = row.grade_band
                row.embedding_ref = ref_id
            else:
                ref_id = str(row.id)
                unit_kind = row.unit_kind

            await vector_store.upsert(
                collection,
                ref_id=ref_id,
                vector=dense,
                subject=subject,
                grade_band=grade_band,
                difficulty=difficulty,
                unit_kind=unit_kind,
            )
            if sparse_vecs:
                await vector_store.upsert_sparse(
                    collection,
                    ref_id=ref_id,
                    sparse=sparse_vecs[idx],
                    subject=subject,
                    grade_band=grade_band,
                    difficulty=difficulty,
                    unit_kind=unit_kind,
                )

        stats.indexed_questions += 1
        if pattern_row is not None and pattern_row.id not in seen_pattern_ids:
            stats.indexed_patterns += 1
            seen_pattern_ids.add(pattern_row.id)
        for row in kp_rows:
            if row.id in seen_kp_ids:
                continue
            stats.indexed_kps += 1
            seen_kp_ids.add(row.id)

    await session.flush()
    return stats
