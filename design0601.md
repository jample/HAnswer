# HAnswer Geometry Visualization Reliability Design 0601

## 1. Current Implementation Understanding

The current math solver pipeline already separates solving from visualization:

```text
ParsedQuestion
  -> Solver AnswerPackage
  -> VisualizationSpecBundle via vizspec
  -> GeoGebraExecutionPayload via geogebra_codegen
  -> local sanitize + static validation
  -> visualizations row + solution.visualizations_json
  -> frontend GeoGebraSandbox
```

Important current strengths:

- Stage 1 is no longer raw code generation. It creates structured `VisualizationSpec` candidates with objects, relations, constraints, parameters, visual design, expected result, and renderability scores.
- Stage 2 is no longer arbitrary frontend code. It emits `GeoGebraExecutionPayload` with ordered `commands`, `property_commands`, `interaction_objects`, and `expected_created_objects`.
- The backend blocks many known GeoGebra Apps API failure shapes before persistence.
- The frontend runs GeoGebra inside `/viz/geogebra-sandbox.html` and logs runtime traces through visual action logs.
- If Stage 2 fails, the system keeps a spec-only degraded artifact instead of losing all visualization intent.

The main remaining problem is not only execution reliability. It is semantic reliability: a visualization can run and still fail to teach the intended geometry relationship. For geometry questions, students need to understand moving-point tracks, invariant relations, changing shapes, and why a construction solves the problem. The current system validates command shape and object existence, but does not yet prove that the rendered point, trace, region, or measurement matches the Stage 1 teaching claim.

Legacy JSXGraph and older `vizcoder` paths still exist, but the optimization target for new work should be the GeoGebra-first HAViz path.

## 2. Main Problems

### 2.1 Runnable Is Not The Same As Correct

Current validation catches many fragile command forms, missing expected objects, reserved names, and bad command ordering. It does not fully check whether:

- the moving point actually moves on the intended path;
- the trace/locus corresponds to the intended locus condition;
- the highlighted shape is the same shape described in the solution;
- measured distances, angles, ratios, or areas represent the intended invariant;
- the visual conclusion a student observes is the same mathematical claim in the spec.

This creates a high-risk failure mode: the visualization appears polished but builds the wrong mental model.

### 2.2 Geometry Motion Is Under-Specified

`VisualizationSpec` already has object definitions, constraints, parameters, animation driver, and expected results. However, moving-point geometry needs a stricter, machine-checkable motion contract:

- which parameter drives the motion;
- how the moving point coordinates depend on the parameter;
- what path or locus the point should follow;
- which invariants must hold at sampled positions;
- what the student should observe at start, middle, and end states.

Without this contract, Stage 2 can invent a simpler motion that looks plausible but no longer demonstrates the solution.

### 2.3 Semantic Drift Across Stage 1 And Stage 2

Stage 1 may require a trace, region, or comparison overlay. Stage 2 can still generate a static diagram or omit a core object if the commands pass syntactic/static validation. Some simplification is acceptable, but it must be explicit and must preserve the teaching claim.

### 2.4 Fallback UX Is Too Thin For Learning

The current fallback card preserves purpose, conclusion, and fallback text. For students, this is better than a blank panel, but it is not enough to explain geometry tracks and shapes. A failed animation should still describe the intended objects, path, invariant, and final observation.

### 2.5 Logs Are Not Yet Quality Feedback

Visual action logs record stage and runtime events, but they do not yet summarize whether the rendered diagram satisfied the intended semantic checks. This makes repeated failures hard to classify and hard to feed back into prompt repair.

## 3. Design Goal

The new design should make geometry visualization reliable at three levels:

1. Execution reliability: the GeoGebra payload runs without hard runtime failure.
2. Semantic reliability: the rendered objects, tracks, shapes, and measurements match the selected `VisualizationSpec`.
3. Learning reliability: the student can observe the intended geometry idea without guessing what the diagram means.

The guiding rule is:

