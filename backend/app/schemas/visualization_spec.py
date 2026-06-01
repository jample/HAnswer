from __future__ import annotations

import re
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TeachingValue = Literal["high", "medium"]
VisualizationType = Literal[
    "static_diagram",
    "construction_steps",
    "parametric_animation",
    "locus_trace",
    "region_shading",
    "comparison_overlay",
    "measurement_demo",
    "function_plot",
]
MathObjectType = Literal[
    "point",
    "line",
    "segment",
    "ray",
    "circle",
    "circle_boundary",
    "polygon",
    "function_graph",
    "region",
    "angle",
    "label",
    "slider_parameter",
    "moving_point",
    "traced_object",
    "auxiliary_object",
    "locus",
    "list_object",
]
RelationType = Literal[
    "distance",
    "intersection",
    "perpendicular",
    "parallel",
    "equality",
    "ratio",
    "midpoint",
    "collinear",
    "on_curve",
    "inside_region",
    "boundary_of",
    "symmetric_about",
    "transformed_from",
    "angle_measure",
    "area_relation",
    "function_relation",
    "locus_condition",
]
CoordinateSystemType = Literal["cartesian_2d", "geometry_plane"]
RegionTraceDisplayType = Literal[
    "none",
    "trace",
    "shaded_region",
    "moving_overlay",
    "boundary_only",
    "stepwise_reveal",
]
AnimationDriver = Literal["none", "slider", "moving_point", "parameter_t", "step_index"]
ParameterType = Literal["number", "angle", "integer_step", "boolean"]
InteractionType = Literal[
    "drag",
    "play_pause",
    "step_forward",
    "step_backward",
    "toggle_visibility",
    "move_slider",
]
AmbiguityImpact = Literal["low", "medium", "high"]
OverallReadiness = Literal["ready", "mostly_ready", "needs_revision"]
PreferredGeoGebraApp = Literal["geometry", "graphing", "classic"]
GeoGebraObjectCreationStrategy = Literal[
    "command_only",
    "mostly_commands_with_minimal_script",
    "requires_script",
]
GeoGebraCommandFamily = Literal[
    "geometry",
    "transformation",
    "list",
    "logic",
    "locus",
    "scripting",
    "conic",
    "function",
]
GeometryContractObjectType = Literal[
    "point",
    "moving_point",
    "line",
    "segment",
    "ray",
    "circle",
    "circle_boundary",
    "polygon",
    "region",
    "angle",
    "measurement",
    "trace",
    "locus",
    "auxiliary_object",
]
GeometryContractPathType = Literal[
    "none",
    "line",
    "segment",
    "circle",
    "circle_boundary",
    "function_graph",
    "locus",
    "region_boundary",
    "free_parameter",
]
GeometryInvariantType = Literal[
    "on_curve",
    "inside_region",
    "boundary_of",
    "collinear",
    "parallel",
    "perpendicular",
    "equal_distance",
    "fixed_distance",
    "ratio",
    "midpoint",
    "angle_equal",
    "angle_measure",
    "area_equal",
    "tangent",
    "symmetric_about",
    "transformed_from",
]
GeometryCheckpointState = Literal["start", "middle", "end", "sample"]


class VisualizationTaskSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_math_topic: str
    source_problem_type: str
    core_learning_goal: str


class VisualizationSourceDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depends_on_solution_steps: list[str] = Field(default_factory=list)
    depends_on_assumptions: list[str] = Field(default_factory=list)


class VisualizationMathObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: MathObjectType
    definition: str
    role: str
    must_exist_before_animation: bool


class VisualizationRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_type: RelationType
    description: str


class VisualizationConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    expression_in_plain_math: str
    meaning: str


