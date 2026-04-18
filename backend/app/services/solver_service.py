"""Solver service (M3, §3.2 + §3.6.2).

Flow
    question_id → load ParsedQuestion → SolverPrompt → AnswerPackage
              → persist (questions.answer_package_json,
                         answer_packages section rows, solution_steps rows)
              → yield SSE events in §6 order.

Stage-1 implementation is **bulk-then-stream**: one LLM call produces the
full AnswerPackage, then we chunk it into SSE events for the frontend.
Spec §4 asks for first-token < 2 s; M8 polish will move to incremental
parsing. Keeping the event names & order contract honored NOW means the
frontend (this milestone) does not have to change later.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import repo
from app.db.models import AnswerPackageSection, SolutionStepRow
from app.prompts import PromptRegistry
from app.schemas import AnswerPackage, ParsedQuestion
from app.services.llm_client import GeminiClient, LLMError


@dataclass
class SSEEvent:
    name: str
    data: dict


def _sections(pkg: AnswerPackage) -> list[SSEEvent]:
    """Chunk an AnswerPackage into ordered SSE events per §6."""
    events: list[SSEEvent] = [
        SSEEvent("question_understanding", pkg.question_understanding.model_dump()),
        SSEEvent("key_points_of_question", {"items": pkg.key_points_of_question}),
    ]
    for step in pkg.solution_steps:
        events.append(SSEEvent("solution_step", step.model_dump()))
    # Visualization events are appended by the VizCoder service; this stream
    # intentionally skips them so the Solver milestone is independent.
    events.extend([
        SSEEvent("key_points_of_answer", {"items": pkg.key_points_of_answer}),
        SSEEvent("method_pattern", pkg.method_pattern.model_dump()),
        SSEEvent(
            "similar_questions",
            {"items": [s.model_dump() for s in pkg.similar_questions]},
        ),
        SSEEvent(
            "knowledge_points",
            {"items": [k.model_dump() for k in pkg.knowledge_points]},
        ),
        SSEEvent("self_check", {"items": pkg.self_check}),
    ])
    return events


async def _persist(
    session: AsyncSession, question_id: uuid.UUID, pkg: AnswerPackage,
) -> None:
    """Idempotent write path for M3.

    Clears prior per-question section/step rows so re-generation leaves
    a consistent state. M5+ layers on kp/pattern sedimentation.
    """
    await session.execute(
        delete(AnswerPackageSection).where(AnswerPackageSection.question_id == question_id)
    )
    await session.execute(
        delete(SolutionStepRow).where(SolutionStepRow.question_id == question_id)
    )

    q = await repo.get_question(session, question_id)
    if q is None:
        raise KeyError(f"question {question_id} not found")

    q.answer_package_json = pkg.model_dump(mode="json")
    q.status = "answered"

    for ev in _sections(pkg):
        session.add(AnswerPackageSection(
            question_id=question_id, section=ev.name, payload_json=ev.data,
        ))

    for step in pkg.solution_steps:
        session.add(SolutionStepRow(
            question_id=question_id,
            step_index=step.step_index,
            statement=step.statement,
            rationale=step.rationale,
            formula=step.formula or "",
            why_this_step=step.why_this_step,
            viz_ref=step.viz_ref or "",
        ))

    await session.flush()


async def generate_answer(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    llm: GeminiClient,
    existing_patterns: list[dict] | None = None,
    existing_kps: list[dict] | None = None,
    user_guidance: str | None = None,
) -> AsyncIterator[SSEEvent]:
    """Run Solver, persist, and stream SSE events.

    Raises `LLMError` on unrecoverable LLM failures (after repair loop).
    Caller is responsible for mapping to an SSE `error` event if needed.
    """
    q = await repo.get_question(session, question_id)
    if q is None:
        raise KeyError(f"question {question_id} not found")

    parsed = ParsedQuestion.model_validate(q.parsed_json)

    solver = PromptRegistry.get("solver")
    kwargs: dict = {
        "parsed_question": parsed.model_dump(mode="json"),
        "existing_patterns": existing_patterns or [],
        "existing_kps": existing_kps or [],
    }
    messages_override = None
    if user_guidance and user_guidance.strip():
        messages_override = solver.build(**kwargs)
        messages_override.append({
            "role": "user",
            "content": (
                "以下是用户在人工审核阶段给出的额外要求。"
                "请在不违背题意和 JSON Schema 的前提下严格遵守：\n"
                f"{user_guidance.strip()}"
            ),
        })

    try:
        pkg = await llm.call_structured(
            template=solver,
            model=settings.gemini.model_solver,
            model_cls=AnswerPackage,
            template_kwargs=kwargs,
            messages_override=messages_override,
            timeout_s=settings.llm.solver_timeout_s,
            stream=settings.llm.stream_solver_json,
        )
    except LLMError:
        raise

    await _persist(session, question_id, pkg)
    for ev in _sections(pkg):
        yield ev
