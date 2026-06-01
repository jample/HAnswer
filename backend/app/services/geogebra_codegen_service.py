"""HAVizNew Stage 2 GeoGebra generation service."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.config import settings
from app.prompts import PromptRegistry
from app.schemas import (
    GeoGebraExecutionPayload,
    GeoGebraExecutionPayloadDraft,
    VisualizationSpec,
)
from app.services.geogebra_validator import (
    GeoGebraExecutionPayloadSanitizationResult,
    GeoGebraValidationError,
    sanitize_geogebra_execution_payload_with_report,
    validate_geogebra_execution_payload,
)
from app.services.llm_client import GeminiClient, LLMError, PromptLogContext
from app.services.visual_action_logger import log_visual_action

log = logging.getLogger(__name__)

_VALID_APPS = {"geometry", "graphing", "classic"}
_LHS_NAME = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*=")
_DROPPABLE_COMMAND_HEADS = {"SetValue", "SetConditionToShowObject"}


@dataclass
class GeoGebraCodegenResult:
    execution_payload: GeoGebraExecutionPayload | None
    error_summary: str | None = None
    repaired: bool = False
    repair_attempted: bool = False


def _summarize_violations(violations: list[dict] | None) -> str:
    if not violations:
        return "GeoGebra validation failed"
    return "; ".join(str(item.get("message", "validation error")) for item in violations)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _coerce_step(value: Any, fallback: int) -> int:
    try:
        step = int(value)
    except (TypeError, ValueError):
        step = fallback
    return max(1, step)


def _command_head(command: str) -> str:
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(", str(command or "").strip())
    return match.group(1) if match else ""


def _infer_command_tier(*, command: str, purpose: str, index: int) -> str:
    lowered = purpose.strip().lower()
    if lowered.startswith("[core]"):
        return "core"
    if lowered.startswith("[support]"):
        return "support"
    if lowered.startswith("[annotation]"):
        return "annotation"
    head = _command_head(command)
    if head in {"Text", "SetCaption", "ShowLabel"}:
        return "annotation"
    return "core" if index < 8 else "support"


def _with_tier_prefix(purpose: str, tier: str) -> str:
    stripped = str(purpose or "").strip()
    if stripped.lower().startswith(("[core]", "[support]", "[annotation]")):
        return stripped
    text = stripped or "Generated GeoGebra command"
    return f"[{tier}] {text}"


def _split_command_text(command: str) -> list[str]:
    return [part.strip() for part in re.split(r"[\r\n]+", command) if part.strip()]


def _normalize_command_steps(rows: list[Any], *, property_commands: bool = False) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(rows, start=1):
        if isinstance(raw, str):
            command = raw.strip()
            purpose = "Style object" if property_commands else "Create object"
            step = index
        elif isinstance(raw, dict):
            command = str(raw.get("command") or "").strip()
            purpose = str(raw.get("purpose") or "").strip()
            step = _coerce_step(raw.get("step"), index)
        else:
            continue
        for command_part in _split_command_text(command):
            if _command_head(command_part) in _DROPPABLE_COMMAND_HEADS:
                continue
            if property_commands:
                normalized.append({
                    "step": step,
                    "purpose": purpose or "Style object",
                    "command": command_part,
                })
                continue
            tier = _infer_command_tier(
                command=command_part,
                purpose=purpose,
                index=len(normalized),
            )
            normalized.append({
                "step": step,
                "purpose": _with_tier_prefix(purpose, tier),
                "command": command_part,
            })

    normalized.sort(key=lambda row: row["step"])
    for index, row in enumerate(normalized, start=1):
        row["step"] = index
    return normalized


def _infer_expected_object_type(command: str) -> str:
    cmd = str(command or "").strip()
    if "(" in cmd.split("=", 1)[0]:
        return "function_graph"
    rhs = cmd.split("=", 1)[1].strip() if "=" in cmd else cmd
    head = _command_head(rhs)
    if head == "Circle":
        return "circle"
    if head in {"Segment", "Line", "Ray", "Vector"}:
        return head.lower()
    if head in {"Intersect", "Midpoint", "Point", "Extremum"}:
        return "point"
    if head in {"Distance", "Length", "Angle", "Area"}:
        return "numeric"
    if head == "Slider":
        return "slider_parameter"
    if rhs.startswith("("):
        return "point"
    return "object"


def _is_annotation_expected(row: dict[str, Any]) -> bool:
    name = str(row.get("name") or "").strip().lower()
    object_type = str(row.get("type") or "").strip().lower()
    role = str(row.get("role") or "").strip().lower()
    return (
        object_type in {"text", "label", "caption", "dynamic_text"}
        or name.endswith("_text")
        or name.endswith("text")
        or any(token in role for token in ("text", "label", "caption", "display", "annotation"))
    )


def _split_expected_name_tier(name: str) -> tuple[str, str | None]:
    stripped = str(name or "").strip()
    lowered = stripped.lower()
    for tier in ("core", "support", "annotation"):
        prefix = f"{tier}:"
        if lowered.startswith(prefix):
            return stripped[len(prefix):].strip(), tier
    return stripped, None


def _role_with_expected_tier(role: str, tier: str | None) -> str:
    text = str(role or "").strip()
    if tier is None or text.lower().startswith(("core:", "support:", "annotation:")):
        return text
    return f"{tier}: {text or 'expected object'}"


def _infer_expected_objects(commands: list[dict[str, Any]], *, limit: int = 6) -> list[dict[str, str]]:
    expected: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(commands):
        command = str(row.get("command") or "").strip()
        match = _LHS_NAME.match(command)
        if not match:
            continue
        if _infer_command_tier(command=command, purpose=str(row.get("purpose") or ""), index=index) == "annotation":
            continue
        name = match.group(1)
        if name in seen:
            continue
        expected.append({
            "name": name,
            "type": _infer_expected_object_type(command),
            "role": "core object inferred during draft normalization",
        })
        seen.add(name)
        if len(expected) >= limit:
            break
    return expected


def _normalize_expected_objects(rows: list[Any], commands: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for raw in rows:
        if isinstance(raw, str):
            name, tier = _split_expected_name_tier(raw)
            item = {
                "name": name,
                "type": "object",
                "role": _role_with_expected_tier("core object from draft", tier),
            }
        elif isinstance(raw, dict):
            name, tier = _split_expected_name_tier(str(raw.get("name") or ""))
            item = {
                "name": name,
                "type": str(raw.get("type") or "object").strip() or "object",
                "role": _role_with_expected_tier(
                    str(raw.get("role") or "core object from draft").strip()
                    or "core object from draft",
                    tier,
                ),
            }
        else:
            continue
        if item["name"] and not _is_annotation_expected(item):
            normalized.append(item)
    return normalized or _infer_expected_objects(commands)


def _defined_command_names(commands: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for row in commands:
        match = _LHS_NAME.match(str(row.get("command") or "").strip())
        if match:
            names.add(match.group(1))
    return names


def _spec_parameter_purposes(spec: VisualizationSpec) -> dict[str, str]:
    dumped = spec.model_dump(mode="json")
    parameters = (
        ((dumped.get("interaction_and_animation") or {}).get("parameters") or [])
        if isinstance(dumped, dict)
        else []
    )
    purposes: dict[str, str] = {}
    for raw in parameters:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        purpose = (
            str(raw.get("meaning") or "").strip()
            or str(raw.get("label") or "").strip()
            or str(raw.get("description") or "").strip()
        )
        if purpose:
            purposes[name] = purpose
    return purposes


def _normalize_interaction_objects(
    rows: list[Any],
    *,
    commands: list[dict[str, Any]],
    spec: VisualizationSpec,
) -> list[dict[str, str]]:
    defined_names = _defined_command_names(commands)
    parameter_purposes = _spec_parameter_purposes(spec)
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name or name in seen:
            continue
        if defined_names and name not in defined_names:
            continue
        raw_type = str(raw.get("type") or "none").strip().lower()
        interaction_type = raw_type if raw_type in {
            "slider",
            "button",
            "checkbox",
            "input_box",
            "none",
        } else "none"
        purpose = (
            str(raw.get("purpose") or "").strip()
            or str(raw.get("caption") or "").strip()
            or str(raw.get("label") or "").strip()
            or str(raw.get("description") or "").strip()
            or parameter_purposes.get(name, "")
            or f"控制 GeoGebra 对象 {name}"
        )
        normalized.append({
            "name": name,
            "type": interaction_type,
            "purpose": purpose,
        })
        seen.add(name)
    return normalized


def normalize_geogebra_execution_payload_draft(
    draft: GeoGebraExecutionPayloadDraft | GeoGebraExecutionPayload | dict[str, Any],
    *,
    spec: VisualizationSpec,
) -> GeoGebraExecutionPayload:
    if isinstance(draft, GeoGebraExecutionPayload):
        raw = draft.model_dump(mode="json")
    elif hasattr(draft, "model_dump"):
        raw = draft.model_dump(mode="json")
    else:
        raw = dict(draft)

    commands = _normalize_command_steps(_as_list(raw.get("commands")))
    property_commands = _normalize_command_steps(
        _as_list(raw.get("property_commands")),
        property_commands=True,
    )
    preferred_app = str(raw.get("preferred_geogebra_app") or spec.preferred_geogebra_app or "classic").strip().lower()
    if preferred_app not in _VALID_APPS:
        preferred_app = str(spec.preferred_geogebra_app or "classic")
    payload = {
        "title": str(raw.get("title") or spec.title),
        "preferred_geogebra_app": preferred_app,
        "execution_mode": "command_only",
        "math_meaning_summary": str(raw.get("math_meaning_summary") or spec.mathematical_claim_being_shown),
        "object_naming_convention": str(
            raw.get("object_naming_convention")
            or spec.implementation_guidance.preferred_geogebra_object_naming_style
        ),
        "commands": commands,
        "property_commands": property_commands,
        "interaction_objects": _normalize_interaction_objects(
            _as_list(raw.get("interaction_objects")),
            commands=commands,
            spec=spec,
        ),
        "optional_script": {
            "needed": False,
            "script_type": "none",
            "reason": "",
            "target_object": "",
            "trigger": "none",
            "script_body": "",
        },
        "expected_created_objects": _normalize_expected_objects(
            _as_list(raw.get("expected_created_objects")),
            commands,
        ),
        "consistency_checks": [
            str(item) for item in _as_list(raw.get("consistency_checks")) if str(item).strip()
        ],
        "fallback_used": bool(raw.get("fallback_used") or False),
        "fallback_reason": str(raw.get("fallback_reason") or ""),
        "implementation_notes": [
            str(item) for item in _as_list(raw.get("implementation_notes")) if str(item).strip()
        ],
    }
    if payload["fallback_used"] and not payload["fallback_reason"]:
        payload["fallback_reason"] = "Stage 2 used a simpler static fallback for runtime stability."
    if not payload["fallback_used"]:
        payload["fallback_reason"] = ""
    return GeoGebraExecutionPayload.model_validate(payload)


def _payload_summary(payload: GeoGebraExecutionPayload) -> dict[str, Any]:
    return {
        "command_count": len(payload.commands),
        "property_command_count": len(payload.property_commands),
        "interaction_object_count": len(payload.interaction_objects),
        "script_needed": payload.optional_script.needed,
    }


def _repair_messages(
    *,
    prompt,
    spec: VisualizationSpec,
    failed_payload: GeoGebraExecutionPayload,
    violations: list[dict],
) -> list[dict]:
    messages = prompt.build(spec=spec.model_dump(mode="json"))
    messages.append({
        "role": "assistant",
        "content": json.dumps(failed_payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
    })
    messages.append({
        "role": "user",
        "content": (
            "The previous GeoGebraExecutionPayload failed local static validation. "
            "Repair the same visualization; do not redesign Stage 1, do not change the teaching goal, "
            "and do not add a second visualization.\n\n"
            "Validator violations:\n"
            f"{json.dumps(violations, ensure_ascii=False, indent=2)}\n\n"
            "Repair rules:\n"
            "- Keep the output as exactly one full GeoGebraExecutionPayload JSON object.\n"
            "- Fix only executable GeoGebra details: object names, command order, property targets, "
            "interaction_objects, optional_script target, and expected_created_objects.\n"
            "- Prefer command_only. Do not introduce JavaScript or GGBScript.\n"
            "- Keep commands[] at or below 16 entries and property_commands[] at or below 16 entries.\n"
            "- Avoid conditional object creation such as If(step >= n, Segment(...)). Use a stable static fallback.\n"
            "- Use direct indexed intersections such as P1=Intersect(c1,c2,1); do not use "
            "tmp=Intersect(c1,c2) followed by Element(tmp,1).\n"
            "- Every interaction_objects[].name and expected_created_objects[].name must be created by commands[].\n"
            "- Put object creation in commands[] and styling/visibility/trace commands in property_commands[].\n"
            "- property_commands[] must not contain left-hand-side assignments such as X=... .\n"
            "- Prefix commands[].purpose with [core], [support], or [annotation].\n"
            "- expected_created_objects should contain core objects only; support numeric values must not become hard runtime requirements.\n"
            "- Use abs(...), not Abs(...), and split fragile support measurements into simple assignments.\n"
            "- If a feature is too fragile, use the spec fallback strategy while preserving the same teaching claim."
        ),
    })
    return messages


async def _repair_payload_once(
    *,
    llm: GeminiClient,
    prompt,
    spec: VisualizationSpec,
    failed_payload: GeoGebraExecutionPayload,
    violations: list[dict],
    prompt_context: PromptLogContext,
    question_id: str | None,
    solution_id: str | None,
) -> GeoGebraExecutionPayload:
    await log_visual_action(
        source="backend",
        phase="stage2",
        action="geogebra.repair.requested",
        status="info",
        question_id=question_id,
        solution_id=solution_id,
        visualization_id=spec.id,
        engine="geogebra",
        component="geogebra_codegen_service",
        details={"violations": violations},
    )
    repaired_draft = await llm.call_structured(
        template=prompt,
        model=settings.llm_model("vizcoder"),
        model_cls=GeoGebraExecutionPayloadDraft,
        template_kwargs={"spec": spec.model_dump(mode="json")},
        messages_override=_repair_messages(
            prompt=prompt,
            spec=spec,
            failed_payload=failed_payload,
            violations=violations,
        ),
        prompt_context=prompt_context,
        timeout_s=settings.llm.vizcoder_timeout_s,
        stream=settings.llm.stream_vizcoder_json,
        disable_repair=True,
    )
    repaired_payload = normalize_geogebra_execution_payload_draft(repaired_draft, spec=spec)
    await log_visual_action(
        source="backend",
        phase="stage2",
        action="geogebra.repair.received",
        status="info",
        question_id=question_id,
        solution_id=solution_id,
        visualization_id=spec.id,
        engine="geogebra",
        component="geogebra_codegen_service",
        details=_payload_summary(repaired_payload),
    )
    return repaired_payload


async def _validate_payload(
    *,
    payload: GeoGebraExecutionPayload,
    spec: VisualizationSpec,
    question_id: str | None,
    solution_id: str | None,
    repaired: bool,
) -> GeoGebraExecutionPayload:
    sanitized: GeoGebraExecutionPayloadSanitizationResult = sanitize_geogebra_execution_payload_with_report(payload)
    await log_visual_action(
        source="backend",
        phase="stage2",
        action="geogebra.sanitize.passed",
        status="ok",
        question_id=question_id,
        solution_id=solution_id,
        visualization_id=spec.id,
        engine="geogebra",
        component="geogebra_codegen_service",
        details={
            **_payload_summary(sanitized.payload),
            "rewrite_map": sanitized.rewrite_map,
            "defined_names": sanitized.defined_names,
            "repaired": repaired,
        },
    )
    report = await validate_geogebra_execution_payload(
        sanitized.payload,
        spec=spec.model_dump(mode="json"),
    )
    await log_visual_action(
        source="backend",
        phase="stage2",
        action="geogebra.static_validation.passed",
        status="ok",
        question_id=question_id,
        solution_id=solution_id,
        visualization_id=spec.id,
        engine="geogebra",
        component="geogebra_validator",
        details={
            **_payload_summary(sanitized.payload),
            "validation_mode": report.validation_mode,
            "repaired": repaired,
        },
    )
    return sanitized.payload


async def generate_geogebra_visualization(
    *,
    llm: GeminiClient,
    spec: VisualizationSpec,
    question_id: str | None = None,
    solution_id: str | None = None,
) -> GeoGebraExecutionPayload:
    result = await generate_geogebra_visualization_or_fallback(
        llm=llm,
        spec=spec,
        question_id=question_id,
        solution_id=solution_id,
    )
    if result.execution_payload is None:
        raise GeoGebraValidationError([
            {"kind": "exhausted", "message": result.error_summary or "GeoGebra codegen failed"}
        ])
    return result.execution_payload


async def generate_geogebra_visualization_or_fallback(
    *,
    llm: GeminiClient,
    spec: VisualizationSpec,
    question_id: str | None = None,
    solution_id: str | None = None,
) -> GeoGebraCodegenResult:
    prompt = PromptRegistry.get("geogebra_codegen")
    prompt_context = PromptLogContext(
        phase_description="生成 GeoGebra 指令",
        question_id=question_id,
        solution_id=solution_id,
        related={"visualization_id": spec.id},
    )

    await log_visual_action(
        source="backend",
        phase="stage2",
        action="geogebra.codegen.requested",
        status="info",
        question_id=question_id,
        solution_id=solution_id,
        visualization_id=spec.id,
        engine="geogebra",
        component="geogebra_codegen_service",
        details={"spec_title": spec.title},
    )

    try:
        draft = await llm.call_structured(
            template=prompt,
            model=settings.llm_model("vizcoder"),
            model_cls=GeoGebraExecutionPayloadDraft,
            template_kwargs={"spec": spec.model_dump(mode="json")},
            prompt_context=prompt_context,
            timeout_s=settings.llm.vizcoder_timeout_s,
            stream=settings.llm.stream_vizcoder_json,
            disable_repair=True,
        )
        payload = normalize_geogebra_execution_payload_draft(draft, spec=spec)
        await log_visual_action(
            source="backend",
            phase="stage2",
            action="geogebra.codegen.received",
            status="info",
            question_id=question_id,
            solution_id=solution_id,
            visualization_id=spec.id,
            engine="geogebra",
            component="geogebra_codegen_service",
            details=_payload_summary(payload),
        )
        try:
            validated = await _validate_payload(
                payload=payload,
                spec=spec,
                question_id=question_id,
                solution_id=solution_id,
                repaired=False,
            )
            return GeoGebraCodegenResult(execution_payload=validated)
        except GeoGebraValidationError as first_exc:
            first_summary = _summarize_violations(first_exc.violations)
            log.warning("GeoGebra Stage 2 initial payload failed for viz %s: %s", spec.id, first_summary)
            await log_visual_action(
                source="backend",
                phase="stage2",
                action="geogebra.static_validation.failed_retrying",
                status="degraded",
                question_id=question_id,
                solution_id=solution_id,
                visualization_id=spec.id,
                engine="geogebra",
                component="geogebra_codegen_service",
                details={
                    "violations": first_exc.violations,
                    "llm_retry_count": 1,
                    "fallback_strategy": "one_repair_then_spec_only",
                },
                error=first_summary,
            )
            try:
                repaired_payload = await _repair_payload_once(
                    llm=llm,
                    prompt=prompt,
                    spec=spec,
                    failed_payload=payload,
                    violations=first_exc.violations,
                    prompt_context=prompt_context,
                    question_id=question_id,
                    solution_id=solution_id,
                )
                validated = await _validate_payload(
                    payload=repaired_payload,
                    spec=spec,
                    question_id=question_id,
                    solution_id=solution_id,
                    repaired=True,
                )
                await log_visual_action(
                    source="backend",
                    phase="stage2",
                    action="geogebra.repair.succeeded",
                    status="ok",
                    question_id=question_id,
                    solution_id=solution_id,
                    visualization_id=spec.id,
                    engine="geogebra",
                    component="geogebra_codegen_service",
                    details=_payload_summary(validated),
                )
                return GeoGebraCodegenResult(
                    execution_payload=validated,
                    repaired=True,
                    repair_attempted=True,
                )
            except (GeoGebraValidationError, ValidationError, LLMError) as repair_exc:
                repair_summary = (
                    _summarize_violations(repair_exc.violations)
                    if isinstance(repair_exc, GeoGebraValidationError)
                    else str(repair_exc)
                )
                await log_visual_action(
                    source="backend",
                    phase="stage2",
                    action="geogebra.repair.failed",
                    status="degraded",
                    question_id=question_id,
                    solution_id=solution_id,
                    visualization_id=spec.id,
                    engine="geogebra",
                    component="geogebra_codegen_service",
                    details={
                        "initial_violations": first_exc.violations,
                        "initial_error": first_summary,
                    },
                    error=repair_summary,
                )
                return GeoGebraCodegenResult(
                    execution_payload=None,
                    error_summary=repair_summary,
                    repair_attempted=True,
                )
    except GeoGebraValidationError as exc:
        summary = _summarize_violations(exc.violations)
        log.warning("GeoGebra Stage 2 degraded for viz %s: %s", spec.id, summary)
        await log_visual_action(
            source="backend",
            phase="stage2",
            action="geogebra.validation.rejected",
            status="degraded",
            question_id=question_id,
            solution_id=solution_id,
            visualization_id=spec.id,
            engine="geogebra",
            component="geogebra_codegen_service",
            details={"violations": exc.violations},
            error=summary,
        )
        return GeoGebraCodegenResult(execution_payload=None, error_summary=summary)
    except LLMError as exc:
        summary = str(exc)
        log.warning("GeoGebra Stage 2 LLM output rejected for viz %s: %s", spec.id, summary)
        await log_visual_action(
            source="backend",
            phase="stage2",
            action="geogebra.schema.rejected",
            status="degraded",
            question_id=question_id,
            solution_id=solution_id,
            visualization_id=spec.id,
            engine="geogebra",
            component="geogebra_codegen_service",
            error=summary,
        )
        return GeoGebraCodegenResult(execution_payload=None, error_summary=summary)
    except ValidationError as exc:
        summary = str(exc)
        log.warning("GeoGebra Stage 2 local draft normalization rejected for viz %s: %s", spec.id, summary)
        await log_visual_action(
            source="backend",
            phase="stage2",
            action="geogebra.draft_normalization.rejected",
            status="degraded",
            question_id=question_id,
            solution_id=solution_id,
            visualization_id=spec.id,
            engine="geogebra",
            component="geogebra_codegen_service",
            error=summary,
        )
        return GeoGebraCodegenResult(execution_payload=None, error_summary=summary)
    except Exception as exc:
        await log_visual_action(
            source="backend",
            phase="stage2",
            action="geogebra.codegen.failed",
            status="error",
            question_id=question_id,
            solution_id=solution_id,
            visualization_id=spec.id,
            engine="geogebra",
            component="geogebra_codegen_service",
            error=str(exc),
        )
        raise
