from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.prompts.schemas import VISUALIZATION_SPEC_BUNDLE_SCHEMA, VISUALIZATION_SPEC_SCHEMA
from app.schemas import VisualizationSpecBundle


def _base_bundle() -> dict:
    payload = {
        "task_summary": {
            "source_math_topic": "geometry",
            "source_problem_type": "circle distance",
            "core_learning_goal": "Clarify the distance relationship to a circle boundary",
        },
        "visualizations": [
            {
                "id": "viz_1",
                "title": "Point-to-circle-boundary distance",
                "priority": 1,
                "teaching_value": "high",
                "recommended": True,
                "visualization_type": "locus_trace",
                "preferred_geogebra_app": "geometry",
                "pedagogical_purpose": "Show how the point distance is measured to the circle boundary",
                "when_to_use": "When students confuse circle boundary and filled disk distance",
                "mathematical_claim_being_shown": "The distance to the circle means distance to the circle boundary",
                "student_observation_goal": [
                    "Observe the shortest segment from the point to the circle boundary"
                ],
                "source_dependency": {
                    "depends_on_solution_steps": ["Identify the reference object"],
                    "depends_on_assumptions": ["Distance is measured to the boundary"],
                },
                "math_definition": {
                    "objects": [
                        {
                            "name": "O",
                            "type": "point",
                            "definition": "Center of the circle",
                            "role": "Reference center",
                            "must_exist_before_animation": True,
                        },
                        {
                            "name": "c",
                            "type": "circle_boundary",
                            "definition": "Circle centered at O with fixed radius r",
                            "role": "Distance reference boundary",
                            "must_exist_before_animation": True,
                        },
                        {
                            "name": "P",
                            "type": "moving_point",
                            "definition": "A point moving along a horizontal guide line",
                            "role": "Observed point",
                            "must_exist_before_animation": False,
                        },
                    ],
                    "relations": [
                        {
                            "relation_type": "distance",
                            "description": "Measure the distance to the circle boundary from P",
                        }
                    ],
                    "constraints": [
                        {
                            "name": "horizontal_motion",
                            "expression_in_plain_math": "P=(t,2), -5<=t<=5",
                            "meaning": "Point P moves horizontally",
                        }
                    ],
                    "key_formulas": [
                        {
                            "formula": "d(P,c)=|OP-r|",
                            "purpose": "Relate the dynamic distance to the analytic formula",
                        }
                    ],
                },
                "geogebra_plan": {
                    "object_creation_strategy": "command_only",
                    "recommended_command_families": ["geometry"],
                    "requires_slider": True,
                    "requires_trace": True,
                    "requires_locus": False,
                    "requires_region_shading": False,
                    "requires_sequence_or_list_generation": False,
                    "requires_minimal_script": False,
                    "script_reason_if_needed": "",
                },
                "visual_design": {
                    "coordinate_system": {
                        "needed": True,
                        "type": "cartesian_2d",
                        "suggested_viewport": {
                            "xmin": -6,
                            "xmax": 6,
                            "ymin": -6,
                            "ymax": 6,
                        },
                        "reason": "A Cartesian plane makes the moving point and circle relation explicit",
                    },
                    "visible_objects": ["O", "c", "P"],
                    "highlighted_objects": ["c", "P"],
                    "optional_hidden_helper_objects": ["projection"],
                    "labels_to_show": ["O", "P"],
                    "measurements_to_show": ["d(P,c)"],
                    "region_or_trace_display": {
                        "needed": True,
                        "type": "trace",
                        "description": "Trace the moving point while keeping the boundary visible",
                    },
                },
                "interaction_and_animation": {
                    "has_animation": True,
                    "animation_driver": "slider",
                    "animation_description": "A slider moves point P along the horizontal line",
                    "parameters": [
                        {
                            "name": "t",
                            "type": "number",
                            "range_or_values": "[-5,5]",
                            "default_value": "0",
                            "meaning": "Horizontal position of P",
                        }
                    ],
                    "user_interactions": [
                        {
                            "interaction_type": "move_slider",
                            "target": "t",
                            "purpose": "Explore how distance changes as P moves",
                        }
                    ],
                    "animation_sequence": [
                        "Place the fixed circle boundary",
                        "Move point P with the slider",
                        "Update the shortest segment to the boundary",
                    ],
                    "stopping_condition_or_final_state": "Stop when the slider reaches either endpoint",
                },
                "geometry_contract": {
                    "core_objects": [
                        {
                            "name": "P",
                            "type": "moving_point",
                            "role": "Observed moving point",
                            "must_be_visible": True,
                        },
                        {
                            "name": "c",
                            "type": "circle_boundary",
                            "role": "Distance target boundary",
                            "must_be_visible": True,
                        },
                    ],
                    "motion": {
                        "driver": "t",
                        "moving_object": "P",
                        "path_type": "line",
                        "path_definition": "P moves along the horizontal line y=2",
                        "sample_values": [-5, 0, 5],
                        "expected_positions_description": "P sweeps horizontally while the circle remains fixed",
                    },
                    "invariants": [
                        {
                            "type": "fixed_distance",
                            "objects": ["O", "c"],
                            "description": "The circle radius remains fixed",
                        }
                    ],
                    "student_checkpoints": [
                        {"state": "start", "observation": "P starts left of the circle"},
                        {"state": "middle", "observation": "P is closest near the circle center line"},
                        {"state": "end", "observation": "P ends right of the circle"},
                    ],
                    "must_not_change_meaning": [
                        "Do not replace the circle boundary with a filled disk"
                    ],
                },
                "expected_result": {
                    "final_visual_outcome": "A circle boundary, moving point, and shortest distance segment remain visible",
                    "mathematical_conclusion_visible_to_student": "The measured distance is to the boundary, not the filled disk",
                    "common_misinterpretations_to_avoid": [
                        "Do not interpret the circle as a filled disk"
                    ],
                },
                "implementation_guidance": {
                    "preferred_rendering_strategy": "Use one slider-driven moving point and a dynamically updated segment",
                    "simplifications_allowed": ["Use one fixed guide line instead of a free drag interaction"],
                    "things_that_must_not_be_omitted": ["The circle boundary must remain visible"],
                    "things_that_must_not_be_invented": ["Do not add a filled disk if the spec only references the boundary"],
                    "fallback_if_animation_is_too_complex": "Use a static diagram with three representative point positions",
                },
                "consistency_checks": [
                    "Confirm that the reference object is rendered as a boundary rather than a filled region"
                ],
                "ambiguities": [
                    {
                        "issue": "The source text says 'distance to the circle' without clarifying boundary or disk",
                        "impact": "medium",
                        "preferred_resolution": "Interpret circle as circle_boundary",
                    }
                ],
                "renderability_assessment": {
                    "clarity_score": 88,
                    "math_completeness_score": 90,
                    "implementation_stability_score": 84,
                    "overall_readiness": "ready",
                },
            }
        ],
    }
    for idx in (2, 3):
        item = copy.deepcopy(payload["visualizations"][0])
        item["id"] = f"viz_{idx}"
        item["title"] = f"Supporting visualization {idx}"
        item["priority"] = idx
        item["recommended"] = False
        item["pedagogical_purpose"] = f"Support the teaching sequence with view {idx}"
        payload["visualizations"].append(item)
    return payload


