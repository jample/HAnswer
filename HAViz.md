# HAnswer Visualization Subsystem Notes

This document summarizes the current implementation of the `生成可视化` path in HAnswer, with emphasis on architecture, function boundaries, data flow, runtime behavior, and optimization-relevant constraints.

It is based on the current codebase, not only on `HAnswerR.md`. Where the code differs from the original spec, this document follows the code.

## 1. Scope

The visualization subsystem starts after Solver has already produced `AnswerPackage`, and ends when the frontend renders one or more interactive visualizations inside a sandbox.

Main coverage:

- backend prompt and schema design for visualization generation
- backend validation, persistence, and stage orchestration
- frontend engine dispatch, sandbox runtime, and answer-page integration
- current design tradeoffs and practical optimization hotspots

Main files:

- `backend/app/prompts/vizplanner_prompt.py`
- `backend/app/prompts/vizitem_prompt.py`
- `backend/app/prompts/vizcoder_prompt.py`
- `backend/app/prompts/schemas.py`
- `backend/app/schemas/llm.py`
- `backend/app/services/vizcoder_service.py`
- `backend/app/services/viz_validator.py`
- `backend/viz_validator/validate.mjs`
- `backend/app/services/answer_job_service.py`
- `backend/app/services/question_solution_service.py`
- `backend/app/routers/answer.py`
- `frontend/components/VizSandbox.tsx`
- `frontend/components/GeoGebraSandbox.tsx`
- `frontend/components/JsxgraphSandbox.tsx`
- `frontend/public/viz/geogebra-sandbox.html`
- `frontend/public/viz/sandbox.html`
- `frontend/app/q/[id]/page.tsx`

## 2. Current High-Level Architecture

### 2.1 Visualization pipeline

The active backend path is now `plan first, then per-viz codegen`.

### 2.1.1 Current active pipeline

```text
ParsedQuestion
  + AnswerPackage
    ↓
VizPlannerPrompt
    ↓
VisualizationStoryboard
  - identifies 3-4 highest-value learning bottlenecks
  - normalizes shared symbols / params
  - orders them into one teaching sequence
    ↓
for item in storyboard.sequence:
  VizItemPrompt
    ↓
  one Visualization
    ↓
  per-viz validation + persistence
    ↓
visualizations table persistence
    ↓
question_solutions.visualizations_json snapshot
  + visualizing-stage refs.storyboard
    ↓
/api/answer/:id/resume
    ↓
frontend VizPanel
    ↓
VizSandbox
  ├─ GeoGebraSandbox -> /viz/geogebra-sandbox.html
  └─ JsxgraphSandbox -> /viz/sandbox.html
```

### 2.1.2 Legacy batch path

```text
ParsedQuestion
  + AnswerPackage
  + solution_steps[].viz_ref
    ↓
VizCoderPrompt
    ↓
GeminiClient.call_structured(...)
    ↓
VisualizationList (Pydantic validation)
    ↓
per-viz backend validation
  - GeoGebra: command-shape validation only
  - JSXGraph: AST validator via Node/acorn
    ↓
visualizations table persistence
    ↓
question_solutions.visualizations_json snapshot
    ↓
/api/answer/:id/resume
    ↓
frontend VizPanel
    ↓
VizSandbox
  ├─ GeoGebraSandbox -> /viz/geogebra-sandbox.html
  └─ JsxgraphSandbox -> /viz/sandbox.html
```

### 2.2 Current engine strategy

The codebase originally shifted toward **GeoGebra-first**, with **JSXGraph as a fallback**.

- `engine="geogebra"` was introduced as the preferred path for many math/physics teaching visuals.
- `engine="jsxgraph"` remained available for cases GeoGebra could not express well, and for backward compatibility with existing rows/payloads.

This was a substantial evolution from `HAnswerR.md`, whose original visualization design was primarily JSXGraph-centric.

### 2.3 New engine policy

The subsystem is now refactored so the **generation preference is config-driven**:

- `backend/config.toml` / `backend/config.example.toml`
- section: `[viz]`
- key: `default_engine = "jsxgraph" | "geogebra"`

