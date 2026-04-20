"""Answer router — SSE streaming of AnswerPackage sections (§6, §3.2)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.db import repo
from app.db.models import (
    AnswerPackageSection,
    QuestionRetrievalProfile,
    RetrievalUnitRow,
    VisualizationRow,
)
from app.db.session import session_scope
from app.schemas import AnswerPackage
from app.services.answer_job_service import (
    _serialize_viz_row,
    build_pipeline_snapshot,
    clear_answer_job_state,
    confirm_stage,
    get_answer_job_state,
    reject_and_rerun_stage,
    start_answer_job,
)
from app.services.embedding import build_dense_embedder
from app.services.llm_client import LLMError, PromptLogContext
from app.services.llm_deps import get_llm_client
from app.services.question_solution_service import (
    create_solution,
    get_current_solution,
    get_solution,
    list_solutions,
    serialize_solution,
    solution_stage_reviews,
    sync_question_from_current_solution,
)
from app.services.sediment_service import sediment
from app.services.solver_service import _sections, generate_answer
from app.services.sparse_encoder import get_sparse_encoder
from app.services.stage_review_service import (
    delete_question_vectors,
    delete_solution_vectors,
    ensure_parsed_stage_review,
    list_stage_reviews,
    serialize_stage_review,
)
from app.services.vector_store import VectorStore, get_vector_store
from app.services.vizcoder_service import generate_visualizations

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/answer", tags=["answer"])
questions_router = APIRouter(prefix="/api/questions", tags=["answer"])


async def _session():
    async with session_scope() as s:
        yield s


def _extract_note(payload: dict | None) -> str | None:
    if payload is None or "note" not in payload:
        return None
    return str(payload.get("note") or "").strip()


def _extract_storyboard_from_solution(solution) -> dict | None:
    if solution is None:
        return None
    review = (solution.stage_reviews_json or {}).get("visualizing") or {}
    refs = review.get("refs") or {}
    storyboard = refs.get("storyboard")
    return dict(storyboard) if isinstance(storyboard, dict) else None


@router.post("/{question_id}/start")
async def start_answer_job_endpoint(
    question_id: UUID,
    solution_id: UUID | None = None,
    session: AsyncSession = Depends(_session),
) -> dict:
    q = await repo.get_question(session, question_id)
    if q is None:
        raise HTTPException(404, "question not found")
    try:
        return await start_answer_job(question_id, solution_id=solution_id)
    except KeyError:
        raise HTTPException(404, "question not found")


@router.post("/{question_id}/stages/{stage}/confirm")
async def confirm_stage_endpoint(
    question_id: UUID,
    stage: str,
    solution_id: UUID | None = None,
    payload: dict | None = Body(default=None),
    session: AsyncSession = Depends(_session),
) -> dict:
    q = await repo.get_question(session, question_id)
    if q is None:
        raise HTTPException(404, "question not found")
    if stage not in {"parsed", "solving", "visualizing", "indexing"}:
        raise HTTPException(400, "unsupported stage")
    note = _extract_note(payload)
    try:
        return await confirm_stage(question_id, stage=stage, note=note, solution_id=solution_id)
    except KeyError:
        raise HTTPException(404, "question not found")


@router.post("/{question_id}/stages/{stage}/rerun")
async def rerun_stage_endpoint(
    question_id: UUID,
    stage: str,
    solution_id: UUID | None = None,
    payload: dict | None = Body(default=None),
    session: AsyncSession = Depends(_session),
    llm=Depends(get_llm_client),
) -> dict:
    q = await repo.get_question(session, question_id)
    if q is None:
        raise HTTPException(404, "question not found")
    if stage not in {"parsed", "solving", "visualizing", "indexing"}:
        raise HTTPException(400, "unsupported stage")
    note = _extract_note(payload)
    try:
        if stage == "parsed":
            from app.services.ingest_service import rescan_question

            result = await rescan_question(
                session,
                question_id=question_id,
                llm=llm,
                user_guidance=note,
            )
            await ensure_parsed_stage_review(session, question=result.question, review_note=note)
            return {
                "question_id": str(result.question.id),
                "state": "awaiting_review",
                "stage": "parsed",
            }
        return await reject_and_rerun_stage(
            question_id,
            stage=stage,
            note=note,
            solution_id=solution_id,
        )
    except KeyError:
        raise HTTPException(404, "question not found")
    except LLMError as e:
        raise HTTPException(502, f"stage rerun failed: {e}")


@questions_router.post("/{question_id}/solutions")
async def create_solution_endpoint(
    question_id: UUID,
    payload: dict | None = Body(default=None),
    session: AsyncSession = Depends(_session),
) -> dict:
    q = await repo.get_question(session, question_id)
    if q is None:
        raise HTTPException(404, "question not found")
    row = await create_solution(
        session,
        question_id=question_id,
        title=None if payload is None else str(payload.get("title") or "").strip() or None,
        make_current=True,
    )
    return {"question_id": str(question_id), "solution": serialize_solution(row)}


@router.post("/{question_id}")
async def start_answer(
    question_id: UUID,
    session: AsyncSession = Depends(_session),
    llm=Depends(get_llm_client),
    vs: VectorStore = Depends(lambda: get_vector_store()),
) -> EventSourceResponse:
    """Start full answer generation. Streams AnswerPackage sections and
    visualizations in §6 order via SSE. Terminates with `done` or `error`.
    """

    async def _gen():
        try:
            yield {
                "event": "status",
                "data": json.dumps({
                    "stage": "solver",
                    "message": "正在调用 Gemini 生成完整教学型答案，复杂题可能需要几十秒。",
                }, ensure_ascii=False),
            }
            async for ev in generate_answer(
                session, question_id=question_id, llm=llm,
            ):
                yield {"event": ev.name, "data": json.dumps(ev.data, ensure_ascii=False)}
            yield {
                "event": "status",
                "data": json.dumps({
                    "stage": "vizcoder",
                    "message": "答案已生成，正在补充可视化。",
                }, ensure_ascii=False),
            }
            # Viz stage is a separate prompt (§7.2.3). Its own errors surface
            # as per-viz `error` events, not a whole-stream failure.
            async for ev in generate_visualizations(
                session, question_id=question_id, llm=llm,
            ):
                yield {"event": ev.name, "data": json.dumps(ev.data, ensure_ascii=False)}

            # Sediment (§3.6.2) — pattern / kp / embeddings.
            q = await repo.get_question(session, question_id)
            if q is not None and q.answer_package_json is not None:
                try:
                    yield {
                        "event": "status",
                        "data": json.dumps({
                            "stage": "sediment",
                            "message": "正在写入知识点、方法模式与检索索引。",
                        }, ensure_ascii=False),
                    }
                    pkg = AnswerPackage.model_validate(q.answer_package_json)
                    result = await sediment(
                        session,
                        question_id=question_id,
                        package=pkg,
                        embedding=build_dense_embedder(
                            llm,
                            prompt_context=PromptLogContext(
                                phase_description="建立索引",
                                question_id=str(question_id),
                            ),
                        ),
                        vector_store=vs,
                        sparse_encoder=get_sparse_encoder(),
                    )
                    yield {
                        "event": "sediment",
                        "data": json.dumps({
                            "pattern_id": str(result.pattern_id),
                            "kp_ids": [str(k) for k in result.kp_ids],
                            "near_dup_of": (
                                str(result.near_dup_of) if result.near_dup_of else None
                            ),
                        }),
                    }
                except Exception as e:  # noqa: BLE001
                    log.exception("sediment failed (non-fatal)")
                    yield {
                        "event": "error",
                        "data": json.dumps({"stage": "sediment", "message": str(e)}),
                    }

            yield {
                "event": "status",
                "data": json.dumps({
                    "stage": "done",
                    "message": "解答完成。",
                }, ensure_ascii=False),
            }
            yield {"event": "done", "data": json.dumps({"question_id": str(question_id)})}
        except KeyError:
            yield {"event": "error", "data": json.dumps({"message": "question not found"})}
        except LLMError as e:
            log.exception("solver LLM failure")
            yield {"event": "error", "data": json.dumps({"message": f"llm: {e}"})}
        except Exception as e:  # last-resort so the stream closes cleanly
            log.exception("answer stream crashed")
            yield {"event": "error", "data": json.dumps({"message": str(e)})}

    return EventSourceResponse(_gen())


@router.get("/{question_id}/resume")
async def resume_answer(
    question_id: UUID,
    solution_id: UUID | None = None,
    session: AsyncSession = Depends(_session),
) -> dict:
    """Return already-streamed sections + visualizations for this question.

    Lets the UI reconstruct the answer view after a page refresh without
    re-calling the LLM (§4 streaming resumability, M8).
    """
    q = await repo.get_question(session, question_id)
    if q is None:
        raise HTTPException(404, "question not found")
    await ensure_parsed_stage_review(session, question=q)
    solution = (
        await get_solution(session, question_id=question_id, solution_id=solution_id)
        if solution_id is not None
        else await get_current_solution(session, question_id=question_id)
    )

    sections: list[dict] = []
    if solution is None:
        sec_rows = (await session.execute(
            select(AnswerPackageSection)
            .where(AnswerPackageSection.question_id == question_id)
            .order_by(AnswerPackageSection.created_at)
        )).scalars().all()
        sections = [
            {"section": s.section, "payload": s.payload_json}
            for s in sec_rows
        ]

    answer_package_json = solution.answer_package_json if solution is not None else q.answer_package_json
    if not sections and answer_package_json is not None:
        try:
            pkg = AnswerPackage.model_validate(answer_package_json)
            sections = [
                {"section": ev.name, "payload": ev.data}
                for ev in _sections(pkg)
            ]
            sediment_payload = solution.sediment_json if solution is not None else None
            if sediment_payload:
                sections.append({"section": "sediment", "payload": sediment_payload})
        except Exception:  # pragma: no cover - defensive fallback
            log.exception("resume fallback failed to rebuild sections")

    if solution is not None:
        visualizations = list(solution.visualizations_json or [])
    else:
        viz_rows = (await session.execute(
            select(VisualizationRow)
            .where(VisualizationRow.question_id == question_id)
            .order_by(VisualizationRow.created_at)
        )).scalars().all()
        visualizations = [_serialize_viz_row(v) for v in viz_rows]

    parsed_stage_reviews = [
        serialize_stage_review(row)
        for row in await list_stage_reviews(session, question_id=question_id)
    ]
    solution_reviews = solution_stage_reviews(solution) if solution is not None else []
    stage_reviews = [
        *[row for row in parsed_stage_reviews if row.get("stage") == "parsed"],
        *solution_reviews,
    ]
    job = await get_answer_job_state(
        session,
        question_id,
        solution.id if solution is not None else None,
    )
    solutions = [serialize_solution(row) for row in await list_solutions(session, question_id=question_id)]
    storyboard = _extract_storyboard_from_solution(solution)
    return {
        "question_id": str(q.id),
        "status": solution.status if solution is not None else q.status,
        "current_solution_id": str(solution.id) if solution is not None else None,
        "solutions": solutions,
        "job": job,
        "pipeline": build_pipeline_snapshot(
            question_status=solution.status if solution is not None else q.status,
            has_parsed=bool(q.parsed_json),
            has_answer=answer_package_json is not None,
            visualizations_generated=bool(visualizations),
            job_state=job,
            stage_reviews=stage_reviews,
        ),
        "stage_reviews": stage_reviews,
        "answer_package": answer_package_json,
        "sections": sections,
        "visualizations": visualizations,
        "storyboard": storyboard,
        "complete": (solution.status if solution is not None else q.status) == "answered",
    }


@questions_router.get("/{question_id}")
async def get_question(
    question_id: UUID,
    solution_id: UUID | None = None,
    session: AsyncSession = Depends(_session),
) -> dict:
    q = await repo.get_question(session, question_id)
    if q is None:
        raise HTTPException(404, "question not found")
    await ensure_parsed_stage_review(session, question=q)
    parsed_stage_reviews = [
        serialize_stage_review(row)
        for row in await list_stage_reviews(session, question_id=question_id)
    ]
    solution = (
        await get_solution(session, question_id=question_id, solution_id=solution_id)
        if solution_id is not None
        else await get_current_solution(session, question_id=question_id)
    )
    solutions = [serialize_solution(row) for row in await list_solutions(session, question_id=question_id)]
    stage_reviews = [
        *[row for row in parsed_stage_reviews if row.get("stage") == "parsed"],
        *(solution_stage_reviews(solution) if solution is not None else []),
    ]
    storyboard = _extract_storyboard_from_solution(solution)
    return {
        "question_id": str(q.id),
        "subject": q.subject,
        "grade_band": q.grade_band,
        "difficulty": q.difficulty,
        "status": solution.status if solution is not None else q.status,
        "parsed": q.parsed_json,
        "answer_package": solution.answer_package_json if solution is not None else q.answer_package_json,
        "seen_count": q.seen_count,
        "stage_reviews": stage_reviews,
        "storyboard": storyboard,
        "solutions": solutions,
        "current_solution_id": str(solution.id) if solution is not None else None,
    }


@questions_router.post("/{question_id}/delete")
async def delete_question_endpoint(
    question_id: UUID,
    session: AsyncSession = Depends(_session),
    vs: VectorStore = Depends(lambda: get_vector_store()),
) -> dict:
    """Delete a question and all its cascade children (solutions, visualizations, index)."""
    q = await repo.get_question(session, question_id)
    if q is None:
        raise HTTPException(404, "question not found")
    solution_ids = [row.id for row in await list_solutions(session, question_id=question_id)]

    retrieval_units = list((await session.execute(
        select(RetrievalUnitRow).where(RetrievalUnitRow.question_id == question_id)
    )).scalars().all())

    milvus_cleaned = await delete_question_vectors(
        vector_store=vs,
        question_id=question_id,
        retrieval_units=retrieval_units,
        solution_ids=solution_ids,
    )

    await clear_answer_job_state(question_id, include_all_solutions=True)
    cascade_counts = await repo.delete_question(session, question_id=question_id)

    return {
        "deleted": str(question_id),
        "cascade": {
            **cascade_counts,
            "milvus_deleted": milvus_cleaned,
        },
    }


@questions_router.post("/{question_id}/solutions/{solution_id}/delete")
async def delete_solution_endpoint(
    question_id: UUID,
    solution_id: UUID,
    session: AsyncSession = Depends(_session),
    vs: VectorStore = Depends(lambda: get_vector_store()),
) -> dict:
    """Delete a specific solution and its cascade children (retrieval units, index)."""
    solution = await get_solution(session, question_id=question_id, solution_id=solution_id)
    if solution is None:
        raise HTTPException(404, "solution not found")
    was_current = bool(solution.is_current)
    viz_refs = [
        str(item.get("id") or item.get("viz_ref") or "").strip()
        for item in (solution.visualizations_json or [])
        if str(item.get("id") or item.get("viz_ref") or "").strip()
    ]

    retrieval_units = list((await session.execute(
        select(RetrievalUnitRow).where(RetrievalUnitRow.solution_id == solution_id)
    )).scalars().all())

    milvus_cleaned = await delete_solution_vectors(
        vector_store=vs,
        question_id=question_id,
        solution_id=solution_id,
        retrieval_units=retrieval_units,
    )
    await clear_answer_job_state(question_id, solution_id=solution_id)

    try:
        cascade_counts = await repo.delete_solution(
            session,
            question_id=question_id,
            solution_id=solution_id,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))

    viz_rows_deleted = 0
    if was_current and viz_refs:
        result = await session.execute(
            sa_delete(VisualizationRow)
            .where(VisualizationRow.question_id == question_id)
            .where(VisualizationRow.viz_ref.in_(viz_refs))
        )
        viz_rows_deleted = int(result.rowcount or 0)

    if was_current:
        question = await repo.get_question(session, question_id)
        if question is not None:
            await sync_question_from_current_solution(session, question=question)

    return {
        "deleted": str(solution_id),
        "question_id": str(question_id),
        "cascade": {
            **cascade_counts,
            "milvus_deleted": milvus_cleaned,
            "visualization_rows_deleted": viz_rows_deleted,
        },
    }


@questions_router.post("/{question_id}/solutions/{solution_id}/visualizations/{viz_ref}/delete")
async def delete_visualization_endpoint(
    question_id: UUID,
    solution_id: UUID,
    viz_ref: str,
    session: AsyncSession = Depends(_session),
) -> dict:
    """Delete a specific visualization from a solution."""
    solution = await get_solution(session, question_id=question_id, solution_id=solution_id)
    if solution is None:
        raise HTTPException(404, "solution not found")

    viz_list = list(solution.visualizations_json or [])
    filtered = [v for v in viz_list if v.get("id") != viz_ref and v.get("viz_ref") != viz_ref]
    if len(filtered) == len(viz_list):
        raise HTTPException(404, "visualization not found")

    solution.visualizations_json = filtered
    solution.updated_at = _utcnow()

    await session.execute(
        sa_delete(VisualizationRow)
        .where(VisualizationRow.question_id == question_id)
        .where(VisualizationRow.viz_ref == viz_ref)
    )

    await session.flush()

    return {"deleted": viz_ref, "solution_id": str(solution_id)}


@questions_router.post("/{question_id}/solutions/{solution_id}/index/clear")
async def clear_solution_index_endpoint(
    question_id: UUID,
    solution_id: UUID,
    session: AsyncSession = Depends(_session),
    vs: VectorStore = Depends(lambda: get_vector_store()),
) -> dict:
    """Clear retrieval index for a solution (Milvus vectors + retrieval units/profiles)."""
    question = await repo.get_question(session, question_id)
    if question is None:
        raise HTTPException(404, "question not found")
    solution = await get_solution(session, question_id=question_id, solution_id=solution_id)
    if solution is None:
        raise HTTPException(404, "solution not found")

    retrieval_units = list((await session.execute(
        select(RetrievalUnitRow).where(RetrievalUnitRow.solution_id == solution_id)
    )).scalars().all())
    retrieval_units_deleted = len(retrieval_units)
    retrieval_profiles_deleted = int(await session.scalar(
        select(func.count())
        .select_from(QuestionRetrievalProfile)
        .where(QuestionRetrievalProfile.solution_id == solution_id)
    ) or 0)

    milvus_cleaned = await delete_solution_vectors(
        vector_store=vs,
        question_id=question_id,
        solution_id=solution_id,
        retrieval_units=retrieval_units,
    )
    await clear_answer_job_state(question_id, solution_id=solution_id)

    await session.execute(
        sa_delete(RetrievalUnitRow)
        .where(RetrievalUnitRow.solution_id == solution_id)
    )

    await session.execute(
        sa_delete(QuestionRetrievalProfile)
        .where(QuestionRetrievalProfile.solution_id == solution_id)
    )

    solution.sediment_json = None
    reviews = dict(solution.stage_reviews_json or {})
    reviews.pop("indexing", None)
    solution.stage_reviews_json = reviews
    solution.status = "review_viz"
    solution.updated_at = _utcnow()
    if solution.is_current:
        await sync_question_from_current_solution(session, question=question)

    await session.flush()

    return {
        "deleted": str(solution_id),
        "index_cleared": {
            "retrieval_units_deleted": retrieval_units_deleted,
            "retrieval_profiles_deleted": retrieval_profiles_deleted,
            "milvus_deleted": milvus_cleaned,
        },
    }


def _utcnow():
    return datetime.now(UTC)
