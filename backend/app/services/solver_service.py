"""Solver service (M3, §3.2 + §3.6.2).

Flow
    question_id → load ParsedQuestion → SolverPrompt → AnswerPackage
              → persist (questions.answer_package_json,
                         answer_packages section rows, solution_steps rows)
              → yield SSE events in §6 order.

Streaming behavior
    When ``[llm].stream_solver_json = true`` (default), the service uses
    ``GeminiClient.call_structured_streaming`` and an incremental JSON
    parser, so each top-level field of ``AnswerPackage`` becomes an SSE
    event the moment it finishes streaming from Gemini. ``solution_steps``
    arrives as a single JSON array, then is fanned out into one
    ``solution_step`` event per step. On streaming-parse failure the
    repair loop falls back to the bulk path and emits all events
    after the recovered package is validated.
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


# Top-level AnswerPackage fields → SSE event payload-shaping rules.
# A handler returns the list of SSEEvents to emit for that field.
def _shape_field(key: str, value: object) -> list[SSEEvent]:
    """Map one streamed top-level field to ordered SSE events."""
    if key == "question_understanding":
        return [SSEEvent("question_understanding", value if isinstance(value, dict) else {})]
    if key == "key_points_of_question":
        return [SSEEvent("key_points_of_question", {"items": list(value or [])})]
    if key == "solution_steps":
        out: list[SSEEvent] = []
        for step in value or []:
            if isinstance(step, dict):
                out.append(SSEEvent("solution_step", step))
        return out
    if key == "key_points_of_answer":
        return [SSEEvent("key_points_of_answer", {"items": list(value or [])})]
    if key == "method_pattern":
        return [SSEEvent("method_pattern", value if isinstance(value, dict) else {})]
    if key == "similar_questions":
        return [SSEEvent("similar_questions", {"items": list(value or [])})]
    if key == "knowledge_points":
        return [SSEEvent("knowledge_points", {"items": list(value or [])})]
    if key == "self_check":
        return [SSEEvent("self_check", {"items": list(value or [])})]
    return []


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

    When streaming is enabled, events are yielded *as they arrive* from
    Gemini (true incremental delivery). The final ``AnswerPackage`` is
    persisted once parsing+validation completes.

    Raises ``LLMError`` on unrecoverable LLM failures (after repair loop).
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

    # Solver streaming can run for tens of seconds. End the current
    # transaction before the LLM call so we don't hold an open DB
    # transaction for the entire stream duration.
    await session.commit()

    if not settings.llm.stream_solver_json:
        # Bulk path — preserved for callers/tests that pin streaming off.
        try:
            pkg = await llm.call_structured(
                template=solver,
                model=settings.gemini.model_solver,
                model_cls=AnswerPackage,
                template_kwargs=kwargs,
                messages_override=messages_override,
                timeout_s=settings.llm.solver_timeout_s,
                stream=False,
            )
        except LLMError:
            raise
        await _persist(session, question_id, pkg)
        for ev in _sections(pkg):
            yield ev
        return

    # Streaming path — emit events as soon as each top-level field of
    # AnswerPackage finishes streaming. Track which sections we already
    # emitted so the post-validation _persist doesn't double-fire.
    emitted_sections: set[str] = set()
    pkg: AnswerPackage | None = None

    try:
        async for item in llm.call_structured_streaming(
            template=solver,
            model=settings.gemini.model_solver,
            model_cls=AnswerPackage,
            template_kwargs=kwargs,
            messages_override=messages_override,
            timeout_s=settings.llm.solver_timeout_s,
        ):
            if isinstance(item, AnswerPackage):
                pkg = item
                break
            # (key, value) tuple from the streaming parser.
            key, value = item
            for ev in _shape_field(key, value):
                emitted_sections.add(ev.name)
                yield ev
    except LLMError:
        raise

    if pkg is None:
        raise LLMError("solver streaming finished without a validated AnswerPackage")

    # Persist the validated package + all sections (idempotent rewrite).
    await _persist(session, question_id, pkg)

    # Re-emit any sections that the streaming parser missed (e.g. when
    # streaming-parse failed and the repair loop produced the package
    # via the bulk path). The frontend deduplicates by section name on
    # the polling side; for the SSE consumer we simply emit the gap.
    for ev in _sections(pkg):
        if ev.name == "solution_step":
            # Steps are emitted individually; identify by step_index.
            already = any(
                e == ev.name for e in emitted_sections
            )
            if already:
                continue
        if ev.name in emitted_sections:
            continue
        yield ev
