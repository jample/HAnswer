"""Answer job error formatting tests."""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy import select

from app.config import settings
from app.db import models
from app.db.session import session_scope
from app.services import answer_job_service
from app.services.answer_job_service import _friendly_llm_failure, recover_inflight_answer_jobs
from app.services.llm_client import TransientLLMError
from app.services.solver_service import SSEEvent
from app.services.vizcoder_service import _persist_viz
from app.schemas.llm import Visualization


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


@pytest.mark.asyncio
async def test_visualizing_stage_reuses_existing_storyboard_on_transient_planner_failure(monkeypatch):
    marker = f"viz-fallback-{uuid.uuid4().hex[:8]}"
    question_id: uuid.UUID | None = None
    solution_id: uuid.UUID | None = None
    fallback_storyboard = {
        "theme_cn": "从交点到最值",
        "selection_rationale_cn": "选择三个关键跳跃",
        "symbol_map": [{"symbol": "A", "meaning_cn": "交点 A"}],
        "shared_params": [],
        "coverage_summary": [
            {
                "item_id": "viz-1",
                "summary_cn": "建立交点关系",
                "anchor_refs": [{"kind": "question_given", "ref": "given:0"}],
            }
        ],
        "sequence": ["viz-1", "viz-2", "viz-3"],
        "items": [
            {
                "id": "viz-1",
                "title_cn": "交点示意",
                "anchor_refs": [{"kind": "question_given", "ref": "given:0"}],
                "difficulty_reason_cn": "条件难映射",
                "student_confusion_risk": "high",
                "conceptual_jump_cn": "从题设到图像",
                "why_visualization_needed_cn": "帮助建立对象",
                "learning_goal_cn": "理解交点位置",
                "engine": "geogebra",
                "shared_symbols": ["A"],
                "shared_params": [],
                "depends_on": [],
                "caption_outline_cn": "对应 step 1",
                "geo_target_cn": "显示交点 A,B",
            },
            {
                "id": "viz-2",
                "title_cn": "顶点比较",
                "anchor_refs": [{"kind": "solution_step", "ref": "2"}],
                "difficulty_reason_cn": "顶点决定最值",
                "student_confusion_risk": "medium",
                "conceptual_jump_cn": "从交点到顶点",
                "why_visualization_needed_cn": "需要补出顶点",
                "learning_goal_cn": "理解顶点与最值",
                "engine": "geogebra",
                "shared_symbols": ["A"],
                "shared_params": [],
                "depends_on": ["viz-1"],
                "caption_outline_cn": "对应 step 2",
                "geo_target_cn": "显示顶点",
            },
            {
                "id": "viz-3",
                "title_cn": "结论回扣",
                "anchor_refs": [{"kind": "final_answer", "ref": "final_answer"}],
                "difficulty_reason_cn": "需要把观察变成答案",
                "student_confusion_risk": "medium",
                "conceptual_jump_cn": "从图像到结论",
                "why_visualization_needed_cn": "帮助回扣答案",
                "learning_goal_cn": "理解最终结论",
                "engine": "geogebra",
                "shared_symbols": ["A"],
                "shared_params": [],
                "depends_on": ["viz-2"],
                "caption_outline_cn": "对应 step 3",
                "geo_target_cn": "标出最终证据",
            },
        ],
    }

    async def _planner_failure(*args, **kwargs):
        raise TransientLLMError("503 UNAVAILABLE")

    async def _generate_from_storyboard(session, *, question_id, llm, storyboard, user_guidance=None):
        assert storyboard.theme_cn == fallback_storyboard["theme_cn"]
        viz = Visualization(
            id="viz-1",
            title_cn="交点示意",
            caption_cn="对应解答 step 1",
            learning_goal="理解交点位置",
            engine="geogebra",
            ggb_commands=["f(x)=x^2-1", "A=(-1,0)", "B=(1,0)"],
        )
        await _persist_viz(session, question_id, viz)
        yield SSEEvent("visualization", viz.model_dump(mode="json"))

    monkeypatch.setattr(answer_job_service, "get_llm_client", lambda: object())
    monkeypatch.setattr(answer_job_service, "plan_visualization_storyboard", _planner_failure)
    monkeypatch.setattr(answer_job_service, "generate_visualizations_from_storyboard", _generate_from_storyboard)

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
            answer_package_json={
                "question_understanding": {
                    "restated_question": "求最值",
                    "givens": [],
                    "unknowns": [],
                    "implicit_conditions": [],
                },
                "key_points_of_question": ["交点与顶点关系"],
                "solution_steps": [],
                "key_points_of_answer": ["先构造图像", "再看顶点"],
                "method_pattern": {
                    "pattern_id_suggested": "p1",
                    "name_cn": "图像法",
                    "when_to_use": "求最值",
                    "general_procedure": ["画图", "看顶点"],
                    "pitfalls": [],
                },
                "similar_questions": [
                    {"statement": "s1", "answer_outline": "a1"},
                    {"statement": "s2", "answer_outline": "a2"},
                    {"statement": "s3", "answer_outline": "a3"},
                ],
                "knowledge_points": [{"node_ref": "kp:quad", "weight": 1.0}],
                "self_check": ["检查顶点"],
            },
            subject="math",
            grade_band="senior",
            difficulty=2,
            dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
            seen_count=1,
            status="review_solve",
        )
        s.add(question)
        await s.flush()
        question_id = question.id

        solution = models.QuestionSolution(
            question_id=question_id,
            ordinal=1,
            title="解法 1",
            is_current=True,
            status="review_solve",
            answer_package_json=question.answer_package_json,
            visualizations_json=[],
            sediment_json=None,
            stage_reviews_json={
                "visualizing": {
                    "stage": "visualizing",
                    "review_status": "rejected",
                    "artifact_version": 1,
                    "run_count": 1,
                    "summary": {"visualization_count": 3},
                    "refs": {"storyboard": fallback_storyboard},
                    "review_note": "",
                    "reviewed_at": None,
                    "updated_at": None,
                }
            },
        )
        s.add(solution)
        await s.flush()
        solution_id = solution.id
        await s.commit()

    try:
        assert question_id is not None and solution_id is not None
        await answer_job_service._run_answer_job(
            question_id,
            stage="visualizing",
            solution_id=solution_id,
        )

        async with session_scope() as s:
            solution = await s.get(models.QuestionSolution, solution_id)
            assert solution is not None
            assert solution.visualizations_json
            assert solution.visualizations_json[0]["id"] == "viz-1"
            review = solution.stage_reviews_json["visualizing"]
            assert review["refs"]["storyboard"]["theme_cn"] == fallback_storyboard["theme_cn"]
            assert review["summary"]["storyboard_theme_cn"] == fallback_storyboard["theme_cn"]
            rows = (await s.execute(
                select(models.VisualizationRow).where(models.VisualizationRow.question_id == question_id)
            )).scalars().all()
            assert len(rows) == 1
    finally:
        async with session_scope() as s:
            if question_id is not None:
                await s.execute(delete(models.AnswerPackageSection).where(
                    models.AnswerPackageSection.question_id == question_id
                ))
                await s.execute(delete(models.QuestionStageReview).where(
                    models.QuestionStageReview.question_id == question_id
                ))
                await s.execute(delete(models.VisualizationRow).where(
                    models.VisualizationRow.question_id == question_id
                ))
            if solution_id is not None:
                await s.execute(delete(models.QuestionSolution).where(
                    models.QuestionSolution.id == solution_id
                ))
            if question_id is not None:
                await s.execute(delete(models.Question).where(models.Question.id == question_id))
            await s.commit()
