"""VizCoder service (M4, §7.2.3 + §3.3).

After the Solver finishes, this service:
  1. Loads the stored AnswerPackage + ParsedQuestion.
  2. Calls VizCoderPrompt → `VisualizationList`.
  3. Runs each viz through the AST validator (`viz_validator`).
  4. Persists passing viz to `visualizations` and emits SSE `visualization`
     events; failures emit an `error` event with the viz id but do not
     abort the stream (§3.3.3 fallback UI).
"""

from __future__ import annotations

import logging
import uuid
from typing import AsyncIterator

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repo
from app.db.models import VisualizationRow
from app.prompts import PromptRegistry
from app.schemas import Visualization, VisualizationList
from app.services.llm_client import GeminiClient, LLMError
from app.services.solver_service import SSEEvent
from app.services.viz_validator import VizValidationError, validate_jsx_code

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
        jsx_code=viz.jsx_code,
        params_json=[p.model_dump(mode="json") for p in viz.params],
        animation_json=viz.animation.model_dump(mode="json") if viz.animation else None,
    ))
    await session.flush()


async def generate_visualizations(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    llm: GeminiClient,
) -> AsyncIterator[SSEEvent]:
    """Generate + validate + persist viz for a question; emit SSE events.

    Yields one `visualization` event per viz that passes validation, and
    an `error` event for those that don't (the rest of the stream continues).
    """
    q = await repo.get_question(session, question_id)
    if q is None or q.answer_package_json is None:
        log.warning("viz: question %s missing AnswerPackage", question_id)
        return

    # Wipe prior viz rows so re-runs don't accumulate.
    await session.execute(
        delete(VisualizationRow).where(VisualizationRow.question_id == question_id)
    )

    template = PromptRegistry.get("vizcoder")
    kwargs: dict = {
        "parsed_question": q.parsed_json,
        "answer_package": q.answer_package_json,
    }

    try:
        result = await llm.call_structured(
            template=template,
            model=settings.gemini.model_vizcoder,
            model_cls=VisualizationList,
            template_kwargs=kwargs,
        )
    except LLMError as e:
        log.exception("vizcoder LLM failed")
        yield SSEEvent("error", {"stage": "vizcoder", "message": str(e)})
        return

    for viz in result.visualizations:
        try:
            report = await validate_jsx_code(viz.jsx_code)
        except VizValidationError as e:
            log.warning("viz %s rejected: %s", viz.id, e.violations)
            yield SSEEvent("error", {
                "stage": "viz_validator",
                "viz_id": viz.id,
                "violations": e.violations,
            })
            continue
        except RuntimeError as e:
            # Node not installed etc. — surface so the operator notices,
            # but don't fail the whole answer stream.
            log.error("viz validator unavailable: %s", e)
            yield SSEEvent("error", {
                "stage": "viz_validator",
                "viz_id": viz.id,
                "message": str(e),
            })
            continue

        await _persist_viz(session, question_id, viz)
        yield SSEEvent("visualization", {
            **viz.model_dump(mode="json"),
            "ast_node_count": report.node_count,
        })
