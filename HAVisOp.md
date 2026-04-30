# HAVisOp

## Goal

Optimize `生成可视化` by making the main HAViz path explicitly GeoGebra-first and phase-driven:

1. Stage 1 produces one selected `VisualizationSpec` that already encodes GeoGebra intent.
2. Stage 2 produces a strict `GeoGebraExecutionPayload`.
3. Backend sanitizes and runtime-validates that payload before persistence.
4. Frontend sandbox executes the payload in ordered phases and logs each phase.
5. If Stage 2 fails, the system keeps a spec-only degraded artifact instead of failing the whole answer.

This is a deliberate hard break for the old GeoGebra artifact shape.

## Current Implemented Design

### Stage 1

`VisualizationSpec` now carries GeoGebra-specific planning data:

- `preferred_geogebra_app`
- `geogebra_plan.object_creation_strategy`
- `geogebra_plan.recommended_command_families`
- `geogebra_plan.requires_slider`
- `geogebra_plan.requires_trace`
- `geogebra_plan.requires_locus`
- `geogebra_plan.requires_region_shading`
- `geogebra_plan.requires_sequence_or_list_generation`
- `geogebra_plan.requires_minimal_script`
- `geogebra_plan.script_reason_if_needed`

Semantic validation was extended so Stage 1 can reject specs that are mathematically acceptable but operationally inconsistent for GeoGebra:

- command-only plans cannot require script
- required script must explain why
- trace and region-shading flags must align with the requested display type
- slider requirements must align with declared parameters or slider-like objects
- bundle selection is restricted to one recommended visualization

The `vizspec` prompt is also rewritten to target GeoGebra realization rather than a generic later-codegen step.

### Stage 2

The old Stage 2 GeoGebra output shape:

- `ggb_commands`
- `ggb_settings`
- `params`
- `animation`

is replaced in the main path by `GeoGebraExecutionPayload`:

- `title`
- `preferred_geogebra_app`
- `execution_mode`
- `math_meaning_summary`
- `object_naming_convention`
- `commands[]`
- `property_commands[]`
- `interaction_objects[]`
- `optional_script`
- `expected_created_objects[]`
- `consistency_checks[]`
- `fallback_used`
- `fallback_reason`
- `implementation_notes[]`

The `geogebra_codegen` prompt now generates only this payload and is instructed to:

- separate object creation from property-setting
- prefer command-only implementations
- emit script only when Stage 1 justifies it
- declare expected created objects for runtime verification

### Backend Validation

`geogebra_codegen_service` now:

1. requests `GeoGebraExecutionPayload`
2. sanitizes reserved or collision-prone identifiers across commands, property commands, interaction objects, script fields, and expected objects
3. validates binding consistency locally
4. runs headless runtime validation through the GeoGebra sandbox

The validator path is payload-based, not `ggb_commands`-based.

Validation now checks:

- ordered command steps
- ordered property steps
- execution mode vs optional script consistency
- fallback flag consistency
- interaction object bindings
- optional script target bindings
- expected object declarations

### Persistence

`VisualizationRow` now stores:

- `spec_json`
- `execution_payload_json`
- `degraded`

The old GeoGebra persistence columns are still present in the table for migration safety, but the main HAViz path no longer depends on them.

Solution and resume serialization now expose:

- `spec_json`
- `execution_payload`
- `degraded`

instead of the old `ggb_commands` / `ggb_settings` / `params` / `animation` render contract.

### Frontend Runtime

`GeoGebraSandbox` and `geogebra-sandbox.html` now use:

- `executionPayload`
- `spec`
- derived live params

The sandbox executes the payload in ordered phases:

1. apply viewport/app settings derived from Stage 1 + payload
2. run `commands`
3. run `property_commands`
4. attach `optional_script` when permitted
5. apply live parameter values
6. verify `expected_created_objects`

The Playwright validator uses the same payload contract so backend validation and frontend rendering stay aligned.

### Logging

The visual action stream is preserved and extended with phase-aware runtime traces:

- `payload.create.start/ok/error`
- `payload.property.start/ok/error`
- `payload.script.attach.start/ok/error`
- `payload.expected_objects.check.ok/error`

This keeps `生成可视化` observable at both prompt/LLM level and runtime execution level.

## Compatibility Decision

- Scope: full pipeline rewrite
- Compatibility: hard break for old generated GeoGebra artifacts in the main HAViz path
- Failure policy: keep spec-only fallback
- Primary engine: GeoGebra

Legacy `vizcoder` / `vizplanner` / `vizitem` codepaths remain in the repo for now, but they are no longer the target design for the optimized `生成可视化` path.