Current default is:

- `default_engine = "geogebra"`

The new planner path and the config default now point in the same direction:

- generation default is GeoGebra-first
- JSXGraph remains supported as an explicit per-item fallback

Important distinction:

- this setting changes which engine VizCoder should prefer for **newly generated** visualizations
- it does **not** remove either engine from runtime support
- old rows with explicit `engine="geogebra"` still render through GeoGebraSandbox
- old legacy rows without an engine discriminator still default to JSXGraph for backward compatibility
- the active planner path chooses bottlenecks first and then drives per-viz codegen with GeoGebra as the default engine

## 3. Upstream Dependency: Solver -> Viz

Visualization is intentionally downstream from Solver.

Relevant behavior:

- Solver does **not** generate `visualizations`.
- Solver may emit `solution_steps[].viz_ref` as a stable anchor id.
- VizCoder is required to align generated visualization `id` with those `viz_ref` values when they exist.

This coupling matters because the frontend and stage-review flow assume visualization is an explanation layer for already-produced solution steps, not an independent artifact.

Key file:

- `backend/app/prompts/solver_prompt.py`

Important design point:

- visualization generation is not free-form "draw something relevant to the problem"
- it is "cover key solution stages, formulas, pitfalls, and final result already present in `AnswerPackage`"

## 4. Prompt and Output Contract

### 4.1 Prompt design

Three prompt roles now matter:

1. `backend/app/prompts/vizplanner_prompt.py` defines `VizPlannerPrompt`
2. `backend/app/prompts/vizitem_prompt.py` defines `VizItemPrompt`
3. `backend/app/prompts/vizcoder_prompt.py` defines the older monolithic `VizCoderPrompt`

`VizPlannerPrompt` is the new first-stage design surface.

Its job is:

- inspect the question plus full `AnswerPackage`
- identify the `3-4` most valuable learning bottlenecks
- explain why each bottleneck needs visualization
- normalize shared symbols / params across the whole set
- order the chosen bottlenecks into one storyboard
- **not** emit `ggb_commands` or `jsx_code`

Key design decisions for the planner layer:

- difficulty-driven bottleneck selection before sequence design
- answer-grounded anchor references instead of fixed step slicing
- one storyboard root with shared symbols, shared params, coverage summary, and ordered sequence
- GeoGebra-first preference for the first implementation of per-viz codegen
- planner output kept compact so failures are isolated to later per-viz calls instead of one giant batch response

`VizItemPrompt` is the second-stage realization surface.

Its job is:

- read one approved `VisualizationStoryboardItem`
- generate exactly one `Visualization` JSON object
- keep `id` aligned with `storyboard_item.id`
- reuse root-level shared symbols / shared params
- default to GeoGebra for this item unless the storyboard explicitly asks for JSXGraph

Key design decisions for the item layer:

- one LLM call per storyboard item
- storyboard item decides the teaching target; codegen does not redesign the scene
- shared symbols and shared params are reused, not renamed
- engine mismatch against the storyboard is treated as a generation failure for that item

Key design decisions encoded in the older monolithic `VizCoderPrompt` remain:

- read the preferred engine from config
- keep both engines available
- default to GeoGebra in the current config
- require `3-4` visualizations
- require each visualization to serve a specific learning goal
- require reuse of symbols from the problem and answer
- require alignment with `solution_steps[].viz_ref`
- require `params[].default` instead of `SetValue(...)` in GeoGebra commands
- push view config into `ggb_settings`, not into `ggb_commands`
- when JSXGraph is preferred, strongly bias the model toward controller-style code and lightweight animation helpers

The prompt is unusually strict. It includes:

- a GeoGebra cheatsheet
- a JSXGraph helper cheatsheet
- allowed/forbidden globals for JSXGraph
- explicit anti-pattern instructions derived from bugs already seen in practice
- engine-specific guidance that flips based on config

### 4.2 Output schema

The system now has three relevant output contracts.

#### 4.2.1 Current active per-item contract

The per-item codegen root is one `Visualization` object:

```json
{
  "id": "viz-1",
  "title_cn": "...",
  "caption_cn": "...",
  "learning_goal": "...",
  "engine": "geogebra",
  "ggb_commands": [...],
  "ggb_settings": {...},
  "params": [...]
}
```

Core schema is defined in:

- `backend/app/prompts/schemas.py`
- `backend/app/schemas/llm.py`

#### 4.2.2 Legacy batch contract

The output root is:

```json
{
  "visualizations": [...]
}
```

Core schema is defined in:

- `backend/app/prompts/schemas.py`
- `backend/app/schemas/llm.py`

`VisualizationList.visualizations` has `min_length=3` and `max_length=4`.

Each `Visualization` currently contains:

- `id`
- `title_cn`
- `caption_cn`
- `learning_goal`
- `interactive_hints`
- `helpers_used`
- `engine`
- `jsx_code`
- `ggb_commands`
- `ggb_settings`
- `params`
- `animation`

#### 4.2.3 Storyboard planning contract

The new planner root is:

```json
{
  "theme_cn": "...",
  "selection_rationale_cn": "...",
  "symbol_map": [...],
  "shared_params": [...],
  "coverage_summary": [...],
  "sequence": ["viz-1", "viz-2", "viz-3"],
  "items": [...]
}
```

Core models now exist in code as:

- `VisualizationStoryboard`
- `VisualizationStoryboardItem`
- `VisualizationAnchorRef`
- `StoryboardSymbol`
- `StoryboardCoverageEntry`

The planner item is intentionally explanation-heavy rather than code-heavy. It includes:

- `id`
- `title_cn`
- `anchor_refs`
- `difficulty_reason_cn`
- `student_confusion_risk`
- `conceptual_jump_cn`
- `why_visualization_needed_cn`
- `learning_goal_cn`
- `engine`
- `shared_symbols`
- `shared_params`
- `depends_on`
- `relation_to_prev_cn`
- `relation_to_next_cn`
- `caption_outline_cn`
- `geo_target_cn`

### 4.3 Important semantics of fields

| Field | Actual role in system |
|---|---|
| `id` | Stable visualization identifier; should align with `solution_steps[].viz_ref` |
| `caption_cn` | Human explanation tied to a solution step |
| `learning_goal` | Forces pedagogy-oriented output instead of decorative visuals |
| `engine` | Runtime switch between GeoGebra and JSXGraph |
| `ggb_commands` | GeoGebra command list interpreted by GeoGebra runtime |
| `ggb_settings` | View/applet configuration, not geometry definition |
| `jsx_code` | Raw JS function body for JSXGraph fallback |
| `params` | UI controls defined at frontend host level, synchronized into sandbox |
| `animation` | Persisted metadata, but not meaningfully consumed by frontend host today |

For the new storyboard layer, the most important semantics are:

| Field | Actual role in system |
|---|---|
| `anchor_refs` | Traceable evidence showing which question/answer fragment justifies this visualization |
| `difficulty_reason_cn` | Why this point is hard for students |
| `conceptual_jump_cn` | The hidden transition the visualization should make visible |
| `shared_symbols` | Guarantees symbol consistency across the 3-4 visualizations |
| `shared_params` | Enables multiple scenes to reuse the same slider/toggle concept when pedagogically useful |
| `depends_on` | Makes the 3-4 visualizations read as a teaching sequence rather than isolated artifacts |
| `selection_rationale_cn` | Explains why these 3-4 bottlenecks were chosen instead of other answer content |

## 5. Validation Model

Validation is split across three layers.

### 5.1 Schema-level validation

`backend/app/schemas/llm.py`

Pydantic validates:

- field presence and type
- visualization count
- `engine="geogebra"` requires non-empty `ggb_commands`
- GeoGebra command anti-patterns via regex/shape guards

The GeoGebra guards reject patterns known to fail in Apps API, for example:

- view directives in `ggb_commands`
- `P=K+(dx,dy)` shorthand
- `Vector((a),(b))`
- `Translate(..., Vector(...))`
- `SetColor(obj, "Red")`
- `SetValue(...)` in command list
- `Line(ax+by=c)` wrappers
- `SetConditionToShowObject(...)`
- Greek alias identifiers like `beta`
- reserved builtin names like `xAxis`

