"""Local GeoGebra sanitization + static validation.

This module keeps visualization recovery entirely local:
  - deterministic identifier sanitization for known GeoGebra collisions;
  - cross-field validation for params / animation drives;
  - cheap static validation for command complexity and known fragile forms.

Runtime rendering is intentionally left to the frontend GeoGebra sandbox. The
backend must not launch a browser in the user-facing generation path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.schemas.llm import (
    GeoGebraExecutionPayload,
    Visualization,
    VisualizationDraft,
)

_LHS_NAME = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*=")
_COMMAND_HEAD = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_PROPERTY_TARGET = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)")
_GREEK_ALIASES = frozenset({
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta",
    "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi", "rho",
    "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
})
_RESERVED_NAMES = frozenset({
    "xAxis", "yAxis", "zAxis", "xOyPlane", "xOzPlane", "yOzPlane", "e", "i",
})
_EXECUTION_MAX_COMMANDS = 16
_EXECUTION_MAX_PROPERTY_COMMANDS = 16
_EXECUTION_MAX_COMMAND_LEN = 512
_POINT_PLUS_VECTOR = re.compile(
    r"^[A-Z][A-Za-z0-9_]*\s*=\s*[A-Z][A-Za-z0-9_]*\s*[+\-]\s*[A-Za-z_][A-Za-z0-9_]*\s*$"
)
_POINT_PLUS_TUPLE = re.compile(
    r"^[A-Z][A-Za-z0-9_]*\s*=\s*[A-Z][A-Za-z0-9_]*\s*[+\-]\s*\("
)
_POINT_PLUS_TUPLE_COMMAND = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\s*([+\-])\s*\((.*)\)\s*$"
)
_INTERSECT_LIST_ASSIGN = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*Intersect\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$"
)
_ELEMENT_OF_LIST_ASSIGN = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*Element\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([12])\s*\)\s*$"
)
_VECTOR_POINT_DIFFERENCE = re.compile(r"Vector\s*\(\s*[A-Za-z_][A-Za-z0-9_]*\s*[-+]\s*[A-Za-z_][A-Za-z0-9_]*\s*\)")
_CONDITIONAL_OBJECT_CREATION = re.compile(
    r"=\s*If\s*\([^,]+,\s*(Segment|Line|Circle|Polygon|Text|Point|Vector|Ray)\s*\(",
    re.IGNORECASE,
)
_ABS_FUNCTION = re.compile(r"\bAbs\s*\(", re.IGNORECASE)
_DISTANCE_EXPR = r"Distance\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)"
_ABS_DISTANCE_ASSIGN = re.compile(
    rf"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:Abs|abs)\s*\(\s*{_DISTANCE_EXPR}\s*-\s*(.+)\s*\)\s*$",
    re.IGNORECASE,
)
_DISTANCE_PLUS_ASSIGN = re.compile(
    rf"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*{_DISTANCE_EXPR}\s*\+\s*(.+)\s*$",
    re.IGNORECASE,
)
_SETCOLOR_NAMED = re.compile(r"SetColor\s*\([^,]+,\s*[\"'][A-Za-z]+[\"']\s*\)")
_SETVALUE = re.compile(r"^SetValue\s*\(")
_SETCONDITION_TO_SHOW = re.compile(r"^SetConditionToShowObject\s*\(")
_VIEW_DIRECTIVES = frozenset({
    "SetCoordSystem", "SetAxesVisible", "SetGridVisible",
    "SetPerspective", "ShowAxes", "ShowGrid",
})
_NON_UI_INTERACTION_TYPES = frozenset({
    "point", "moving_point", "draggable_point", "segment", "line", "circle", "locus",
})


@dataclass
class GeoGebraValidationReport:
    ok: bool
    render_ms: float | None = None
    violations: list[dict[str, Any]] | None = None
    validation_mode: str = "static"


@dataclass
class GeoGebraSanitizationResult:
    visualization: Visualization
    rewrite_map: dict[str, str]
    defined_names: list[str]


@dataclass
class GeoGebraExecutionPayloadSanitizationResult:
    payload: GeoGebraExecutionPayload
    rewrite_map: dict[str, str]
    defined_names: list[str]


class GeoGebraValidationError(Exception):
    def __init__(self, violations: list[dict[str, Any]]) -> None:
        self.violations = violations
        super().__init__(
            "GeoGebra validation failed: "
            + "; ".join(str(v.get("message", "")) for v in violations)
        )


def _pydantic_errors_to_violations(err: ValidationError) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for item in err.errors():
        loc = ".".join(str(part) for part in item.get("loc", []))
        violations.append({
            "kind": "schema",
            "loc": loc,
            "message": str(item.get("msg", "validation error")),
        })
    return violations


def _extract_defined_names(commands: list[str]) -> set[str]:
    names: set[str] = set()
    for raw in commands:
        name = _assigned_name(str(raw or ""))
        if name:
            names.add(name)
    return names


def _extract_step_defined_names(steps: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for row in steps:
        name = _assigned_name(str((row or {}).get("command") or ""))
        if name:
            names.add(name)
    return names


def _assigned_name(command: str) -> str:
    match = _LHS_NAME.match(str(command or "").strip())
    return match.group(1) if match else ""


def _step_number(row: dict[str, Any], fallback: int) -> int:
    try:
        return max(1, int(row.get("step")))
    except (TypeError, ValueError):
        return fallback


def _replace_identifier(text: str, old: str, new: str) -> str:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])")
    return pattern.sub(new, text)


def _safe_identifier(name: str, used_names: set[str]) -> str:
    if name.lower() in _GREEK_ALIASES:
        base = f"param_{name.lower()}"
    elif name in _RESERVED_NAMES:
        base = f"obj_{name}"
    else:
        base = f"obj_{name}"

    candidate = base
    suffix = 1
    while (
        candidate in used_names
        or candidate in _RESERVED_NAMES
        or candidate.lower() in _GREEK_ALIASES
    ):
        suffix += 1
        candidate = f"{base}_{suffix}"
    return candidate


def _split_top_level_pair(text: str) -> tuple[str, str] | None:
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        elif char == "," and depth == 0:
            left = text[:index].strip()
            right = text[index + 1:].strip()
            if left and right:
                return left, right
            return None
    return None


def _rewrite_point_plus_tuple_command(command: str) -> str:
    match = _POINT_PLUS_TUPLE_COMMAND.match(str(command or "").strip())
    if not match:
        return command
    lhs, base, operator, tuple_body = match.groups()
    pair = _split_top_level_pair(tuple_body)
    if pair is None:
        return command
    dx, dy = pair
    return f"{lhs} = (x({base}) {operator} {dx}, y({base}) {operator} {dy})"


def _rewrite_intersection_element_command(
    command: str,
    intersection_lists: dict[str, tuple[str, str]],
) -> str:
    match = _ELEMENT_OF_LIST_ASSIGN.match(str(command or "").strip())
    if not match:
        return command
    lhs, list_name, index = match.groups()
    pair = intersection_lists.get(list_name)
    if pair is None:
        return command
    first, second = pair
    return f"{lhs} = Intersect({first}, {second}, {index})"


def _normalize_optional_script(raw: dict[str, Any]) -> dict[str, Any]:
    optional_script = dict(raw.get("optional_script") or {})
    if not optional_script:
        optional_script = {"needed": False}
    if optional_script.get("needed") is False:
        optional_script["script_type"] = "none"
        optional_script["trigger"] = "none"
        optional_script.setdefault("reason", "")
        optional_script.setdefault("target_object", "")
        optional_script.setdefault("script_body", "")
    return optional_script


def _is_soft_expected_object(row: dict[str, Any]) -> bool:
    name = str(row.get("name") or "").strip().lower()
    object_type = str(row.get("type") or "").strip().lower()
    role = str(row.get("role") or "").strip().lower()
    if object_type in {"text", "label", "caption", "dynamic_text"}:
        return True
    if name.endswith("_text") or name.endswith("text"):
        return True
    return any(token in role for token in ("text", "label", "caption", "display", "annotation"))


def _is_support_expected_object(row: dict[str, Any]) -> bool:
    object_type = str(row.get("type") or "").strip().lower()
    role = str(row.get("role") or "").strip().lower()
    return (
        role.startswith("support:")
        or any(token in role for token in ("support", "helper", "auxiliary"))
        or object_type in {"helper", "auxiliary"}
    )


def _purpose_tier(row: dict[str, Any], *, index: int = 0) -> str:
    purpose = str(row.get("purpose") or "").strip().lower()
    if purpose.startswith("[core]"):
        return "core"
    if purpose.startswith("[support]"):
        return "support"
    if purpose.startswith("[annotation]"):
        return "annotation"
    command = str(row.get("command") or "").strip()
    head = _command_head(command)
    rhs_head = _command_head(command.split("=", 1)[1].strip()) if "=" in command else head
    if head in {"Text", "SetCaption", "ShowLabel"} or rhs_head in {"Text", "SetCaption", "ShowLabel"}:
        return "annotation"
    return "core" if index < 8 else "support"


def _with_tier_prefix(purpose: str, tier: str) -> str:
    text = str(purpose or "").strip()
    if text.lower().startswith(("[core]", "[support]", "[annotation]")):
        return text
    return f"[{tier}] {text or 'Generated GeoGebra command'}"


def _role_with_tier(role: str, tier: str) -> str:
    text = str(role or "").strip()
    lowered = text.lower()
    if lowered.startswith(("core:", "support:", "annotation:")):
        return text
    return f"{tier}: {text or 'expected object'}"


def _split_expected_name_tier(name: str) -> tuple[str, str | None]:
    stripped = str(name or "").strip()
    lowered = stripped.lower()
    for tier in ("core", "support", "annotation"):
        prefix = f"{tier}:"
        if lowered.startswith(prefix):
            return stripped[len(prefix):].strip(), tier
    return stripped, None


def _command_tiers_by_lhs(commands: list[dict[str, Any]]) -> dict[str, str]:
    tiers: dict[str, str] = {}
    for index, row in enumerate(commands):
        name = _assigned_name(str((row or {}).get("command") or ""))
        if name and name not in tiers:
            tiers[name] = _purpose_tier(dict(row or {}), index=index)
    return tiers


def _infer_expected_object_type(command: str) -> str:
    cmd = str(command or "").strip()
    lhs = _LHS_NAME.match(cmd)
    if lhs and "(" in cmd.split("=", 1)[0]:
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


def _infer_core_expected_objects(commands: list[dict[str, Any]], *, limit: int = 6) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(commands):
        command = str((row or {}).get("command") or "").strip()
        if _purpose_tier(dict(row or {}), index=index) != "core":
            continue
        name = _assigned_name(command)
        if not name:
            continue
        if name in seen:
            continue
        out.append({
            "name": name,
            "type": _infer_expected_object_type(command),
            "role": "core: object inferred by local sanitizer",
        })
        seen.add(name)
        if len(out) >= limit:
            break
    return out


def _renumber_steps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        out.append({**dict(row), "step": index})
    return out


def _migrate_property_creations(
    commands: list[dict[str, Any]],
    property_commands: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    migrated: list[dict[str, Any]] = []
    kept_properties: list[dict[str, Any]] = []
    for index, row in enumerate(property_commands, start=1):
        item = dict(row)
        command = str(item.get("command") or "").strip()
        if command and _LHS_NAME.match(command):
            purpose = str(item.get("purpose") or "").strip()
            item["purpose"] = _with_tier_prefix(purpose, "support")
            item["command"] = command
            item["step"] = len(commands) + len(migrated) + 1
            migrated.append(item)
            continue
        item["step"] = _step_number(item, index)
        item["command"] = command
        kept_properties.append(item)
    return [*commands, *migrated], kept_properties


def _trim_command_budget(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(commands) <= _EXECUTION_MAX_COMMANDS:
        return _renumber_steps(commands)

    indexed = list(enumerate(commands))
    selected: list[tuple[int, dict[str, Any]]] = []
    selected_indices: set[int] = set()

    for desired_tier in ("core", "support", "annotation"):
        for index, row in indexed:
            if index in selected_indices:
                continue
            if _purpose_tier(dict(row), index=index) != desired_tier:
                continue
            selected.append((index, row))
            selected_indices.add(index)
            if len(selected) >= _EXECUTION_MAX_COMMANDS:
                return _renumber_steps([row for _, row in sorted(selected, key=lambda item: item[0])])

    return _renumber_steps([row for _, row in sorted(selected, key=lambda item: item[0])])


def _normalize_numeric_functions(command: str) -> str:
    return _ABS_FUNCTION.sub("abs(", str(command or ""))


def _compact_expression(expr: str) -> str:
    return re.sub(r"\s+", "", str(expr or ""))


def _unique_name(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 1
    while candidate in used:
        suffix += 1
        candidate = f"{base}_{suffix}"
    used.add(candidate)
    return candidate


def _rewrite_support_numeric_commands(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used = _extract_step_defined_names(commands)
    distance_helpers: dict[tuple[str, str, str], tuple[str, str]] = {}
    out: list[dict[str, Any]] = []

    for index, row in enumerate(commands):
        item = dict(row)
        command = _normalize_numeric_functions(str(item.get("command") or "").strip())
        item["command"] = command
        tier = _purpose_tier(item, index=index)

        abs_match = _ABS_DISTANCE_ASSIGN.match(command)
        if tier != "core" and abs_match:
            lhs, first, second, expr = abs_match.groups()
            key = (first, second, _compact_expression(expr))
            helper_names = distance_helpers.get(key)
            if helper_names is None:
                distance_name = _unique_name(f"d{first}{second}", used)
                gap_name = _unique_name("gap", used)
                distance_helpers[key] = (distance_name, gap_name)
                out.append({
                    "step": item.get("step", len(out) + 1),
                    "purpose": "[support] Compute reusable distance measurement",
                    "command": f"{distance_name}=Distance({first},{second})",
                })
                out.append({
                    "step": item.get("step", len(out) + 1),
                    "purpose": "[support] Compute reusable comparison value",
                    "command": f"{gap_name}={expr.strip()}",
                })
            else:
                distance_name, gap_name = helper_names
            distance_name, gap_name = distance_helpers[key]
            item["purpose"] = _with_tier_prefix(str(item.get("purpose") or ""), "support")
            item["command"] = f"{lhs}=abs({distance_name}-{gap_name})"
            out.append(item)
            continue

        plus_match = _DISTANCE_PLUS_ASSIGN.match(command)
        if tier != "core" and plus_match:
            lhs, first, second, expr = plus_match.groups()
            helper_names = distance_helpers.get((first, second, _compact_expression(expr)))
            if helper_names is not None:
                distance_name, gap_name = helper_names
                item["purpose"] = _with_tier_prefix(str(item.get("purpose") or ""), "support")
                item["command"] = f"{lhs}={distance_name}+{gap_name}"
        out.append(item)

    return _renumber_steps(out)


def _align_expected_created_objects(
    rows: list[Any],
    *,
    rewrite_map: dict[str, str],
    commands: list[dict[str, Any]],
    support_names: set[str] | None = None,
) -> list[dict[str, str]]:
    command_tiers = _command_tiers_by_lhs(commands)
    forced_support_names = support_names or set()
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()

    for raw in rows:
        if isinstance(raw, str):
            name, explicit_tier = _split_expected_name_tier(raw)
            item = {
                "name": name,
                "type": "object",
                "role": _role_with_tier("object from draft", explicit_tier or "core"),
            }
        elif isinstance(raw, dict):
            name, explicit_tier = _split_expected_name_tier(str(raw.get("name") or ""))
            item = {
                "name": name,
                "type": str(raw.get("type") or "object").strip() or "object",
                "role": str(raw.get("role") or "core object from draft").strip() or "core object from draft",
            }
            if explicit_tier:
                item["role"] = _role_with_tier(item["role"], explicit_tier)
        else:
            continue

        if item["role"].lower().startswith("annotation:"):
            continue
        item["name"] = rewrite_map.get(item["name"], item["name"])
        if not item["name"] or item["name"] in seen:
            continue

        tier = command_tiers.get(item["name"])
        if item["name"] in forced_support_names:
            item["role"] = _role_with_tier(item["role"], "support")
        elif tier == "annotation":
            continue
        elif tier == "support":
            item["role"] = _role_with_tier(item["role"], "support")
        elif tier == "core":
            item["role"] = _role_with_tier(item["role"], "core")
        elif _is_soft_expected_object(item):
            continue

        normalized.append(item)
        seen.add(item["name"])

    has_core_expected = any(command_tiers.get(item["name"]) == "core" for item in normalized)
    if not has_core_expected:
        for item in _infer_core_expected_objects(commands):
            if item["name"] not in seen:
                normalized.insert(0, item)
                seen.add(item["name"])
        # Preserve the inferred command order after front-inserting multiple rows.
        inferred_names = [item["name"] for item in _infer_core_expected_objects(commands)]
        normalized.sort(
            key=lambda item: (
                0 if item["name"] in inferred_names else 1,
                inferred_names.index(item["name"]) if item["name"] in inferred_names else 0,
            )
        )

    return normalized


def _sanitize_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    if str(payload.get("engine") or "jsxgraph") != "geogebra":
        return payload, {}

    commands = [str(cmd or "") for cmd in list(payload.get("ggb_commands") or [])]
    used_names = set(_extract_defined_names(commands))
    rewrite_map: dict[str, str] = {}
    for raw in commands:
        match = _LHS_NAME.match(raw.strip())
        if not match:
            continue
        name = match.group(1)
        if name.lower() in _GREEK_ALIASES or name in _RESERVED_NAMES:
            rewrite_map[name] = _safe_identifier(name, used_names | set(rewrite_map.values()))

    if not rewrite_map:
        return payload, {}

    sanitized = dict(payload)
    sanitized["ggb_commands"] = commands
    for old, new in rewrite_map.items():
        sanitized["ggb_commands"] = [
            _replace_identifier(cmd, old, new) for cmd in sanitized["ggb_commands"]
        ]

    params = []
    for param in list(sanitized.get("params") or []):
        row = dict(param)
        row["name"] = rewrite_map.get(str(row.get("name") or ""), str(row.get("name") or ""))
        params.append(row)
    sanitized["params"] = params

    animation = sanitized.get("animation")
    if animation:
        anim = dict(animation)
        anim["drives"] = [rewrite_map.get(str(name or ""), str(name or "")) for name in list(anim.get("drives") or [])]
        sanitized["animation"] = anim

    return sanitized, rewrite_map


def _sanitize_execution_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    commands = [dict(row) for row in list(payload.get("commands") or [])]
    property_commands = [dict(row) for row in list(payload.get("property_commands") or [])]
    commands = [
        {
            **dict(row),
            "step": _step_number(dict(row), index),
            "command": str((row or {}).get("command") or "").strip(),
        }
        for index, row in enumerate(commands, start=1)
        if str((row or {}).get("command") or "").strip()
    ]
    commands, property_commands = _migrate_property_creations(commands, property_commands)

    used_names = set(_extract_step_defined_names(commands))
    rewrite_map: dict[str, str] = {}
    for row in commands:
        match = _LHS_NAME.match(str(row.get("command") or "").strip())
        if not match:
            continue
        name = match.group(1)
        if name.lower() in _GREEK_ALIASES or name in _RESERVED_NAMES:
            rewrite_map[name] = _safe_identifier(name, used_names | set(rewrite_map.values()))

    intersection_lists: dict[str, tuple[str, str]] = {}
    for row in commands:
        command = _replace_many_identifiers(str(row.get("command") or ""), rewrite_map)
        match = _INTERSECT_LIST_ASSIGN.match(command.strip())
        if match:
            name, first, second = match.groups()
            intersection_lists[name] = (first, second)

    sanitized_commands: list[dict[str, Any]] = []
    dropped_support_names: set[str] = set()
    for index, row in enumerate(commands):
        command = _replace_many_identifiers(str(row.get("command") or ""), rewrite_map)
        if _INTERSECT_LIST_ASSIGN.match(command.strip()):
            continue
        command = _rewrite_intersection_element_command(command, intersection_lists)
        command = _rewrite_point_plus_tuple_command(command)
        command = _normalize_numeric_functions(command)
        if _SETVALUE.match(command.strip()) or _SETCONDITION_TO_SHOW.match(command.strip()):
            continue
        if _CONDITIONAL_OBJECT_CREATION.search(command.strip()):
            tier = _purpose_tier({**dict(row), "command": command}, index=index)
            if tier != "core":
                name = _assigned_name(command)
                if name:
                    dropped_support_names.add(name)
                continue
        sanitized_commands.append({**row, "command": command})
    sanitized_commands = _rewrite_support_numeric_commands(sanitized_commands)
    sanitized_commands = _trim_command_budget(sanitized_commands)

    sanitized = dict(payload)
    sanitized["commands"] = sanitized_commands
    defined_after_budget = _extract_step_defined_names(sanitized_commands)
    sanitized_property_commands: list[dict[str, Any]] = []
    for row in property_commands:
        item = dict(row)
        command = _replace_many_identifiers(str((row or {}).get("command") or ""), rewrite_map)
        command = _normalize_numeric_functions(command)
        if _SETVALUE.match(command.strip()) or _SETCONDITION_TO_SHOW.match(command.strip()):
            continue
        target = _property_target(command)
        if target and target not in defined_after_budget:
            continue
        sanitized_property_commands.append({**item, "command": command})
        if len(sanitized_property_commands) >= _EXECUTION_MAX_PROPERTY_COMMANDS:
            break
    sanitized["property_commands"] = _renumber_steps(sanitized_property_commands)
    interaction_objects: list[dict[str, Any]] = []
    for row in list(payload.get("interaction_objects") or []):
        item = {
            **dict(row),
            "name": rewrite_map.get(str((row or {}).get("name") or ""), str((row or {}).get("name") or "")),
        }
        raw_type = str(item.get("type") or "").strip()
        if raw_type in _NON_UI_INTERACTION_TYPES:
            continue
        if raw_type == "":
            item["type"] = "none"
        interaction_objects.append(item)
    sanitized["interaction_objects"] = interaction_objects
    optional_script = _normalize_optional_script(payload)
    if optional_script:
        optional_script["target_object"] = rewrite_map.get(
            str(optional_script.get("target_object") or ""),
            str(optional_script.get("target_object") or ""),
        )
        optional_script["script_body"] = _replace_many_identifiers(
            str(optional_script.get("script_body") or ""),
            rewrite_map,
        )
        sanitized["optional_script"] = optional_script
    sanitized["expected_created_objects"] = _align_expected_created_objects(
        list(payload.get("expected_created_objects") or []),
        rewrite_map=rewrite_map,
        commands=sanitized_commands,
        support_names=dropped_support_names,
    )
    return sanitized, rewrite_map


def sanitize_geogebra_visualization_with_report(
    viz: VisualizationDraft | Visualization | dict[str, Any],
) -> GeoGebraSanitizationResult:
    payload = viz.model_dump(mode="json") if hasattr(viz, "model_dump") else dict(viz)
    payload, rewrite_map = _sanitize_payload(payload)

    try:
        strict = Visualization.model_validate(payload)
    except ValidationError as err:
        raise GeoGebraValidationError(_pydantic_errors_to_violations(err)) from err

    defined_names = _extract_defined_names(strict.ggb_commands)
    violations: list[dict[str, str]] = []
    for param in strict.params:
        if param.name not in defined_names:
            violations.append({
                "kind": "param_binding",
                "message": f"param '{param.name}' does not match any defined GeoGebra object",
            })
    if strict.animation:
        for drive in strict.animation.drives:
            if drive not in defined_names:
                violations.append({
                    "kind": "animation_binding",
                    "message": f"animation drive '{drive}' does not match any defined GeoGebra object",
                })
    if violations:
        raise GeoGebraValidationError(violations)

    return GeoGebraSanitizationResult(
        visualization=strict,
        rewrite_map=rewrite_map,
        defined_names=sorted(defined_names),
    )


def sanitize_geogebra_visualization(
    viz: VisualizationDraft | Visualization | dict[str, Any],
) -> Visualization:
    return sanitize_geogebra_visualization_with_report(viz).visualization


def _replace_many_identifiers(text: str, rewrite_map: dict[str, str]) -> str:
    out = str(text or "")
    for old, new in rewrite_map.items():
        out = _replace_identifier(out, old, new)
    return out


def sanitize_geogebra_execution_payload_with_report(
    payload: GeoGebraExecutionPayload | dict[str, Any],
) -> GeoGebraExecutionPayloadSanitizationResult:
    raw = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else dict(payload)
    raw, rewrite_map = _sanitize_execution_payload(raw)

    try:
        strict = GeoGebraExecutionPayload.model_validate(raw)
    except ValidationError as err:
        raise GeoGebraValidationError(_pydantic_errors_to_violations(err)) from err

    defined_names = _extract_step_defined_names([row.model_dump(mode="json") for row in strict.commands])
    violations: list[dict[str, str]] = []

    for obj in strict.interaction_objects:
        if obj.type != "none" and obj.name and obj.name not in defined_names:
            violations.append({
                "kind": "interaction_binding",
                "message": f"interaction object '{obj.name}' does not match any defined GeoGebra object",
            })
    if strict.optional_script.needed and strict.optional_script.target_object:
        if strict.optional_script.target_object not in defined_names:
            violations.append({
                "kind": "script_binding",
                "message": f"optional script target '{strict.optional_script.target_object}' does not match any defined GeoGebra object",
            })
    for obj in strict.expected_created_objects:
        row = obj.model_dump(mode="json")
        if (
            obj.name not in defined_names
            and not _is_soft_expected_object(row)
            and not _is_support_expected_object(row)
        ):
            violations.append({
                "kind": "expected_object",
                "message": f"expected created object '{obj.name}' does not match any defined GeoGebra object",
            })
    if violations:
        raise GeoGebraValidationError(violations)

    return GeoGebraExecutionPayloadSanitizationResult(
        payload=strict,
        rewrite_map=rewrite_map,
        defined_names=sorted(defined_names),
    )


def _command_head(command: str) -> str:
    match = _COMMAND_HEAD.match(str(command or "").strip())
    return match.group(1) if match else ""


def _property_target(command: str) -> str:
    match = _PROPERTY_TARGET.match(str(command or "").strip())
    return match.group(1) if match else ""


def _static_command_violations(commands: list[str], *, property_command: bool = False) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for idx, raw in enumerate(commands, start=1):
        cmd = str(raw or "").strip()
        if not cmd:
            violations.append({
                "kind": "empty_command",
                "message": f"GeoGebra command #{idx} is empty",
            })
            continue
        if len(cmd) > _EXECUTION_MAX_COMMAND_LEN:
            violations.append({
                "kind": "command_length",
                "message": (
                    f"GeoGebra command #{idx} is too long "
                    f"({len(cmd)} > {_EXECUTION_MAX_COMMAND_LEN})"
                ),
            })
        if "\n" in cmd or "\r" in cmd:
            violations.append({
                "kind": "command_newline",
                "message": f"GeoGebra command #{idx} contains a newline; split it into commands",
            })

        head = _command_head(cmd)
        if head in _VIEW_DIRECTIVES:
            violations.append({
                "kind": "view_directive",
                "message": (
                    f"GeoGebra command #{idx} uses {head}; put view/axes/grid settings "
                    "in ggb_settings or the frontend config, not commands"
                ),
            })
        if _POINT_PLUS_VECTOR.match(cmd) or _POINT_PLUS_TUPLE.match(cmd):
            violations.append({
                "kind": "fragile_vector_expression",
                "message": (
                    f"GeoGebra command #{idx} uses point-plus-vector shorthand. "
                    "Use explicit coordinates such as P=(x(A)+dx, y(A)+dy)."
                ),
            })
        if _VECTOR_POINT_DIFFERENCE.search(cmd):
            violations.append({
                "kind": "fragile_vector_expression",
                "message": (
                    f"GeoGebra command #{idx} uses Vector(B-A)-style syntax. "
                    "Use Vector(A,B) or explicit coordinate formulas."
                ),
            })
        if _CONDITIONAL_OBJECT_CREATION.search(cmd):
            violations.append({
                "kind": "conditional_object_creation",
                "message": (
                    f"GeoGebra command #{idx} creates an object inside If(...). "
                    "Use a simpler static fallback or create stable base objects."
                ),
            })
        if _SETCOLOR_NAMED.search(cmd):
            violations.append({
                "kind": "setcolor_named",
                "message": (
                    f"GeoGebra command #{idx} uses a named color. "
                    "Use SetColor(obj, r, g, b)."
                ),
            })
        if _SETVALUE.match(cmd):
            violations.append({
                "kind": "setvalue",
                "message": (
                    f"GeoGebra command #{idx} uses SetValue(...). "
                    "Put initial values in params/defaults instead."
                ),
            })
        if _SETCONDITION_TO_SHOW.match(cmd):
            violations.append({
                "kind": "setcondition_to_show",
                "message": (
                    f"GeoGebra command #{idx} uses SetConditionToShowObject, "
                    "which is not stable through the Apps API."
                ),
            })
        if property_command and _LHS_NAME.match(cmd):
            violations.append({
                "kind": "property_command_creates_object",
                "message": (
                    f"GeoGebra property command #{idx} appears to create an object. "
                    "Move object creation into commands[]."
                ),
            })
    return violations


def _validate_property_targets(
    property_commands: list[str],
    *,
    defined_names: set[str],
) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for idx, command in enumerate(property_commands, start=1):
        target = _property_target(command)
        if target and target not in defined_names:
            violations.append({
                "kind": "property_target",
                "message": (
                    f"GeoGebra property command #{idx} targets '{target}', "
                    "which is not created by commands[]"
                ),
            })
    return violations


def _validate_execution_payload_static(payload: GeoGebraExecutionPayload) -> GeoGebraValidationReport:
    command_strings = [row.command for row in payload.commands]
    property_command_strings = [row.command for row in payload.property_commands]
    defined_names = _extract_step_defined_names([row.model_dump(mode="json") for row in payload.commands])

    violations: list[dict[str, str]] = []
    if len(command_strings) > _EXECUTION_MAX_COMMANDS:
        violations.append({
            "kind": "command_budget",
            "message": (
                f"Stage 2 GeoGebra payload has too many creation commands "
                f"({len(command_strings)} > {_EXECUTION_MAX_COMMANDS}); use a simpler static fallback"
            ),
        })
    if len(property_command_strings) > _EXECUTION_MAX_PROPERTY_COMMANDS:
        violations.append({
            "kind": "property_command_budget",
            "message": (
                f"Stage 2 GeoGebra payload has too many property commands "
                f"({len(property_command_strings)} > {_EXECUTION_MAX_PROPERTY_COMMANDS}); "
                "remove nonessential styling"
            ),
        })

    violations.extend(_static_command_violations(command_strings))
    violations.extend(_static_command_violations(property_command_strings, property_command=True))
    violations.extend(_validate_property_targets(property_command_strings, defined_names=defined_names))

    core_expected = [
        row for row in payload.expected_created_objects
        if (
            not _is_soft_expected_object(row.model_dump(mode="json"))
            and not _is_support_expected_object(row.model_dump(mode="json"))
        )
    ]
    if not core_expected:
        violations.append({
            "kind": "core_expected_objects",
            "message": (
                "Stage 2 GeoGebra payload must expose at least one core expected object "
                "so the frontend can detect empty or partially failed renders"
            ),
        })

    if payload.optional_script.needed:
        violations.append({
            "kind": "optional_script",
            "message": (
                "Stage 2 default path does not allow optional scripts; "
                "use command_only or a static fallback"
            ),
        })

    if violations:
        raise GeoGebraValidationError(violations)

    return GeoGebraValidationReport(ok=True, render_ms=None, violations=[])


def _spec_field(spec: dict[str, Any] | None, *path: str) -> Any:
    value: Any = spec or {}
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _spec_list(spec: dict[str, Any] | None, *path: str) -> list[Any]:
    value = _spec_field(spec, *path)
    return value if isinstance(value, list) else []


def _command_for_name(commands: list[dict[str, Any]], name: str) -> str:
    for row in commands:
        command = str(row.get("command") or "").strip()
        if _assigned_name(command) == name:
            return command
    return ""


def _command_mentions_identifier(command: str, name: str) -> bool:
    if not name:
        return False
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", command) is not None


def _has_slider_command(commands: list[dict[str, Any]], name: str | None = None) -> bool:
    for row in commands:
        command = str(row.get("command") or "").strip()
        if name and _assigned_name(command) != name:
            continue
        rhs = command.split("=", 1)[1].strip() if "=" in command else command
        if "Slider(" in command or _command_head(rhs) == "Slider":
            return True
    return False


def _has_trace_or_locus(payload: GeoGebraExecutionPayload) -> bool:
    commands = [row.command for row in payload.commands]
    properties = [row.command for row in payload.property_commands]
    if any("Locus(" in command or _assigned_name(command).lower().startswith("trace") for command in commands):
        return True
    return any(command.strip().startswith("SetTrace(") for command in properties)


def _has_region_signal(payload: GeoGebraExecutionPayload) -> bool:
    commands = [row.command for row in payload.commands]
    properties = [row.command for row in payload.property_commands]
    region_heads = {"Polygon", "Integral", "Inequality", "IntersectPath"}
    for command in commands:
        rhs = command.split("=", 1)[1].strip() if "=" in command else command
        if _command_head(rhs) in region_heads or "Inequality(" in rhs:
            return True
    return any(command.strip().startswith(("SetFilling(", "SetColor(")) for command in properties)


def validate_payload_against_spec(
    payload: GeoGebraExecutionPayload,
    *,
    spec: dict[str, Any] | None,
) -> GeoGebraValidationReport:
    """Validate that a runnable payload still matches the selected visualization spec.

    This is not a theorem prover. It catches semantic drift that is cheap to
    detect deterministically: missing core objects, missing motion drivers,
    missing trace/region signals, and moving objects that do not depend on
    their declared driver.
    """
    if not spec:
        return GeoGebraValidationReport(ok=True, render_ms=None, violations=[])

    command_rows = [row.model_dump(mode="json") for row in payload.commands]
    defined_names = _extract_step_defined_names(command_rows)
    expected_names = {row.name for row in payload.expected_created_objects}
    interaction_names = {row.name for row in payload.interaction_objects}
    violations: list[dict[str, str]] = []

    preferred_app = str(spec.get("preferred_geogebra_app") or "").strip()
    if preferred_app and payload.preferred_geogebra_app != preferred_app:
        violations.append({
            "kind": "spec_app_mismatch",
            "message": (
                f"payload preferred_geogebra_app='{payload.preferred_geogebra_app}' "
                f"does not match spec preferred_geogebra_app='{preferred_app}'"
            ),
        })

    visible_or_highlighted = {
        str(name) for name in [
            *_spec_list(spec, "visual_design", "visible_objects"),
            *_spec_list(spec, "visual_design", "highlighted_objects"),
        ]
        if str(name or "").strip()
    }
    concrete_visible = {
        name for name in visible_or_highlighted
        if name in defined_names or name in expected_names
    }
    if visible_or_highlighted and not concrete_visible:
        violations.append({
            "kind": "spec_visible_objects_missing",
            "message": (
                "payload does not create or expect any spec visible/highlighted object: "
                + ", ".join(sorted(visible_or_highlighted))
            ),
        })

    if bool(_spec_field(spec, "geogebra_plan", "requires_slider")):
        if not payload.interaction_objects and not _has_slider_command(command_rows):
            violations.append({
                "kind": "spec_slider_missing",
                "message": "spec requires a slider, but payload has no slider command or interaction object",
            })

    if bool(_spec_field(spec, "geogebra_plan", "requires_trace")) and not _has_trace_or_locus(payload):
        fallback_used = bool(payload.fallback_used or str(payload.fallback_reason or "").strip())
        if not fallback_used:
            violations.append({
                "kind": "spec_trace_missing",
                "message": "spec requires trace/locus behavior, but payload has no trace/locus signal",
            })

    if bool(_spec_field(spec, "geogebra_plan", "requires_region_shading")) and not _has_region_signal(payload):
        fallback_used = bool(payload.fallback_used or str(payload.fallback_reason or "").strip())
        if not fallback_used:
            violations.append({
                "kind": "spec_region_missing",
                "message": "spec requires region shading, but payload has no region/filling signal",
            })

    contract = _spec_field(spec, "geometry_contract")
    if isinstance(contract, dict):
        core_objects = [
            item for item in list(contract.get("core_objects") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        for item in core_objects:
            name = str(item.get("name") or "").strip()
            must_be_visible = bool(item.get("must_be_visible", True))
            if must_be_visible and name not in defined_names and name not in expected_names:
                violations.append({
                    "kind": "geometry_core_object_missing",
                    "message": f"geometry core object '{name}' is not created or expected by payload",
                })

        motion = contract.get("motion") if isinstance(contract.get("motion"), dict) else {}
        driver = str((motion or {}).get("driver") or "").strip()
        moving_object = str((motion or {}).get("moving_object") or "").strip()
        path_type = str((motion or {}).get("path_type") or "none").strip()
        if path_type and path_type != "none":
            if driver and driver not in defined_names and driver not in interaction_names:
                violations.append({
                    "kind": "geometry_motion_driver_missing",
                    "message": f"geometry motion driver '{driver}' is not created or exposed by payload",
                })
            elif driver and driver in defined_names and not _has_slider_command(command_rows, driver):
                driver_command = _command_for_name(command_rows, driver)
                if "Slider(" not in driver_command:
                    violations.append({
                        "kind": "geometry_motion_driver_not_slider",
                        "message": f"geometry motion driver '{driver}' should be a stable slider parameter",
                    })
            if moving_object and moving_object not in defined_names and moving_object not in expected_names:
                violations.append({
                    "kind": "geometry_moving_object_missing",
                    "message": f"geometry moving object '{moving_object}' is not created or expected by payload",
                })
            if driver and moving_object and moving_object in defined_names:
                moving_command = _command_for_name(command_rows, moving_object)
                if moving_command and not _command_mentions_identifier(moving_command, driver):
                    fallback_used = bool(payload.fallback_used or str(payload.fallback_reason or "").strip())
                    if not fallback_used:
                        violations.append({
                            "kind": "geometry_motion_not_driver_bound",
                            "message": (
                                f"geometry moving object '{moving_object}' command does not reference "
                                f"declared driver '{driver}'"
                            ),
                        })

        invariant_names: set[str] = set()
        for invariant in list(contract.get("invariants") or []):
            if not isinstance(invariant, dict):
                continue
            invariant_names.update(
                str(name).strip()
                for name in list(invariant.get("objects") or [])
                if str(name or "").strip()
            )
        missing_invariant = sorted(
            name for name in invariant_names
            if name not in defined_names and name not in expected_names and name not in interaction_names
        )
        if missing_invariant:
            violations.append({
                "kind": "geometry_invariant_object_missing",
                "message": (
                    "geometry invariant references objects missing from payload: "
                    + ", ".join(missing_invariant)
                ),
            })

    if violations:
        raise GeoGebraValidationError(violations)

    return GeoGebraValidationReport(ok=True, render_ms=None, violations=[])


async def validate_geogebra_visualization(
    viz: Visualization,
    *,
    timeout_s: float = 20.0,
) -> GeoGebraValidationReport:
    if viz.engine != "geogebra":
        raise ValueError("GeoGebra validator requires engine='geogebra'")
    violations = _static_command_violations(list(viz.ggb_commands))
    if violations:
        raise GeoGebraValidationError(violations)
    return GeoGebraValidationReport(ok=True, render_ms=None, violations=[])


async def validate_geogebra_execution_payload(
    payload: GeoGebraExecutionPayload,
    *,
    spec: dict[str, Any] | None = None,
    timeout_s: float = 20.0,
) -> GeoGebraValidationReport:
    _validate_execution_payload_static(payload)
    validate_payload_against_spec(payload, spec=spec)
    return GeoGebraValidationReport(ok=True, render_ms=None, violations=[])
