"""Compaction helpers for visualization prompts.

Goal: shrink the context sent to Gemini for the planner and per-item
visualization codegen calls. Logs showed each `vizitem` call was sending
~13K input tokens, the bulk of which was the full `AnswerPackage` (with
`similar_questions`, `key_points_of_answer`, `self_check`, full step
`rationale` / `why_this_step`) plus a duplicate JSON schema (the schema
is already enforced via the API's `response_json_schema` channel).

These helpers project the originals down to the fields each prompt
actually needs. They are lossless w.r.t. the bottleneck → image mapping:
question text, diagram description, solution-step *statements* and
*formulas*, the method pattern, and the storyboard structure.
"""

from __future__ import annotations

from typing import Any, Iterable


def _strip_keys(d: dict | None, keep: Iterable[str]) -> dict:
    if not isinstance(d, dict):
        return {}
    keep_set = set(keep)
    out = {}
    for k in keep_set:
        if k in d:
            v = d[k]
            if v is None:
                continue
            if isinstance(v, (list, str)) and not v:
                continue
            out[k] = v
    return out


def compact_question(parsed_question: dict | None) -> dict:
    """Keep only the fields a viz prompt needs to picture the figure."""
    return _strip_keys(
        parsed_question,
        keep=(
            "question_text",
            "diagram_description",
            "given",
            "find",
            "subject",
            "grade_band",
        ),
    )


def _compact_step(step: dict) -> dict:
    return _strip_keys(
        step,
        keep=("step_index", "statement", "formula", "viz_ref"),
    )


def _compact_method_pattern(mp: dict | None) -> dict:
    return _strip_keys(mp, keep=("name_cn", "when_to_use", "pitfalls"))


def compact_answer_for_planner(answer_package: dict | None) -> dict:
    """Drop everything the planner doesn't use to pick bottlenecks.

    Removed: similar_questions, key_points_of_answer, self_check,
    knowledge_points, question_understanding (the planner reads the
    parsed question for that), per-step rationale / why_this_step.
    """
    if not isinstance(answer_package, dict):
        return {}
    out: dict[str, Any] = {}
    steps = answer_package.get("solution_steps")
    if isinstance(steps, list) and steps:
        out["solution_steps"] = [_compact_step(s) for s in steps if isinstance(s, dict)]
    mp = _compact_method_pattern(answer_package.get("method_pattern"))
    if mp:
        out["method_pattern"] = mp
    final_answer = answer_package.get("final_answer")
    if final_answer:
        out["final_answer"] = final_answer
    kpq = answer_package.get("key_points_of_question")
    if isinstance(kpq, list) and kpq:
        out["key_points_of_question"] = kpq
    return out


def compact_answer_for_item(
    answer_package: dict | None,
    *,
    storyboard_item: dict | None = None,
) -> dict:
    """Per-item context: only the steps anchored by this storyboard item.

    `storyboard_item.anchor_refs[*].ref` may reference solution-step
    indices ("1", "2", ...) or "given:0" etc. We keep all anchored
    steps, plus a tiny fallback (full step list trimmed to statement +
    formula) so the per-item codegen still has continuity.
    """
    if not isinstance(answer_package, dict):
        return {}
    out: dict[str, Any] = {}

    anchored_idx: set[str] = set()
    if isinstance(storyboard_item, dict):
        for ref in storyboard_item.get("anchor_refs") or []:
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or "").strip()
            value = str(ref.get("ref") or "").strip()
            if kind == "solution_step" and value:
                anchored_idx.add(value)

    steps = answer_package.get("solution_steps")
    if isinstance(steps, list) and steps:
        if anchored_idx:
            picked = [
                s for s in steps
                if isinstance(s, dict)
                and str(s.get("step_index")) in anchored_idx
            ]
            # If the planner's anchors didn't match any step, fall back
            # to the first 2 steps so the figure still has narrative anchors.
            if not picked:
                picked = [s for s in steps[:2] if isinstance(s, dict)]
            # Anchored steps may need rationale to draw correctly.
            out["anchored_steps"] = [
                _strip_keys(s, keep=("step_index", "statement", "formula", "rationale", "viz_ref"))
                for s in picked
            ]
            # Trim the rest to statement-only so the model sees the arc.
            out["other_steps_outline"] = [
                _strip_keys(s, keep=("step_index", "statement"))
                for s in steps
                if isinstance(s, dict) and str(s.get("step_index")) not in anchored_idx
            ]
        else:
            out["solution_steps"] = [_compact_step(s) for s in steps if isinstance(s, dict)]
    mp = _compact_method_pattern(answer_package.get("method_pattern"))
    if mp:
        out["method_pattern"] = mp
    return out


def compact_storyboard_root(storyboard: dict | None, *, item: dict | None = None) -> dict:
    """Keep only the storyboard fields a per-item codegen call needs.

    Drops the `items` list (the current item is sent separately) and
    filters `symbol_map` / `shared_params` down to symbols actually
    referenced by the current item when possible.
    """
    if not isinstance(storyboard, dict):
        return {}
    out = _strip_keys(
        storyboard,
        keep=("theme_cn", "selection_rationale_cn", "sequence", "coverage_summary"),
    )

    used_syms: set[str] | None = None
    used_params: set[str] | None = None
    if isinstance(item, dict):
        ss = item.get("shared_symbols") or []
        sp = item.get("shared_params") or []
        if isinstance(ss, list):
            used_syms = {str(s) for s in ss if s}
        if isinstance(sp, list):
            used_params = {str(s) for s in sp if s}

    sym_map = storyboard.get("symbol_map") or []
    if isinstance(sym_map, list) and sym_map:
        if used_syms:
            sym_map = [s for s in sym_map if isinstance(s, dict) and s.get("symbol") in used_syms]
        if sym_map:
            out["symbol_map"] = sym_map

    shared_params = storyboard.get("shared_params") or []
    if isinstance(shared_params, list) and shared_params:
        if used_params:
            shared_params = [
                p for p in shared_params
                if isinstance(p, dict) and p.get("name") in used_params
            ]
        if shared_params:
            out["shared_params"] = shared_params

    return out


def compact_previous_items(previous_items: list | None) -> list[dict]:
    """Per-item codegen only needs to know what's already been covered."""
    if not isinstance(previous_items, list):
        return []
    out: list[dict] = []
    for it in previous_items:
        if not isinstance(it, dict):
            continue
        out.append(_strip_keys(it, keep=("id", "title_cn", "engine", "learning_goal_cn")))
    return out