This validator is not only for safety. It is also part of the **LLM repair loop**.

### 5.2 Repair loop behavior

`backend/app/services/llm_client.py`

When Pydantic validation fails:

- the bad JSON is appended as assistant content
- validation errors are appended as a repair prompt
- the model is asked to re-emit the full corrected JSON

Implication:

- many GeoGebra format errors are corrected before persistence
- prompt quality and validator quality jointly determine final robustness

### 5.3 Runtime validation split by engine

`backend/app/services/vizcoder_service.py`

- planner path: `VisualizationStoryboard` validates sequence and root/item consistency
- GeoGebra path: no AST validator; backend trusts Pydantic command-shape checks
- JSXGraph path: backend calls `validate_jsx_code(...)`

`validate_jsx_code(...)` is a Python wrapper around:

- `backend/viz_validator/validate.mjs`

The Node validator uses `acorn` and `acorn-walk` to reject:

- network access
- dynamic import / importScripts / require
- `eval` / `Function`
- string timers
- global escape paths like `window`, `document`, `parent`, `top`, `globalThis`
- storage APIs
- oversized or syntactically invalid code

For the new storyboard layer, validation shifts earlier and becomes partly pedagogical:

- `VisualizationStoryboard` validates sequence integrity
- `depends_on` must point to earlier storyboard items
- `shared_symbols` and `shared_params` must resolve against root-level declarations
- `coverage_summary` must only reference existing storyboard item ids
- each item must include at least one `anchor_ref`

### 5.4 Important limitation

GeoGebra currently gets **shape validation**, not **semantic execution validation**.

That means:

- a command list can pass backend validation
- still fail later in the frontend GeoGebra sandbox

This is one of the biggest current optimization opportunities.

### 5.5 Current optimization direction

Because the current monolithic LLM call was fragile and GeoGebra semantic failures are still difficult to recover from inside the rendering engine, the preferred mitigation is now:

- use a difficulty-driven storyboard planner before code generation
- generate one item at a time so provider failures only kill one item instead of the whole batch
- keep the default render engine GeoGebra-first because payloads are shorter and the runtime already tolerates imperfect outputs better than JSXGraph

## 6. Backend Generation and Persistence Flow

### 6.1 Main generation entry

`backend/app/services/vizcoder_service.py::generate_visualizations(...)`

Behavior:

1. load question and stored `AnswerPackage`
2. delete prior `VisualizationRow` rows for the question
3. call `plan_visualization_storyboard(...)`
4. iterate `storyboard.sequence`
5. for each item, call `VizItemPrompt` to generate one `Visualization`
6. validate that item
7. persist each passing visualization
8. yield one `SSEEvent("visualization", ...)` per persisted row

Important implementation detail:

- failures of one visualization do **not** necessarily abort the whole stage
- per-viz failures emit `error` events and continue

Key helpers now are:

- `backend/app/services/vizcoder_service.py::plan_visualization_storyboard(...)`
- `backend/app/services/vizcoder_service.py::generate_visualizations_from_storyboard(...)`

The planner helper:

- loads question + `AnswerPackage`
- calls `VizPlannerPrompt`
- returns a `VisualizationStoryboard`

The per-item generation helper:

- walks `storyboard.sequence`
- calls `VizItemPrompt` once per item
- validates engine compliance against the storyboard
- persists each successful result independently

There is now one important overload fallback:

- if the visualizing stage is a rerun and the planner call ends in a final transient overload
- and the solution already has a previously stored storyboard in `stage_reviews_json["visualizing"].refs.storyboard`
- the backend reuses that stored storyboard and continues with per-item codegen instead of failing the whole stage immediately

That means the orchestration switchover is now complete on the backend side.

### 6.2 Persistence model

Persistent table:

- `backend/app/db/models.py::VisualizationRow`

Stored columns:

