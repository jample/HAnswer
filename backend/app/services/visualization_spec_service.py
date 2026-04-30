"""HAVizNew Stage 1 visualization-spec planning service."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repo
from app.prompts import PromptRegistry
from app.schemas import VisualizationSpec, VisualizationSpecBundle
from app.services.llm_client import GeminiClient, PromptLogContext
from app.services.question_solution_service import update_solution_visualization_plan
from app.services.visual_action_logger import log_visual_action


def summarize_answer_for_visualization(answer_package: Any) -> dict[str, Any]:
    """Trim AnswerPackage to the slice that is useful for visualization design.

    Stage 1 only needs the mathematical scaffold — the conclusion to
    show, what's hard about the problem, and the ordered solution
    statements. Feeding the full answer package (with similar_questions,
    self_check, knowledge_points, per-step rationale) bloats the prompt
    by 60%+ and pushes vizspec onto the timeout cliff without improving
    spec quality.
    """
    if not isinstance(answer_package, dict):
        return {}

    out: dict[str, Any] = {}

    qu = answer_package.get("question_understanding")
    if isinstance(qu, dict):
        out["question_understanding"] = {
            "restated_question": qu.get("restated_question") or "",
            "givens": list(qu.get("givens") or []),
            "unknowns": list(qu.get("unknowns") or []),
            "implicit_conditions": list(qu.get("implicit_conditions") or []),
        }

    key_points = answer_package.get("key_points_of_question")
    if isinstance(key_points, list):
        out["key_points_of_question"] = list(key_points)

    steps = answer_package.get("solution_steps")
    if isinstance(steps, list):
        compact_steps: list[dict[str, Any]] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            compact_steps.append({
                "step_index": step.get("step_index"),
                "statement": step.get("statement") or "",
            })
        out["solution_steps"] = compact_steps

    answer_points = answer_package.get("key_points_of_answer")
    if isinstance(answer_points, list):
        out["key_points_of_answer"] = list(answer_points)

    method_pattern = answer_package.get("method_pattern")
    if isinstance(method_pattern, dict):
        out["method_pattern"] = {
            "name_cn": method_pattern.get("name_cn") or "",
            "when_to_use": method_pattern.get("when_to_use") or "",
        }

    return out


def select_recommended_visualization(bundle: VisualizationSpecBundle) -> VisualizationSpec:
    candidates = [
        item for item in bundle.visualizations
        if item.recommended and item.renderability_assessment.overall_readiness in {"ready", "mostly_ready"}
    ]
    if not candidates:
        # Fall back to the most stable visualization regardless of the
        # recommended/readiness flags so visualizing never crashes the
        # pipeline once Stage 1 produced anything at all.
        candidates = list(bundle.visualizations)
    candidates.sort(key=lambda item: (item.priority, -item.renderability_assessment.implementation_stability_score))
    return candidates[0]


async def generate_visualization_spec_bundle(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    llm: GeminiClient,
    solution_id: uuid.UUID | None = None,
    teaching_preference: str | None = None,
) -> VisualizationSpecBundle:
    question = await repo.get_question(session, question_id)
    if question is None or question.answer_package_json is None:
        raise KeyError(f"question {question_id} missing AnswerPackage")

    prompt = PromptRegistry.get("vizspec")
    await log_visual_action(
        source="backend",
        phase="stage1",
        action="vizspec.requested",
        status="info",
        question_id=str(question_id),
        solution_id=str(solution_id) if solution_id else None,
        component="visualization_spec_service",
        details={"has_teaching_preference": bool(teaching_preference)},
    )
    try:
        bundle = await llm.call_structured(
            template=prompt,
            model=settings.llm_model("vizcoder"),
            model_cls=VisualizationSpecBundle,
            template_kwargs={
                "original_problem": question.parsed_json,
                "answer_package": summarize_answer_for_visualization(question.answer_package_json),
                "teaching_preference": teaching_preference or "",
            },
            prompt_context=PromptLogContext(
                phase_description="规划可视化规格",
                question_id=str(question_id),
                solution_id=str(solution_id) if solution_id else None,
                related={"teaching_preference": teaching_preference or ""},
            ),
            timeout_s=settings.llm.vizcoder_timeout_s,
            stream=settings.llm.stream_vizcoder_json,
            min_repair_attempts=2,
        )
    except Exception as exc:
        await log_visual_action(
            source="backend",
            phase="stage1",
            action="vizspec.failed",
            status="error",
            question_id=str(question_id),
            solution_id=str(solution_id) if solution_id else None,
            component="visualization_spec_service",
            error=str(exc),
        )
        raise

    await log_visual_action(
        source="backend",
        phase="stage1",
        action="vizspec.succeeded",
        status="ok",
        question_id=str(question_id),
        solution_id=str(solution_id) if solution_id else None,
        component="visualization_spec_service",
        details={
            "visualization_count": len(bundle.visualizations),
            "visualization_ids": [item.id for item in bundle.visualizations],
            "recommended_ids": [item.id for item in bundle.visualizations if item.recommended],
        },
    )
    return bundle


async def persist_visualization_spec_bundle(
    session: AsyncSession,
    *,
    solution,
    bundle: VisualizationSpecBundle,
    selected_spec: VisualizationSpec | None = None,
) -> None:
    payload: dict[str, Any] = bundle.model_dump(mode="json")
    if selected_spec is not None:
        payload["selected_visualization_id"] = selected_spec.id
        payload["selected_visualization"] = selected_spec.model_dump(mode="json")
    await update_solution_visualization_plan(
        session,
        solution=solution,
        visualization_plan_json=payload,
    )
    await log_visual_action(
        source="backend",
        phase="stage1",
        action="vizspec.persisted",
        status="ok",
        question_id=str(solution.question_id),
        solution_id=str(solution.id),
        visualization_id=selected_spec.id if selected_spec is not None else None,
        component="visualization_spec_service",
        details={
            "visualization_count": len(bundle.visualizations),
            "selected_visualization_id": selected_spec.id if selected_spec is not None else None,
        },
    )
