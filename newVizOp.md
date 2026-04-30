# newVizOp: 生成可视化优化审查与设计建议

Date: 2026-04-23
Scope: current `生成可视化` path, including prompts, schemas, backend orchestration, GeoGebra validation, persistence, and frontend rendering.

## Current Code Understanding

The current main path is already GeoGebra-first and phase-driven:

1. `backend/app/services/answer_job_service.py` enters `visualizing`.
2. Stage 1 calls `generate_visualization_spec_bundle(...)` with `VizSpecPrompt`.
3. The selected `VisualizationSpec` is persisted to `solution.visualization_plan_json`.
4. Stage 2 calls `generate_geogebra_visualization_or_fallback(...)` with `GeoGebraCodegenPrompt`.
5. Stage 2 output is `GeoGebraExecutionPayload`, then local sanitize and headless runtime validation run.
6. `VisualizationRow` stores `spec_json`, `execution_payload_json`, and `degraded`.
7. Frontend `VizSandbox` routes to `GeoGebraSandbox`, which renders `/viz/geogebra-sandbox.html`.

Good foundations already present:

- Stage 1 trims `AnswerPackage` before prompting, which helps latency.
- `VisualizationSpec` now has structured parameter ranges and GeoGebra planning fields.
- Stage 2 is no longer arbitrary frontend code; it emits a constrained execution payload.
- Runtime validation uses the same GeoGebra sandbox HTML as the frontend.
- Spec-only fallback prevents total visualization loss.
- Visual action logs exist across backend and frontend runtime phases.

## Key Optimization Points

### 1. Add A Bounded Stage 2 Repair Loop

Current issue:

- `geogebra_codegen_service.py` calls `llm.call_structured(..., disable_repair=True)`.
- If the payload passes JSON schema but fails local binding checks or runtime validation, the system immediately degrades to spec-only fallback.
- This is reliable in the sense that it avoids bad renders, but it leaves recoverable errors unrepaired.

Design:

- Keep schema repair disabled for expensive broad retries, but add one targeted Stage 2 repair pass after local sanitize/runtime failure.
- Repair prompt should include only:
  - selected spec id/title,
  - failed payload,
  - validator violations,
  - a short list of allowed command/property patterns.
- Do not rerun Stage 1.
- Do not allow more than one repair by default.

Expected impact:

- Reliability: high improvement, because many failures are simple missing expected object names, bad bindings, or one bad property command.
- Speed: small cost only on failed Stage 2 cases.
- UX: fewer spec-only degraded cards.

### 2. Try The Secondary Stage 1 Candidate Before Degrading

Current issue:

- Stage 1 can produce 1-2 candidate specs, but `answer_job_service.py` only attempts Stage 2 for `select_recommended_visualization(bundle)`.
- If the best candidate fails codegen/runtime validation, a second ready/mostly-ready candidate is ignored.

Design:

- After selected candidate fails Stage 2, try the next candidate if:
  - `overall_readiness` is `ready` or `mostly_ready`,
  - `implementation_stability_score` is above a threshold, e.g. `80`,
  - it is not the same id as the first candidate.
- Persist the originally selected bundle, but add `selected_visualization_id` for the candidate that actually rendered.
- In review summary, show both: planned first choice and rendered fallback choice.

Expected impact:

- Reliability: high, because Stage 1 already paid for candidate planning.
- Speed: no extra Stage 1 call; only an extra Stage 2 call on failure.
- UX: better than spec-only fallback because the user still gets a working visualization.

### 3. Surface Visualizing-Phase Runtime Status In The UI

Current issue:

- `生成解答` has detailed heartbeat/status UI.
- `生成可视化` has only coarse Stage 1/2 messages: planning spec, then generating GeoGebra payload.
- Runtime validation and degraded fallback are logged, but not surfaced clearly to the user during the wait.

Design:

- Add visualizing status updates similar to solver progress:
  - `Stage 1/2: 规划可视化规格`
  - `已选中规格: <title>`
  - `Stage 2/2: 生成 GeoGebra 执行载荷`
  - `本地校验: sanitize`
  - `本地校验: headless runtime`
  - `已降级: 保留规格说明卡片` when applicable.