- `viz_ref`
- `title`
- `caption`
- `learning_goal`
- `helpers_used_json`
- `engine`
- `jsx_code`
- `ggb_commands_json`
- `ggb_settings_json`
- `params_json`
- `animation_json`

Persistence helper:

- `_persist_viz(...)` in `vizcoder_service.py`

### 6.3 Solution snapshot

The system also stores a per-solution JSON snapshot in:

- `question_solutions.visualizations_json`

The storyboard itself is currently stored in:

- `question_solutions.stage_reviews_json["visualizing"].refs.storyboard`
- mirrored question-level `question_stage_reviews.refs_json.storyboard`

This snapshot is updated by:

- `update_solution_visualizations(...)` in `question_solution_service.py`

The background-job path reads back `VisualizationRow` rows, serializes them through:

- `_serialize_viz_row(...)` in `answer_job_service.py`

and then writes the result into `question_solutions.visualizations_json`.

### 6.4 Review workflow

Visualization is a reviewable stage.

Stage metadata is recorded in:

- `question_stage_reviews`

Current review-stage label:

- `visualizing -> review_viz`

Summary payload is created via:

- `summarize_visualizations(...)` in `stage_review_service.py`

Summary currently includes:

- visualization count
- viz refs
- titles

## 7. API and Orchestration Behavior

### 7.1 Two paths exist

There are effectively two answer-generation paths in code:

1. direct stream path
   - `POST /api/answer/{question_id}`
   - streams answer + viz + sediment through SSE

2. background job path
   - `POST /api/answer/{question_id}/start`
   - frontend polls `/resume`

The current answer page mainly uses the **background job + polling** model, not direct `EventSource`.

### 7.2 Resume endpoint

`backend/app/routers/answer.py::resume_answer(...)`

This endpoint reconstructs the current answer view by returning:

- `sections`
- `visualizations`
- `storyboard`
- `stage_reviews`
- `pipeline`
- `job`
- `solutions`

Visualization payload source:

- if a current solution exists: use `solution.visualizations_json`
- otherwise: query `visualizations` table rows and serialize them

Important consequence:

- in the normal solution-versioned path, `/resume` does **not** read directly from fresh `VisualizationRow` rows while the viz stage is still running
- it reads `solution.visualizations_json`
- that snapshot is updated only after the visualizing stage finishes in `answer_job_service.py`

So the current polling UI usually sees visualization results as a **batch**, not progressively one by one, even though `generate_visualizations(...)` persists rows incrementally.

However, the resume payload can now also expose the chosen storyboard, so review/debug tooling can inspect the planner output separately from the final rendered visualizations.

When the planner is temporarily unavailable during a visualizing-stage rerun, `/resume` will continue to expose the reused storyboard because it is stored in the visualizing-stage review refs.

### 7.3 Important reality vs old comments

Some comments still describe live SSE consumption in the frontend, but the actual page implementation mostly does:

- `POST /start`
- periodic polling of `/resume`

This matters for optimization because "streaming behavior" in code is now partly backend-internal and partly surfaced through polling snapshots rather than direct browser SSE updates.

## 8. Frontend Rendering Design

### 8.1 Engine dispatcher

`frontend/components/VizSandbox.tsx`

This component dispatches by `engine`:

- `geogebra` -> `GeoGebraSandbox`
- anything else -> `JsxgraphSandbox`

Backward compatibility choice:

- missing engine defaults to `jsxgraph`

### 8.2 Answer page integration

`frontend/app/q/[id]/page.tsx`

Current UI behavior:

- `resumeToEvents(...)` converts backend `sections` and `visualizations` into one event stream
- `VizPanel` renders tabs for the available visualizations
- selected visualization is passed into `VizSandbox`
- `interactive_hints` are shown below the sandbox
- `learning_goal` is rendered above the sandbox

### 8.3 Host-side control model

Both sandbox hosts use the same general message protocol:

- `ready`
- `render`
- `update-params`
- `dispose`

This is good design because the page component does not need engine-specific rendering logic.

## 9. GeoGebra Runtime Path

### 9.1 Host component

`frontend/components/GeoGebraSandbox.tsx`

