"""Answer job error formatting tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

import pytest
from sqlalchemy import delete, select

from app import schemas
from app.config import settings
from app.db import models
from app.db.session import session_scope
from app.schemas import VisualizationSpecBundle
from app.services import answer_job_service
from app.services.answer_job_service import (
    _friendly_llm_failure,
    _latest_failed_visualization_phase_description_sync,
    _solver_progress_message,
    recover_inflight_answer_jobs,
)


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


def test_friendly_llm_failure_formats_provider_permission_errors():
    payload = _friendly_llm_failure(
        "Access denied: You are not authorized to use the 'text-embedding-3-large' model",
        failed_stage="indexing",
    )
    assert payload["kind"] == "provider_permission"
    assert payload["failed_stage"] == "indexing"
    assert "拒绝" in payload["message"]
    assert "EMB_URL" in payload["hint"]
    assert "EMB_API_KEY" in payload["hint"]
    assert "[embedding].provider" in payload["hint"]
    assert "[embedding].model" in payload["hint"]


def test_solver_progress_message_formats_solution_step():
    assert _solver_progress_message("question_understanding", {}) == "正在生成解答：已完成题目理解。"
    assert _solver_progress_message("solution_step", {"step_index": 3}) == "正在生成解答：已输出第 3 步。"
    assert _solver_progress_message("unknown", {}) is None


def test_latest_failed_visualization_phase_description_uses_latest_failed_row(tmp_path, monkeypatch):
    log_path = tmp_path / "llm_prompts.jsonl"
    question_id = uuid.uuid4()
    solution_id = uuid.uuid4()
    rows = [
        {
            "question_id": str(question_id),
            "solution_id": str(solution_id),
            "task": "vizitem",
            "phase_description": "生成可视化",
            "status": "ok",
        },
        {
            "question_id": str(question_id),
            "solution_id": str(solution_id),
            "task": "vizcoder",
            "phase_description": "生成整组可视化（批量回退）",
            "status": "validation_error",
        },
    ]
    log_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings.storage, "llm_prompt_log_file", str(log_path))

    phase_description = _latest_failed_visualization_phase_description_sync(
        question_id,
        solution_id=solution_id,
    )

    assert phase_description == "生成整组可视化（批量回退）"


def test_latest_failed_visualization_phase_description_ignores_latest_success(tmp_path, monkeypatch):
    log_path = tmp_path / "llm_prompts.jsonl"
    question_id = uuid.uuid4()
    solution_id = uuid.uuid4()
    rows = [
        {
            "question_id": str(question_id),
            "solution_id": str(solution_id),
            "task": "vizcoder",
            "phase_description": "生成整组可视化（批量回退）",
            "status": "error",
        },
        {
            "question_id": str(question_id),
            "solution_id": str(solution_id),
            "task": "vizitem",
            "phase_description": "生成可视化",
            "status": "ok",
        },
    ]
    log_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings.storage, "llm_prompt_log_file", str(log_path))

    phase_description = _latest_failed_visualization_phase_description_sync(
        question_id,
        solution_id=solution_id,
    )

    assert phase_description is None


def test_latest_failed_visualization_phase_description_ignores_haviznew_legacy_jsxgraph_task(tmp_path, monkeypatch):
    log_path = tmp_path / "llm_prompts.jsonl"
    question_id = uuid.uuid4()
    solution_id = uuid.uuid4()
    rows = [
        {
            "question_id": str(question_id),
            "solution_id": str(solution_id),
            "task": "jsxgraph_codegen",
            "phase_description": "生成 JSXGraph 代码",
            "status": "validation_error",
        },
    ]
    log_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings.storage, "llm_prompt_log_file", str(log_path))

    phase_description = _latest_failed_visualization_phase_description_sync(
        question_id,
        solution_id=solution_id,
    )

    assert phase_description is None


def test_latest_failed_visualization_phase_description_accepts_geogebra_stage2_task(tmp_path, monkeypatch):
    log_path = tmp_path / "llm_prompts.jsonl"
    question_id = uuid.uuid4()
    solution_id = uuid.uuid4()
    rows = [
        {
            "question_id": str(question_id),
            "solution_id": str(solution_id),
            "task": "geogebra_codegen",
            "phase_description": "生成 GeoGebra 指令",
            "status": "validation_error",
        },
    ]
    log_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings.storage, "llm_prompt_log_file", str(log_path))

    phase_description = _latest_failed_visualization_phase_description_sync(
        question_id,
        solution_id=solution_id,
    )

    assert phase_description == "生成 GeoGebra 指令"


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
async def test_start_answer_job_force_cancels_existing_task(monkeypatch):
    marker = f"force-restart-{uuid.uuid4().hex[:8]}"
    question_id: uuid.UUID | None = None
    solution_id: uuid.UUID | None = None
    first_started = asyncio.Event()
    first_cancelled = asyncio.Event()
    second_started = asyncio.Event()
    release_second = asyncio.Event()
    invocation_count = 0

    async def _stub_run_answer_job(
        question_id_arg: uuid.UUID,
        *,
        stage: str,
        solution_id: uuid.UUID,
    ) -> None:
        nonlocal invocation_count
        invocation_count += 1
        assert question_id_arg == question_id
        assert stage == "visualizing"
        if invocation_count == 1:
            first_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                first_cancelled.set()
                raise
        else:
            second_started.set()
            await release_second.wait()

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
            answer_package_json={
                "question_understanding": {
                    "restated_question": marker,
                    "givens": [],
                    "unknowns": [],
                    "implicit_conditions": [],
                },
                "key_points_of_question": ["k"],
                "solution_steps": [],
                "key_points_of_answer": ["a"],
                "method_pattern": {
                    "pattern_id_suggested": "p1",
                    "name_cn": "图像法",
                    "when_to_use": "求最值",
                    "general_procedure": ["画图"],
                    "pitfalls": [],
                },
                "similar_questions": [
                    {"statement": "s1", "answer_outline": "a1"},
                    {"statement": "s2", "answer_outline": "a2"},
                    {"statement": "s3", "answer_outline": "a3"},
                ],
                "knowledge_points": [{"node_ref": "kp:quad", "weight": 1.0}],
                "self_check": ["检查"],
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
                "solving": {
                    "stage": "solving",
                    "review_status": "confirmed",
                    "artifact_version": 1,
                    "run_count": 1,
                    "summary": {},
                    "refs": {},
                    "review_note": "",
                    "reviewed_at": None,
                    "updated_at": None,
                },
            },
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

    answer_job_service._states.clear()
    answer_job_service._tasks.clear()

    try:
        first = await answer_job_service.start_answer_job(
            question_id,
            from_stage="visualizing",
            solution_id=solution_id,
        )
        assert first["state"] == "started"
        await asyncio.wait_for(first_started.wait(), timeout=1)

        key = answer_job_service._job_key(question_id, solution_id)
        first_task = answer_job_service._tasks.get(key)
        assert first_task is not None

        second = await answer_job_service.start_answer_job(
            question_id,
            from_stage="visualizing",
            solution_id=solution_id,
            force=True,
        )
        assert second["state"] == "started"
        await asyncio.wait_for(first_cancelled.wait(), timeout=1)
        await asyncio.wait_for(second_started.wait(), timeout=1)

        second_task = answer_job_service._tasks.get(key)
        assert second_task is not None
        assert second_task is not first_task
        assert second_task.done() is False
    finally:
        release_second.set()
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
async def test_visualizing_stage_uses_haviznew_spec_and_codegen(monkeypatch):
    marker = f"viz-haviznew-{uuid.uuid4().hex[:8]}"
    question_id: uuid.UUID | None = None
    solution_id: uuid.UUID | None = None

    async def _stub_generate_bundle(*args, **kwargs):
        payload = {
            "task_summary": {
                "source_math_topic": "二次函数",
                "source_problem_type": "最值问题",
                "core_learning_goal": "看懂最值与图像的关系",
            },
            "visualizations": [
                {
                    "id": "viz-1",
                    "title": "交点示意",
                    "priority": 1,
                    "teaching_value": "high",
                    "recommended": True,
                    "visualization_type": "function_plot",
                    "preferred_geogebra_app": "graphing",
                    "pedagogical_purpose": "帮助学生把最值和函数图像联系起来",
                    "when_to_use": "二次函数最值题",
                    "mathematical_claim_being_shown": "抛物线顶点决定最值",
                    "student_observation_goal": ["观察顶点位置与最值的关系"],
                    "source_dependency": {
                        "depends_on_solution_steps": [],
                        "depends_on_assumptions": [],
                    },
                    "math_definition": {
                        "objects": [
                            {
                                "name": "f",
                                "type": "function_graph",
                                "definition": "二次函数图像",
                                "role": "主对象",
                                "must_exist_before_animation": True,
                            }
                        ],
                        "relations": [],
                        "constraints": [],
                        "key_formulas": [],
                    },
                    "geogebra_plan": {
                        "object_creation_strategy": "command_only",
                        "recommended_command_families": ["function"],
                        "requires_slider": True,
                        "requires_trace": False,
                        "requires_locus": False,
                        "requires_region_shading": False,
                        "requires_sequence_or_list_generation": False,
                        "requires_minimal_script": False,
                        "script_reason_if_needed": "",
                    },
                    "visual_design": {
                        "coordinate_system": {
                            "needed": True,
                            "type": "cartesian_2d",
                            "suggested_viewport": {"xmin": -5, "xmax": 5, "ymin": -5, "ymax": 5},
                            "reason": "展示抛物线和顶点",
                        },
                        "visible_objects": ["f"],
                        "highlighted_objects": ["f"],
                        "optional_hidden_helper_objects": [],
                        "labels_to_show": [],
                        "measurements_to_show": [],
                        "region_or_trace_display": {
                            "needed": False,
                            "type": "none",
                            "description": "静态函数图像",
                        },
                    },
                    "interaction_and_animation": {
                        "has_animation": True,
                        "animation_driver": "slider",
                        "animation_description": "通过系数滑块观察顶点变化",
                        "parameters": [
                            {
                                "name": "a",
                                "type": "number",
                                "range": {"min": -3, "max": 3, "step": 0.5},
                                "default_value": 1,
                                "meaning": "二次项系数 a",
                            }
                        ],
                        "user_interactions": [
                            {
                                "interaction_type": "move_slider",
                                "target": "a",
                                "purpose": "观察开口方向和顶点变化",
                            }
                        ],
                        "animation_sequence": ["移动滑块 a", "观察顶点位置变化"],
                        "stopping_condition_or_final_state": "滑块停在当前值",
                    },
                    "expected_result": {
                        "final_visual_outcome": "展示抛物线与顶点",
                        "mathematical_conclusion_visible_to_student": "顶点决定最值",
                        "common_misinterpretations_to_avoid": [],
                    },
                    "implementation_guidance": {
                        "preferred_rendering_strategy": "画函数图像并标出顶点",
                        "preferred_geogebra_object_naming_style": "Use short English labels such as a, f, V",
                        "simplifications_allowed": [],
                        "things_that_must_not_be_omitted": ["顶点"],
                        "things_that_must_not_be_invented": [],
                        "fallback_if_animation_is_too_complex": "退化为静态图",
                    },
                    "consistency_checks": [],
                    "ambiguities": [],
                    "renderability_assessment": {
                        "clarity_score": 90,
                        "math_completeness_score": 90,
                        "implementation_stability_score": 90,
                        "overall_readiness": "ready",
                    },
                }
            ],
        }
        for idx in (2, 3):
            item = json.loads(json.dumps(payload["visualizations"][0], ensure_ascii=False))
            item["id"] = f"viz-{idx}"
            item["title"] = f"辅助示意 {idx}"
            item["priority"] = idx
            item["recommended"] = False
            item["pedagogical_purpose"] = f"补充展示第 {idx} 个关键观察点"
            payload["visualizations"].append(item)
        return VisualizationSpecBundle.model_validate(payload)

    geogebra_calls: list[str] = []

    async def _stub_geogebra_codegen(**kwargs):
        from app.services.geogebra_codegen_service import GeoGebraCodegenResult
        spec = kwargs["spec"]
        geogebra_calls.append(spec.id)
        idx = len(geogebra_calls)
        execution_payload = schemas.GeoGebraExecutionPayload.model_validate({
            "title": spec.title,
            "preferred_geogebra_app": "graphing",
            "execution_mode": "command_only",
            "math_meaning_summary": "通过滑块驱动抛物线变化并标出顶点。",
            "object_naming_convention": "Use short English labels such as a, f, V",
            "commands": [
                {"step": 1, "purpose": "[core] Create slider", "command": f"a{idx}=Slider(-3,3,0.5)"},
                {"step": 2, "purpose": "[core] Create quadratic", "command": f"f{idx}(x)=a{idx}*x^2"},
                {"step": 3, "purpose": "[core] Create vertex", "command": f"V{idx}=Extremum(f{idx})"},
            ],
            "property_commands": [
                {"step": 1, "purpose": "Highlight vertex", "command": f"SetPointSize(V{idx},6)"}
            ],
            "interaction_objects": [
                {"name": f"a{idx}", "type": "slider", "purpose": "Drive the quadratic coefficient"}
            ],
            "optional_script": {
                "needed": False,
                "script_type": "none",
                "reason": "",
                "target_object": "",
                "trigger": "none",
                "script_body": "",
            },
            "expected_created_objects": [
                {"name": f"a{idx}", "type": "slider_parameter", "role": "coefficient"},
                {"name": f"f{idx}", "type": "function_graph", "role": "curve"},
                {"name": f"V{idx}", "type": "point", "role": "vertex"},
            ],
            "consistency_checks": ["The vertex stays on the parabola as a changes."],
            "fallback_used": False,
            "fallback_reason": "",
            "implementation_notes": ["Command-only graphing payload."],
        })
        return GeoGebraCodegenResult(execution_payload=execution_payload)

    monkeypatch.setattr(answer_job_service, "get_llm_client", lambda: object())
    monkeypatch.setattr(answer_job_service, "generate_visualization_spec_bundle", _stub_generate_bundle)
    monkeypatch.setattr(answer_job_service, "select_recommended_visualization", lambda bundle: bundle.visualizations[0])
    monkeypatch.setattr(answer_job_service, "generate_geogebra_visualization_or_fallback", _stub_geogebra_codegen)

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
            stage_reviews_json={},
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
            assert solution.visualization_plan_json is not None
            assert geogebra_calls == ["viz-1", "viz-2", "viz-3"]
            assert len(solution.visualizations_json) == 3
            assert solution.visualizations_json[0]["id"] == "viz-1"
            assert solution.visualizations_json[0]["engine"] == "geogebra"
            assert solution.visualizations_json[0]["spec_json"]["id"] == "viz-1"
            assert solution.visualizations_json[0]["degraded"] is False
            assert solution.visualizations_json[0]["execution_payload"]["preferred_geogebra_app"] == "graphing"
            assert [row["command"] for row in solution.visualizations_json[0]["execution_payload"]["commands"]] == [
                "a1=Slider(-3,3,0.5)",
                "f1(x)=a1*x^2",
                "V1=Extremum(f1)",
            ]
            review = solution.stage_reviews_json["visualizing"]
            assert review["summary"]["candidate_count"] == 3
            assert review["summary"]["visualization_count"] == 3
            assert review["summary"]["rendered_visualization_ids"] == ["viz-1", "viz-2", "viz-3"]
            rows = (await s.execute(
                select(models.VisualizationRow).where(models.VisualizationRow.question_id == question_id)
            )).scalars().all()
            assert len(rows) == 3
            assert rows[0].degraded is False
            assert rows[0].execution_payload_json is not None
            assert rows[0].execution_payload_json["interaction_objects"] == [
                {"name": "a1", "type": "slider", "purpose": "Drive the quadratic coefficient"}
            ]
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