- Frontend `GeminiProgress` should display those sub-states for the active visualizing stage.
- Stage review summary should include render mode: `execution_payload` or `spec_only`.

Expected impact:

- Reliability: no direct change.
- Speed: no material change.
- UX: high improvement, especially when GeoGebra CDN/runtime validation is slow.

### 4. Make Runtime Validation Faster Without Removing It

Current issue:

- `validate_geogebra_execution_payload(...)` spawns `node`, launches Chromium, loads sandbox HTML, then loads GeoGebra.
- This is safe but expensive. It can dominate visualizing latency.

Design:

- Prefer a warm validator worker over skipping validation:
  - a long-lived Node process,
  - one Chromium browser reused across requests,
  - new page/context per validation,
  - hard timeout and worker restart on failure.
- Keep the current subprocess validator as fallback for tests and local simplicity.
- Add timing logs around:
  - Node startup,
  - browser launch,
  - GeoGebra applet load,
  - command execution.

Expected impact:

- Reliability: preserved if each validation uses a fresh page/context.
- Speed: large improvement after warmup.
- UX: shorter `生成可视化` wait time.

### 5. Ban Optional JavaScript In The Main GeoGebra Path For Now

Current issue:

- `GeoGebraExecutionPayload` allows `optional_script.script_type="javascript"`.
- The frontend sandbox executes it with `new Function(...)`.
- Backend validation does not statically inspect JavaScript body.
- The prompt says script should be minimal, but the reliability/safety envelope is weaker than command-only payloads.

Design:

- For the main path, restrict `optional_script.script_type` to `none` or `ggbscript`.
- If JavaScript is ever needed, add a separate reviewed capability with:
  - AST validation,
  - strict API allowlist,
  - no access to `window`, `document`, `fetch`, timers, storage, or parent messaging,
  - explicit user-visible “advanced script” label.

Expected impact:

- Reliability: higher, fewer runtime surprises.
- Speed: no meaningful change.
- UX: fewer hard-to-debug sandbox failures.

### 6. Add Cross-Validation Between Spec And Execution Payload

Current issue:

- Local validation checks whether `interaction_objects`, script targets, and expected objects exist in GeoGebra commands.
- It does not deeply check whether payload objects actually satisfy the selected spec:
  - `visible_objects`,
  - `highlighted_objects`,
  - parameter names,
  - animation driver,
  - required trace/region/locus flags.

Design:

- Add a deterministic `validate_payload_against_spec(payload, spec)` before runtime validation.
- Examples:
  - every spec parameter should either be represented as an interaction object or intentionally omitted with an implementation note,
  - if `requires_slider=true`, at least one slider interaction must exist,
  - if `requires_trace=true`, property commands should include `SetTrace(...)` or a trace/locus object,
  - expected objects should include important visible/highlighted objects when those are concrete GeoGebra names,
  - `preferred_geogebra_app` must match.

Expected impact:

- Reliability: high, catches semantically incomplete but runnable payloads.
- Speed: small positive, because bad payloads can fail before launching Chromium.
- UX: fewer “runs but teaches the wrong thing” visualizations.

### 7. Make Stage 1 Simpler For Easy Problems

Current issue:

- `VizSpecPrompt` asks for a complete rich spec every time.
- The auto-derived contract is useful but large.
- For simple function plots or static diagrams, Stage 1 may over-plan and slow down the pipeline.

Design:

- Add a cheap deterministic classifier before Stage 1:
  - `no_visual_needed`,
  - `simple_function_plot`,
  - `simple_geometry_static`,
  - `full_spec_required`.
- For simple cases, use a smaller prompt/schema or deterministic spec template.
- Full `VisualizationSpecBundle` remains the fallback for complex cases.

Expected impact:

- Reliability: neutral to positive if templates are conservative.
- Speed: high improvement for routine algebra/function questions.
- UX: faster visual results and fewer overcomplicated animations.

### 8. Improve Spec-Only Fallback UX

Current issue:

