"""Visualization generation service (M4, §7.2.3 + §3.3).

Primary path: a batch VizCoder call returns the two candidate visuals.
Each candidate is then sanitized locally, validated against the strict
schema, and executed in a local GeoGebra runtime validator before
persistence. If the batch path yields no usable results, the service
falls back to planner + per-item generation.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repo
from app.db.models import VisualizationRow
from app.prompts import PromptRegistry
from app.schemas import (
    Visualization,
    VisualizationDraft,
    VisualizationListDraft,
    VisualizationStoryboard,
    VisualizationStoryboardItem,
)
from app.services.geogebra_validator import (
    GeoGebraValidationError,
    sanitize_geogebra_visualization,
    validate_geogebra_visualization,
)
from app.services.llm_client import GeminiClient, LLMError, PromptLogContext
from app.services.solver_service import SSEEvent
from app.services.viz_validator import VizValidationError, normalize_jsx_code, validate_jsx_code

log = logging.getLogger(__name__)


async def _persist_viz(
    session: AsyncSession,
    question_id: uuid.UUID,
    viz: Visualization,
    *,
    spec_json: dict[str, Any] | None = None,
) -> None:
    session.add(VisualizationRow(
        question_id=question_id,
        viz_ref=viz.id,
        title=viz.title_cn,
        caption=viz.caption_cn,
        learning_goal=viz.learning_goal,
        interactive_hints_json=list(viz.interactive_hints),
        helpers_used_json=list(viz.helpers_used),
        engine=viz.engine,
        jsx_code=viz.jsx_code,
        spec_json=dict(spec_json) if spec_json is not None else None,
        ggb_commands_json=list(viz.ggb_commands),
        ggb_settings_json=(
            viz.ggb_settings.model_dump(mode="json") if viz.ggb_settings else None
        ),
        params_json=[p.model_dump(mode="json") for p in viz.params],
        animation_json=viz.animation.model_dump(mode="json") if viz.animation else None,
    ))
    await session.flush()


async def generate_visualizations(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    llm: GeminiClient,
    solution_id: uuid.UUID | None = None,
    user_guidance: str | None = None,
    fallback_storyboard: VisualizationStoryboard | dict[str, Any] | None = None,
) -> AsyncIterator[SSEEvent]:
    """Generate + validate + persist visualizations.

    Primary path: single-call batch (VizCoderPrompt → VisualizationList).
    Fallback: if batch produces zero results, attempt the planner+per-item
    path (uses `fallback_storyboard` when provided and planner LLM fails).
    """
    async for ev in generate_visualizations_batch(
        session,
        question_id=question_id,
        llm=llm,
        solution_id=solution_id,
        user_guidance=user_guidance,
    ):
        yield ev

    rows = list((await session.execute(
        select(VisualizationRow.id)
        .where(VisualizationRow.question_id == question_id)
        .limit(1)
    )).scalars().all())
    if rows:
        return

    log.warning("batch vizcoder produced no usable visuals for %s; trying planner+item fallback", question_id)
    yield SSEEvent("error", {
        "stage": "visualizing",
        "message": "批量生成未产出可用结果，尝试 planner+逐图回退路径。",
    })
    try:
        storyboard = await plan_visualization_storyboard(
            session,
            question_id=question_id,
            llm=llm,
            solution_id=solution_id,
            user_guidance=user_guidance,
        )
    except LLMError as e:
        storyboard = _coerce_storyboard(fallback_storyboard)
        if storyboard is None:
            log.warning("planner+item fallback also failed for %s: %s", question_id, e)
            yield SSEEvent("error", {"stage": "vizplanner", "message": str(e)})
            return
        log.warning(
            "viz planner unavailable for %s; reusing existing storyboard: %s",
            question_id, e,
        )

    async for ev in generate_visualizations_from_storyboard(
        session,
        question_id=question_id,
        llm=llm,
        solution_id=solution_id,
        storyboard=storyboard,
        user_guidance=user_guidance,
    ):
        yield ev


def _with_user_guidance(
    template,
    *,
    kwargs: dict,
    user_guidance: str | None,
) -> list[dict] | None:
    if not user_guidance or not user_guidance.strip():
        return None
    messages = template.build(**kwargs)
    messages.append({
        "role": "user",
        "content": (
            "以下是用户在人工审核阶段给出的额外要求。"
            "请在不违背题意、教学目标和 JSON Schema 的前提下严格遵守：\n"
            f"{user_guidance.strip()}"
        ),
    })
    return messages


def _coerce_storyboard(
    payload: VisualizationStoryboard | dict[str, Any] | None,
) -> VisualizationStoryboard | None:
    if payload is None:
        return None
    if isinstance(payload, VisualizationStoryboard):
        return payload
    return VisualizationStoryboard.model_validate(payload)


def _ordered_storyboard_items(
    storyboard: VisualizationStoryboard,
) -> list[VisualizationStoryboardItem]:
    items_by_id = {item.id: item for item in storyboard.items}
    return [items_by_id[item_id] for item_id in storyboard.sequence]


def _merge_storyboard_item_defaults(
    viz: VisualizationDraft,
    *,
    storyboard: VisualizationStoryboard,
    item: VisualizationStoryboardItem,
) -> VisualizationDraft:
    params_by_name = {param.name: param for param in viz.params}
    merged_params = list(viz.params)
    for shared_param in storyboard.shared_params:
        if shared_param.name in item.shared_params and shared_param.name not in params_by_name:
            merged_params.append(shared_param)
    updates: dict = {"id": item.id}
    if merged_params != list(viz.params):
        updates["params"] = merged_params
    return viz.model_copy(update=updates)


async def _generate_visualization_for_storyboard_item(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    llm: GeminiClient,
    solution_id: uuid.UUID | None = None,
    storyboard: VisualizationStoryboard,
    item: VisualizationStoryboardItem,
    previous_items: list[VisualizationStoryboardItem],
    user_guidance: str | None = None,
) -> VisualizationDraft:
    q = await repo.get_question(session, question_id)
    if q is None or q.answer_package_json is None:
        raise KeyError(f"question {question_id} missing AnswerPackage")

    template = PromptRegistry.get("vizitem")
    kwargs: dict = {
        "parsed_question": q.parsed_json,
        "answer_package": q.answer_package_json,
        "storyboard": storyboard.model_dump(mode="json"),
        "storyboard_item": item.model_dump(mode="json"),
        "previous_items": [prev.model_dump(mode="json") for prev in previous_items],
        "preferred_engine": item.engine or settings.viz.default_engine,
    }
    viz = await llm.call_structured(
        template=template,
        model=settings.llm_model("vizcoder"),
        model_cls=VisualizationDraft,
        template_kwargs=kwargs,
        messages_override=_with_user_guidance(
            template,
            kwargs=kwargs,
            user_guidance=user_guidance,
        ),
        prompt_context=PromptLogContext(
            phase_description="生成可视化",
            question_id=str(question_id),
            solution_id=str(solution_id) if solution_id else None,
            related={
                "storyboard_item_id": item.id,
                "engine": item.engine,
                "user_guidance": user_guidance or "",
            },
        ),
        timeout_s=settings.llm.vizcoder_timeout_s,
        stream=settings.llm.stream_vizcoder_json,
        disable_repair=True,
    )
    return _merge_storyboard_item_defaults(viz, storyboard=storyboard, item=item)


async def _generate_visualization_batch_payload(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    llm: GeminiClient,
    solution_id: uuid.UUID | None = None,
    user_guidance: str | None = None,
) -> VisualizationListDraft:
    q = await repo.get_question(session, question_id)
    if q is None or q.answer_package_json is None:
        raise KeyError(f"question {question_id} missing AnswerPackage")

    template = PromptRegistry.get("vizcoder")
    kwargs: dict = {
        "parsed_question": q.parsed_json,
        "answer_package": q.answer_package_json,
        "preferred_engine": settings.viz.default_engine,
    }
    return await llm.call_structured(
        template=template,
        model=settings.llm_model("vizcoder"),
        model_cls=VisualizationListDraft,
        template_kwargs=kwargs,
        messages_override=_with_user_guidance(
            template,
            kwargs=kwargs,
            user_guidance=user_guidance,
        ),
        prompt_context=PromptLogContext(
            phase_description="生成整组可视化（批量回退）",
            question_id=str(question_id),
            solution_id=str(solution_id) if solution_id else None,
            related={"user_guidance": user_guidance or "", "mode": "batch_fallback"},
        ),
        timeout_s=settings.llm.vizcoder_timeout_s,
        stream=settings.llm.stream_vizcoder_json,
        disable_repair=True,
    )


def _strict_visualization(viz: VisualizationDraft | Visualization) -> Visualization:
    if isinstance(viz, Visualization):
        payload = viz.model_dump(mode="json")
    else:
        payload = viz.model_dump(mode="json")

    if str(payload.get("engine") or "jsxgraph") == "geogebra":
        return sanitize_geogebra_visualization(payload)
    try:
        return Visualization.model_validate(payload)
    except Exception as err:
        violations = [{"kind": "schema", "message": str(err)}]
        raise GeoGebraValidationError(violations) from err


async def _prepare_visualization_for_persist(
    viz: VisualizationDraft | Visualization,
) -> tuple[Visualization, int]:
    viz = _strict_visualization(viz)
    ast_node_count = 0
    if viz.engine == "jsxgraph":
        normalized_code = normalize_jsx_code(viz.jsx_code)
        if normalized_code != viz.jsx_code:
            viz = viz.model_copy(update={"jsx_code": normalized_code})
        report = await validate_jsx_code(viz.jsx_code)
        ast_node_count = report.node_count
    elif viz.engine == "geogebra":
        await validate_geogebra_visualization(viz)
    return viz, ast_node_count


async def generate_visualizations_batch(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    llm: GeminiClient,
    solution_id: uuid.UUID | None = None,
    user_guidance: str | None = None,
) -> AsyncIterator[SSEEvent]:
    await session.execute(
        delete(VisualizationRow).where(VisualizationRow.question_id == question_id)
    )

    try:
        payload = await _generate_visualization_batch_payload(
            session,
            question_id=question_id,
            llm=llm,
            solution_id=solution_id,
            user_guidance=user_guidance,
        )
    except LLMError as e:
        log.warning("batch vizcoder generation failed: %s", e)
        yield SSEEvent("error", {
            "stage": "vizcoder",
            "message": str(e),
        })
        return

    successful = 0
    for viz in payload.visualizations:
        try:
            viz, ast_node_count = await _prepare_visualization_for_persist(viz)
        except (GeoGebraValidationError, VizValidationError) as e:
            log.warning("batch viz %s rejected: %s", viz.id, e.violations)
            yield SSEEvent("error", {
                "stage": "viz_validator",
                "viz_id": viz.id,
                "violations": e.violations,
            })
            continue
        except RuntimeError as e:
            log.error("viz validator unavailable: %s", e)
            yield SSEEvent("error", {
                "stage": "viz_validator",
                "viz_id": viz.id,
                "message": str(e),
            })
            continue

        await _persist_viz(session, question_id, viz)
        successful += 1
        yield SSEEvent("visualization", {
            **viz.model_dump(mode="json"),
            "ast_node_count": ast_node_count,
            "generation_mode": "batch_fallback",
        })

    if successful == 0:
        yield SSEEvent("error", {
            "stage": "visualizing",
            "message": "旧版批量可视化生成同样没有产出可用结果。",
        })


async def generate_visualizations_from_storyboard(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    llm: GeminiClient,
    storyboard: VisualizationStoryboard,
    solution_id: uuid.UUID | None = None,
    user_guidance: str | None = None,
) -> AsyncIterator[SSEEvent]:
    await session.execute(
        delete(VisualizationRow).where(VisualizationRow.question_id == question_id)
    )

    ordered_items = _ordered_storyboard_items(storyboard)
    successful = 0
    previous_items: list[VisualizationStoryboardItem] = []
    for item in ordered_items:
        try:
            viz = await _generate_visualization_for_storyboard_item(
                session,
                question_id=question_id,
                llm=llm,
                solution_id=solution_id,
                storyboard=storyboard,
                item=item,
                previous_items=previous_items,
                user_guidance=user_guidance,
            )
        except LLMError as e:
            log.warning("viz item %s generation failed: %s", item.id, e)
            yield SSEEvent("error", {
                "stage": "vizitem",
                "viz_id": item.id,
                "message": str(e),
            })
            previous_items.append(item)
            continue

        if viz.engine != item.engine:
            yield SSEEvent("error", {
                "stage": "vizitem",
                "viz_id": item.id,
                "message": (
                    f"storyboard item expected engine='{item.engine}' but codegen returned "
                    f"engine='{viz.engine}'"
                ),
            })
            previous_items.append(item)
            continue

        try:
            viz, ast_node_count = await _prepare_visualization_for_persist(viz)
        except (GeoGebraValidationError, VizValidationError) as e:
            log.warning("viz %s rejected: %s", viz.id, e.violations)
            yield SSEEvent("error", {
                "stage": "viz_validator",
                "viz_id": viz.id,
                "violations": e.violations,
            })
            previous_items.append(item)
            continue
        except RuntimeError as e:
            log.error("viz validator unavailable: %s", e)
            yield SSEEvent("error", {
                "stage": "viz_validator",
                "viz_id": viz.id,
                "message": str(e),
            })
            previous_items.append(item)
            continue

        await _persist_viz(session, question_id, viz)
        successful += 1
        yield SSEEvent("visualization", {
            **viz.model_dump(mode="json"),
            "ast_node_count": ast_node_count,
            "storyboard_item_id": item.id,
            "storyboard_theme_cn": storyboard.theme_cn,
        })
        previous_items.append(item)

    if successful == 0:
        yield SSEEvent("error", {
            "stage": "visualizing",
            "message": "storyboard 已生成, 但没有任何可视化通过逐项代码生成与校验。",
        })


async def plan_visualization_storyboard(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    llm: GeminiClient,
    solution_id: uuid.UUID | None = None,
    user_guidance: str | None = None,
) -> VisualizationStoryboard | None:
    """Generate a difficulty-driven storyboard for per-viz codegen."""
    q = await repo.get_question(session, question_id)
    if q is None or q.answer_package_json is None:
        log.warning("viz planner: question %s missing AnswerPackage", question_id)
        raise KeyError(f"question {question_id} missing AnswerPackage")

    template = PromptRegistry.get("vizplanner")
    kwargs: dict = {
        "parsed_question": q.parsed_json,
        "answer_package": q.answer_package_json,
        "preferred_engine": "geogebra",
    }
    return await llm.call_structured(
        template=template,
        model=settings.llm_model("vizcoder"),
        model_cls=VisualizationStoryboard,
        template_kwargs=kwargs,
        messages_override=_with_user_guidance(
            template,
            kwargs=kwargs,
            user_guidance=user_guidance,
        ),
        prompt_context=PromptLogContext(
            phase_description="生成可视化规划",
            question_id=str(question_id),
            solution_id=str(solution_id) if solution_id else None,
            related={"user_guidance": user_guidance or ""},
        ),
        timeout_s=settings.llm.vizcoder_timeout_s,
        stream=settings.llm.stream_vizcoder_json,
        disable_repair=True,
    )