> A visualization should be persisted as successful only when it both runs and demonstrates the promised geometry claim. Otherwise it should be repaired, simplified, switched to another candidate, or clearly degraded.

## 4. Geometry Visualization Contract

Add an explicit geometry contract to Stage 1. This can be introduced as a new nested field in `VisualizationSpec`, for example `geometry_contract`, while keeping the existing spec fields.

### 4.1 Contract Shape

The contract should be structured enough for deterministic validation:

```json
{
  "geometry_contract": {
    "core_objects": [
      {
        "name": "P",
        "type": "moving_point",
        "role": "point whose track is observed",
        "must_be_visible": true
      }
    ],
    "motion": {
      "driver": "t",
      "moving_object": "P",
      "path_type": "circle | line | segment | function_graph | locus | free_parameter",
      "path_definition": "P lies on circle c",
      "sample_values": [0, 0.5, 1],
      "expected_positions_description": "P moves along the circle boundary counterclockwise"
    },
    "invariants": [
      {
        "type": "on_curve",
        "objects": ["P", "c"],
        "description": "P always stays on circle c"
      },
      {
        "type": "equal_distance",
        "objects": ["O", "P", "r"],
        "description": "OP remains equal to the radius"
      }
    ],
    "student_checkpoints": [
      {
        "state": "start",
        "observation": "P is on the right side of the circle"
      },
      {
        "state": "middle",
        "observation": "P remains on the boundary while the angle changes"
      },
      {
        "state": "end",
        "observation": "The traced path is the circle boundary"
      }
    ],
    "must_not_change_meaning": [
      "Do not replace circle boundary motion with motion inside the disk"
    ]
  }
}
```

### 4.2 Supported Invariant Types

Start with a small set that covers most middle/high-school geometry:

- `on_curve`
- `inside_region`
- `boundary_of`
- `collinear`
- `parallel`
- `perpendicular`
- `equal_distance`
- `fixed_distance`
- `ratio`
- `midpoint`
- `angle_equal`
- `angle_measure`
- `area_equal`
- `tangent`
- `symmetric_about`
- `transformed_from`

Do not attempt full theorem proving. The first version should validate object presence, command support, sampled values, and observable measurements.

### 4.3 Stage 1 Prompt Changes

Update `vizspec_prompt.py` so every geometry visualization with a moving point, locus, trace, region, or construction sequence must fill `geometry_contract`.

Prompt requirements:

- Name the exact moving object and driver.
- State the intended track or region in plain math.
- Provide 3 sample driver values when there is motion.
- List 1-3 invariants that must remain true.
- State what the student should observe at start/middle/end.
- Prefer simple, stable tracks over decorative motion.
- If a fallback changes from animation to static, specify the representative static positions.

For non-geometry function plots, the field can be omitted or reduced to a simpler function contract later.

## 5. Pipeline Changes

### 5.1 Stage 1 Selection

Keep generating three visualization candidates. Selection should still prefer:

1. `recommended=true`;
2. `overall_readiness in {"ready", "mostly_ready"}`;
3. higher `implementation_stability_score`;
4. lower `priority`.

Add geometry-specific selection pressure:

- Prefer candidates whose `geometry_contract` is complete when the visualization type is `locus_trace`, `parametric_animation`, `region_shading`, `construction_steps`, or `measurement_demo`.
- Penalize candidates that rely on scripts or vague motion descriptions.
- If no candidate has a complete contract, allow spec-only fallback but mark the plan as `needs_geometry_contract_review`.

### 5.2 Stage 2 Codegen

Update `geogebra_codegen_prompt.py` so Stage 2 must preserve the selected contract:

- Every `core_objects[].name` must be created or intentionally mapped to an equivalent object name.
- The motion driver must be represented as a slider or stable GeoGebra parameter when motion exists.
- The moving object must be defined from the driver, not as an unrelated fixed point.
- Trace/locus/region requirements must be implemented or explicitly downgraded using the Stage 1 fallback strategy.
- `expected_created_objects` must include the core semantic objects, not annotations.
- `implementation_notes` must list any semantic simplification.