- The fallback card preserves teaching intent, conclusion, and fallback text.
- It does not show enough actionable detail for students or reviewers when rendering fails.

Design:

- Extend the fallback card with:
  - selected spec title,
  - key objects,
  - expected visual outcome,
  - common misinterpretations to avoid,
  - the degraded reason from Stage 2 if available.
- Add a “retry codegen for this spec” action later, using the current stage review/rerun mechanism.

Expected impact:

- Reliability: no direct change.
- Speed: no change.
- UX: medium to high improvement when degradation happens.

### 9. Separate Or Retire Legacy Visualization Paths

Current issue:

- Main path uses `vizspec -> geogebra_codegen -> GeoGebraExecutionPayload`.
- Legacy modules still exist:
  - `vizcoder_service.py`,
  - `vizplanner_prompt.py`,
  - `vizitem_prompt.py`,
  - `jsxgraph_codegen_prompt.py`,
  - older `ggb_commands` schema/tests.
- This increases maintenance load and can confuse future changes.

Design:

- Make the legacy path explicit:
  - move it under `legacy_visualization/`, or
  - mark entrypoints as deprecated in docstrings and tests.
- Keep only compatibility serialization needed for old persisted rows.
- New tests should focus on the payload path unless they explicitly test legacy compatibility.

Expected impact:

- Reliability: positive, fewer accidental regressions across two incompatible contracts.
- Speed: no direct change.
- UX: indirect improvement because future changes target the active path.

### 10. Use Visual Action Logs For Automatic Failure Summaries

Current issue:

- Visual action logs are recorded, but the user/reviewer has to infer what happened.
- Stage review summary does not include the full failure chain.

Design:

- When visualizing ends, summarize latest visual actions into review summary:
  - Stage 1 latency and candidate count,
  - Stage 2 payload command count,
  - sanitize rewrite map,
  - runtime render time,
  - degraded reason if any.
- Expose a compact “可视化诊断” block in the stage review panel.

Expected impact:

- Reliability: helps debugging and prompt iteration.
- Speed: no direct change.
- UX: improves transparency for long or failed visualizing stages.

## Proposed Implementation Order

### Phase 1: High-Return Reliability Fixes

1. Add one targeted Stage 2 repair pass.
2. Try a secondary Stage 1 candidate before spec-only fallback.
3. Ban optional JavaScript in the main path.
4. Add spec-vs-payload deterministic validation.

### Phase 2: Speed And Observability

1. Add visualizing sub-status updates to backend and frontend.
2. Add validator timing breakdown.
3. Introduce a warm Node/Chromium validator worker behind the existing validator API.

### Phase 3: UX And Maintenance

1. Enrich spec-only fallback card.
2. Add visual action summaries to stage review.
3. Quarantine or retire legacy `ggb_commands` / JSXGraph generation code paths.
4. Add simple-case deterministic templates for common visualization categories.

## Recommended Near-Term Target Design

The best next version should keep the current two-stage architecture but add one recovery layer:

```text
Stage 1: VisualizationSpecBundle
  -> select candidate list ordered by recommended/readiness/stability

For each candidate, up to 2 attempts:
  Stage 2 initial GeoGebraExecutionPayload
  -> local sanitize
  -> spec-vs-payload validation
  -> runtime validation
  -> if failed once: targeted repair
  -> if still failed: try next candidate

If every candidate fails:
  persist spec-only degraded artifact with detailed degraded reason
```

This design is conservative: it does not add unbounded LLM calls, does not trust generated code, and keeps the current fallback behavior. It should materially improve the chance of getting a working visualization while keeping latency and cost bounded.

## My Recommendation

Start with Phase 1. The most important change is not prompt wording; it is the recovery strategy after Stage 2 validation fails. Current prompts are already fairly strong. The bigger practical weakness is that the system gives up too early after a recoverable GeoGebra payload error.

The second most important change is visualizing-stage status UX. Users can tolerate a slow visualization phase if they can see whether the system is planning, generating, validating, repairing, or degrading. Right now that internal state exists in logs but is not visible enough in the page.
