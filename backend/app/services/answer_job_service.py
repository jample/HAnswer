"""Background answer-job orchestration.

For long Gemini solves the browser should not own the entire request.
This module runs answer generation in a background task, persists stage
status to `answer_packages`, and lets the frontend poll `/resume`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import delete

from app.config import settings
from app.db import repo
from app.db.models import AnswerPackageSection
from app.db.models import AnswerPackageSection as AnswerSectionModel
from app.db.session import session_scope
from app.schemas import AnswerPackage
from app.services.embedding import build_dense_embedder
from app.services.llm_client import LLMError
from app.services.llm_deps import get_llm_client
from app.services.sediment_service import sediment
from app.services.solver_service import generate_answer
from app.services.sparse_encoder import get_sparse_encoder
from app.services.vector_store import get_vector_store
from app.services.vizcoder_service import generate_visualizations

log = logging.getLogger(__name__)


@dataclass
class JobState:
    question_id: str
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


def _friendly_llm_failure(message: str, *, failed_stage: str | None) -> dict:
    stage = failed_stage or "llm"
    timeout_s = _TIMEOUT_BY_STAGE.get(stage)
    lowered = message.lower()
    if "timeout" in lowered:
        stage_label = str(_STAGE_META.get(stage, {}).get("label") or stage)
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
                delete(AnswerSectionModel).where(
                    AnswerSectionModel.question_id == question_id,
                    AnswerSectionModel.section == "status",
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


async def _set_stage(question_id: uuid.UUID, *, stage: str, message: str) -> None:
    meta = _STAGE_META.get(stage, {"call_index": 0, "label": stage, "description": message})
    _states[str(question_id)] = JobState(
        question_id=str(question_id),
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


async def _append_error(question_id: uuid.UUID, *, stage: str, message: str) -> None:
    last = _states.get(str(question_id))
    payload = _friendly_llm_failure(
        message,
        failed_stage=last.stage if stage == "llm" and last else stage,
    )
    _states[str(question_id)] = JobState(
        question_id=str(question_id),
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


async def _run_answer_job(question_id: uuid.UUID) -> None:
    qid = str(question_id)
    llm = get_llm_client()
    vector_store = get_vector_store()
    try:
        await _set_stage(
            question_id,
            stage="solving",
            message="正在调用 Gemini 生成完整教学型答案，复杂题可能需要几十秒。",
        )
        async with session_scope() as session:
            async for _ in generate_answer(session, question_id=question_id, llm=llm):
                pass

        await _set_stage(
            question_id,
            stage="visualizing",
            message="答案已生成，正在补充可视化。",
        )
        async with session_scope() as session:
            async for ev in generate_visualizations(session, question_id=question_id, llm=llm):
                if ev.name == "error":
                    await _append_section(question_id, section="error", payload=ev.data)

        await _set_stage(
            question_id,
            stage="indexing",
            message="正在写入知识点、方法模式与检索索引。",
        )
        async with session_scope() as session:
            q = await repo.get_question(session, question_id)
            if q is not None and q.answer_package_json is not None:
                pkg = AnswerPackage.model_validate(q.answer_package_json)
                result = await sediment(
                    session,
                    question_id=question_id,
                    package=pkg,
                    embedding=build_dense_embedder(llm),
                    vector_store=vector_store,
                    sparse_encoder=get_sparse_encoder(),
                )
                await _append_section(
                    question_id,
                    section="sediment",
                    payload={
                        "pattern_id": str(result.pattern_id),
                        "kp_ids": [str(k) for k in result.kp_ids],
                        "near_dup_of": (
                            str(result.near_dup_of) if result.near_dup_of else None
                        ),
                    },
                )

        await _set_question_status(question_id, "answered")
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
        _states[qid] = JobState(
            question_id=qid,
            stage="done",
            call_index=4,
            label="全部完成",
            message="解答完成。",
            done=True,
        )
    except KeyError as e:
        log.exception("answer job question missing")
        await _append_error(question_id, stage="question", message=str(e))
    except LLMError as e:
        log.exception("answer job llm failure")
        await _append_error(question_id, stage="llm", message=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("answer job crashed")
        await _append_error(question_id, stage="job", message=str(e))
    finally:
        _tasks.pop(qid, None)


async def start_answer_job(question_id: uuid.UUID) -> dict:
    qid = str(question_id)
    existing = _tasks.get(qid)
    if existing is not None and not existing.done():
        state = _states.get(qid)
        return {
            "question_id": qid,
            "state": "running",
            "stage": state.stage if state else "solving",
        }

    async with session_scope() as session:
        q = await repo.get_question(session, question_id)
        if q is None:
            raise KeyError(f"question {question_id} not found")
        if q.answer_package_json is not None and q.status == "answered":
            return {"question_id": qid, "state": "complete"}
        await repo.clear_generated_content(session, question_id=question_id)
        q.answer_package_json = None
        q.status = "parsed"
        await session.flush()

    task = asyncio.create_task(_run_answer_job(question_id))
    _tasks[qid] = task
    _states[qid] = JobState(
        question_id=qid,
        stage="queued",
        call_index=1,
        label="等待开始",
        message="题面已解析，等待开始后续 3 次 Gemini 调用。",
    )
    return {"question_id": qid, "state": "started", "stage": "queued"}


def get_answer_job_state(question_id: uuid.UUID) -> dict:
    qid = str(question_id)
    state = _states.get(qid)
    task = _tasks.get(qid)
    return {
        "question_id": qid,
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
) -> dict:
    current_stage = (job_state or {}).get("stage") or question_status
    current_call = int((job_state or {}).get("call_index") or 0)
    error = (job_state or {}).get("error")
    steps: list[dict] = []
    for item in _CALL_STAGES:
        key = str(item["key"])
        call_index = int(item["call_index"])
        state = "pending"
        if key == "parsed":
            state = "done" if has_parsed else "pending"
        elif key == "solving":
            if has_answer or question_status in {"visualizing", "indexing", "answered"}:
                state = "done"
            elif current_stage == "solving":
                state = "active"
        elif key == "visualizing":
            if question_status in {"indexing", "answered"}:
                state = "done"
            elif current_stage == "visualizing":
                state = "active"
        elif key == "indexing":
            if question_status == "answered":
                state = "done"
            elif current_stage == "indexing":
                state = "active"

        if question_status == "error" and current_call == call_index:
            state = "error"

        steps.append({
            **item,
            "state": state,
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