Stage 2 must not silently replace a moving-track visualization with an unrelated static diagram. Static fallback is allowed only when it preserves the same claim through representative positions.

### 5.3 Candidate Retry Policy

The current job path already generates and persists up to three candidates. Strengthen the policy:

- Generate payloads for candidates in selected order.
- If a candidate fails static or semantic validation, attempt one bounded repair.
- If repair fails, persist that candidate as degraded and continue with the next candidate.
- At least one non-degraded candidate should be preferred in the UI tab order.
- Store each candidate's failure summary in `execution_payload.__meta` or `spec_json.__meta` for review.

### 5.4 Bounded Stage 2 Repair

`geogebra_codegen_service.py` already contains repair-message scaffolding. Activate a single targeted repair pass for validation failures.

Repair input should include only:

- selected spec id/title;
- `geometry_contract`;
- failed payload;
- validator violations;
- allowed repair operations.

Repair must not:

- redesign Stage 1;
- change the teaching goal;
- add JavaScript;
- exceed command budgets;
- add unrelated objects.

## 6. Validation Strategy

### 6.1 Static Payload-Against-Spec Validation

Add a deterministic validator, for example:

```python
validate_payload_against_spec(payload: GeoGebraExecutionPayload, spec: VisualizationSpec) -> None
```

This should run after sanitize and before existing static GeoGebra validation.

Initial checks:

- `preferred_geogebra_app` matches the spec.
- Each required core object has a created object or declared mapping.
- `visible_objects` and `highlighted_objects` are not all omitted.
- If `requires_slider=true`, a slider-like command and interaction object exist.
- If `requires_trace=true`, `SetTrace(...)`, locus object, or explicit static fallback exists.
- If `requires_region_shading=true`, a region object or boundary/region fallback exists.
- If `geometry_contract.motion.driver` exists, the driver is created and used by the moving object command.
- If `geometry_contract.invariants` reference objects, those objects are created or mapped.
- `implementation_notes` explain any semantic simplification.

Failures should be structured as validator violations so they can drive the Stage 2 repair prompt.

### 6.2 Runtime Semantic Sampling

Add optional runtime semantic sampling in `geogebra-sandbox.html` after command execution.

For a moving driver:

1. Set the driver to each `sample_values` entry.
2. Query coordinates or values for key objects.
3. Check simple observable conditions where possible.
4. Emit trace events.

Example trace events:

- `semantic.sample.start`
- `semantic.sample.ok`
- `semantic.object.missing`
- `semantic.invariant.failed`
- `semantic.locus.partial`
- `semantic.contract.passed`

This does not need to block initial rendering in the frontend. It should give the system quality feedback after render.

### 6.3 What Runtime Should Check First

Keep first-version runtime checks conservative:

- object exists;
- object is visible;
- slider can be set;
- point coordinates change when driver changes;
- fixed point coordinates do not unexpectedly change;
- distance/angle numeric values are finite;
- trace/locus object exists when required.

Avoid advanced symbolic checks in v1. The purpose is to catch obvious wrong diagrams cheaply.

### 6.4 Persistence Metadata

Add validation metadata into existing JSON fields before introducing a new migration:

```json
{
  "__meta": {
    "validation_status": "static_passed",
    "semantic_validation_status": "passed | failed | partial | skipped",
    "semantic_validation_errors": [],
    "stage2_repair_attempted": true,
    "stage2_repaired": false,
    "runtime_status": "unknown | passed | partial | failed"
  }
}
```

This keeps compatibility with current `execution_payload_json` storage.

## 7. Frontend Runtime And Student UX

### 7.1 GeoGebraSandbox

Update `GeoGebraSandbox.tsx` and `geogebra-sandbox.html` to pass and use the geometry contract:

- Host sends `spec` as it does today.
- Sandbox extracts `spec.geometry_contract`.
- After render, sandbox performs semantic sampling when the contract exists.
- Host displays a small status only when useful:
  - "可视化已通过关键对象检查"
  - "部分辅助元素未显示"
  - "轨迹检查未通过，已显示规格说明"

