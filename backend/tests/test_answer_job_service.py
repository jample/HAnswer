"""Answer job error formatting tests."""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from sqlalchemy import delete

from app.config import settings
from app.db import models
from app.db.session import session_scope
from app.services import answer_job_service
from app.services.answer_job_service import _friendly_llm_failure, recover_inflight_answer_jobs


def test_friendly_llm_failure_formats_solver_timeout():
    payload = _friendly_llm_failure(
        f"timeout after {settings.llm.solver_timeout_s}s:",
        failed_stage="solving",
    )
    assert payload["kind"] == "timeout"
    assert payload["failed_stage"] == "solving"
    assert payload["timeout_s"] == settings.llm.solver_timeout_s
    assert "超时" in payload["message"]
    assert "solver_timeout_s" in payload["hint"]


def test_friendly_llm_failure_preserves_non_timeout_errors():
    payload = _friendly_llm_failure(
        "schema validation failed",
        failed_stage="solving",
    )
    assert payload["kind"] == "llm_error"
    assert payload["message"] == "schema validation failed"


def test_friendly_llm_failure_formats_service_unavailable():
    payload = _friendly_llm_failure(
        (
            "503 Service Unavailable. {'message': '{\"error\": {\"code\": 503, "
            "\"message\": \"This model is currently experiencing high demand.\", "
            "\"status\": \"UNAVAILABLE\"}}'}"
        ),
        failed_stage="solving",
    )
    assert payload["kind"] == "service_overloaded"
    assert payload["failed_stage"] == "solving"
    assert payload["retryable"] is True
    assert "暂时繁忙" in payload["message"]
    assert "等待 30 到 90 秒后重试" in payload["hint"]


@pytest.mark.asyncio
async def test_recover_inflight_answer_jobs_reenqueues_persisted_status(monkeypatch):
    marker = f"recover-job-{uuid.uuid4().hex[:8]}"
    question_id: uuid.UUID | None = None
    solution_id: uuid.UUID | None = None
    release = asyncio.Event()
    started = asyncio.Event()

    async def _stub_run_answer_job(
        question_id_arg: uuid.UUID,
        *,
        stage: str,
        solution_id: uuid.UUID,
    ) -> None:
        assert stage == "solving"
        started.set()
        await release.wait()

    monkeypatch.setattr(answer_job_service, "_run_answer_job", _stub_run_answer_job)

    async with session_scope() as s:
        question = models.Question(
            parsed_json={
                "subject": "math",
                "grade_band": "senior",
                "topic_path": [],
                "question_text": marker,
                "given": [],
                "find": [],
                "diagram_description": "",
                "difficulty": 2,
                "tags": [],
                "confidence": 0.9,
            },
            answer_package_json=None,
            subject="math",
            grade_band="senior",
            difficulty=2,
            dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
            seen_count=1,
            status="review_parse",
        )
        s.add(question)
        await s.flush()
        question_id = question.id

        solution = models.QuestionSolution(
            question_id=question_id,
            ordinal=1,
            title="解法 1",
            is_current=True,
            status="review_parse",
            answer_package_json=None,
            visualizations_json=[],
            sediment_json=None,
            stage_reviews_json={},
        )
        s.add(solution)
        await s.flush()
        solution_id = solution.id

        s.add(models.QuestionStageReview(
            question_id=question_id,
            stage="parsed",
            review_status="confirmed",
            artifact_version=1,
            run_count=1,
            summary_json={"question_text": marker},
            refs_json={"question_id": str(question_id)},
        ))
        s.add(models.AnswerPackageSection(
            question_id=question_id,
            section="status",
            payload_json={
                "stage": "solving",
                "message": "正在调用 Gemini 生成完整教学型答案，复杂题可能需要几十秒。",
                "call_index": 2,
                "total_calls": 4,
                "label": "生成解答",
                "solution_id": str(solution_id),
            },
        ))

    answer_job_service._states.clear()
    answer_job_service._tasks.clear()

    try:
        recovered = await recover_inflight_answer_jobs()
        assert recovered == 1
        await asyncio.wait_for(started.wait(), timeout=1)

        key = answer_job_service._job_key(question_id, solution_id)
        task = answer_job_service._tasks.get(key)
        assert task is not None
        assert task.done() is False
    finally:
        release.set()
        task = answer_job_service._tasks.pop(
            answer_job_service._job_key(question_id, solution_id),
            None,
        )
        if task is not None:
            await task
        answer_job_service._states.clear()
        async with session_scope() as s:
            if question_id is not None:
                await s.execute(delete(models.AnswerPackageSection).where(
                    models.AnswerPackageSection.question_id == question_id
                ))
                await s.execute(delete(models.QuestionStageReview).where(
                    models.QuestionStageReview.question_id == question_id
                ))
            if solution_id is not None:
                await s.execute(delete(models.QuestionSolution).where(
                    models.QuestionSolution.id == solution_id
                ))
            if question_id is not None:
                await s.execute(delete(models.Question).where(models.Question.id == question_id))
            await s.commit()