Responsibilities:

- create iframe to `/viz/geogebra-sandbox.html`
- wait for `ready`
- send initial `render`
- keep `liveParams`
- send `update-params` on user interaction
- display error text and render metric

### 9.2 Sandbox page

`frontend/public/viz/geogebra-sandbox.html`

Responsibilities:

- load GeoGebra Apps API from official CDN
- bootstrap one applet instance
- infer `app_name` and `perspective`
- apply `ggb_settings`
- execute `ggb_commands`
- apply param defaults/updates through API
- return `ready`, `error`, and `metric`

### 9.3 Interesting implementation details

The GeoGebra sandbox is not a dumb wrapper. It contains compatibility logic:

- `maybeApplyApiCommand(...)`
  - routes some commands directly to GeoGebra API instead of `evalCommand`
- `maybeCreateImplicitLine(...)`
  - supports legacy `Line(x+y=c)`-style payloads
- `maybeApplyShowCondition(...)`
  - emulates `SetConditionToShowObject(...)` for legacy payloads

This means the frontend is currently carrying part of the backward-compatibility burden for old/bad payloads.

### 9.4 Security posture

GeoGebra sandbox requires:

- `sandbox="allow-scripts allow-same-origin allow-popups"`
- CSP allowing `geogebra.org` and `cdn.geogebra.org`
- `unsafe-eval` in CSP because of GeoGebra loader/runtime needs

Compared with JSXGraph sandbox, this is a weaker isolation model.

Why this is still acceptable in current design:

- the LLM does not emit JavaScript in GeoGebra mode
- it emits command strings interpreted by GeoGebra runtime

Still, from a threat-model perspective, GeoGebra and JSXGraph are not equivalent.

## 10. JSXGraph Runtime Path

### 10.1 Host component

`frontend/components/JsxgraphSandbox.tsx`

Responsibilities are parallel to `GeoGebraSandbox`, but it sends `jsxCode` instead of `ggbCommands`.

### 10.2 Sandbox page

`frontend/public/viz/sandbox.html`

This is the stricter sandbox.

It:

- loads `jsxgraphcore.js` and `h-library.js` locally
- removes or freezes dangerous globals
- blocks `eval` and `Function`
- wraps `requestAnimationFrame`
- enforces frame-budget and initial-render budget
- injects the LLM function body into a controlled runtime wrapper

### 10.2.1 Helper-library improvements

The JSXGraph helper library now exposes stronger animation primitives:

- `H.anim.loop(...)`
- `H.anim.oscillate(...)`
- `H.anim.animate(...)` reimplemented on top of the loop helper

This matters because the prompt can now ask for a stable controller pattern instead of leaving the LLM to improvise raw `requestAnimationFrame` loops every time.

### 10.3 Safety model

JSXGraph uses both:

- backend AST validation
- frontend runtime hardening

This is a layered defense:

- backend blocks obviously unsafe code before persistence
- frontend guards against runtime abuse and performance runaway

## 11. Current Tests Covering Visualization

Main test files:

- `backend/tests/test_vizcoder_geogebra.py`
- `backend/tests/test_viz_validator.py`

Coverage includes:

- GeoGebra schema acceptance
- storyboard schema integrity
- prompt registry coverage for `vizplanner` and `vizitem`
- storyboard-driven `generate_visualizations(...)` orchestration
- resume endpoint storyboard exposure
- backward compatibility for JSXGraph default engine
- minimum visualization count
- GeoGebra anti-pattern rejection
- DB round-trip for engine-specific fields
- AST validator adversarial cases for JSXGraph

Notably missing from backend tests:

- end-to-end GeoGebra command execution validation
- frontend sandbox behavior under real browser automation
- resume/polling behavior focused specifically on visualization stage errors

## 12. Design Strengths

- GeoGebra-first is a strong fit for education-oriented math visuals.
- Prompt, schema, and validators are tightly aligned.
- Visualization is explicitly tied to `AnswerPackage`, not generated independently.
- Engine dispatch is clean and easy to extend.
- JSXGraph path has solid defense-in-depth.
- Stage review and solution versioning make the subsystem auditable.