Do not overload the student with validator details. Full details belong in logs and review surfaces.

### 7.2 Fallback Card

Improve the current fallback card so it remains educational:

- selected spec title;
- core objects;
- intended motion/track;
- expected final shape or region;
- key invariant;
- expected student observation;
- degraded reason.

For a geometry track failure, the fallback should answer:

- what point was supposed to move;
- along what path;
- what shape or trace should appear;
- what relationship stays unchanged.

### 7.3 Visualization Tab Ordering

When multiple candidates exist:

- show non-degraded visualizations first;
- keep degraded specs accessible after working ones;
- label degraded tabs as "说明" rather than treating them as failed diagrams.

This avoids making the first visible tab a failure when a later candidate rendered correctly.

## 8. Testing And Acceptance Criteria

### 8.1 Backend Unit Tests

Add tests for:

- valid `geometry_contract` in `VisualizationSpec`;
- missing motion driver rejected for moving-point specs;
- missing core object rejected by payload-against-spec validation;
- `requires_slider=true` without slider rejected;
- `requires_trace=true` without trace/locus/static fallback rejected;
- one-repair path called after semantic validation failure;
- degraded metadata contains semantic failure reason.

### 8.2 Frontend Runtime Tests

Add sandbox tests or Playwright checks for:

- GeoGebra renders a simple slider-driven moving point;
- semantic sampling emits `semantic.contract.passed`;
- missing expected object emits a semantic failure event;
- fallback card displays geometry contract fields;
- non-degraded visualization tabs appear before degraded ones.

### 8.3 Geometry QA Set

Create a small repeatable QA set for common geometry learning patterns:

- point moving on a circle boundary;
- locus of a point with fixed distance;
- perpendicular bisector construction;
- angle bisector construction;
- similar triangles with ratio preservation;
- tangent from an external point;
- area-preserving moving vertex;
- region shading for inequality or feasible set;
- comparison of circle boundary vs filled disk.

Acceptance criteria:

- At least one non-degraded visualization for each QA problem.
- No visualization may pass if the core moving point or target shape is missing.
- Trace/locus problems must expose either a real trace/locus or a declared static fallback with representative positions.
- Fallback cards must be understandable without reading logs.

## 9. Implementation Roadmap

### Phase 1: Contract And Static Semantic Validation

- Add `geometry_contract` schema models.
- Update `vizspec_prompt.py` and prompt schema generation.
- Add `validate_payload_against_spec`.
- Call it inside `geogebra_codegen_service._validate_payload`.
- Add backend tests.

This phase should produce the biggest correctness gain without changing frontend runtime behavior.

### Phase 2: Repair And Candidate Quality Policy

- Activate one Stage 2 repair pass for static/semantic validation failures.
- Include contract and violations in the repair prompt.
- Add metadata for repair attempted/repaired.
- Improve candidate ordering so non-degraded visualizations surface first.

### Phase 3: Runtime Semantic Sampling

- Add sandbox-side sampling for object existence, visibility, slider updates, coordinate changes, and finite measurements.
- Emit semantic trace events.
- Store summarized runtime status through visual action logs.
- Add Playwright coverage for representative cases.

### Phase 4: Student-Facing Fallback And Review UX

- Expand `VisualizationFallbackCard`.
- Add degraded tab labels.
- Show concise semantic status in review/stage summaries.
- Use visual action logs to summarize frequent failure categories.

## 10. Assumptions

- GeoGebra remains the primary engine for new visualizations.
- JSXGraph remains compatibility-only unless a future task explicitly revives it.
- Browser-based runtime validation should not be added back into the user-facing backend generation path by default.
- The first semantic validator should be conservative and deterministic, not a full geometry theorem prover.
- Existing JSON fields are enough for first implementation; a database migration should be deferred until the metadata shape stabilizes.
- The goal is better student understanding, so stable simple diagrams are preferred over fragile complex animation.
