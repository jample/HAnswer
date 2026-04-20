"""Visualization generation service (M4, §7.2.3 + §3.3).

After the Solver finishes, this service now follows a planner-first flow:
  1. Loads the stored AnswerPackage + ParsedQuestion.
  2. Calls VizPlannerPrompt → `VisualizationStoryboard`.
  3. Walks `storyboard.sequence` and calls VizItemPrompt once per item.
  4. Validates each generated visualization.
  5. Persists passing viz to `visualizations` and emits SSE
      `visualization` events; failures emit an `error` event but do not
      abort later storyboard items (§3.3.3 fallback UI).

The older batch-style VizCoderPrompt remains in the codebase, but it is no
longer the active backend orchestration path.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repo
from app.db.models import VisualizationRow
from app.prompts import PromptRegistry
from app.schemas import Visualization, VisualizationStoryboard, VisualizationStoryboardItem
from app.services.llm_client import GeminiClient, LLMError, PromptLogContext
from app.services.solver_service import SSEEvent
from app.services.viz_validator import VizValidationError, normalize_jsx_code, validate_jsx_code

log = logging.getLogger(__name__)


async def _persist_viz(
    session: AsyncSession, question_id: uuid.UUID, viz: Visualization,
) -> None:
    session.add(VisualizationRow(
        question_id=question_id,
        viz_ref=viz.id,
        title=viz.title_cn,
        caption=viz.caption_cn,
        learning_goal=viz.learning_goal,
        helpers_used_json=list(viz.helpers_used),
        engine=viz.engine,
        jsx_code=viz.jsx_code,
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
    """Generate + validate + persist storyboard-driven visualizations.

    The active path is now `plan first, then per-viz codegen`. Planning is
    done once, then each storyboard item is generated independently so a
    single failing item does not abort the whole visualization stage.
    """
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
            log.exception("viz planner LLM failed")
            yield SSEEvent("error", {"stage": "vizplanner", "message": str(e)})
            return
        log.warning(
            "viz planner unavailable for question %s; reusing existing storyboard: %s",
            question_id,
            e,
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
    viz: Visualization,
    *,
    storyboard: VisualizationStoryboard,
    item: VisualizationStoryboardItem,
) -> Visualization:
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
) -> Visualization:
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
        model=settings.gemini.model_vizcoder,
        model_cls=Visualization,
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
    )
    return _merge_storyboard_item_defaults(viz, storyboard=storyboard, item=item)


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

        ast_node_count = 0
        if viz.engine == "jsxgraph":
            normalized_code = normalize_jsx_code(viz.jsx_code)
            if normalized_code != viz.jsx_code:
                viz = viz.model_copy(update={"jsx_code": normalized_code})
            try:
                report = await validate_jsx_code(viz.jsx_code)
                ast_node_count = report.node_count
            except VizValidationError as e:
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
        model=settings.gemini.model_vizcoder,
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
    )