## 13. Optimization-Relevant Weak Points

These are the most important current implementation realities if you plan to optimize this subsystem later.

### 13.1 Polling still sees batch snapshots, not true per-item live reveal

`vizcoder_service.generate_visualizations(...)` uses:

- one storyboard call
- then one call per storyboard item

This is more failure-isolated than the old batch path, but the polling UI still reads:

- `solution.visualizations_json`

which is refreshed only after the stage finishes in `answer_job_service.py`.

This means:

- per-item generation really does happen independently in the backend
- direct SSE can surface each successful visualization as soon as it is persisted
- background-job polling still mostly sees the completed batch snapshot at stage end

So the generation architecture is now incremental internally, but the main UI delivery path is still snapshot-based.

### 13.2 GeoGebra validation is shallow compared with JSXGraph

GeoGebra has no AST validator and no backend dry-run executor.

Current consequence:

- syntax-shape errors are caught early
- semantic/runtime GeoGebra failures often surface only in browser runtime

### 13.3 Frontend still contains legacy-compatibility repair logic

The GeoGebra sandbox carries behavior to tolerate older payload styles.

This improves resilience, but it also means:

- validation rules and runtime behavior are split across backend and frontend
- some correctness assumptions are not centralized

### 13.4 Metadata is stored more richly than it is consumed

Current examples:

- `animation` is persisted but not meaningfully used by the frontend host
- `helpers_used` is persisted but not surfaced in UI

This suggests schema depth is ahead of runtime product behavior.

### 13.5 Dual orchestration model increases complexity

Both direct SSE and background-job polling paths exist.

This creates maintenance cost in:

- state reconstruction
- stage transitions
- resume behavior
- debugging user-visible timing issues

It also means user-visible visualization latency differs by path:

- direct SSE path can surface each visualization event as soon as it is persisted
- background-job polling path usually exposes them only after `solution.visualizations_json` is refreshed at stage end

### 13.6 One migration/backfill path drops new visualization fields

This issue has now been fixed in code. Previously,
`question_solution_service.bootstrap_solution_from_question(...)` rebuilt `visualizations_json` from `VisualizationRow`, but only kept a legacy subset of fields and omitted:

- `engine`
- `ggb_commands`
- `ggb_settings`

That was a real inconsistency for older question-level data being bootstrapped into solution snapshots. The bootstrap path now preserves those fields.

### 13.7 Frontend answer page comments lag behind actual runtime model

The answer page comments still describe SSE-centric behavior, but actual implementation is resume/poll based.

That drift makes future optimization harder because the mental model in comments no longer matches the real code path.

## 14. Suggested Optimization Priorities

If the goal is to improve robustness and perceived quality with minimum wasted work, the current code suggests this order:

1. make `/resume` and the review UI surface storyboard data more explicitly
2. decide whether background-job polling should reveal successful visualizations progressively instead of only at stage end
3. make GeoGebra validation stronger before persistence if GeoGebra remains in active use
4. decide whether viz delivery should become truly incremental or remain snapshot-based
5. remove or isolate legacy compatibility bridges after old payloads are migrated
6. re-evaluate whether JSXGraph should be selectively reintroduced for exceptional scenes only

## 15. Short Conclusion

The current visualization subsystem is already more mature than a simple "LLM generates chart code" pipeline. It has:

- a strong teaching-oriented prompt contract
- a clear engine abstraction
- stage review and versioning
- a hardened JSXGraph sandbox
- a practical GeoGebra-first migration path

The main architectural tension now is this:

- the schema/prompt layer is already ambitious and structured
- but the GeoGebra validation/runtime path is still partly heuristic and partly browser-resolved

The planner-first layer is no longer only a design direction; it is now the active backend path:

- selection of visualizations should be difficulty-driven and answer-grounded
- the 3-4 visualizations should be linked by a shared storyboard root
- the active implementation is `plan first, then per-viz codegen` with GeoGebra as default engine

That tension is the most important place to focus if the next phase is optimization rather than feature expansion.
