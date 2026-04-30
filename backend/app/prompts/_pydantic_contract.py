"""Auto-derive a prompt-facing contract block from a Pydantic model.

Background: ``_compact_schema_for_gemini`` strips ``enum``, numeric
bounds, and string-length bounds from the JSON Schema we send to
Gemini, so the model never sees the actual literal tokens the
validator will accept. Hand-written contract sections in prompts then
silently drift from the Python schema.

This module walks a Pydantic v2 model and produces a self-describing
Markdown block that contains:

  * Every ``Literal[...]`` field with its allowed tokens.
  * Every numeric / length bound (``Field(ge=, le=, min_length=, max_length=)``).
  * Every cross-field rule declared on a model via the optional class
    variable ``__contract_rules__: ClassVar[list[str]]``.

The block is meant to be embedded verbatim into a system prompt next
to the JSON Schema reference.
"""

from __future__ import annotations

from typing import Literal, Union, get_args, get_origin

from annotated_types import Ge, Gt, Le, Lt, MaxLen, MinLen
from pydantic import BaseModel
from pydantic.fields import FieldInfo


def _unwrap(tp):
    """Strip Optional / Union[None, X] / list[X] / dict[_, X] wrappers."""
    origin = get_origin(tp)
    if origin is Union:
        non_none = [a for a in get_args(tp) if a is not type(None)]
        if len(non_none) == 1:
            return _unwrap(non_none[0])
        return tp
    if origin in (list, set, tuple):
        args = get_args(tp)
        if args:
            return _unwrap(args[0])
    if origin is dict:
        args = get_args(tp)
        if len(args) == 2:
            return _unwrap(args[1])
    return tp


def _literal_values(tp) -> list | None:
    inner = _unwrap(tp)
    if get_origin(inner) is Literal:
        return list(get_args(inner))
    return None


def _bound_summary(field_info: FieldInfo) -> str | None:
    parts: list[str] = []
    for meta in field_info.metadata or []:
        if isinstance(meta, Ge):
            parts.append(f">= {meta.ge}")
        elif isinstance(meta, Gt):
            parts.append(f"> {meta.gt}")
        elif isinstance(meta, Le):
            parts.append(f"<= {meta.le}")
        elif isinstance(meta, Lt):
            parts.append(f"< {meta.lt}")
        elif isinstance(meta, MinLen):
            parts.append(f"min length {meta.min_length}")
        elif isinstance(meta, MaxLen):
            parts.append(f"max length {meta.max_length}")
    return ", ".join(parts) if parts else None


def _is_pydantic_model(tp) -> bool:
    inner = _unwrap(tp)
    return isinstance(inner, type) and issubclass(inner, BaseModel)


def _walk(
    model_cls: type[BaseModel],
    *,
    path: str,
    visited: set[type[BaseModel]],
    literals: list[tuple[str, list]],
    bounds: list[tuple[str, str, str]],
    rules_by_model: list[tuple[str, list[str]]],
) -> None:
    if model_cls in visited:
        return
    visited.add(model_cls)

    rules = getattr(model_cls, "__contract_rules__", None)
    if rules:
        rules_by_model.append((path or model_cls.__name__, list(rules)))

    for field_name, field_info in model_cls.model_fields.items():
        field_path = f"{path}.{field_name}" if path else field_name
        annotation = field_info.annotation

        lit = _literal_values(annotation)
        if lit is not None:
            literals.append((field_path, lit))

        kind: str
        inner = _unwrap(annotation)
        origin = get_origin(annotation)
        if origin in (list, set, tuple):
            kind = "array"
        elif inner is int:
            kind = "integer"
        elif inner is float:
            kind = "number"
        else:
            kind = "value"
        bound = _bound_summary(field_info)
        if bound:
            bounds.append((field_path, kind, bound))

        if _is_pydantic_model(annotation):
            _walk(
                _unwrap(annotation),
                path=field_path,
                visited=visited,
                literals=literals,
                bounds=bounds,
                rules_by_model=rules_by_model,
            )


def summarize_pydantic_contract(model_cls: type[BaseModel]) -> str:
    """Produce a Markdown block describing literals, bounds, and rules.

    The output is deterministic so it can be diffed in tests.
    """
    literals: list[tuple[str, list]] = []
    bounds: list[tuple[str, str, str]] = []
    rules_by_model: list[tuple[str, list[str]]] = []
    _walk(
        model_cls,
        path="",
        visited=set(),
        literals=literals,
        bounds=bounds,
        rules_by_model=rules_by_model,
    )

    lines: list[str] = []
    lines.append("## Schema contract (auto-derived from Pydantic, must be obeyed exactly)")
    lines.append("")
    lines.append(
        "All values listed below are exact tokens. Never paraphrase, translate, "
        "decorate, or invent new variants. Use the literal string as written."
    )
    lines.append("")

    if literals:
        lines.append("### Literal fields and their allowed values")
        for path, values in literals:
            rendered = " | ".join(str(v) for v in values)
            lines.append(f"- `{path}`: {rendered}")
        lines.append("")

    if bounds:
        lines.append("### Numeric and length bounds")
        for path, kind, bound in bounds:
            lines.append(f"- `{path}` ({kind}): {bound}")
        lines.append("")

    if rules_by_model:
        lines.append("### Cross-field rules (validator will reject violations)")
        for model_name, rules in rules_by_model:
            lines.append(f"- {model_name}:")
            for rule in rules:
                lines.append(f"  - {rule}")
        lines.append("")

    lines.append(
        "If you are unsure which token applies, choose the most conservative "
        "literal value listed above instead of inventing a new one."
    )
    return "\n".join(lines)


__all__ = ["summarize_pydantic_contract"]