def test_visualization_spec_bundle_accepts_valid_payload():
    bundle = VisualizationSpecBundle.model_validate(_base_bundle())
    assert bundle.visualizations[0].recommended is True
    assert bundle.visualizations[0].math_definition.objects[1].type == "circle_boundary"
    assert bundle.visualizations[0].geometry_contract is not None
    assert bundle.visualizations[0].geometry_contract.motion.driver == "t"


def test_visualization_spec_bundle_normalizes_common_llm_stage1_drift():
    payload = _base_bundle()
    for item in payload["visualizations"]:
        item.pop("teaching_value")
        item["geogebra_plan"].pop("requires_minimal_script")
        item["geogebra_plan"]["requires_script"] = False

    bundle = VisualizationSpecBundle.model_validate(payload)

    assert [item.teaching_value for item in bundle.visualizations] == [
        "high",
        "medium",
        "medium",
    ]
    assert all(
        item.geogebra_plan.requires_minimal_script is False
        for item in bundle.visualizations
    )


def test_visualization_spec_bundle_requires_recommended_candidate():
    payload = _base_bundle()
    payload["visualizations"][0]["recommended"] = False
    with pytest.raises(ValidationError, match="at least one recommended"):
        VisualizationSpecBundle.model_validate(payload)


def test_visualization_spec_bundle_allows_zero_recommended_when_every_candidate_needs_revision():
    payload = _base_bundle()
    for item in payload["visualizations"]:
        item["recommended"] = False
        item["renderability_assessment"]["overall_readiness"] = "needs_revision"

    bundle = VisualizationSpecBundle.model_validate(payload)

    assert bundle.visualizations[0].recommended is False