class VisualizationFormula(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formula: str
    purpose: str


class VisualizationMathDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objects: list[VisualizationMathObject] = Field(min_length=1)
    relations: list[VisualizationRelation] = Field(default_factory=list)
    constraints: list[VisualizationConstraint] = Field(default_factory=list)
    key_formulas: list[VisualizationFormula] = Field(default_factory=list)


class VisualizationViewport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    __contract_rules__: ClassVar[list[str]] = [
        "suggested_viewport requires xmin < xmax and ymin < ymax",
    ]

    xmin: float
    xmax: float
    ymin: float
    ymax: float

    @model_validator(mode="after")
    def _check_bounds(self) -> VisualizationViewport:
        if self.xmin >= self.xmax:
            raise ValueError("suggested_viewport requires xmin < xmax")
        if self.ymin >= self.ymax:
            raise ValueError("suggested_viewport requires ymin < ymax")
        return self


class VisualizationCoordinateSystem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    needed: bool
    type: CoordinateSystemType
    suggested_viewport: VisualizationViewport
    reason: str


class VisualizationRegionTraceDisplay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    needed: bool
    type: RegionTraceDisplayType
    description: str


class VisualizationDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coordinate_system: VisualizationCoordinateSystem
    visible_objects: list[str] = Field(default_factory=list)
    highlighted_objects: list[str] = Field(default_factory=list)
    optional_hidden_helper_objects: list[str] = Field(default_factory=list)
    labels_to_show: list[str] = Field(default_factory=list)
    measurements_to_show: list[str] = Field(default_factory=list)
    region_or_trace_display: VisualizationRegionTraceDisplay


class VisualizationGeoGebraPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    __contract_rules__: ClassVar[list[str]] = [
        "object_creation_strategy='command_only' cannot require minimal script",
        "requires_minimal_script=true requires script_reason_if_needed",
    ]

    object_creation_strategy: GeoGebraObjectCreationStrategy
    recommended_command_families: list[GeoGebraCommandFamily] = Field(default_factory=list)
    requires_slider: bool = False
    requires_trace: bool = False
    requires_locus: bool = False
    requires_region_shading: bool = False
    requires_sequence_or_list_generation: bool = False
    requires_minimal_script: bool = False
    script_reason_if_needed: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize_script_aliases(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if "requires_minimal_script" not in payload and "requires_script" in payload:
            payload["requires_minimal_script"] = bool(payload.get("requires_script"))
        payload.pop("requires_script", None)
        return payload

    @model_validator(mode="after")
    def _check_geogebra_plan(self) -> VisualizationGeoGebraPlan:
        if self.requires_minimal_script and not self.script_reason_if_needed.strip():
            raise ValueError("requires_minimal_script=true requires script_reason_if_needed")
        if self.object_creation_strategy == "command_only" and self.requires_minimal_script:
            raise ValueError("command_only strategy cannot require minimal script")
        return self


class GeometryContractObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: GeometryContractObjectType
    role: str
    must_be_visible: bool = True


class GeometryMotionContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    driver: str = ""
    moving_object: str = ""
    path_type: GeometryContractPathType = "none"
    path_definition: str = ""
    sample_values: list[float] = Field(default_factory=list)
    expected_positions_description: str = ""

    @model_validator(mode="after")
    def _check_motion_contract(self) -> GeometryMotionContract:
        if self.path_type == "none":
            return self
        if not self.driver.strip():
            raise ValueError("geometry motion with path_type!='none' requires driver")
        if not self.moving_object.strip():
            raise ValueError("geometry motion with path_type!='none' requires moving_object")
        if not self.path_definition.strip():
            raise ValueError("geometry motion with path_type!='none' requires path_definition")
        if len(self.sample_values) < 2:
            raise ValueError("geometry motion requires at least two sample_values")
        return self


class GeometryInvariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: GeometryInvariantType
    objects: list[str] = Field(min_length=1)
    description: str


class GeometryStudentCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: GeometryCheckpointState
    observation: str


class GeometryVisualizationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_objects: list[GeometryContractObject] = Field(default_factory=list)
    motion: GeometryMotionContract = Field(default_factory=GeometryMotionContract)
    invariants: list[GeometryInvariant] = Field(default_factory=list)
    student_checkpoints: list[GeometryStudentCheckpoint] = Field(default_factory=list)
    must_not_change_meaning: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_geometry_contract(self) -> GeometryVisualizationContract:
        moving_names = {
            item.name for item in self.core_objects
            if item.type in {"moving_point", "trace", "locus"}
        }
        if moving_names and self.motion.path_type == "none":
            raise ValueError("moving geometry core objects require a non-'none' motion.path_type")
        if self.motion.moving_object and self.motion.moving_object not in {
            item.name for item in self.core_objects
        }:
            raise ValueError("motion.moving_object must reference one geometry_contract.core_objects name")
        return self


_LEGACY_RANGE_STEP_RE = re.compile(r"step\s*[:=]?\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_LEGACY_RANGE_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_LEGACY_RANGE_WORD_RE = re.compile(r"[A-Za-z]+")
_LEGACY_RANGE_UNSUPPORTED_RE = re.compile(r"[πΠ√]")


class VisualizationParameterRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    __contract_rules__: ClassVar[list[str]] = [
        "range.min must be < range.max",
        "range.step must be > 0",
    ]

    min: float
    max: float
    step: float = Field(default=0.1, gt=0)

    @model_validator(mode="after")
    def _check_bounds(self) -> VisualizationParameterRange:
        if self.min >= self.max:
            raise ValueError("parameter range requires min < max")
        return self


def _parse_legacy_parameter_range(raw: str, param_type: str | None) -> VisualizationParameterRange:
    text = raw.strip()
    if not text:
        raise ValueError("range_or_values cannot be empty")

    without_step = _LEGACY_RANGE_STEP_RE.sub("", text)
    words = {word.lower() for word in _LEGACY_RANGE_WORD_RE.findall(without_step)}
    unsupported_words = words - {"to"}
    if _LEGACY_RANGE_UNSUPPORTED_RE.search(without_step) or unsupported_words:
        raise ValueError(
            "range_or_values with symbolic or textual bounds is no longer supported; provide numeric range.min/max/step"
        )

    numbers = [float(part) for part in _LEGACY_RANGE_NUMBER_RE.findall(text)]
    if len(numbers) < 2:
        raise ValueError("range_or_values must contain at least numeric min and max bounds")

    step_match = _LEGACY_RANGE_STEP_RE.search(text)
    if step_match is not None:
        step = float(step_match.group(1))
    elif param_type == "integer_step":
        step = 1.0
    else:
        step = 0.1

    return VisualizationParameterRange(min=numbers[0], max=numbers[1], step=step)


def _coerce_parameter_default(
    *,
    value: object,
    param_type: str | None,
    range_payload: VisualizationParameterRange | dict | None,
) -> float | bool:
    if param_type == "boolean":
        if isinstance(value, bool):
            return value
        lowered = str(value or "").strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
        raise ValueError("boolean parameters require default_value=true/false")

    if isinstance(value, bool):
        raise ValueError("numeric parameters cannot use boolean default_value")

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "numeric parameters require a numeric default_value; symbolic expressions such as 'pi/4' are not supported"
        ) from exc

    if isinstance(range_payload, VisualizationParameterRange):
        range_value = range_payload
    elif isinstance(range_payload, dict):
        range_value = VisualizationParameterRange.model_validate(range_payload)
    else:
        range_value = None

    if range_value is not None and not (range_value.min <= number <= range_value.max):
        raise ValueError("default_value must lie within range.min and range.max")

    return number


class VisualizationParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    __contract_rules__: ClassVar[list[str]] = [
        "if parameters.type='boolean' then range MUST be omitted and default_value MUST be true/false",
        "if parameters.type is 'number', 'angle', or 'integer_step' then range MUST be provided",
        "if parameters.type='integer_step' then range.step should be 1 unless a different positive integer step is truly required",
    ]

    name: str
    type: ParameterType
    range: VisualizationParameterRange | None = None
    default_value: float | bool
    meaning: str

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_shape(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        upgraded = dict(data)
        param_type = str(upgraded.get("type") or "")
        legacy_range = upgraded.pop("range_or_values", None)
        if legacy_range is not None and upgraded.get("range") is None:
            upgraded["range"] = _parse_legacy_parameter_range(str(legacy_range), param_type or None)

        if "default_value" in upgraded:
            upgraded["default_value"] = _coerce_parameter_default(
                value=upgraded.get("default_value"),
                param_type=param_type or None,
                range_payload=upgraded.get("range"),
            )
        return upgraded

    @model_validator(mode="after")
    def _check_parameter_contract(self) -> VisualizationParameter:
        if self.type == "boolean":
            if self.range is not None:
                raise ValueError("boolean parameters cannot define range")
            if not isinstance(self.default_value, bool):
                raise ValueError("boolean parameters require boolean default_value")
            return self

        if self.range is None:
            raise ValueError("numeric parameters require range")
        if isinstance(self.default_value, bool):
            raise ValueError("numeric parameters require numeric default_value")
        if self.type == "integer_step" and not float(self.range.step).is_integer():
            raise ValueError("integer_step parameters require an integer range.step")
        return self


class VisualizationUserInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interaction_type: InteractionType
    target: str
    purpose: str


class VisualizationInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    __contract_rules__: ClassVar[list[str]] = [
        "if has_animation=true then animation_driver must NOT be 'none'",
        "if has_animation=true then parameters must contain at least one entry",
        "if has_animation=true then animation_sequence must NOT be empty",
        "if has_animation=false then animation_driver MUST be 'none'",
        "if has_animation=false then animation_sequence MUST be empty",
    ]

    has_animation: bool
    animation_driver: AnimationDriver
    animation_description: str
    animation_duration_ms: int = Field(default=3000, gt=0)
    parameters: list[VisualizationParameter] = Field(default_factory=list)
    user_interactions: list[VisualizationUserInteraction] = Field(default_factory=list)
    animation_sequence: list[str] = Field(default_factory=list)
    stopping_condition_or_final_state: str

    @model_validator(mode="before")
    @classmethod
    def _normalize_missing_animation_sequence(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if payload.get("has_animation") is True and not payload.get("animation_sequence"):
            description = str(payload.get("animation_description") or "").strip()
            if description:
                payload["animation_sequence"] = [description]
        return payload

    @model_validator(mode="after")
    def _check_animation_contract(self) -> VisualizationInteraction:
        if self.has_animation:
            if self.animation_driver == "none":
                raise ValueError("has_animation=true requires a non-'none' animation_driver")
            if not self.parameters:
                raise ValueError("has_animation=true requires at least one parameter")
            if not self.animation_sequence:
                raise ValueError("has_animation=true requires animation_sequence")
        else:
            if self.animation_driver != "none":
                raise ValueError("has_animation=false requires animation_driver='none'")
            if self.animation_sequence:
                raise ValueError("has_animation=false cannot include animation_sequence")
        return self


class VisualizationExpectedResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_visual_outcome: str
    mathematical_conclusion_visible_to_student: str
    common_misinterpretations_to_avoid: list[str] = Field(default_factory=list)


class VisualizationImplementationGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_rendering_strategy: str
    preferred_geogebra_object_naming_style: str = (
        "Use short English labels such as A, B, C, O, P, t, r, c1, c2, f, g"
    )
    simplifications_allowed: list[str] = Field(default_factory=list)
    things_that_must_not_be_omitted: list[str] = Field(default_factory=list)
    things_that_must_not_be_invented: list[str] = Field(default_factory=list)
    fallback_if_animation_is_too_complex: str


class VisualizationAmbiguity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: str
    impact: AmbiguityImpact
    preferred_resolution: str


class VisualizationRenderabilityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clarity_score: int = Field(ge=0, le=100)
    math_completeness_score: int = Field(ge=0, le=100)
    implementation_stability_score: int = Field(ge=0, le=100)
    overall_readiness: OverallReadiness


class VisualizationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    __contract_rules__: ClassVar[list[str]] = [
        "if visualization_type='region_shading' then visual_design.region_or_trace_display.type must NOT be 'none'",
        "any object whose type is 'moving_point' or 'traced_object' requires interaction_and_animation.has_animation=true",
        "if any text mentions 'distance to circle' or 'distance to the circle' then math_definition.objects must include at least one of type 'circle_boundary' or 'region'",
        "a visualization with renderability_assessment.overall_readiness='needs_revision' MUST have recommended=false",
        "preferred_geogebra_app must be one of geometry/graphing/classic",
        "teaching_value is required; if omitted by an LLM draft, recommended/priority=1 defaults to high and others to medium",
        "geogebra_plan.requires_minimal_script=true requires script_reason_if_needed",
        "legacy geogebra_plan.requires_script is normalized to requires_minimal_script and removed",
    ]

    id: str
    title: str
    priority: int = Field(ge=1)
    teaching_value: TeachingValue
    recommended: bool
    visualization_type: VisualizationType
    preferred_geogebra_app: PreferredGeoGebraApp
    pedagogical_purpose: str
    when_to_use: str
    mathematical_claim_being_shown: str
    student_observation_goal: list[str] = Field(min_length=1)
    source_dependency: VisualizationSourceDependency
    math_definition: VisualizationMathDefinition
    geogebra_plan: VisualizationGeoGebraPlan
    visual_design: VisualizationDesign
    interaction_and_animation: VisualizationInteraction
    geometry_contract: GeometryVisualizationContract | None = None
    expected_result: VisualizationExpectedResult
    implementation_guidance: VisualizationImplementationGuidance
    consistency_checks: list[str] = Field(default_factory=list)
    ambiguities: list[VisualizationAmbiguity] = Field(default_factory=list)
    renderability_assessment: VisualizationRenderabilityAssessment

    @model_validator(mode="before")
    @classmethod
    def _normalize_motion_metadata(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if not str(payload.get("teaching_value") or "").strip():
            payload["teaching_value"] = (
                "high"
                if payload.get("recommended") is True or payload.get("priority") == 1
                else "medium"
            )
        interaction = dict(payload.get("interaction_and_animation") or {})
        if interaction.get("has_animation") is False:
            math_definition = dict(payload.get("math_definition") or {})
            objects = []
            for raw in list(math_definition.get("objects") or []):
                if not isinstance(raw, dict):
                    objects.append(raw)
                    continue
                item = dict(raw)
                if item.get("type") in {"moving_point", "traced_object"}:
                    item["type"] = "point"
                objects.append(item)
            math_definition["objects"] = objects
            payload["math_definition"] = math_definition
            if not interaction.get("parameters"):
                geogebra_plan = dict(payload.get("geogebra_plan") or {})
                geogebra_plan["requires_slider"] = False
                payload["geogebra_plan"] = geogebra_plan
        return payload

    @model_validator(mode="after")
    def _check_semantics(self) -> VisualizationSpec:
        display_type = self.visual_design.region_or_trace_display.type
        if self.visualization_type == "region_shading" and display_type == "none":
            raise ValueError("region_shading visualizations cannot use region_or_trace_display.type='none'")

        moving_types = {obj.type for obj in self.math_definition.objects}
        if {"moving_point", "traced_object"} & moving_types and not self.interaction_and_animation.has_animation:
            raise ValueError(
                "moving_point or traced_object objects require has_animation=true so motion is explicit"
            )

        text_parts = [
            self.mathematical_claim_being_shown,
            self.expected_result.final_visual_outcome,
            *[relation.description for relation in self.math_definition.relations],
            *[constraint.expression_in_plain_math for constraint in self.math_definition.constraints],
        ]
        lowered_text = "\n".join(text_parts).lower()
        if "distance to circle" in lowered_text or "distance to the circle" in lowered_text:
            object_types = {obj.type for obj in self.math_definition.objects}
            if "circle_boundary" not in object_types and "region" not in object_types:
                raise ValueError(
                    "distance-to-circle specs must clarify whether the reference object is circle_boundary or region"
                )

        if self.renderability_assessment.overall_readiness == "needs_revision" and self.recommended:
            raise ValueError("recommended visualizations cannot have overall_readiness='needs_revision'")

        if self.geogebra_plan.requires_trace and display_type not in {
            "trace",
            "moving_overlay",
            "boundary_only",
            "stepwise_reveal",
        }:
            raise ValueError("requires_trace is inconsistent with region_or_trace_display.type")

        if self.geogebra_plan.requires_region_shading and display_type not in {
            "shaded_region",
            "moving_overlay",
            "boundary_only",
        }:
            raise ValueError("requires_region_shading is inconsistent with region_or_trace_display.type")

        if self.geogebra_plan.requires_slider:
            has_slider_param = any(obj.type == "slider_parameter" for obj in self.math_definition.objects) or any(
                param.type in {"number", "angle", "integer_step", "boolean"}
                for param in self.interaction_and_animation.parameters
            )
            if not has_slider_param:
                raise ValueError("requires_slider=True but no slider-like parameter/object is defined")
        if self.geometry_contract is not None:
            spec_object_names = {obj.name for obj in self.math_definition.objects}
            spec_param_names = {param.name for param in self.interaction_and_animation.parameters}
            contract_names = {obj.name for obj in self.geometry_contract.core_objects}
            missing_core = sorted(contract_names - spec_object_names - spec_param_names)
            if missing_core:
                raise ValueError(
                    "geometry_contract.core_objects must reference math_definition objects or parameters: "
                    + ", ".join(missing_core)
                )
            motion = self.geometry_contract.motion
            if motion.driver and motion.driver not in spec_param_names and motion.driver not in spec_object_names:
                raise ValueError("geometry_contract.motion.driver must reference a parameter or math object")
        return self


class VisualizationSpecBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    __contract_rules__: ClassVar[list[str]] = [
        "visualizations must contain exactly 3 entries",
        "at least one visualization in the bundle must have recommended=true unless every visualization is needs_revision",
    ]

    task_summary: VisualizationTaskSummary
    visualizations: list[VisualizationSpec] = Field(min_length=3, max_length=3)

    @model_validator(mode="before")
    @classmethod
    def _normalize_recommendations(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        visualizations = [
            dict(item) if isinstance(item, dict) else item
            for item in list(payload.get("visualizations") or [])
        ]
        if len(visualizations) > 3:
            visualizations = visualizations[:3]

        recommended_indices = [
            idx for idx, item in enumerate(visualizations)
            if isinstance(item, dict) and item.get("recommended") is True
        ]
        if len(recommended_indices) > 1:
            readiness_rank = {"ready": 0, "mostly_ready": 1, "needs_revision": 2}

            def _sort_key(index: int) -> tuple[int, int, int]:
                item = visualizations[index]
                assessment = item.get("renderability_assessment") if isinstance(item, dict) else {}
                if not isinstance(assessment, dict):
                    assessment = {}
                readiness = readiness_rank.get(str(assessment.get("overall_readiness") or ""), 3)
                priority = item.get("priority") if isinstance(item, dict) else 999
                if not isinstance(priority, int):
                    priority = 999
                stability = assessment.get("implementation_stability_score")
                if not isinstance(stability, int):
                    stability = 0
                return readiness, priority, -stability

            selected = sorted(recommended_indices, key=_sort_key)[0]
            for idx, item in enumerate(visualizations):
                if isinstance(item, dict):
                    item["recommended"] = idx == selected

        payload["visualizations"] = visualizations
        return payload

    @model_validator(mode="after")
    def _check_recommendation(self) -> VisualizationSpecBundle:
        recommended = [item for item in self.visualizations if item.recommended]
        if not recommended:
            if all(
                item.renderability_assessment.overall_readiness == "needs_revision"
                for item in self.visualizations
            ):
                return self
            raise ValueError("VisualizationSpecBundle requires at least one recommended visualization")
        if len(recommended) > 1:
            raise ValueError("VisualizationSpecBundle allows only one recommended visualization")
        return self
