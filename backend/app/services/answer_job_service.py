"""Background answer-job orchestration.

For long Gemini solves the browser should not own the entire request.
This module runs answer generation in a background task, persists stage
status to `answer_packages`, and lets the frontend poll `/resume`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import delete, select

from app.config import settings
from app.db import repo
from app.db.models import AnswerPackageSection, RetrievalUnitRow, VisualizationRow
from app.db.session import session_scope
from app.schemas import AnswerPackage
from app.services.question_solution_service import (
    build_solution_stage_user_guidance,
    clear_solution_stage_outputs,
    create_solution,
    ensure_current_solution,
    get_solution_or_create,
    serialize_solution,
    set_current_solution,
    set_solution_stage_review_status,
    solution_stage_reviews,
    sync_solution_stage_reviews_to_question,
    update_solution_answer,
    update_solution_indexing,
    update_solution_visualizations,
    record_solution_stage_artifact,
)
from app.services.embedding import build_dense_embedder
from app.services.llm_client import LLMError
from app.services.llm_deps import get_llm_client
from app.services.sediment_service import sediment
from app.services.solver_service import generate_answer
from app.services.sparse_encoder import get_sparse_encoder
from app.services.stage_review_service import (
    REVIEW_CONFIRMED,
    REVIEW_PENDING,
    REVIEW_REJECTED,
    build_stage_user_guidance,
    clear_stage_outputs,
    list_stage_reviews,
    next_stage,
    record_stage_artifact,
    review_question_status,
    serialize_stage_review,
    set_stage_review_status,
    summarize_answer,
    summarize_indexing,
    summarize_visualizations,
)
from app.services.vector_store import get_vector_store
from app.services.vizcoder_service import generate_visualizations

log = logging.getLogger(__name__)


@dataclass
class JobState:
    question_id: str
    solution_id: str | None
    stage: str
    call_index: int = 0
    total_calls: int = 4
    label: str = ""
    message: str = ""
    done: bool = False
    error: str | None = None


_tasks: dict[str, asyncio.Task] = {}
_states: dict[str, JobState] = {}

_CALL_STAGES: list[dict[str, object]] = [
    {
        "key": "parsed",
        "call_index": 1,
        "label": "解析题面",
        "description": "Gemini Parser 读取题图并抽取结构化题面。",
    },
    {
        "key": "solving",
        "call_index": 2,
        "label": "生成解答",
        "description": "Gemini Solver 生成完整教学型答案包。",
    },
    {
        "key": "visualizing",
        "call_index": 3,
        "label": "生成可视化",
        "description": "Gemini VizCoder 为关键步骤生成交互式图形。",
    },
    {
        "key": "indexing",
        "call_index": 4,
        "label": "建立索引",
        "description": "Gemini Embedding 为问题、答案与检索单元建立向量索引。",
    },
]

_STAGE_META = {
    str(item["key"]): {
        "call_index": int(item["call_index"]),
        "label": str(item["label"]),
        "description": str(item["description"]),
    }
    for item in _CALL_STAGES
}

_TIMEOUT_BY_STAGE = {
    "solving": settings.llm.solver_timeout_s,
    "visualizing": settings.llm.vizcoder_timeout_s,
    "indexing": settings.llm.embed_timeout_s,
    "dialog": settings.llm.dialog_timeout_s,
    "parsed": settings.llm.parser_timeout_s,
}


def _job_key(question_id: uuid.UUID | str, solution_id: uuid.UUID | str | None = None) -> str:
    qid = str(question_id)
    sid = str(solution_id) if solution_id is not None else "question"
    return f"{qid}:{sid}"


def _serialize_viz_row(row: VisualizationRow) -> dict:
    return {
        "id": row.viz_ref,
        "title_cn": row.title,
        "caption_cn": row.caption,
        "learning_goal": row.learning_goal,
        "helpers_used": list(row.helpers_used_json or []),
        "engine": getattr(row, "engine", None) or "jsxgraph",
        "jsx_code": row.jsx_code,
        "ggb_commands": list(getattr(row, "ggb_commands_json", None) or []),
        "ggb_settings": getattr(row, "ggb_settings_json", None),
        "params": list(row.params_json or []),
        "animation": row.animation_json,
    }


def _friendly_llm_failure(message: str, *, failed_stage: str | None) -> dict:
    stage = failed_stage or "llm"
    timeout_s = _TIMEOUT_BY_STAGE.get(stage)
    lowered = message.lower()
    stage_label = str(_STAGE_META.get(stage, {}).get("label") or stage)
    if "timeout" in lowered:
        friendly = (
            f"Gemini 在“{stage_label}”阶段超时"
            + (f"（>{timeout_s} 秒）" if timeout_s else "")
            + "。这通常表示当前请求较大，或模型服务长时间没有返回结果。"
        )
        hint = (
            "可以直接重试；如果经常出现，建议提高 backend/config.toml 中对应的 "
            "timeout 配置"
            + (f"（当前 {timeout_s}s）" if timeout_s else "")
            + "。Solver 阶段优先检查 [llm].solver_timeout_s。"
        )
        return {
            "kind": "timeout",
            "failed_stage": stage,
            "timeout_s": timeout_s,
            "message": friendly,
            "raw_message": message,
            "hint": hint,
        }
    if any(
        marker in lowered
        for marker in (
            "503",
            "service unavailable",
            "currently experiencing high demand",
            '"status": "unavailable"',
            "'status': 'service unavailable'",
            "resource exhausted",
            "rate limit",
            "429",
        )
    ):
        friendly = (
            f"Gemini 在“{stage_label}”阶段暂时繁忙。"
            f"后端已自动重试 {settings.llm.max_retries} 次，但服务仍未恢复。"
        )
        hint = (
            "这通常是 Gemini 服务端瞬时高负载，不是题目内容错误。"
            "建议等待 30 到 90 秒后重试；如果频繁出现，可减少并发，"
            "或改用更稳定的非预览模型。"
        )
        return {
            "kind": "service_overloaded",
            "failed_stage": stage,
            "message": friendly,
            "raw_message": message,
            "hint": hint,
            "retryable": True,
        }
    return {
        "kind": "llm_error",
        "failed_stage": stage,
        "message": message,
        "raw_message": message,
    }


async def _append_section(
    question_id: uuid.UUID,
    *,
    section: str,
    payload: dict,
    clear_prior_status: bool = False,
) -> None:
    async with session_scope() as session:
        if clear_prior_status and section == "status":
            await session.execute(
                delete(AnswerPackageSection).where(
                    AnswerPackageSection.question_id == question_id,
                    AnswerPackageSection.section == "status",
                )
            )
        session.add(
            AnswerPackageSection(
                question_id=question_id,
                section=section,
                payload_json=payload,
            )
        )


async def _set_question_status(question_id: uuid.UUID, status: str) -> None:
    async with session_scope() as session:
        q = await repo.get_question(session, question_id)
        if q is None:
            raise KeyError(f"question {question_id} not found")
        q.status = status
        await session.flush()


async def _set_stage(
    question_id: uuid.UUID,
    *,
    stage: str,
    message: str,
    solution_id: uuid.UUID | None = None,
) -> None:
    meta = _STAGE_META.get(stage, {"call_index": 0, "label": stage, "description": message})
    _states[_job_key(question_id, solution_id)] = JobState(
        question_id=str(question_id),
        solution_id=str(solution_id) if solution_id else None,
        stage=stage,
        call_index=int(meta["call_index"]),
        label=str(meta["label"]),
        message=message,
    )
    await _set_question_status(question_id, stage)
    await _append_section(
        question_id,
        section="status",
        payload={
            "stage": stage,
            "message": message,
            "call_index": int(meta["call_index"]),
            "total_calls": 4,
            "label": str(meta["label"]),
            "description": str(meta["description"]),
        },
        clear_prior_status=True,
    )


async def _append_error(
    question_id: uuid.UUID,
    *,
    stage: str,
    message: str,
    solution_id: uuid.UUID | None = None,
) -> None:
    key = _job_key(question_id, solution_id)
    last = _states.get(key)
    payload = _friendly_llm_failure(
        message,
        failed_stage=last.stage if stage == "llm" and last else stage,
    )
    _states[key] = JobState(
        question_id=str(question_id),
        solution_id=str(solution_id) if solution_id else None,
        stage=last.stage if last else stage,
        call_index=last.call_index if last else 0,
        label=last.label if last else stage,
        message=str(payload.get("message") or message),
        done=True,
        error=str(payload.get("message") or message),
    )
    await _set_question_status(question_id, "error")
    await _append_section(
        question_id,
        section="error",
        payload={
            "stage": stage,
            **payload,
        },
    )
    await _append_section(
        question_id,
        section="status",
        payload={
            "stage": "error",
            "failed_stage": last.stage if last else stage,
            "message": str(payload.get("message") or f"{stage} 失败: {message}"),
            "call_index": last.call_index if last else 0,
            "total_calls": 4,
            "label": last.label if last else stage,
            "kind": payload.get("kind"),
            "hint": payload.get("hint"),
        },
        clear_prior_status=True,
    )


async def _mark_stage_ready_for_review(
    question_id: uuid.UUID,
    *,
    stage: str,
    summary: dict,
    refs: dict | None = None,
    message: str,
    solution_id: uuid.UUID | None = None,
) -> None:
    async with session_scope() as session:
        q = await repo.get_question(session, question_id)
        if q is None:
            raise KeyError(f"question {question_id} not found")
        if solution_id is None:
            await record_stage_artifact(
                session,
                question_id=question_id,
                stage=stage,
                summary=summary,
                refs=refs or {},
            )
        else:
            solution = await get_solution_or_create(
                session,
                question_id=question_id,
                solution_id=solution_id,
            )
            await record_solution_stage_artifact(
                session,
                solution=solution,
                stage=stage,
                summary=summary,
                refs=refs or {},
            )
            await sync_solution_stage_reviews_to_question(
                session,
                question_id=question_id,
                solution=solution,
            )
        q.status = review_question_status(stage)
        await session.flush()

    meta = _STAGE_META[stage]
    _states[_job_key(question_id, solution_id)] = JobState(
        question_id=str(question_id),
        solution_id=str(solution_id) if solution_id else None,
        stage=stage,
        call_index=int(meta["call_index"]),
        label=str(meta["label"]),
        message=message,
        done=True,
    )
    await _append_section(
        question_id,
        section="status",
        payload={
            "stage": review_question_status(stage),
            "review_stage": stage,
            "message": message,
            "call_index": int(meta["call_index"]),
            "total_calls": 4,
            "label": str(meta["label"]),
            "description": str(meta["description"]),
            "needs_confirmation": True,
        },
        clear_prior_status=True,
    )


async def _run_answer_job(
    question_id: uuid.UUID,
    *,
    stage: str,
    solution_id: uuid.UUID,
) -> None:
    key = _job_key(question_id, solution_id)
    llm = get_llm_client()
    vector_store = get_vector_store()
    try:
        if stage == "solving":
            await _set_stage(
                question_id,
                stage="solving",
                message="正在调用 Gemini 生成完整教学型答案，复杂题可能需要几十秒。",
                solution_id=solution_id,
            )
            summary: dict | None = None
            async with session_scope() as session:
                q = await repo.get_question(session, question_id)
                if q is None:
                    raise KeyError(f"question {question_id} not found")
                solution = await get_solution_or_create(
                    session,
                    question_id=question_id,
                    solution_id=solution_id,
                )
                await set_current_solution(session, question=q, solution=solution)
                user_guidance = await build_solution_stage_user_guidance(
                    session,
                    question_id=question_id,
                    solution=solution,
                    target_stage="solving",
                )
                async for ev in generate_answer(
                    session,
                    question_id=question_id,
                    llm=llm,
                    user_guidance=user_guidance,
                ):
                    # Persist each streamed section in its own transaction
                    # so the polling /resume endpoint sees progress while
                    # Gemini is still generating later sections. The
                    # solver's final _persist rewrites these rows
                    # transactionally with the validated payload.
                    try:
                        await _append_section(
                            question_id,
                            section=ev.name,
                            payload=ev.data,
                        )
                    except Exception:  # noqa: BLE001
                        # Streaming-progress writes are best-effort; the
                        # canonical write still happens in solver._persist.
                        log.exception(
                            "incremental section persist failed for %s/%s",
                            question_id, ev.name,
                        )
                q = await repo.get_question(session, question_id)
                if q is None or q.answer_package_json is None:
                    raise KeyError(f"question {question_id} missing answer package")
                await update_solution_answer(
                    session,
                    solution=solution,
                    answer_package_json=deepcopy(q.answer_package_json),
                )
                solution.status = review_question_status("solving")
                summary = summarize_answer(q.answer_package_json)
            assert summary is not None
            await _mark_stage_ready_for_review(
                question_id,
                stage="solving",
                summary=summary,
                refs={"question_id": str(question_id), "solution_id": str(solution_id)},
                message="Gemini Solver 已完成。请先人工确认解答，再进入下一阶段。",
                solution_id=solution_id,
            )
        elif stage == "visualizing":
            await _set_stage(
                question_id,
                stage="visualizing",
                message="答案已生成，正在补充可视化。",
                solution_id=solution_id,
            )
            rows: list[VisualizationRow] = []
            async with session_scope() as session:
                q = await repo.get_question(session, question_id)
                if q is None:
                    raise KeyError(f"question {question_id} not found")
                solution = await get_solution_or_create(
                    session,
                    question_id=question_id,
                    solution_id=solution_id,
                )
                await set_current_solution(session, question=q, solution=solution)
                q.answer_package_json = deepcopy(solution.answer_package_json)
                await session.flush()
                user_guidance = await build_solution_stage_user_guidance(
                    session,
                    question_id=question_id,
                    solution=solution,
                    target_stage="visualizing",
                )
                async for ev in generate_visualizations(
                    session,
                    question_id=question_id,
                    llm=llm,
                    user_guidance=user_guidance,
                ):
                    if ev.name == "error":
                        await _append_section(question_id, section="error", payload=ev.data)
                rows = list((await session.execute(
                    select(VisualizationRow)
                    .where(VisualizationRow.question_id == question_id)
                    .order_by(VisualizationRow.created_at)
                )).scalars().all())
                await update_solution_visualizations(
                    session,
                    solution=solution,
                    visualizations=[_serialize_viz_row(row) for row in rows],
                )
                solution.status = review_question_status("visualizing")
            await _mark_stage_ready_for_review(
                question_id,
                stage="visualizing",
                summary=summarize_visualizations(rows),
                refs={
                    "question_id": str(question_id),
                    "solution_id": str(solution_id),
                    "visualization_ids": [str(row.id) for row in rows],
                },
                message="Gemini VizCoder 已完成。请确认这些可视化是否可用。",
                solution_id=solution_id,
            )
        elif stage == "indexing":
            await _set_stage(
                question_id,
                stage="indexing",
                message="准备进入索引阶段…",
                solution_id=solution_id,
            )

            async def _progress(msg: str) -> None:
                await _set_stage(
                    question_id,
                    stage="indexing",
                    message=msg,
                    solution_id=solution_id,
                )

            summary: dict | None = None
            refs: dict | None = None
            async with session_scope() as session:
                q = await repo.get_question(session, question_id)
                if q is None:
                    raise KeyError(f"question {question_id} not found")
                solution = await get_solution_or_create(
                    session,
                    question_id=question_id,
                    solution_id=solution_id,
                )
                await set_current_solution(session, question=q, solution=solution)
                q.answer_package_json = deepcopy(solution.answer_package_json)
                await session.flush()
                q = await repo.get_question(session, question_id)
                if q is not None and q.answer_package_json is not None:
                    pkg = AnswerPackage.model_validate(q.answer_package_json)
                    result = await sediment(
                        session,
                        question_id=question_id,
                        solution_id=solution_id,
                        package=pkg,
                        embedding=build_dense_embedder(llm),
                        vector_store=vector_store,
                        sparse_encoder=get_sparse_encoder(),
                        progress=_progress,
                    )
                    retrieval_rows = list((await session.execute(
                        select(RetrievalUnitRow).where(RetrievalUnitRow.question_id == question_id)
                    )).scalars().all())
                    payload = {
                        "pattern_id": str(result.pattern_id),
                        "kp_ids": [str(k) for k in result.kp_ids],
                        "near_dup_of": (
                            str(result.near_dup_of) if result.near_dup_of else None
                        ),
                    }
                    await update_solution_indexing(
                        session,
                        solution=solution,
                        payload={
                            **payload,
                            "retrieval_unit_ids": [str(row.id) for row in retrieval_rows],
                        },
                    )
                    await _append_section(
                        question_id,
                        section="sediment",
                        payload=payload,
                    )
                    solution.status = review_question_status("indexing")
                    summary = summarize_indexing(
                        pattern_id=str(result.pattern_id),
                        kp_ids=[str(k) for k in result.kp_ids],
                        retrieval_unit_ids=[str(row.id) for row in retrieval_rows],
                        near_dup_of=(
                            str(result.near_dup_of) if result.near_dup_of else None
                        ),
                    )
                    refs = {
                        "question_id": str(question_id),
                        "solution_id": str(solution_id),
                        "pattern_id": str(result.pattern_id),
                        "kp_ids": [str(k) for k in result.kp_ids],
                        "retrieval_unit_ids": [str(row.id) for row in retrieval_rows],
                    }
                else:
                    raise KeyError(f"question {question_id} missing answer package")
            assert summary is not None
            await _mark_stage_ready_for_review(
                question_id,
                stage="indexing",
                summary=summary,
                refs=refs,
                message="索引构建已完成。确认后该题会进入可检索题库。",
                solution_id=solution_id,
            )
        else:
            raise ValueError(f"unsupported stage: {stage}")
    except KeyError as e:
        log.exception("answer job question missing")
        await _append_error(question_id, stage=stage, message=str(e), solution_id=solution_id)
    except LLMError as e:
        log.exception("answer job llm failure")
        await _append_error(question_id, stage=stage, message=str(e), solution_id=solution_id)
    except Exception as e:  # noqa: BLE001
        log.exception("answer job crashed")
        await _append_error(question_id, stage=stage, message=str(e), solution_id=solution_id)
    finally:
        _tasks.pop(key, None)


async def start_answer_job(
    question_id: uuid.UUID,
    *,
    from_stage: str | None = None,
    solution_id: uuid.UUID | None = None,
) -> dict:
    resolved_solution_id: uuid.UUID | None = solution_id
    async with session_scope() as session:
        q = await repo.get_question(session, question_id)
        if q is None:
            raise KeyError(f"question {question_id} not found")
        solution = await get_solution_or_create(
            session,
            question_id=question_id,
            solution_id=resolved_solution_id,
        )
        resolved_solution_id = solution.id
        await set_current_solution(session, question=q, solution=solution)
        await sync_solution_stage_reviews_to_question(
            session,
            question_id=question_id,
            solution=solution,
        )

    key = _job_key(question_id, resolved_solution_id)
    qid = str(question_id)
    existing = _tasks.get(key)
    if existing is not None and not existing.done():
        state = _states.get(key)
        return {
            "question_id": qid,
            "solution_id": str(resolved_solution_id),
            "state": "running",
            "stage": state.stage if state else "solving",
        }

    async with session_scope() as session:
        q = await repo.get_question(session, question_id)
        if q is None:
            raise KeyError(f"question {question_id} not found")
        solution = await get_solution_or_create(
            session,
            question_id=question_id,
            solution_id=resolved_solution_id,
        )
        if solution.answer_package_json is not None and solution.status == "answered":
            return {"question_id": qid, "solution_id": str(solution.id), "state": "complete"}
        reviews = {
            item["stage"]: item
            for item in solution_stage_reviews(solution)
        }
        parsed_reviews = {
            row.stage: row for row in await list_stage_reviews(session, question_id=question_id)
        }
        parsed_review = parsed_reviews.get("parsed")
        if parsed_review is None or parsed_review.review_status != REVIEW_CONFIRMED:
            return {
                "question_id": qid,
                "solution_id": str(solution.id),
                "state": "awaiting_review",
                "stage": "parsed",
            }
        stage = from_stage
        if stage is None:
            if reviews.get("solving") is None:
                stage = "solving"
            elif reviews.get("solving") and reviews["solving"].get("review_status") != REVIEW_CONFIRMED:
                return {"question_id": qid, "solution_id": str(solution.id), "state": "awaiting_review", "stage": "solving"}
            elif reviews.get("visualizing") is None:
                stage = "visualizing"
            elif reviews.get("visualizing") and reviews["visualizing"].get("review_status") != REVIEW_CONFIRMED:
                return {"question_id": qid, "solution_id": str(solution.id), "state": "awaiting_review", "stage": "visualizing"}
            elif reviews.get("indexing") is None:
                stage = "indexing"
            elif reviews.get("indexing") and reviews["indexing"].get("review_status") != REVIEW_CONFIRMED:
                return {"question_id": qid, "solution_id": str(solution.id), "state": "awaiting_review", "stage": "indexing"}
            else:
                return {"question_id": qid, "solution_id": str(solution.id), "state": "complete"}
        elif stage == "solving":
            pass
        elif stage == "visualizing":
            if reviews.get("solving") is None or reviews["solving"].get("review_status") != REVIEW_CONFIRMED:
                return {"question_id": qid, "solution_id": str(solution.id), "state": "awaiting_review", "stage": "solving"}
        elif stage == "indexing":
            if reviews.get("visualizing") is None or reviews["visualizing"].get("review_status") != REVIEW_CONFIRMED:
                return {"question_id": qid, "solution_id": str(solution.id), "state": "awaiting_review", "stage": "visualizing"}

    assert stage is not None
    assert resolved_solution_id is not None
    task = asyncio.create_task(_run_answer_job(question_id, stage=stage, solution_id=resolved_solution_id))
    _tasks[key] = task
    meta = _STAGE_META[stage]
    _states[key] = JobState(
        question_id=qid,
        solution_id=str(resolved_solution_id),
        stage="queued",
        call_index=int(meta["call_index"]),
        label="等待开始",
        message=f"等待开始 Gemini {int(meta['call_index'])}/4 · {str(meta['label'])}",
    )
    return {
        "question_id": qid,
        "solution_id": str(resolved_solution_id),
        "state": "started",
        "stage": stage,
    }


async def confirm_stage(
    question_id: uuid.UUID,
    *,
    stage: str,
    note: str | None = None,
    solution_id: uuid.UUID | None = None,
) -> dict:
    qid = str(question_id)
    async with session_scope() as session:
        q = await repo.get_question(session, question_id)
        if q is None:
            raise KeyError(f"question {question_id} not found")
        review: dict | None = None
        resolved_solution_id = solution_id
        if stage == "parsed":
            row = await set_stage_review_status(
                session,
                question_id=question_id,
                stage=stage,
                review_status=REVIEW_CONFIRMED,
                review_note=note,
            )
            review = serialize_stage_review(row)
        else:
            solution = await get_solution_or_create(
                session,
                question_id=question_id,
                solution_id=resolved_solution_id,
            )
            resolved_solution_id = solution.id
            await set_current_solution(session, question=q, solution=solution)
            review = await set_solution_stage_review_status(
                session,
                solution=solution,
                stage=stage,
                review_status=REVIEW_CONFIRMED,
                review_note=note,
            )
            await sync_solution_stage_reviews_to_question(
                session,
                question_id=question_id,
                solution=solution,
            )
        next_up = next_stage(stage)
        if next_up is None:
            if stage != "parsed":
                assert resolved_solution_id is not None
                solution = await get_solution_or_create(
                    session,
                    question_id=question_id,
                    solution_id=resolved_solution_id,
                )
                solution.status = "answered"
            q.status = "answered"
            await session.flush()
            await _append_section(
                question_id,
                section="status",
                payload={
                    "stage": "done",
                    "message": "解答完成。",
                    "call_index": 4,
                    "total_calls": 4,
                    "label": "全部完成",
                },
                clear_prior_status=True,
            )
            final_key = _job_key(question_id, resolved_solution_id)
            _states[final_key] = JobState(
                question_id=qid,
                solution_id=str(resolved_solution_id) if resolved_solution_id else None,
                stage="done",
                call_index=4,
                label="全部完成",
                message="解答完成。",
                done=True,
            )
            return {"question_id": qid, "solution_id": str(resolved_solution_id) if resolved_solution_id else None, "state": "complete", "review": review}

    started = await start_answer_job(question_id, from_stage=next_up, solution_id=solution_id)
    started["confirmed_stage"] = stage
    return started


async def reject_and_rerun_stage(
    question_id: uuid.UUID,
    *,
    stage: str,
    note: str | None = None,
    solution_id: uuid.UUID | None = None,
) -> dict:
    qid = str(question_id)
    key = _job_key(question_id, solution_id)
    existing = _tasks.get(key)
    if existing is not None and not existing.done():
        state = _states.get(key)
        return {
            "question_id": qid,
            "solution_id": str(solution_id) if solution_id else None,
            "state": "running",
            "stage": state.stage if state else None,
        }

    vector_store = get_vector_store()
    async with session_scope() as session:
        q = await repo.get_question(session, question_id)
        if q is None:
            raise KeyError(f"question {question_id} not found")
        resolved_solution_id = solution_id
        if stage == "parsed":
            await set_stage_review_status(
                session,
                question_id=question_id,
                stage=stage,
                review_status="rejected",
                review_note=note,
            )
            await clear_stage_outputs(
                session,
                question_id=question_id,
                stage=stage,
                vector_store=vector_store,
                solution_id=None,
            )
        else:
            solution = await get_solution_or_create(
                session,
                question_id=question_id,
                solution_id=resolved_solution_id,
            )
            resolved_solution_id = solution.id
            await set_current_solution(session, question=q, solution=solution)
            await set_solution_stage_review_status(
                session,
                solution=solution,
                stage=stage,
                review_status=REVIEW_REJECTED,
                review_note=note,
            )
            await clear_solution_stage_outputs(
                session,
                solution=solution,
                stage=stage,
            )
            await clear_stage_outputs(
                session,
                question_id=question_id,
                stage=stage,
                vector_store=vector_store,
                solution_id=resolved_solution_id,
            )
            q.answer_package_json = deepcopy(solution.answer_package_json)
            q.status = solution.status
            await sync_solution_stage_reviews_to_question(
                session,
                question_id=question_id,
                solution=solution,
            )

    if stage == "parsed":
        return {"question_id": qid, "state": "needs_manual_rescan", "stage": stage}
    return await start_answer_job(question_id, from_stage=stage, solution_id=resolved_solution_id)


def get_answer_job_state(question_id: uuid.UUID, solution_id: uuid.UUID | None = None) -> dict:
    qid = str(question_id)
    key = _job_key(question_id, solution_id)
    state = _states.get(key)
    task = _tasks.get(key)
    return {
        "question_id": qid,
        "solution_id": str(solution_id) if solution_id else None,
        "running": bool(task and not task.done()),
        "stage": state.stage if state else None,
        "done": state.done if state else False,
        "error": state.error if state else None,
        "call_index": state.call_index if state else 0,
        "total_calls": state.total_calls if state else 4,
        "label": state.label if state else "",
        "message": state.message if state else "",
    }


def build_pipeline_snapshot(
    *,
    question_status: str,
    has_parsed: bool,
    has_answer: bool,
    visualizations_generated: bool,
    job_state: dict | None,
    stage_reviews: list[dict] | None = None,
) -> dict:
    current_stage = (job_state or {}).get("stage") or question_status
    current_call = int((job_state or {}).get("call_index") or 0)
    error = (job_state or {}).get("error")
    reviews_by_stage = {
        str(item.get("stage")): item for item in (stage_reviews or []) if item.get("stage")
    }
    steps: list[dict] = []
    for item in _CALL_STAGES:
        key = str(item["key"])
        call_index = int(item["call_index"])
        state = "pending"
        review = reviews_by_stage.get(key)
        if current_stage == key and (job_state or {}).get("running"):
            state = "active"
        elif review and review.get("review_status") == REVIEW_CONFIRMED:
            state = "done"
        elif key == "parsed" and has_parsed and question_status == "answered":
            state = "done"
        elif key == "solving" and has_answer and question_status == "answered":
            state = "done"
        elif key == "visualizing" and visualizations_generated and question_status == "answered":
            state = "done"
        elif key == "indexing" and question_status == "answered":
            state = "done"
        elif review and int(review.get("artifact_version") or 0) > 0:
            state = "review"
        elif key == "parsed" and has_parsed:
            state = "review"

        if question_status == "error" and current_call == call_index:
            state = "error"

        steps.append({
            **item,
            "state": state,
            "review_status": review.get("review_status") if review else None,
            "artifact_version": int(review.get("artifact_version") or 0) if review else 0,
        })

    completed_calls = sum(1 for step in steps if step["state"] == "done")
    return {
        "current_stage": current_stage,
        "current_call": current_call,
        "total_calls": 4,
        "completed_calls": completed_calls,
        "visualizations_generated": visualizations_generated,
        "error": error,
        "steps": steps,
    }
