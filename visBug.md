# Visualization Pipeline Bug & Defect Report

> Generated: 2026-04-22
> Scope: visualization generation (HAVizNew + legacy batch/storyboard paths)

---

## Table of Contents

1. [Critical — Schema & Data Bridge Defects](#1-critical--schema--data-bridge-defects)
2. [High — Code Logic Bugs](#2-high--code-logic-bugs)
3. [High — Data Loss & Storage Inconsistency](#3-high--data-loss--storage-inconsistency)
4. [Medium — Validator Fragility](#4-medium--validator-fragility)
5. [Medium — Prompt Quality Defects](#5-medium--prompt-quality-defects)
6. [Medium — Cross-Prompt Contradictions](#6-medium--cross-prompt-contradictions)
7. [Low — Frontend Runtime Gaps](#7-low--frontend-runtime-gaps)
8. [Proposed Fix Plan](#8-proposed-fix-plan)

---

## Verification Summary

This document has now been verified against the current codebase on 2026-04-22.

Status legend:
- `Confirmed` = real current bug or real current quality gap.
- `Partial` = the report found a real weakness, but the described failure mode or proposed fix is overstated.
- `Stale` = based on older code assumptions; not a current bug in the present implementation.
- `Future` = valid architectural gap, but not a current production defect.

Verification totals:
- Confirmed: BUG-01, BUG-02, BUG-03, BUG-04, BUG-06, BUG-08, BUG-12, BUG-13, BUG-15, BUG-17, BUG-18, BUG-21
- Partial: BUG-09, BUG-14, BUG-19
- Stale / not a current bug: BUG-05, BUG-07, BUG-16
- Hardening / cleanup / future-facing: BUG-10, BUG-11, BUG-20

Implementation tracking policy used below:
- `Track: P1` = implement soon; correctness / reliability impact.
- `Track: P2` = implement after P1; worthwhile but not the first unblocker.
- `Track: P3` = cleanup / consistency work.
- `Track: No current action` = do not implement now unless the architecture changes.

---

## 1. Critical — Schema & Data Bridge Defects

### BUG-01: `range_or_values` parsed via regex, frequently breaks sliders

**Verification:** `Confirmed`

**Current assessment:** This bug exists in the current code. The schema still stores `range_or_values` as free text, the backend still regex-extracts numeric bounds, and the frontend still has a regex-based fallback path. This is a real source of broken slider bounds.

**Implementation track:** `P1` — replace free-text ranges with a structured range model and remove regex parsing from both bridge layers.

**Files:**
- `backend/app/schemas/visualization_spec.py:188` — field `range_or_values: str`
- `backend/app/services/answer_job_service.py:195-216` — `_spec_params_json()`
- `frontend/components/JsxgraphSandbox.tsx` — `deriveVizParams()`

**Problem:**
`VisualizationParameter.range_or_values` is a free-text string (e.g., `"0 ≤ θ ≤ 2π, step 0.1"`). Both backend and frontend extract numeric bounds via regex:

```python
# answer_job_service.py:206
bounds = [float(part) for part in re.findall(r"-?\d+(?:\.\d+)?", item.range_or_values)]
```

This fails silently for:
- Symbolic expressions: `"0 to 2π"` → only extracts `0` and `2` (drops π)
- Non-standard formatting: `"theta ∈ [0, 6.28]"` → works but fragile
- Missing step: no `step` keyword → `step` field omitted entirely
- Negative ranges: `"-3 ≤ x ≤ 3, step 0.5"` → works but relies on regex order

When parsing fails, sliders get no `min`/`max` and render broken or invisible.

**Fix:**
Replace `range_or_values: str` with a structured model:

```python
class ParameterRange(BaseModel):
    min: float
    max: float
    step: float = 0.1

class VisualizationParameter(BaseModel):
    name: str
    type: ParameterType  # "slider" | "toggle" | "integer_step" | "discrete_choice"
    range: ParameterRange | None = None  # required for slider types
    default_value: float | bool = 0      # typed, not str
    meaning: str = ""
```

Update `_spec_params_json()` and frontend `deriveVizParams()` to read structured fields directly.

---

### BUG-02: `default_value` typed as `str`, causes `NaN` at runtime

**Verification:** `Confirmed`

**Current assessment:** This bug exists in the current code. `default_value` is still stringly typed across the HAVizNew path, and the frontend still depends on numeric coercion. `NaN` and invalid boolean coercion remain possible.

**Implementation track:** `P1` — make the field typed in the schema and bridge, and validate before persistence.

**Files:**
- `backend/app/schemas/visualization_spec.py:189` — `default_value: str`
- `backend/app/services/answer_job_service.py:203` — `"default": item.default_value`
- `frontend/components/vizCommon.tsx` — `Number(p.default)` for sliders

**Problem:**
The LLM may emit `default_value: "pi/4"` or `default_value: "√2"`. The backend passes the raw string to the frontend. When `Number("pi/4")` evaluates, it returns `NaN`, breaking the slider initial value and potentially the entire visualization.

Even when the value is numeric (e.g., `"1.5"`), the backend stores it as a string in `params_json` while the frontend `VizParam` type expects `number`. The implicit JS coercion `Number("1.5")` happens to work, but there's no validation.

**Fix:**
Change `default_value` to `float | bool` in the Pydantic schema. Add a Pydantic validator to coerce common expressions:

```python
@field_validator("default_value", mode="before")
@classmethod
def _coerce_default(cls, v: str) -> float | bool:
    if isinstance(v, (int, float, bool)):
        return v
    # Handle common symbolic defaults
    known = {"pi": 3.14159, "π": 3.14159, "e": 2.71828}
    return float(known.get(v.strip().lower(), v))
```

---

### BUG-03: `_spec_animation_json` never provides `duration_ms`

**Verification:** `Confirmed`

**Current assessment:** This is real schema drift. The bridge writes an animation JSON shape that does not match the stricter legacy animation schema and omits `duration_ms`. It looks more latent than immediately user-visible, but it is still an off-contract bug.

**Implementation track:** `P2` — either align the bridge output to the actual animation schema or introduce an explicit compact legacy animation payload with a documented contract.

**File:** `backend/app/services/answer_job_service.py:219-232`

**Problem:**
The function builds an animation dict with extra fields that are not in the `VizAnimation` schema:

```python
# answer_job_service.py:225-232
return {
    "kind": kind,
    "drives": drives,
    "driver": interaction.animation_driver,        # NOT in VizAnimation schema
    "description": interaction.animation_description, # NOT in VizAnimation schema
    "sequence": list(interaction.animation_sequence), # NOT in VizAnimation schema
    "stopping_condition_or_final_state": ...,        # NOT in VizAnimation schema
}
```

`VizAnimation` expects `kind`, `duration_ms`, `drives`. The `duration_ms` field is never populated. The extra fields are silently stored in JSONB (no Pydantic validation at persist time) but are never read.

**Fix:**
Add `duration_ms` derivation from the spec and remove extra fields:

```python
duration = interaction.animation_duration_ms  # add to VisualizationInteraction schema
return {
    "kind": kind,
    "duration_ms": duration or 3000,  # sensible default
    "drives": drives,
}
```

---

## 2. High — Code Logic Bugs

### BUG-04: jsxgraph_codegen repair loop reuses stale error context

**Verification:** `Confirmed`

**Current assessment:** The underlying issue is real, but the precise wording in the original report is slightly off. Each retry sees the latest failed code, but the repair conversation is rebuilt from the original prompt each time, so repair history does not accumulate across attempts.

**Implementation track:** `P1` — preserve repair history across retries instead of replacing the conversation each time.

**File:** `backend/app/services/jsxgraph_codegen_service.py:123-151`

**Problem:**
After the first repair attempt fails, the second iteration correctly updates `last_error` (line 151) and `code` (line 146). However, `_repair_messages()` at line 137 receives `invalid_code=code` where `code` was updated to `repaired` from the first attempt. On closer inspection this is actually correct — each repair iteration sees the most recent code.

**However**, the real issue is that `_repair_messages()` (line 33-58) appends the invalid code as an **assistant message** and violations as a **user message**. On the second repair, the conversation has:
1. Original system + user messages
2. First failed code (assistant)
3. First violations (user)
4. Second failed code (assistant) — but this is NOT appended; instead `messages_override` replaces the entire conversation

Wait — `messages_override` replaces all messages. So the second repair attempt only sees:
- Original system + user messages (from the template)
- First failed code (assistant)
- First violations (user)

It does NOT see the second failed code or second violations. The repair context effectively **resets to the first failure** on every attempt.

**Fix:**
Accumulate repair history:

```python
repair_history: list[dict] = []
# ... in the loop:
repair_history.append({"role": "assistant", "content": code})
repair_history.append({"role": "user", "content": _format_violations(err.violations)})

# Pass accumulated history
messages_override=_repair_messages(
    prompt=prompt,
    spec=spec,
    invalid_code=code,
    error=last_error,
    history=repair_history,  # NEW
)
```

---

### BUG-05: `select_recommended_visualization` crashes on empty bundle

**Verification:** `Stale`

**Current assessment:** The fallback logic would indeed crash on an empty list, but the current `VisualizationSpecBundle` schema already rejects empty bundles. So this is not a current reachable bug in normal validated flow.

**Implementation track:** `No current action` — optional defensive guard only.

**File:** `backend/app/services/visualization_spec_service.py:72-83`

**Problem:**

```python
# line 77-83
candidates = [v for v in bundle.visualizations
              if v.recommended and v.renderability_assessment.overall_readiness in {"ready", "mostly_ready"}]
if not candidates:
    candidates = list(bundle.visualizations)  # fallback
candidates.sort(key=lambda v: (v.priority, -v.renderability_assessment.implementation_stability_score))
return candidates[0]  # IndexError if bundle.visualizations is empty
```

If the bundle has zero visualizations, the fallback still produces an empty list and `candidates[0]` raises `IndexError`.

**Fix:**

```python
if not candidates:
    candidates = list(bundle.visualizations)
if not candidates:
    raise ValueError("VisualizationSpecBundle has no visualizations")
```

---

### BUG-06: `VisualizationSpecBundle._check_recommendation` impossible constraint

**Verification:** `Confirmed`

**Current assessment:** This impossible-validator edge case exists. If all candidates are `needs_revision`, the bundle can become unsatisfiable: none may be recommended, yet at least one must be recommended.

**Implementation track:** `P1` — relax the bundle contract for all-`needs_revision` cases. Do not silently mutate the model inside validation unless that behavior is explicitly intended.

**File:** `backend/app/schemas/visualization_spec.py:344-348`

**Problem:**
Two validators create an unsatisfiable combination:

1. `_check_semantics` (line 328-329): `if overall_readiness == "needs_revision" and recommended → raise`
2. `_check_recommendation` (line 346-347): `if not any(recommended) → raise`

If the LLM produces a bundle where both specs have `overall_readiness='needs_revision'`, there is **no valid output** — it cannot recommend any spec, but it must recommend at least one.

The LLM will hit this edge case on difficult/ambiguous problems and fail with a Pydantic validation error, triggering repair attempts that are equally likely to fail.

**Fix:**
Relax the recommendation constraint. The direction is correct, but the current recommended implementation is too magical because it mutates validated output. Prefer allowing zero recommended when all specs are `needs_revision`, then let selection or review fallback choose the least-bad candidate explicitly.

```python
@model_validator(mode="after")
def _check_recommendation(self) -> VisualizationSpecBundle:
    if not any(v.recommended for v in self.visualizations):
        if all(
            v.renderability_assessment.overall_readiness == "needs_revision"
            for v in self.visualizations
        ):
            return self
        raise ValueError("At least one visualization must be recommended")
    return self
```

Keep the `needs_revision -> not recommended` rule, but avoid pairing it with a bundle-level rule that makes some outputs impossible.

---

### BUG-07: `_build_visualization_row` hardcodes `engine="jsxgraph"`

**Verification:** `Stale`

**Current assessment:** This is not a current HAVizNew bug. The present Stage 2 architecture is intentionally JSXGraph-only, and the spec path does not currently model multiple render engines.

**Implementation track:** `No current action` — revisit only if HAVizNew is expanded to multi-engine rendering.

**File:** `backend/app/services/answer_job_service.py:235-250`

**Problem:**

```python
# line 243
engine="jsxgraph",
```

The HAVizNew spec-based path always stores `engine="jsxgraph"`. That is consistent with the current implementation because Stage 2 currently generates JSXGraph only. This becomes a real bug only if the spec path later supports multiple engines.

**Fix:**
Read engine from the spec or derive it:

```python
engine=spec.engine or "jsxgraph",  # add engine field to VisualizationSpec, or default jsxgraph
```

---

## 3. High — Data Loss & Storage Inconsistency

### BUG-08: `interactive_hints` not persisted, lost on page reload

**Verification:** `Confirmed`

**Current assessment:** This bug exists on the legacy visualization row path. The schemas and frontend expect `interactive_hints`, but the row model and persistence bridge do not store them.

**Implementation track:** `P2` — add storage, migration, persistence, and serialization.

**Files:**
- `backend/app/schemas/llm.py:277-299` — `VisualizationDraft` includes `interactive_hints: list[str]`
- `backend/app/db/models.py:210-229` — `VisualizationRow` has no `interactive_hints_json` column
- `backend/app/services/answer_job_service.py:178-192` — `_serialize_viz_row()` does not include it
- `frontend/app/q/[id]/page.tsx:1732` — reads `active.data.interactive_hints`

**Problem:**
The LLM generates `interactive_hints` (e.g., `["拖动滑块改变参数", "观察轨迹变化"]`). These are included in SSE events during generation, but:
- `VisualizationRow` has no column for them
- `_serialize_viz_row()` does not include them
- On page refresh, the frontend reads from the serialized row and gets `undefined`

Users see hints during generation but they disappear on reload.

**Fix:**
1. Add `interactive_hints_json = Column(JSONB, default=list)` to `VisualizationRow` (with Alembic migration)
2. Include it in `_persist_viz()` and `_build_visualization_row()`
3. Include it in `_serialize_viz_row()`

---

### BUG-09: Dual storage — `visualizations_json` vs `VisualizationRow` table

**Verification:** `Partial`

**Current assessment:** Dual storage is real, but the stale-data claim is overstated. Current stage-reset paths clear both the table rows and the solution JSON in the main reset flows. The architectural duplication is still undesirable and complicates consistency.

**Implementation track:** `P3` — reduce duplication when working in this area next; not the highest-priority reliability fix.

**Files:**
- `backend/app/services/answer_job_service.py:734-738` — `update_solution_visualizations()`
- `backend/app/routers/answer.py:408-415` — reads from solution first, then table
- `backend/app/services/vizcoder_service.py:313-375` — batch path only writes to table

**Problem:**
Visualization data lives in two places:
1. `question_solutions.visualizations_json` (JSONB column) — updated by `update_solution_visualizations()`
2. `visualizations` table (separate rows) — written by `_persist_viz()` and `_build_visualization_row()`

The batch path (`generate_visualizations_batch`) writes to the table only. If it fails before reaching `update_solution_visualizations()`, the table has data but the solution column is empty. The router reads from the solution first; if it finds data there, it uses it. If not, it falls back to the table.

This means:
- Partial results may be served from one source but not the other
- The system has to keep two representations aligned

**Fix:**
Single source of truth: always read from the table, and write `visualizations_json` as a denormalized cache only. Ensure both writes happen in the same transaction:

```python
# In the visualizing stage, after all viz rows are persisted:
await session.flush()  # ensure rows are written
rows = await session.scalars(select(VisualizationRow).where(...))
solution.visualizations_json = [_serialize_viz_row(r) for r in rows]
```

---

## 4. Medium — Validator Fragility

### BUG-10: Regex brace-matching over-captures on nested braces

**Verification:** `Hardening`

**Current assessment:** The validator regexes are fragile, but the specific breakage described here is not clearly reproduced in the current wrapper-matching flow because the expressions are anchored to the full input. This is a robustness improvement, not a demonstrated current defect.

**Implementation track:** `P3` — improve when touching validator normalization again.

**File:** `backend/app/services/viz_validator.py:21-60`

**Problem:**
All three function-unwrapping regexes use greedy `(?P<body>[\s\S]*)` followed by `\}`:

```python
# line 29-30
(?P<body>[\s\S]*)
\}
```

For code like:

```javascript
function renderVisualization(containerId, spec) {
    if (spec.hasAnimation) {
        animate();
    }  // ← greedy match continues past this
    drawBoard();
}  // ← matches here
```

The greedy `[\s\S]*` captures from the first `{` to the **last** `}` in the entire string. For complex JSXGraph code with many nested blocks, this means the "body" includes content beyond the actual function, potentially including trailing comments, extra code, or even other function definitions.

In practice this works because the regex requires the match to span the **entire** input (anchored with `^...$`), and the last `}` is typically the closing brace of the wrapper function. But if there's trailing code after the function, the body capture is wrong.

**Fix:**
Replace with brace-counting logic:

```python
def _extract_function_body(source: str, func_start: int) -> str:
    """Extract function body by counting braces from the opening {."""
    depth = 0
    start = source.index("{", func_start) + 1
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            if depth == 0:
                return source[start:i]
            depth -= 1
    return source[start:]  # fallback: no matching brace found
```

---

### BUG-11: `normalize_jsx_code` called twice in `validate_jsx_code`

**Verification:** `Hardening`

**Current assessment:** True but low-value. The double stripping is redundant cleanup, not an actual functional problem.

**Implementation track:** `P3` — cleanup only.

**File:** `backend/app/services/viz_validator.py:88-107, 119-120`

**Problem:**

```python
# line 119-120
source = _strip_code_fence(str(code_or_text))
source = source if _RENDER_VISUALIZATION_RE.match(source) else normalize_jsx_code(source)
```

`normalize_jsx_code()` (line 88) internally calls `_strip_code_fence()` at line 101. Then `validate_jsx_code` already strips code fences at line 119 before calling `normalize_jsx_code`. The double-strip is harmless but indicates a design confusion about where normalization happens.

**Fix:**
Move all normalization into `normalize_jsx_code()` and call it once:

```python
# In validate_jsx_code:
source = normalize_jsx_code(str(code_or_text))
```

---

## 5. Medium — Prompt Quality Defects

### BUG-12: All viz prompts have zero few-shot examples

**Verification:** `Confirmed`

**Current assessment:** This remains true across vizspec, jsxgraph_codegen, vizcoder, vizplanner, and vizitem. It is a real prompt-quality gap and likely contributes to output drift.

**Implementation track:** `P2` — add minimal, carefully chosen examples to the highest-value prompts first.

**Files:**
- `backend/app/prompts/vizspec_prompt.py` — no `fewshot_examples()` override
- `backend/app/prompts/jsxgraph_codegen_prompt.py` — no `fewshot_examples()` override
- `backend/app/prompts/vizcoder_prompt.py` — no `fewshot_examples()` override
- `backend/app/prompts/vizplanner_prompt.py` — no `fewshot_examples()` override
- `backend/app/prompts/vizitem_prompt.py` — no `fewshot_examples()` override

**Problem:**
All five visualization prompt classes inherit the base class default `fewshot_examples()` which returns `[]`. This means the LLM must infer the correct output structure from the JSON schema alone.

For complex nested schemas:
- `VisualizationSpecBundle`: ~35 fields per spec, 4 cross-field validators
- `VisualizationStoryboard`: ~15 fields per item, symbol_map + dependency constraints
- `Visualization` (batch): dual-engine with different required fields

Without examples, the model frequently:
- Omits required fields (especially nested ones like `renderability_assessment`)
- Fills enum fields with invalid values
- Gets the nesting wrong (e.g., puts `parameters` at the wrong level)

**Fix:**
Add 1 concrete example per prompt. Priority order:
1. `vizspec_prompt` — most complex schema, highest value from example
2. `jsxgraph_codegen_prompt` — critical for correct `renderVisualization` signature
3. `vizcoder_prompt` — batch output with correct 2-visualization structure
4. `vizplanner_prompt` — storyboard with symbol_map and dependency ordering
5. `vizitem_prompt` — single item with shared symbol reuse

---

### BUG-13: `jsxgraph_codegen` prompt missing H helper documentation

**Verification:** `Confirmed`

**Current assessment:** This is a real gap. The Stage 2 JSXGraph prompt now documents sandbox restrictions well, but still lacks the H helper reference available in the legacy coder prompt.

**Implementation track:** `P2` — add a concise H helper reference to Stage 2 prompt instructions.

**File:** `backend/app/prompts/jsxgraph_codegen_prompt.py`

**Problem:**
The system prompt tells the model to "use H helpers" but never documents what `H` provides. Compare with `vizcoder_prompt.py` which has a detailed `H_CHEATSHEET` with signatures like `H.point(board, x, y, opts)`, `H.slider(board, min, max, step, init, name)`, etc.

Without this reference, the LLM:
- Guesses H helper signatures (often wrong)
- Falls back to raw JSXGraph API calls (verbose, error-prone)
- Cannot use convenience methods that the sandbox actually provides

**Fix:**
Add an H helper reference section to the jsxgraph_codegen system prompt:

```
## H helper library reference

### Points & Geometry
- H.point(board, x, y, opts) — labeled point
- H.segment(board, p1, p2, opts) — segment with endpoints
- H.line(board, p1, p2, opts) — infinite line through two points
- H.circle(board, center, radius, opts) — circle (radius can be point or number)

### Sliders & Controls
- H.slider(board, min, max, step, init, name, opts) — interactive slider
- H.toggle(board, x, y, label, init) — boolean toggle button

### Styling
- H.setStyle(el, color, strokeWidth, opts) — apply visual style
- H.label(el, text) — set element label

### Animation
- H.animate(board, slider, targetValue, durationMs) — smooth slider animation
```

---

### BUG-14: `jsxgraph_codegen` prompt missing spec field guide and parameter reading pattern

**Verification:** `Partial`

**Current assessment:** The prompt gap is real, but the runtime diagnosis is partly stale. The current JSXGraph sandbox already mirrors updated parameter values back into `default_value` as well as `host_runtime.parameter_values`, so code reading the former can still stay in sync. The prompt still needs clearer parameter-reading guidance.

**Implementation track:** `P3` — prompt improvement, not a blocker-level runtime fix.

**File:** `backend/app/prompts/jsxgraph_codegen_prompt.py`

**Problem:**
The user prompt dumps the entire `VisualizationSpec` (35+ fields) as raw JSON. The model has no guidance on:
- Which fields are critical for code generation
- How to read parameter values from the spec
- How to handle re-render when the user moves a slider

This leads to generated code that:
- Reads params from wrong locations in the spec
- Ignores the `interaction_and_animation.parameters` structure
- Hardcodes initial values instead of reading `default_value`

**Fix:**
Add a "Critical spec fields" section:

```
## Reading spec parameters

Your code MUST read parameter values from:
  spec.interaction_and_animation.parameters[]

Each parameter has: name, type, default_value, range (min/max/step).

IMPORTANT: On re-render, updated values arrive at:
  spec.host_runtime?.parameter_values?.[paramName]

Use this pattern:
  const pv = spec.host_runtime?.parameter_values ?? {};
  const val = pv[paramName] ?? param.default_value;
```

---

### BUG-15: `vizspec` prompt missing field-level guidance

**Verification:** `Confirmed`

**Current assessment:** The auto-derived contract block improved literal correctness, but this prompt still lacks decision guidance for type choice, priority, readiness calibration, and recommendation criteria.

**Implementation track:** `P2` — add compact field-level calibration guidance.

**File:** `backend/app/prompts/vizspec_prompt.py`

**Problem:**
The prompt tells the model to "produce 1-2 visualization specifications" but provides no guidance for:
- When to use each `visualization_type` (8 types: `static_diagram`, `parametric_animation`, `locus_trace`, etc.)
- How to score `priority` (what does 1 vs 2 mean?)
- How to assess `renderability_assessment` scores (0-100 scale with no calibration)
- What makes a visualization `recommended`
- How to write a good `fallback_if_animation_is_too_complex`

The model fills these fields with random or inconsistent values.

**Fix:**
Add explicit guidance sections:

```
## visualization_type selection guide
- static_diagram: fixed geometric figure, no user interaction needed
- construction_steps: step-by-step geometric construction (compass, ruler)
- parametric_animation: one or more parameters drive continuous change
- locus_trace: trace the path of a moving point
- region_shading: highlight or shade a region (inequality, area)
- comparison_overlay: overlay two or more cases for comparison
- measurement_demo: interactive measurement of length, angle, area
- function_plot: plot and explore a function graph

## priority scoring
- 1 = highest priority (the most pedagogically impactful visualization)
- 2 = second priority (complementary, adds depth)

## renderability_assessment calibration
- clarity_score: 90+ = crystal clear math, 70-89 = minor ambiguity, <70 = unclear
- implementation_stability_score: 90+ = simple JSXGraph, 70-89 = moderate complexity, <70 = requires careful engineering
- overall_readiness: "ready" (90+ both), "mostly_ready" (70+ both), "needs_revision" (<70 either)
```

---

## 6. Medium — Cross-Prompt Contradictions

### BUG-16: Contradictory default engine across prompts

**Verification:** `Stale`

**Current assessment:** Mostly stale. The actual engine preference helpers are now largely centralized through settings, but some narrative prompt text still reflects older defaults and causes confusion.

**Implementation track:** `No current action` for behavior; `P3` if you want prompt-text cleanup.

**Files:**
- `backend/app/prompts/vizcoder_prompt.py:140` — `_preferred_engine()` returns `"jsxgraph"`
- `backend/app/prompts/vizplanner_prompt.py:19` — `_preferred_engine()` returns `"geogebra"`
- `backend/app/prompts/vizitem_prompt.py:30` — `_preferred_engine()` returns `"geogebra"`

**Problem:**
The remaining issue is mostly prompt wording and cross-prompt narrative consistency, not a live engine-selection bug in the current helper implementation.

This means the planner will generate GeoGebra-targeted storyboards, but if the system falls back to the batch coder (which prefers JSXGraph), the output engine is different. Conversely, in the storyboard path, the planner says "GeoGebra" but the system might not have GeoGebra support in the spec-based path (see BUG-07).

**Fix:**
Centralize engine preference into a single configuration source:

```python
# In a shared config or the prompt registry
def default_viz_engine() -> Literal["jsxgraph", "geogebra"]:
    return settings.visualization.preferred_engine  # single source of truth
```

All three prompts read from this config.

---

### BUG-17: Contradictory cardinality — "at most 2" vs "exactly 2"

**Verification:** `Confirmed`

**Current assessment:** This inconsistency is real across the new and legacy pipelines. It is not a current HAVizNew runtime failure by itself, but it is a real cross-pipeline contract mismatch.

**Implementation track:** `P3` — align legacy prompt/schema cardinality when touching that path next.

**Files:**
- `backend/app/prompts/vizspec_prompt.py:123` — "Produce at most 2. Choose fewer if only one is truly valuable."
- `backend/app/prompts/vizcoder_prompt.py:313` — "必须且只能生成 2 个可视化" (exactly 2)
- `backend/app/prompts/vizplanner_prompt.py:89` — "exactly 2 related visualization items"

**Problem:**
The new HAVizNew pipeline (vizspec) says "at most 2" and "choose fewer if only one is truly valuable." The legacy pipeline (vizcoder, vizplanner) says "exactly 2." If these pipelines share any validation or downstream code, a 1-visualization output from the new path could fail validation expecting 2.

**Schema enforcement:**
- `VisualizationSpecBundle`: `min_length=1, max_length=2` — allows 1 or 2
- `VisualizationListDraft`: `min_length=2, max_length=2` — requires exactly 2

**Fix:**
Align the legacy pipeline to allow 1-2 visualizations, matching the new pipeline's flexibility:

```python
# In VisualizationListDraft and VisualizationList:
visualizations: list[...] = Field(min_length=1, max_length=2)
```

And update vizcoder/vizplanner prompt text to say "1 or 2 visualizations."

---

### BUG-18: `vizitem_prompt` contradicts itself on engine when JSXGraph is selected

**Verification:** `Confirmed`

**Current assessment:** This contradiction is present in the current prompt and should be removed. It is a real instruction-quality bug.

**Implementation track:** `P2` — make the wording conditional on the resolved engine.

**File:** `backend/app/prompts/vizitem_prompt.py:113,153`

**Problem:**
When a storyboard item requires `engine="jsxgraph"`:
- Line 113: engine_policy says "This storyboard item explicitly requires engine='jsxgraph'"
- Line 153: "The overall rendering preference is still GeoGebra-first."

The model receives contradictory instructions about which engine to target. This can cause it to generate JSXGraph code with GeoGebra patterns, or vice versa.

**Fix:**
Make the engine preference line conditional:

```python
if resolved_engine == "jsxgraph":
    engine_preference = "This item uses JSXGraph. Generate JSXGraph code only."
else:
    engine_preference = "The overall rendering preference is GeoGebra-first."
```

---

## 7. Low — Frontend Runtime Gaps

### BUG-19: `host_runtime.parameter_values` not reliably read by generated code

**Verification:** `Partial`

**Current assessment:** The prompt concern is valid, but the frontend now already rewrites updated values into both `host_runtime.parameter_values` and parameter `default_value`. So the specific “slider moves but visualization never updates because only host_runtime changed” claim is no longer the full picture.

**Implementation track:** `P3` — improve prompt guidance; no urgent sandbox rewrite needed.

**Files:**
- `frontend/components/JsxgraphSandbox.tsx` — `updateSpecParameter()` injects `host_runtime.parameter_values`
- `frontend/public/viz/sandbox.html` — passes spec as-is to user code

**Problem:**
When the user moves a slider, `updateSpecParameter()` injects updated values into `spec.host_runtime.parameter_values`. But whether the generated `renderVisualization(containerId, spec)` code reads this field depends entirely on the LLM output.

Common failure mode: the generated code reads `spec.interaction_and_animation.parameters[i].default_value` which stays at the initial value. The slider moves but the visualization doesn't update.

**Fix:**
Add explicit parameter reading guidance to the jsxgraph_codegen prompt (see BUG-14). Sandbox-side normalization is now optional because the current sandbox already mirrors updated values back into the parameter defaults.

```javascript
// In sandbox.html, before calling renderVisualization:
if (spec.host_runtime?.parameter_values) {
    for (const p of spec.interaction_and_animation.parameters) {
        const updated = spec.host_runtime.parameter_values[p.name];
        if (updated !== undefined) {
            p.default_value = updated;  // normalize into the standard field
        }
    }
}
```

---

### BUG-20: GeoGebra sandbox does not receive `specJson`

**Verification:** `Future`

**Current assessment:** True as an architectural gap, but not a current functional bug because the present GeoGebra path does not consume spec JSON.

**Implementation track:** `No current action` — revisit only if spec-driven GeoGebra support is added.

**Files:**
- `frontend/components/VizSandbox.tsx` — routes to `GeoGebraSandbox` without spec
- `frontend/components/GeoGebraSandbox.tsx` — no spec prop

**Problem:**
`GeoGebraSandbox` only receives `ggbCommands`, `ggbSettings`, and `params`. It does not receive the `VisualizationSpec`. If future development needs spec-aware GeoGebra rendering (e.g., reading animation parameters from spec), this data is unavailable.

Currently this is not a functional issue since GeoGebra viz uses `ggb_commands` only. But combined with BUG-07 (spec path hardcodes JSXGraph), it means the GeoGebra path is effectively unreachable from the new pipeline.

**Fix:**
When spec-based GeoGebra support is added (BUG-07), pass `specJson` to `GeoGebraSandbox` as well.

---

### BUG-21: Frontend `VizParam.default` typed as `unknown`

**Verification:** `Confirmed`

**Current assessment:** This typing gap is real. Runtime coercion hides it, but the type should be narrowed so invalid defaults are caught earlier.

**Implementation track:** `P2` — narrow the type to the supported runtime value set.

**File:** `frontend/components/vizCommon.tsx`

**Problem:**
```typescript
default: unknown;  // should be number | boolean
```

No compile-time type safety. If a parameter has `default: "hello"`, the toggle handler does `!!p.default` → `true`, and the slider does `Number(p.default)` → `NaN`.

**Fix:**
```typescript
default: number | boolean;
```

---

## 8. Proposed Fix Plan

This section is now a verified implementation tracker rather than a raw proposal list.

Implementation status on 2026-04-22:
- Completed in code: BUG-01, BUG-02, BUG-03, BUG-04, BUG-06, BUG-08, BUG-09, BUG-10, BUG-11, BUG-12, BUG-13, BUG-14, BUG-15, BUG-17, BUG-18, BUG-19, BUG-21
- Intentionally left unchanged: BUG-05, BUG-07, BUG-16, BUG-20

### Phase 1 — P1 correctness and reliability work

| Bug | Status | Tracked implementation |
|-----|--------|------------------------|
| BUG-01 | Completed | Replaced free-text ranges with structured numeric range fields and updated backend/frontend bridges to consume them directly |
| BUG-02 | Completed | Changed `default_value` to typed numeric / boolean values and validated them before persistence |
| BUG-04 | Completed | Stage 2 repair retries now accumulate prior failure context instead of resetting to the original repair conversation |
| BUG-06 | Completed | Relaxed the impossible recommendation constraint when every candidate is `needs_revision` |

### Phase 2 — P2 quality fixes with clear payoff

| Bug | Status | Tracked implementation |
|-----|--------|------------------------|
| BUG-03 | Completed | `_spec_animation_json` now emits the compact animation shape expected by the stricter schema, including `duration_ms` |
| BUG-08 | Completed | Added `interactive_hints` persistence, migration, and serialization for visualization rows |
| BUG-12 | Completed | Added few-shot examples across the visualization prompts |
| BUG-13 | Completed | Added the H helper reference to the Stage 2 JSXGraph prompt |
| BUG-15 | Completed | Added field-level visualization-type / priority / readiness guidance to `vizspec` |
| BUG-18 | Completed | Removed contradictory engine wording from `vizitem_prompt` |
| BUG-21 | Completed | Narrowed frontend `VizParam.default` typing |

### Phase 3 — P3 consistency and cleanup work

| Bug | Status | Tracked implementation |
|-----|--------|------------------------|
| BUG-09 | Completed | Resume/read paths now prefer `VisualizationRow` data when rows exist, reducing stale dual-store reads |
| BUG-10 | Completed | Replaced greedy regex body extraction with brace-aware wrapper parsing |
| BUG-11 | Completed | Consolidated validator normalization so validation flows through one normalization path |
| BUG-14 | Completed | Added explicit JSXGraph parameter-reading guidance to the Stage 2 prompt |
| BUG-16 | Stale | Optional prompt-text cleanup only |
| BUG-17 | Completed | Aligned legacy prompt/schema cardinality with HAVizNew at 1–2 visualizations |
| BUG-19 | Completed | Kept prompt/runtime parameter-reading guidance explicit without adding an unnecessary sandbox rewrite |

### No current action

| Bug | Status | Reason |
|-----|--------|--------|
| BUG-05 | Stale | Schema validation already prevents empty bundles in normal flow |
| BUG-07 | Stale | HAVizNew Stage 2 is intentionally JSXGraph-only today |
| BUG-20 | Future | Relevant only if spec-driven GeoGebra support is added later |
