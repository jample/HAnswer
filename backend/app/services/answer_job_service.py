"""Background answer-job orchestration.

For long LLM solves the browser should not own the entire request.
This module runs answer generation in a background task, persists stage
status to `answer_packages`, and lets the frontend poll `/resume`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repo
from app.db.models import AnswerPackageSection, RetrievalUnitRow, VisualizationRow
from app.db.session import session_scope
from app.schemas import AnswerPackage, GeoGebraExecutionPayload, VisualizationSpec
from app.services.embedding import build_dense_embedder
from app.services.geogebra_codegen_service import (
    generate_geogebra_visualization_or_fallback,
)
from app.services.llm_client import LLMError, PromptLogContext, TransientLLMError
from app.services.llm_deps import get_llm_client
from app.services.question_solution_service import (
    build_solution_stage_user_guidance,
    clear_solution_stage_outputs,
    get_solution_or_create,
    record_solution_stage_artifact,
    set_current_solution,
    set_solution_stage_review_status,
    solution_stage_reviews,
    sync_solution_stage_reviews_to_question,
    update_solution_answer,
    update_solution_indexing,
    update_solution_visualizations,
)
from app.services.sediment_service import sediment
from app.services.solver_service import generate_answer
from app.services.sparse_encoder import get_sparse_encoder
from app.services.stage_review_service import (
    REVIEW_CONFIRMED,
    REVIEW_REJECTED,
    clear_stage_outputs,
    list_stage_reviews,
    next_stage,
    record_stage_artifact,
    review_question_status,
    serialize_stage_review,
    set_stage_review_status,
    summarize_answer,
    summarize_indexing,
    summarize_visualization_plan,
    summarize_visualizations,
)
from app.services.vector_store import get_vector_store
from app.services.visual_action_logger import log_visual_action
from app.services.visualization_spec_service import (
    generate_visualization_spec_bundle,
    persist_visualization_spec_bundle,
    select_recommended_visualization,
)

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
        "description": f"{settings.active_llm_provider_label} Parser 读取题图并抽取结构化题面。",
    },
    {
        "key": "solving",
        "call_index": 2,
        "label": "生成解答",
        "description": f"{settings.active_llm_provider_label} Solver 生成完整教学型答案包。",
    },
    {
        "key": "visualizing",
        "call_index": 3,
        "label": "生成可视化",
        "description": "可视化阶段先规划 Stage 1 规格，再生成并校验 Stage 2 GeoGebra 指令。",
    },
    {
        "key": "indexing",
        "call_index": 4,
        "label": "建立索引",
        "description": f"{settings.active_llm_provider_label} Embedding 为问题、答案与检索单元建立向量索引。",
    },
]

_TOTAL_CALLS = len(_CALL_STAGES)

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


async def clear_answer_job_state(
    question_id: uuid.UUID,
    *,
    solution_id: uuid.UUID | None = None,
    include_all_solutions: bool = False,
    wait: bool = False,
) -> None:
    qid = str(question_id)
    if include_all_solutions:
        keys = [key for key in set([*_tasks.keys(), *_states.keys()]) if key.startswith(f"{qid}:")]
    else:
        keys = [_job_key(question_id, solution_id)]

    for key in keys:
        task = _tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
            if wait:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    log.warning("cancelled answer job %s exited with an error", key)
        _states.pop(key, None)


def _parse_uuid(value: object) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _serialize_viz_row(row: VisualizationRow) -> dict | None:
    if str(getattr(row, "engine", "") or "").lower() != "geogebra":
        return None
    return {
        "id": row.viz_ref,
        "title_cn": row.title,
        "caption_cn": row.caption,
        "learning_goal": row.learning_goal,
        "interactive_hints": list(getattr(row, "interactive_hints_json", None) or []),
        "helpers_used": list(row.helpers_used_json or []),
        "engine": "geogebra",
        "spec_json": getattr(row, "spec_json", None),
        "execution_payload": getattr(row, "execution_payload_json", None),
        "degraded": bool(getattr(row, "degraded", False)),
    }

def _build_visualization_row(
    *,
    question_id: uuid.UUID,
    spec: VisualizationSpec,
    generated: GeoGebraExecutionPayload | None,
) -> VisualizationRow:
    if generated is not None:
        execution_payload = generated.model_dump(mode="json")
        execution_payload["__meta"] = {
            "validation_status": "static_passed",
            "runtime_status": "unknown",
            "static_validation_mode": "static",
            "stage2_llm_retry_count": 0,
            "partial_render_allowed": True,
        }
        return VisualizationRow(
            question_id=question_id,
            viz_ref=spec.id,
            title=generated.title,
            caption=spec.mathematical_claim_being_shown,
            learning_goal=spec.pedagogical_purpose,
            interactive_hints_json=[],
            helpers_used_json=[],
            engine="geogebra",
            jsx_code="",
            spec_json=spec.model_dump(mode="json"),
            execution_payload_json=execution_payload,
            degraded=False,
            ggb_commands_json=[],
            ggb_settings_json=None,
            params_json=[],
            animation_json=None,
        )
    return VisualizationRow(
        question_id=question_id,
        viz_ref=spec.id,
        title=spec.title,
        caption=spec.mathematical_claim_being_shown,
        learning_goal=spec.pedagogical_purpose,
        interactive_hints_json=[],
        helpers_used_json=[],
        engine="geogebra",
        jsx_code="",
        spec_json=spec.model_dump(mode="json"),
        execution_payload_json=None,
        degraded=True,
        ggb_commands_json=[],
        ggb_settings_json=None,
        params_json=[],
        animation_json=None,
    )


def _ordered_visualization_candidates(
    *,
    bundle,
    selected_spec: VisualizationSpec,
    min_stability_score: int = 80,
) -> list[VisualizationSpec]:
    candidates: list[VisualizationSpec] = [selected_spec]
    for item in sorted(
        bundle.visualizations,
        key=lambda spec: (
            spec.priority,
            -spec.renderability_assessment.implementation_stability_score,
        ),
    ):
        if item.id == selected_spec.id:
            continue
        if item.renderability_assessment.overall_readiness not in {"ready", "mostly_ready"}:
            continue
        if item.renderability_assessment.implementation_stability_score < min_stability_score:
            continue
        candidates.append(item)
    return candidates


def _friendly_llm_failure(message: str, *, failed_stage: str | None) -> dict:
    stage = failed_stage or "llm"
    timeout_s = _TIMEOUT_BY_STAGE.get(stage)
    lowered = message.lower()
    stage_label = str(_STAGE_META.get(stage, {}).get("label") or stage)
    if "timeout" in lowered:
        friendly = (
            f"{settings.active_llm_provider_label} 在“{stage_label}”阶段超时"
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
            f"{settings.active_llm_provider_label} 在“{stage_label}”阶段暂时繁忙。"
            f"后端已自动重试 {settings.llm.max_retries} 次，但服务仍未恢复。"
        )
        hint = (
            f"这通常是 {settings.active_llm_provider_label} 服务端瞬时高负载，不是题目内容错误。"
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
    if any(
        marker in lowered
        for marker in (
            "access denied",
            "not authorized",
            "permission denied",
            "permissiondenied",
            "unauthorized",
        )
    ):
        return {
            "kind": "provider_permission",
            "failed_stage": stage,
            "message": (
                f"{settings.active_llm_provider_label} 在“{stage_label}”阶段拒绝了当前"
                "模型或账号权限。"
            ),
            "raw_message": message,
            "hint": (
                "如果发生在“建立索引”阶段，请检查 EMB_URL、EMB_API_KEY、"
                "[embedding].provider、[embedding].model、[embedding].dimensions "
                "是否与当前网关支持的 embedding 服务一致；修改后重启后端。"
            ),
        }
    return {
        "kind": "llm_error",
        "failed_stage": stage,
        "message": message,
        "raw_message": message,
    }


def _is_failed_prompt_log_status(status: object) -> bool:
    value = str(status or "").strip().lower()
    return bool(value) and value not in {"ok", "repaired"}


def _is_visualization_prompt_log_row(row: dict) -> bool:
    task = str(row.get("task") or "").strip().lower()
    if task in {"vizplanner", "vizitem", "vizcoder", "vizspec", "geogebra_codegen"}:
        return True
    phase_description = str(row.get("phase_description") or "")
    return "可视化" in phase_description or "GeoGebra" in phase_description


def _latest_failed_visualization_phase_description_sync(
    question_id: uuid.UUID,
    *,
    solution_id: uuid.UUID | None = None,
) -> str | None:
    path = Path(settings.storage.llm_prompt_log_file)
    if not path.exists():
        return None

    latest_match: dict | None = None
    question_id_str = str(question_id)
    solution_id_str = str(solution_id) if solution_id is not None else None
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("question_id") or "") != question_id_str:
                continue
            if solution_id_str is not None and str(row.get("solution_id") or "") != solution_id_str:
                continue
            if not _is_visualization_prompt_log_row(row):
                continue
            latest_match = row

    if latest_match is None or not _is_failed_prompt_log_status(latest_match.get("status")):
        return None
    phase_description = str(latest_match.get("phase_description") or "").strip()
    return phase_description or None


async def _latest_failed_visualization_phase_description(
    question_id: uuid.UUID,
    *,
    solution_id: uuid.UUID | None = None,
) -> str | None:
    return await asyncio.to_thread(
        _latest_failed_visualization_phase_description_sync,
        question_id,
        solution_id=solution_id,
    )


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
            "total_calls": _TOTAL_CALLS,
            "label": str(meta["label"]),
            "description": str(meta["description"]),
            "solution_id": str(solution_id) if solution_id else None,
        },
        clear_prior_status=True,
    )


def _solver_progress_message(section: str, payload: dict[str, Any] | None = None) -> str | None:
    row = payload or {}
    if section == "question_understanding":
        return "正在生成解答：已完成题目理解。"
    if section == "key_points_of_question":
        return "正在生成解答：已提炼题目关键点。"
    if section == "solution_step":
        step_index = row.get("step_index")
        if isinstance(step_index, int):
            return f"正在生成解答：已输出第 {step_index} 步。"
        return "正在生成解答：正在输出分步解答。"
    if section == "key_points_of_answer":
        return "正在生成解答：已整理答案关键点。"
    if section == "method_pattern":
        return "正在生成解答：已生成方法模式。"
    if section == "similar_questions":
        return "正在生成解答：已生成同类题目。"
    if section == "knowledge_points":
        return "正在生成解答：已整理知识点。"
    if section == "self_check":
        return "正在生成解答：已生成自我检查。"
    return None


def _solver_heartbeat_message(elapsed_s: int) -> str:
    return f"正在生成解答：{settings.active_llm_provider_label} 正在推理，已等待约 {elapsed_s} 秒。"


async def _update_solver_progress_status(
    question_id: uuid.UUID,
    *,
    solution_id: uuid.UUID | None,
    section: str,
    payload: dict[str, Any] | None = None,
) -> None:
    key = _job_key(question_id, solution_id)
    state = _states.get(key)
    if state is None or state.stage != "solving" or state.done:
        return
    message = _solver_progress_message(section, payload)
    if not message or message == state.message:
        return
    state.message = message
    await _append_section(
        question_id,
        section="status",
        payload={
            "stage": "solving",
            "message": message,
            "call_index": state.call_index,
            "total_calls": _TOTAL_CALLS,
            "label": state.label,
            "description": _STAGE_META["solving"]["description"],
            "progress_section": section,
            "solution_id": str(solution_id) if solution_id else None,
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
    failed_stage = last.stage if stage == "llm" and last else stage
    payload = _friendly_llm_failure(
        message,
        failed_stage=failed_stage,
    )
    public_message = str(payload.get("message") or message)
    if failed_stage == "visualizing":
        latest_phase_description = await _latest_failed_visualization_phase_description(
            question_id,
            solution_id=solution_id,
        )
        if latest_phase_description:
            public_message = latest_phase_description
    _states[key] = JobState(
        question_id=str(question_id),
        solution_id=str(solution_id) if solution_id else None,
        stage=last.stage if last else stage,
        call_index=last.call_index if last else 0,
        label=last.label if last else stage,
        message=public_message,
        done=True,
        error=public_message,
    )
    await _set_question_status(question_id, "error")
    # Also update the solution status so /resume returns "error" for the
    # solution path (not just the question-level status).
    if solution_id is not None:
        try:
            async with session_scope() as session:
                from app.db.models import QuestionSolution
                sol = await session.get(QuestionSolution, solution_id)
                if sol is not None:
                    sol.status = "error"
                    await session.flush()
        except Exception:  # noqa: BLE001
            log.warning("failed to update solution %s status to error", solution_id)
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
            "message": public_message,
            "call_index": last.call_index if last else 0,
            "total_calls": _TOTAL_CALLS,
            "label": last.label if last else stage,
            "kind": payload.get("kind"),
            "hint": payload.get("hint"),
            "solution_id": str(solution_id) if solution_id else None,
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
            "total_calls": _TOTAL_CALLS,
            "label": str(meta["label"]),
            "description": str(meta["description"]),
            "needs_confirmation": True,
            "solution_id": str(solution_id) if solution_id else None,
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
                message=f"正在调用 {settings.active_llm_provider_label} 生成完整教学型答案，复杂题可能需要几十秒。",
                solution_id=solution_id,
            )
            summary: dict | None = None
            solver_progress_seen = asyncio.Event()
            solver_stop_heartbeat = asyncio.Event()

            async def _solver_heartbeat() -> None:
                elapsed_s = 0
                while True:
                    try:
                        await asyncio.wait_for(solver_stop_heartbeat.wait(), timeout=15)
                        return
                    except TimeoutError:
                        pass
                    if solver_progress_seen.is_set():
                        return
                    elapsed_s += 15
                    key = _job_key(question_id, solution_id)
                    state = _states.get(key)
                    if state is None or state.stage != "solving" or state.done:
                        return
                    state.message = _solver_heartbeat_message(elapsed_s)
                    await _append_section(
                        question_id,
                        section="status",
                        payload={
                            "stage": "solving",
                            "message": state.message,
                            "call_index": state.call_index,
                            "total_calls": _TOTAL_CALLS,
                            "label": state.label,
                            "description": _STAGE_META["solving"]["description"],
                            "heartbeat": True,
                            "wait_elapsed_s": elapsed_s,
                            "solution_id": str(solution_id),
                        },
                        clear_prior_status=True,
                    )

            heartbeat_task = asyncio.create_task(_solver_heartbeat())
            async with session_scope() as session:
                try:
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
                        solution_id=solution_id,
                        user_guidance=user_guidance,
                    ):
                        solver_progress_seen.set()
                        # Persist each streamed section in its own transaction
                        # so the polling /resume endpoint sees progress while
                        # The LLM is still generating later sections. The
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
                        try:
                            await _update_solver_progress_status(
                                question_id,
                                solution_id=solution_id,
                                section=ev.name,
                                payload=ev.data if isinstance(ev.data, dict) else None,
                            )
                        except Exception:  # noqa: BLE001
                            log.exception(
                                "solver progress status update failed for %s/%s",
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
                finally:
                    solver_stop_heartbeat.set()
                    await heartbeat_task
            assert summary is not None
            await _mark_stage_ready_for_review(
                question_id,
                stage="solving",
                summary=summary,
                refs={"question_id": str(question_id), "solution_id": str(solution_id)},
                message=f"{settings.active_llm_provider_label} Solver 已完成。请先人工确认解答，再进入下一阶段。",
                solution_id=solution_id,
            )
        elif stage == "visualizing":
            await _set_stage(
                question_id,
                stage="visualizing",
                message="可视化阶段 Stage 1/2：正在规划候选可视化规格…",
                solution_id=solution_id,
            )
            rows: list[VisualizationRow] = []
            review_summary: dict | None = None
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
                bundle = await generate_visualization_spec_bundle(
                    session,
                    question_id=question_id,
                    llm=llm,
                    solution_id=solution_id,
                    teaching_preference=user_guidance,
                )
                selected_spec = select_recommended_visualization(bundle)
                await persist_visualization_spec_bundle(
                    session,
                    solution=solution,
                    bundle=bundle,
                    selected_spec=selected_spec,
                )
                await _set_stage(
                    question_id,
                    stage="visualizing",
                    message="可视化阶段 Stage 2/2：正在分别生成并校验 3 个 GeoGebra 可视化…",
                    solution_id=solution_id,
                )
                primary_spec = selected_spec
                target_specs = sorted(
                    bundle.visualizations,
                    key=lambda spec: (
                        spec.priority,
                        -spec.renderability_assessment.implementation_stability_score,
                    ),
                )[:3]
                attempt_errors: dict[str, str] = {}
                persisted_viz_refs: list[str] = []
                for index, candidate_spec in enumerate(target_specs, start=1):
                    await _set_stage(
                        question_id,
                        stage="visualizing",
                        message=(
                            "可视化阶段 Stage 2/2：正在生成并校验 "
                            f"{index}/{len(target_specs)} 个 GeoGebra 可视化…"
                        ),
                        solution_id=solution_id,
                    )
                    geogebra = await generate_geogebra_visualization_or_fallback(
                        llm=llm,
                        spec=candidate_spec,
                        question_id=str(question_id),
                        solution_id=str(solution_id),
                    )
                    if geogebra.execution_payload is None:
                        attempt_errors[candidate_spec.id] = (
                            geogebra.error_summary or "GeoGebra codegen failed"
                        )
                    generated_payload = geogebra.execution_payload
                    viz_row = _build_visualization_row(
                        question_id=question_id,
                        spec=candidate_spec,
                        generated=generated_payload,
                    )
                    session.add(viz_row)
                    await session.flush()
                    persisted_viz_refs.append(candidate_spec.id)
                    await log_visual_action(
                        source="backend",
                        phase="persist",
                        action="visualization.row_persisted",
                        status="ok",
                        question_id=str(question_id),
                        solution_id=str(solution_id),
                        visualization_id=candidate_spec.id,
                        engine=viz_row.engine,
                        component="answer_job_service",
                        details={
                            "row_id": str(viz_row.id),
                            "execution_mode": (
                                (viz_row.execution_payload_json or {}).get("execution_mode")
                                if viz_row.execution_payload_json else None
                            ),
                            "command_count": len((viz_row.execution_payload_json or {}).get("commands") or []),
                            "spec_only": bool(viz_row.degraded),
                            "generation_mode": "per_visualization",
                        },
                    )
                rows = list((await session.execute(
                    select(VisualizationRow)
                    .where(VisualizationRow.question_id == question_id)
                    .order_by(VisualizationRow.created_at)
                )).scalars().all())
                if not rows:
                    raise RuntimeError("visualizing produced no valid visualizations")
                await update_solution_visualizations(
                    session,
                    solution=solution,
                    visualizations=[
                        payload
                        for row in rows
                        if (payload := _serialize_viz_row(row)) is not None
                    ],
                )
                solution.status = review_question_status("visualizing")
                review_summary = {
                    **summarize_visualization_plan(solution.visualization_plan_json),
                    **summarize_visualizations(rows),
                    "primary_visualization_id": primary_spec.id,
                    "rendered_visualization_id": persisted_viz_refs[0] if persisted_viz_refs else None,
                    "rendered_visualization_ids": persisted_viz_refs,
                    "attempted_visualization_ids": [item.id for item in target_specs],
                    "visualization_fallback_used": any(row.degraded for row in rows),
                    "attempt_errors": attempt_errors,
                }
            assert review_summary is not None
            await _mark_stage_ready_for_review(
                question_id,
                stage="visualizing",
                summary=review_summary,
                refs={
                    "question_id": str(question_id),
                    "solution_id": str(solution_id),
                    "visualization_ids": [str(row.id) for row in rows],
                },
                message="可视化规格与代码已生成。请确认这一阶段产物后再进入索引。",
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
                        embedding=build_dense_embedder(
                            prompt_context=PromptLogContext(
                                phase_description="建立索引",
                                question_id=str(question_id),
                                solution_id=str(solution_id),
                            ),
                        ),
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
        if isinstance(e, TransientLLMError):
            log.warning("answer job transient llm failure: %s", e)
        else:
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
    force: bool = False,
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
    if existing is not None and not existing.done() and force:
        await clear_answer_job_state(
            question_id,
            solution_id=resolved_solution_id,
            wait=True,
        )
        existing = None
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
        message=f"等待开始 {settings.active_llm_provider_label} {int(meta['call_index'])}/{_TOTAL_CALLS} · {str(meta['label'])}",
    )
    await _append_section(
        question_id,
        section="status",
        payload={
            "stage": stage,
            "message": f"等待开始 {settings.active_llm_provider_label} {int(meta['call_index'])}/{_TOTAL_CALLS} · {str(meta['label'])}",
            "call_index": int(meta["call_index"]),
            "total_calls": _TOTAL_CALLS,
            "label": "等待开始",
            "description": str(meta["description"]),
            "solution_id": str(resolved_solution_id),
        },
        clear_prior_status=True,
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
                    "call_index": _TOTAL_CALLS,
                    "total_calls": _TOTAL_CALLS,
                    "label": "全部完成",
                    "solution_id": str(resolved_solution_id) if resolved_solution_id else None,
                },
                clear_prior_status=True,
            )
            final_key = _job_key(question_id, resolved_solution_id)
            _states[final_key] = JobState(
                question_id=qid,
                solution_id=str(resolved_solution_id) if resolved_solution_id else None,
                stage="done",
                call_index=_TOTAL_CALLS,
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


def _state_from_status_payload(
    question_id: uuid.UUID,
    payload: dict | None,
    *,
    solution_id: uuid.UUID | None,
) -> JobState | None:
    if not payload:
        return None
    payload_solution_id = payload.get("solution_id")
    if solution_id is not None and payload_solution_id not in (None, "", str(solution_id)):
        return None
    stage = str(
        payload.get("failed_stage")
        or payload.get("review_stage")
        or payload.get("stage")
        or ""
    )
    persisted_stage = str(payload.get("stage") or "")
    needs_confirmation = bool(payload.get("needs_confirmation"))
    done = persisted_stage in {"done", "error"} or needs_confirmation
    error = str(payload.get("message") or "") if persisted_stage == "error" else None
    return JobState(
        question_id=str(question_id),
        solution_id=str(solution_id) if solution_id else None,
        stage=stage,
        call_index=int(payload.get("call_index") or 0),
        total_calls=int(payload.get("total_calls") or _TOTAL_CALLS),
        label=str(payload.get("label") or ""),
        message=str(payload.get("message") or ""),
        done=done,
        error=error,
    )


def _should_recover_persisted_job(payload: dict | None, state: JobState | None) -> bool:
    if not payload or state is None or state.done:
        return False
    if payload.get("needs_confirmation"):
        return False
    persisted_stage = str(payload.get("stage") or "")
    if persisted_stage in {"", "done", "error"}:
        return False
    return state.stage in _STAGE_META


async def _load_persisted_job_state(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    solution_id: uuid.UUID | None,
) -> JobState | None:
    rows = (await session.execute(
        select(AnswerPackageSection)
        .where(AnswerPackageSection.question_id == question_id)
        .where(AnswerPackageSection.section == "status")
        .order_by(AnswerPackageSection.created_at.desc())
    )).scalars().all()
    for row in rows:
        state = _state_from_status_payload(
            question_id,
            row.payload_json,
            solution_id=solution_id,
        )
        if state is not None:
            return state
    return None


async def recover_inflight_answer_jobs() -> int:
    """Re-enqueue persisted in-flight jobs after a process restart."""
    candidates: list[tuple[uuid.UUID, uuid.UUID | None, str]] = []
    seen: set[str] = set()

    async with session_scope() as session:
        rows = (await session.execute(
            select(AnswerPackageSection)
            .where(AnswerPackageSection.section == "status")
            .order_by(AnswerPackageSection.created_at.desc())
        )).scalars().all()

        for row in rows:
            payload = dict(row.payload_json or {})
            solution_id = _parse_uuid(payload.get("solution_id"))
            key = _job_key(row.question_id, solution_id)
            if key in seen:
                continue
            seen.add(key)
            state = _state_from_status_payload(
                row.question_id,
                payload,
                solution_id=solution_id,
            )
            if not _should_recover_persisted_job(payload, state):
                continue
            candidates.append((row.question_id, solution_id, state.stage))

    recovered = 0
    for question_id, solution_id, stage in reversed(candidates):
        key = _job_key(question_id, solution_id)
        existing = _tasks.get(key)
        if existing is not None and not existing.done():
            continue
        try:
            result = await start_answer_job(
                question_id,
                from_stage=stage,
                solution_id=solution_id,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "failed to recover answer job %s/%s at stage %s",
                question_id,
                solution_id,
                stage,
            )
            continue
        if result.get("state") in {"started", "running"}:
            recovered += 1
            log.info(
                "recovered answer job %s/%s at stage %s",
                question_id,
                solution_id,
                stage,
            )
    return recovered


async def get_answer_job_state(
    session: AsyncSession,
    question_id: uuid.UUID,
    solution_id: uuid.UUID | None = None,
) -> dict:
    qid = str(question_id)
    key = _job_key(question_id, solution_id)
    state = _states.get(key)
    task = _tasks.get(key)
    if state is None:
        state = await _load_persisted_job_state(
            session,
            question_id=question_id,
            solution_id=solution_id,
        )
    running = bool(task and not task.done() and not (state.done if state else False))
    return {
        "question_id": qid,
        "solution_id": str(solution_id) if solution_id else None,
        "running": running,
        "stage": state.stage if state else None,
        "done": state.done if state else False,
        "error": state.error if state else None,
        "call_index": state.call_index if state else 0,
        "total_calls": state.total_calls if state else _TOTAL_CALLS,
        "label": state.label if state else "",
        "message": state.message if state else "",
    }


def build_pipeline_snapshot(
    *,
    question_status: str,
    has_parsed: bool,
    has_answer: bool,
    has_visualization_plan: bool,
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
        elif key == "visualizing" and has_visualization_plan:
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
        "total_calls": _TOTAL_CALLS,
        "completed_calls": completed_calls,
        "visualizations_generated": visualizations_generated,
        "error": error,
        "steps": steps,
    }
