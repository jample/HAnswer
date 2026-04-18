"""Answer router — SSE streaming of AnswerPackage sections (§6, §3.2)."""

from __future__ import annotations

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.db import repo
from app.db.models import AnswerPackageSection, VisualizationRow
from app.db.session import session_scope
from app.schemas import AnswerPackage
from app.services.embedding import build_dense_embedder
from app.services.llm_client import LLMError
from app.services.llm_deps import get_llm_client
from app.services.sediment_service import sediment
from app.services.solver_service import generate_answer
from app.services.sparse_encoder import get_sparse_encoder
from app.services.vector_store import VectorStore, get_vector_store
from app.services.vizcoder_service import generate_visualizations

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/answer", tags=["answer"])
questions_router = APIRouter(prefix="/api/questions", tags=["answer"])


async def _session():
    async with session_scope() as s:
        yield s


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
            async for ev in generate_answer(
                session, question_id=question_id, llm=llm,
            ):
                yield {"event": ev.name, "data": json.dumps(ev.data, ensure_ascii=False)}
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
                    pkg = AnswerPackage.model_validate(q.answer_package_json)
                    result = await sediment(
                        session,
                        question_id=question_id,
                        package=pkg,
                        embedding=build_dense_embedder(llm),
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
    session: AsyncSession = Depends(_session),
) -> dict:
    """Return already-streamed sections + visualizations for this question.

    Lets the UI reconstruct the answer view after a page refresh without
    re-calling the LLM (§4 streaming resumability, M8).
    """
    q = await repo.get_question(session, question_id)
    if q is None:
        raise HTTPException(404, "question not found")

    sec_rows = (await session.execute(
        select(AnswerPackageSection)
        .where(AnswerPackageSection.question_id == question_id)
        .order_by(AnswerPackageSection.created_at)
    )).scalars().all()
    sections = [
        {"section": s.section, "payload": s.payload_json}
        for s in sec_rows
    ]

    viz_rows = (await session.execute(
        select(VisualizationRow)
        .where(VisualizationRow.question_id == question_id)
        .order_by(VisualizationRow.created_at)
    )).scalars().all()
    visualizations = [
        {
            "id": v.viz_ref,
            "title_cn": v.title,
            "caption_cn": v.caption,
            "learning_goal": v.learning_goal,
            "helpers_used": v.helpers_used_json,
            "jsx_code": v.jsx_code,
            "params": v.params_json,
            "animation": v.animation_json,
        }
        for v in viz_rows
    ]

    return {
        "question_id": str(q.id),
        "status": q.status,
        "answer_package": q.answer_package_json,
        "sections": sections,
        "visualizations": visualizations,
        "complete": q.answer_package_json is not None,
    }


@questions_router.get("/{question_id}")
async def get_question(
    question_id: UUID,
    session: AsyncSession = Depends(_session),
) -> dict:
    q = await repo.get_question(session, question_id)
    if q is None:
        raise HTTPException(404, "question not found")
    return {
        "question_id": str(q.id),
        "subject": q.subject,
        "grade_band": q.grade_band,
        "difficulty": q.difficulty,
        "status": q.status,
        "parsed": q.parsed_json,
        "answer_package": q.answer_package_json,
        "seen_count": q.seen_count,
    }
