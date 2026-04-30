"""HAVizNew Stage 2 JSXGraph code generation service."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.config import settings
from app.prompts import PromptRegistry
from app.schemas import VisualizationSpec
from app.services.llm_client import GeminiClient, PromptLogContext
from app.services.visual_action_logger import log_visual_action
from app.services.viz_validator import VizValidationError, validate_jsx_code

log = logging.getLogger(__name__)

_MAX_REPAIR_ATTEMPTS = 2

_FREE_BOARD_BY_CONTAINER_PATTERN = re.compile(r"JXG\.JSXGraph\.freeBoard\(\s*containerId\s*\)")
_NULL_BOARD_RETURN_PATTERN = re.compile(r"return\s*\{[^{}]*\bboard\s*:\s*null\b", re.DOTALL)
_NULL_LITERAL_RETURN_PATTERN = re.compile(r"return\s+null\s*;")
_UNDEFINED_LITERAL_RETURN_PATTERN = re.compile(r"return\s+undefined\s*;")
_TOP_LEVEL_CATCH_PATTERN = re.compile(
    r"function\s+renderVisualization\s*\([^)]*\)\s*\{[\s\S]*?catch\s*\([^)]*\)\s*\{",
    re.DOTALL,
)


@dataclass
class JsxgraphCodegenResult:
    """Outcome of Stage 2 codegen.

    `code` is empty when every attempt was rejected by the AST
    validator. `error_summary` carries the last validator violations so
    the frontend can render the spec-only fallback card with context.
    """

    code: str
    error_summary: str | None = None


def _repair_messages(*, prompt, spec: VisualizationSpec, invalid_code: str, error: VizValidationError) -> list[dict]:
    return _repair_messages_with_history(
        prompt=prompt,
        spec=spec,
        history=_repair_exchange(invalid_code=invalid_code, error=error),
    )


def _repair_exchange(*, invalid_code: str, error: VizValidationError) -> list[dict]:
    return [
        {"role": "assistant", "content": invalid_code},
        {
            "role": "user",
            "content": (
                "The previous JavaScript was rejected by the backend AST validator and must be rewritten. "
                "Output the full JavaScript again, still defining exactly one public function "
                "renderVisualization(containerId, spec).\n\n"
                "Validator violations (fix EVERY one):\n"
                + json.dumps(error.violations, ensure_ascii=False, indent=2)
            ),
        },
    ]


def _repair_messages_with_history(*, prompt, spec: VisualizationSpec, history: list[dict]) -> list[dict]:
    messages = prompt.build(spec=spec.model_dump(mode="json"))
    messages.extend(history)
    messages.append({
        "role": "user",
        "content": (
            "Reminders for this rewrite:\n"
            "- Do not use document, window, globalThis, self, Date, performance, fetch, "
            "XMLHttpRequest, WebSocket, Worker, importScripts, localStorage, sessionStorage, "
            "indexedDB, navigator, eval, Function, require, import, or with.\n"
            "- Do not pass a string to setTimeout / setInterval.\n"
            "- Do not access ['constructor'], ['eval'], or ['Function'] via computed members.\n"
            "- Use JXG.JSXGraph.initBoard(containerId, ...) instead of resolving the container yourself.\n"
            "- The host disposes previous renders. Do not call JXG.JSXGraph.freeBoard(containerId).\n"
            "- Do not wrap the whole renderVisualization body in a catch that returns a blank fallback.\n"
            "- Never return null, undefined, or an object with board: null from renderVisualization.\n"
            "- For timing use requestAnimationFrame; never use Date.now() or performance.now().\n"
            "- If the requested interaction is too fragile, follow this fallback exactly: "
            f"{spec.implementation_guidance.fallback_if_animation_is_too_complex}\n"
            "- Keep the mathematics faithful to spec and return JavaScript only."
        ),
    })
    return messages


def _runtime_contract_violations(code: str) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []

    if _FREE_BOARD_BY_CONTAINER_PATTERN.search(code):
        violations.append({
            "kind": "runtime-contract",
            "message": "Do not call JXG.JSXGraph.freeBoard(containerId); the host runtime owns disposal.",
        })

    if _NULL_BOARD_RETURN_PATTERN.search(code):
        violations.append({
            "kind": "runtime-contract",
            "message": "renderVisualization must not return an object with board: null.",
        })

    if _NULL_LITERAL_RETURN_PATTERN.search(code):
        violations.append({
            "kind": "runtime-contract",
            "message": "renderVisualization must not return null.",
        })

    if _UNDEFINED_LITERAL_RETURN_PATTERN.search(code):
        violations.append({
            "kind": "runtime-contract",
            "message": "renderVisualization must not return undefined.",
        })

    if _TOP_LEVEL_CATCH_PATTERN.search(code):
        violations.append({
            "kind": "runtime-contract",
            "message": "Do not wrap renderVisualization in a top-level catch; let hard render failures propagate.",
        })

    return violations


def _validate_runtime_contract(code: str) -> None:
    violations = _runtime_contract_violations(code)
    if violations:
        raise VizValidationError(violations)


async def generate_jsxgraph_code(
    *,
    llm: GeminiClient,
    spec: VisualizationSpec,
    question_id: str | None = None,
    solution_id: str | None = None,
) -> str:
    """Backwards-compatible wrapper that raises on persistent failure.

    Prefer ``generate_jsxgraph_code_or_fallback`` in callers that should
    degrade gracefully to a spec-only visualization card instead of
    failing the whole stage.
    """
    result = await generate_jsxgraph_code_or_fallback(
        llm=llm,
        spec=spec,
        question_id=question_id,
        solution_id=solution_id,
    )
    if not result.code:
        raise VizValidationError(
            [{"kind": "exhausted", "message": result.error_summary or "viz codegen failed"}]
        )
    return result.code


async def generate_jsxgraph_code_or_fallback(
    *,
    llm: GeminiClient,
    spec: VisualizationSpec,
    question_id: str | None = None,
    solution_id: str | None = None,
) -> JsxgraphCodegenResult:
    """Generate JSXGraph code with up to ``_MAX_REPAIR_ATTEMPTS`` retries.

    Returns a result with empty ``code`` when every attempt fails AST
    validation. Transient/LLM errors propagate so the answer-job layer
    can decide whether to retry the whole stage.
    """
    prompt = PromptRegistry.get("jsxgraph_codegen")
    base_context = PromptLogContext(
        phase_description="生成 JSXGraph 代码",
        question_id=question_id,
        solution_id=solution_id,
        related={"visualization_id": spec.id},
    )

    await log_visual_action(
        source="backend",
        phase="stage2",
        action="jsxgraph.codegen.requested",
        status="info",
        question_id=question_id,
        solution_id=solution_id,
        visualization_id=spec.id,
        engine="jsxgraph",
        component="jsxgraph_codegen_service",
        details={"repair_attempt_limit": _MAX_REPAIR_ATTEMPTS},
    )

    try:
        code = await llm.call_text(
            template=prompt,
            model=settings.llm_model("vizcoder"),
            template_kwargs={"spec": spec.model_dump(mode="json")},
            prompt_context=base_context,
            timeout_s=settings.llm.vizcoder_timeout_s,
        )
    except Exception as exc:
        await log_visual_action(
            source="backend",
            phase="stage2",
            action="jsxgraph.codegen.failed",
            status="error",
            question_id=question_id,
            solution_id=solution_id,
            visualization_id=spec.id,
            engine="jsxgraph",
            component="jsxgraph_codegen_service",
            error=str(exc),
        )
        raise

    last_error: VizValidationError | None = None
    try:
        await validate_jsx_code(code)
        _validate_runtime_contract(code)
        await log_visual_action(
            source="backend",
            phase="stage2",
            action="jsxgraph.validation.passed",
            status="ok",
            question_id=question_id,
            solution_id=solution_id,
            visualization_id=spec.id,
            engine="jsxgraph",
            component="viz_validator",
            details={"attempt": 0, "code_length": len(code)},
        )
        return JsxgraphCodegenResult(code=code)
    except VizValidationError as err:
        last_error = err
        await log_visual_action(
            source="backend",
            phase="stage2",
            action="jsxgraph.validation.rejected",
            status="error",
            question_id=question_id,
            solution_id=solution_id,
            visualization_id=spec.id,
            engine="jsxgraph",
            component="viz_validator",
            details={"attempt": 0, "violations": err.violations, "code_length": len(code)},
            error="; ".join(v.get("message", "") for v in err.violations),
        )

    repair_history = _repair_exchange(invalid_code=code, error=last_error)

    for attempt in range(1, _MAX_REPAIR_ATTEMPTS + 1):
        await log_visual_action(
            source="backend",
            phase="stage2",
            action="jsxgraph.repair.requested",
            status="info",
            question_id=question_id,
            solution_id=solution_id,
            visualization_id=spec.id,
            engine="jsxgraph",
            component="jsxgraph_codegen_service",
            details={
                "attempt": attempt,
                "previous_violations": last_error.violations if last_error else [],
            },
        )
        repair_context = PromptLogContext(
            phase_description=f"生成 JSXGraph 代码（校验失败后回退重试 {attempt}/{_MAX_REPAIR_ATTEMPTS}）",
            question_id=question_id,
            solution_id=solution_id,
            related={
                "visualization_id": spec.id,
                "fallback_strategy": spec.implementation_guidance.fallback_if_animation_is_too_complex,
                "previous_violations": last_error.violations if last_error else [],
            },
        )
        repaired = await llm.call_text(
            template=prompt,
            model=settings.llm_model("vizcoder"),
            messages_override=_repair_messages_with_history(prompt=prompt, spec=spec, history=repair_history),
            prompt_context=repair_context,
            timeout_s=settings.llm.vizcoder_timeout_s,
        )
        code = repaired
        try:
            await validate_jsx_code(code)
            _validate_runtime_contract(code)
            await log_visual_action(
                source="backend",
                phase="stage2",
                action="jsxgraph.validation.passed",
                status="ok",
                question_id=question_id,
                solution_id=solution_id,
                visualization_id=spec.id,
                engine="jsxgraph",
                component="viz_validator",
                details={"attempt": attempt, "code_length": len(code)},
            )
            return JsxgraphCodegenResult(code=code)
        except VizValidationError as err:
            last_error = err
            await log_visual_action(
                source="backend",
                phase="stage2",
                action="jsxgraph.validation.rejected",
                status="error",
                question_id=question_id,
                solution_id=solution_id,
                visualization_id=spec.id,
                engine="jsxgraph",
                component="viz_validator",
                details={"attempt": attempt, "violations": err.violations, "code_length": len(code)},
                error="; ".join(v.get("message", "") for v in err.violations),
            )
            repair_history.extend(_repair_exchange(invalid_code=code, error=last_error))

    summary = (
        "; ".join(v.get("message", "") for v in (last_error.violations if last_error else []))
        or "viz validation failed"
    )
    log.warning(
        "Stage 2 codegen exhausted %d repair attempts for viz %s: %s",
        _MAX_REPAIR_ATTEMPTS,
        spec.id,
        summary,
    )
    await log_visual_action(
        source="backend",
        phase="stage2",
        action="jsxgraph.codegen.exhausted",
        status="degraded",
        question_id=question_id,
        solution_id=solution_id,
        visualization_id=spec.id,
        engine="jsxgraph",
        component="jsxgraph_codegen_service",
        details={"repair_attempt_limit": _MAX_REPAIR_ATTEMPTS},
        error=summary,
    )
    return JsxgraphCodegenResult(code="", error_summary=summary)