def test_visualization_spec_bundle_normalizes_multiple_recommended_candidates():
    payload = _base_bundle()
    payload["visualizations"][1]["recommended"] = True
    payload["visualizations"][1]["renderability_assessment"]["implementation_stability_score"] = 95

    bundle = VisualizationSpecBundle.model_validate(payload)

    assert [item.id for item in bundle.visualizations if item.recommended] == ["viz_1"]


def test_visualization_spec_accepts_locus_family_and_static_motion_metadata():
    payload = _base_bundle()
    viz = payload["visualizations"][0]
    viz["geogebra_plan"]["recommended_command_families"] = ["geometry", "locus"]
    viz.pop("geometry_contract")
    viz["interaction_and_animation"] = {
        "has_animation": False,
        "animation_driver": "none",
        "animation_description": "Static fallback showing the same locus relation.",
        "parameters": [],
        "user_interactions": [],
        "animation_sequence": [],
        "stopping_condition_or_final_state": "The static relation remains visible.",
    }

    bundle = VisualizationSpecBundle.model_validate(payload)

    assert bundle.visualizations[0].geogebra_plan.recommended_command_families == [
        "geometry",
        "locus",
    ]
    assert bundle.visualizations[0].math_definition.objects[2].type == "point"


def test_visualization_spec_rejects_geometry_contract_unknown_core_object():
    payload = _base_bundle()
    payload["visualizations"][0]["geometry_contract"]["core_objects"][0]["name"] = "UnknownPoint"

    with pytest.raises(ValidationError, match="geometry_contract.core_objects"):
        VisualizationSpecBundle.model_validate(payload)


def test_visualization_spec_fills_missing_animation_sequence_from_description():
    payload = _base_bundle()
    interaction = payload["visualizations"][0]["interaction_and_animation"]
    interaction["animation_sequence"] = []

    bundle = VisualizationSpecBundle.model_validate(payload)

    assert bundle.visualizations[0].interaction_and_animation.animation_sequence == [
        "A slider moves point P along the horizontal line"
    ]


def test_visualization_spec_parameter_accepts_structured_range_and_numeric_default():
    payload = _base_bundle()
    payload["visualizations"][0]["interaction_and_animation"]["parameters"] = [
        {
            "name": "t",
            "type": "number",
            "range": {"min": -5, "max": 5, "step": 0.5},
            "default_value": 0,
            "meaning": "Horizontal position of P",
        }
    ]

    bundle = VisualizationSpecBundle.model_validate(payload)

    param = bundle.visualizations[0].interaction_and_animation.parameters[0]
    assert param.range is not None
    assert param.range.min == -5
    assert param.range.max == 5
    assert param.range.step == 0.5
    assert param.default_value == 0.0


def test_visualization_spec_rejects_animation_without_parameters():
    payload = _base_bundle()
    payload["visualizations"][0]["interaction_and_animation"]["parameters"] = []
    with pytest.raises(ValidationError, match="requires at least one parameter"):
        VisualizationSpecBundle.model_validate(payload)


def test_visualization_spec_rejects_distance_to_circle_without_explicit_reference_type():
    payload = _base_bundle()
    payload["visualizations"][0]["math_definition"]["objects"][1]["type"] = "circle"
    with pytest.raises(ValidationError, match="circle_boundary or region"):
        VisualizationSpecBundle.model_validate(payload)


def test_visualization_spec_prompt_schemas_export_object_roots():
    assert VISUALIZATION_SPEC_SCHEMA["type"] == "object"
    assert VISUALIZATION_SPEC_BUNDLE_SCHEMA["type"] == "object"
    assert "visualizations" in VISUALIZATION_SPEC_BUNDLE_SCHEMA["properties"]


def test_visualization_spec_prompt_schemas_strip_high_state_constraints_for_gemini():
    forbidden_keys = {
        "enum",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "pattern",
        "format",
    }

    def _collect(node):
        seen: set[str] = set()
        if isinstance(node, dict):
            seen.update(key for key in node if key in forbidden_keys)
            for value in node.values():
                seen.update(_collect(value))
        elif isinstance(node, list):
            for item in node:
                seen.update(_collect(item))
        return seen

    assert not (_collect(VISUALIZATION_SPEC_SCHEMA) & forbidden_keys)
    assert not (_collect(VISUALIZATION_SPEC_BUNDLE_SCHEMA) & forbidden_keys)
