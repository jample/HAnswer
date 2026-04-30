from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import delete

from app import schemas
from app.db import models
from app.db.session import session_scope
from app.schemas import VisualizationSpecBundle
from app.services import answer_job_service
from app.services.geogebra_codegen_service import GeoGebraCodegenResult
from app.services.stage_review_service import next_stage


def _selected_vizspec_payload() -> dict:
    return {
        "id": "viz_1",
        "title": "Boundary distance",
        "priority": 1,
        "teaching_value": "high",
        "recommended": True,
        "visualization_type": "measurement_demo",
        "preferred_geogebra_app": "geometry",
        "pedagogical_purpose": "Show shortest boundary distance",
        "when_to_use": "When boundary distance matters",
        "mathematical_claim_being_shown": "Distance refers to boundary",
        "student_observation_goal": ["Observe the boundary reference"],
        "source_dependency": {"depends_on_solution_steps": [], "depends_on_assumptions": []},
        "math_definition": {
            "objects": [
                {
                    "name": "C",
                    "type": "circle_boundary",
                    "definition": "The reference boundary",
                    "role": "reference",
                    "must_exist_before_animation": True,
                }
            ],
            "relations": [],
            "constraints": [],
            "key_formulas": [],
        },
        "geogebra_plan": {
            "object_creation_strategy": "command_only",
            "recommended_command_families": ["geometry"],
            "requires_slider": False,
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
                "reason": "Keep the boundary and distance segment visible",
            },
            "visible_objects": ["C"],
            "highlighted_objects": ["C"],
            "optional_hidden_helper_objects": [],
            "labels_to_show": [],
            "measurements_to_show": ["d"],
            "region_or_trace_display": {
                "needed": False,
                "type": "boundary_only",
                "description": "Show only the boundary, not the filled disk",
            },
        },
        "interaction_and_animation": {
            "has_animation": False,
            "animation_driver": "none",
            "animation_description": "Static",
            "parameters": [],
            "user_interactions": [],
            "animation_sequence": [],
            "stopping_condition_or_final_state": "Static",
        },
        "expected_result": {
            "final_visual_outcome": "Boundary distance diagram",
            "mathematical_conclusion_visible_to_student": "Distance uses the boundary",
            "common_misinterpretations_to_avoid": [],
        },
        "implementation_guidance": {
            "preferred_rendering_strategy": "Static segment",
            "simplifications_allowed": [],
            "things_that_must_not_be_omitted": ["boundary"],
            "things_that_must_not_be_invented": ["disk fill"],
            "fallback_if_animation_is_too_complex": "Use a static plot",
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


@pytest.mark.asyncio
async def test_next_stage_moves_from_solving_to_visualizing():
    assert next_stage("solving") == "visualizing"
    assert next_stage("visualizing") == "indexing"


@pytest.mark.asyncio
async def test_start_answer_job_defaults_to_visualizing_after_solving_confirmed():
    marker = f"vizspec-stage-{uuid.uuid4().hex[:8]}"
    question_id: uuid.UUID | None = None
    solution_id: uuid.UUID | None = None

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
    original = answer_job_service._run_answer_job

    async def _stub_run_answer_job(*args, **kwargs):
        return None

    try:
        answer_job_service._run_answer_job = _stub_run_answer_job
        result = await answer_job_service.start_answer_job(question_id, solution_id=solution_id)
        assert result["state"] == "started"
        assert result["stage"] == "visualizing"
    finally:
        answer_job_service._run_answer_job = original
        await answer_job_service.clear_answer_job_state(question_id, solution_id=solution_id, wait=True)
        answer_job_service._states.clear()
        answer_job_service._tasks.clear()
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
async def test_visualizing_stage_uses_selected_vizspec(monkeypatch):
    marker = f"vizspec-render-{uuid.uuid4().hex[:8]}"
    question_id: uuid.UUID | None = None
    solution_id: uuid.UUID | None = None
    selected_spec = _selected_vizspec_payload()
    spec_2 = {**selected_spec, "id": "viz_2", "title": "Second key visual", "priority": 2, "recommended": False}
    spec_3 = {**selected_spec, "id": "viz_3", "title": "Third key visual", "priority": 3, "recommended": False}

    geogebra_calls: list[str] = []

    async def _stub_geogebra_codegen(**kwargs):
        spec = kwargs["spec"]
        geogebra_calls.append(spec.id)
        idx = len(geogebra_calls)
        return GeoGebraCodegenResult(
            execution_payload=schemas.GeoGebraExecutionPayload.model_validate({
                "title": spec.title,
                "preferred_geogebra_app": "geometry",
                "execution_mode": "command_only",
                "math_meaning_summary": "Show that the measured distance targets the boundary.",
                "object_naming_convention": "Use short English labels.",
                "commands": [
                    {"step": 1, "purpose": "[core] Create center", "command": f"O{idx}=(0,0)"},
                    {"step": 2, "purpose": "[core] Create boundary point", "command": f"A{idx}=(3,0)"},
                    {"step": 3, "purpose": "[core] Create circle", "command": f"C{idx}=Circle(O{idx},A{idx})"},
                ],
                "property_commands": [],
                "interaction_objects": [],
                "optional_script": {
                    "needed": False,
                    "script_type": "none",
                    "reason": "",
                    "target_object": "",
                    "trigger": "none",
                    "script_body": "",
                },
                "expected_created_objects": [
                    {"name": f"O{idx}", "type": "point", "role": "center"},
                    {"name": f"A{idx}", "type": "point", "role": "boundary point"},
                    {"name": f"C{idx}", "type": "circle", "role": "boundary"},
                ],
                "consistency_checks": ["Boundary remains visible"],
                "fallback_used": False,
                "fallback_reason": "",
                "implementation_notes": [],
            })
        )

    async def _stub_generate_bundle(*args, **kwargs):
        return VisualizationSpecBundle.model_validate({
            "task_summary": {
                "source_math_topic": "圆与最短距离",
                "source_problem_type": "边界距离辨析",
                "core_learning_goal": "明确到圆的距离以圆周边界为准",
            },
            "visualizations": [selected_spec, spec_2, spec_3],
        })

    monkeypatch.setattr(answer_job_service, "get_llm_client", lambda: object())
    monkeypatch.setattr(answer_job_service, "generate_geogebra_visualization_or_fallback", _stub_geogebra_codegen)
    monkeypatch.setattr(answer_job_service, "generate_visualization_spec_bundle", _stub_generate_bundle)
    monkeypatch.setattr(
        answer_job_service,
        "select_recommended_visualization",
        lambda bundle: bundle.visualizations[0],
    )

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
            assert solution.visualization_plan_json["selected_visualization"]["id"] == "viz_1"
            assert geogebra_calls == ["viz_1", "viz_2", "viz_3"]
            assert len(solution.visualizations_json) == 3
            assert solution.visualizations_json[0]["id"] == "viz_1"
            assert solution.visualizations_json[0]["spec_json"]["id"] == "viz_1"
            review = solution.stage_reviews_json["visualizing"]
            assert review["summary"]["candidate_count"] == 3
            assert review["summary"]["visualization_count"] == 3
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
