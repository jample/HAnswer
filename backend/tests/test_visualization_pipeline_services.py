from __future__ import annotations

import json
import uuid

import pytest

from app import schemas
from app.config import settings
from app.services import visualization_spec_service
from app.services.answer_job_service import _ordered_visualization_candidates
from app.services.geogebra_codegen_service import (
    generate_geogebra_visualization_or_fallback,
    normalize_geogebra_execution_payload_draft,
)
from app.services.geogebra_validator import (
    GeoGebraValidationError,
    GeoGebraValidationReport,
    sanitize_geogebra_execution_payload_with_report,
    validate_geogebra_execution_payload,
)
from app.services.jsxgraph_codegen_service import (
    generate_jsxgraph_code,
    generate_jsxgraph_code_or_fallback,
)
from app.services.llm_client import FakeTransport, GeminiClient
from app.services.visualization_spec_service import (
    generate_visualization_spec_bundle,
    select_recommended_visualization,
)


def _spec_bundle_payload() -> dict:
    payload = {
        "task_summary": {
            "source_math_topic": "geometry",
            "source_problem_type": "circle distance",
            "core_learning_goal": "Clarify boundary distance",
        },
        "visualizations": [
            {
                "id": "viz_1",
                "title": "Boundary distance",
                "priority": 1,
                "teaching_value": "high",
                "recommended": True,
                "visualization_type": "measurement_demo",
                "preferred_geogebra_app": "geometry",
                "pedagogical_purpose": "Show shortest boundary distance",
                "when_to_use": "When the boundary/region distinction matters",
                "mathematical_claim_being_shown": "Distance is measured to the circle boundary",
                "student_observation_goal": ["Observe the shortest segment to the boundary"],
                "source_dependency": {"depends_on_solution_steps": ["clarify definition"], "depends_on_assumptions": []},
                "math_definition": {
                    "objects": [
                        {"name": "c", "type": "circle_boundary", "definition": "Boundary", "role": "reference", "must_exist_before_animation": True}
                    ],
                    "relations": [],
                    "constraints": [],
                    "key_formulas": [],
                },
                "geogebra_plan": {
                    "object_creation_strategy": "command_only",
                    "recommended_command_families": ["geometry"],
                    "requires_slider": False,
                    "requires_trace": False,
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
                        "suggested_viewport": {"xmin": -5, "xmax": 5, "ymin": -5, "ymax": 5},
                        "reason": "clear geometry view",
                    },
                    "visible_objects": ["c"],
                    "highlighted_objects": ["c"],
                    "optional_hidden_helper_objects": [],
                    "labels_to_show": [],
                    "measurements_to_show": ["d"],
                    "region_or_trace_display": {"needed": False, "type": "boundary_only", "description": "Boundary only"},
                },
                "interaction_and_animation": {
                    "has_animation": False,
                    "animation_driver": "none",
                    "animation_description": "Static",
                    "parameters": [],
                    "user_interactions": [],
                    "animation_sequence": [],
                    "stopping_condition_or_final_state": "Static final state",
                },
                "expected_result": {
                    "final_visual_outcome": "Circle boundary and measured segment",
                    "mathematical_conclusion_visible_to_student": "Distance refers to the boundary",
                    "common_misinterpretations_to_avoid": ["Do not fill the disk"],
                },
                "implementation_guidance": {
                    "preferred_rendering_strategy": "Use a fixed segment",
                    "preferred_geogebra_object_naming_style": "Use short English labels such as A, B, C, O, P, d",
                    "simplifications_allowed": [],
                    "things_that_must_not_be_omitted": ["boundary"],
                    "things_that_must_not_be_invented": ["disk fill"],
                    "fallback_if_animation_is_too_complex": "Use a static diagram",
                },
                "consistency_checks": ["boundary remains visible"],
                "ambiguities": [],
                "renderability_assessment": {
                    "clarity_score": 90,
                    "math_completeness_score": 90,
                    "implementation_stability_score": 90,
                    "overall_readiness": "ready",
                },
            }
        ],
    }
    for idx in (2, 3):
        item = json.loads(json.dumps(payload["visualizations"][0]))
        item["id"] = f"viz_{idx}"
        item["title"] = f"Supporting view {idx}"
        item["priority"] = idx
        item["recommended"] = False
        item["pedagogical_purpose"] = f"Support the teaching sequence with view {idx}"
        payload["visualizations"].append(item)
    return payload


def test_select_recommended_visualization_prefers_ready_candidate():
    bundle = schemas.VisualizationSpecBundle.model_validate(_spec_bundle_payload())
    selected = select_recommended_visualization(bundle)
    assert selected.id == "viz_1"


@pytest.mark.asyncio
async def test_generate_visualization_spec_bundle_requests_one_repair_attempt(monkeypatch):
    question_id = uuid.uuid4()
    captured: dict = {}

    class _Question:
        parsed_json = {"subject": "math", "grade_band": "junior", "question_text": "Q"}
        answer_package_json = {
            "question_understanding": {
                "restated_question": "Find x",
                "givens": ["a"],
                "unknowns": ["x"],
                "implicit_conditions": [],
            },
            "key_points_of_question": ["k1"],
            "solution_steps": [
                {
                    "step_index": 1,
                    "statement": "step one",
                    "rationale": "drop me",
                    "formula": "drop me",
                    "why_this_step": "drop me",
                }
            ],
            "key_points_of_answer": ["a1"],
            "method_pattern": {
                "name_cn": "method",
                "when_to_use": "always",
                "general_procedure": ["drop me"],
                "pitfalls": ["drop me"],
            },
            "similar_questions": [{"statement": "drop"}],
            "knowledge_points": [{"node_ref": "drop", "weight": 1.0}],
            "self_check": ["drop me"],
        }

    async def _fake_get_question(session, requested_question_id):
        assert requested_question_id == question_id
        return _Question()

    class _StubLLM:
        async def call_structured(self, **kwargs):
            captured.update(kwargs)
            return schemas.VisualizationSpecBundle.model_validate(_spec_bundle_payload())

    monkeypatch.setattr(visualization_spec_service.repo, "get_question", _fake_get_question)

    bundle = await generate_visualization_spec_bundle(
        session=object(),
        question_id=question_id,
        llm=_StubLLM(),
        teaching_preference="keep it simple",
    )

    assert isinstance(bundle, schemas.VisualizationSpecBundle)
    assert captured["model"] == settings.llm_model("vizcoder")
    assert captured["timeout_s"] == settings.llm.vizcoder_timeout_s
    assert captured["min_repair_attempts"] == 2
    assert captured["stream"] is settings.llm.stream_vizcoder_json
    assert captured["template_kwargs"]["teaching_preference"] == "keep it simple"

    # Stage 1 must receive a TRIMMED answer package, not the raw one.
    sent_pkg = captured["template_kwargs"]["answer_package"]
    assert "similar_questions" not in sent_pkg
    assert "knowledge_points" not in sent_pkg
    assert "self_check" not in sent_pkg
    assert sent_pkg["solution_steps"] == [{"step_index": 1, "statement": "step one"}]
    assert sent_pkg["method_pattern"] == {"name_cn": "method", "when_to_use": "always"}


@pytest.mark.asyncio
async def test_generate_jsxgraph_code_accepts_render_visualization_contract():
    transport = FakeTransport(
        text_by_model={
            settings.gemini.model_vizcoder: "function renderVisualization(containerId, spec) { var board = JXG.JSXGraph.initBoard(containerId, { axis: true }); return { board: board, spec: spec }; }"
        }
    )
    client = GeminiClient(transport)
    spec = schemas.VisualizationSpec.model_validate(_spec_bundle_payload()["visualizations"][0])
    code = await generate_jsxgraph_code(llm=client, spec=spec)
    assert code.startswith("function renderVisualization")


class _SequencedTextTransport(FakeTransport):
    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self._responses = list(responses)

    async def generate_text(self, *, model, messages, timeout_s):
        self.calls.append({"model": model, "messages": messages, "text": True})
        raw = self._responses.pop(0)
        return raw, 0, 0


class _SequencedJsonTransport(FakeTransport):
    def __init__(self, responses: list[dict]) -> None:
        super().__init__()
        self._responses = [json.dumps(item, ensure_ascii=False) for item in responses]

    def _next(self) -> str:
        return self._responses.pop(0)

    async def generate_json(self, *, model, messages, response_schema, timeout_s):
        self.calls.append({"model": model, "messages": messages})
        return self._next(), 0, 0

    async def generate_json_stream(self, *, model, messages, response_schema, timeout_s):
        self.calls.append({"model": model, "messages": messages, "stream": True})
        return self._next(), 0, 0


@pytest.mark.asyncio
async def test_generate_jsxgraph_code_retries_with_fallback_after_validation_failure():
    transport = _SequencedTextTransport([
        "function renderVisualization(containerId, spec) { return document.body; }",
        "function renderVisualization(containerId, spec) { var board = JXG.JSXGraph.initBoard(containerId, { axis: true }); return { board: board, spec: spec }; }",
    ])
    client = GeminiClient(transport)
    spec = schemas.VisualizationSpec.model_validate(_spec_bundle_payload()["visualizations"][0])

    code = await generate_jsxgraph_code(llm=client, spec=spec)

    assert code.startswith("function renderVisualization")
    assert len(transport.calls) == 2
    repair_message = transport.calls[1]["messages"][-1]["content"]
    assert "fallback exactly" in repair_message
    assert spec.implementation_guidance.fallback_if_animation_is_too_complex in repair_message


@pytest.mark.asyncio
async def test_generate_jsxgraph_code_retries_after_runtime_contract_failure():
    transport = _SequencedTextTransport([
        "function renderVisualization(containerId, spec) { try { JXG.JSXGraph.freeBoard(containerId); var board = JXG.JSXGraph.initBoard(containerId, { axis: true }); return { board: null, update: function(){}, destroy: function(){} }; } catch (err) { return null; } }",
        "function renderVisualization(containerId, spec) { var board = JXG.JSXGraph.initBoard(containerId, { axis: true }); return { board: board, update: function() {}, destroy: function() { JXG.JSXGraph.freeBoard(board); } }; }",
    ])
    client = GeminiClient(transport)
    spec = schemas.VisualizationSpec.model_validate(_spec_bundle_payload()["visualizations"][0])

    code = await generate_jsxgraph_code(llm=client, spec=spec)

    assert code.startswith("function renderVisualization")
    assert len(transport.calls) == 2
    repair_message = transport.calls[1]["messages"][-1]["content"]
    assert "Do not call JXG.JSXGraph.freeBoard(containerId)" in repair_message
    assert "Do not wrap the whole renderVisualization body in a catch" in repair_message
    assert "Never return null, undefined, or an object with board: null" in repair_message


@pytest.mark.asyncio
async def test_generate_jsxgraph_code_or_fallback_returns_empty_after_exhaustion():
    bad = "function renderVisualization(containerId, spec) { return document.body; }"
    transport = _SequencedTextTransport([bad, bad, bad])
    client = GeminiClient(transport)
    spec = schemas.VisualizationSpec.model_validate(_spec_bundle_payload()["visualizations"][0])

    result = await generate_jsxgraph_code_or_fallback(llm=client, spec=spec)

    assert result.code == ""
    assert result.error_summary
    assert "document" in result.error_summary
    # one initial + two repair attempts
    assert len(transport.calls) == 3
    second_repair_messages = transport.calls[2]["messages"]
    assistant_failures = [msg["content"] for msg in second_repair_messages if msg["role"] == "assistant"]
    assert assistant_failures.count(bad) == 2


def test_select_recommended_visualization_falls_back_when_no_ready_candidate():
    bundle = schemas.VisualizationSpecBundle.model_validate(_spec_bundle_payload())
    # Mutate to simulate the (rare) case where every recommended viz
    # somehow lost its ready/mostly_ready badge after validation.
    bundle.visualizations[0].recommended = False
    selected = visualization_spec_service.select_recommended_visualization(bundle)
    assert selected.id == "viz_1"


def test_ordered_visualization_candidates_keeps_primary_then_stable_fallbacks():
    payload = _spec_bundle_payload()
    payload["visualizations"][1]["title"] = "Simpler static fallback"
    payload["visualizations"][1]["renderability_assessment"]["implementation_stability_score"] = 85
    bundle = schemas.VisualizationSpecBundle.model_validate(payload)

    ordered = _ordered_visualization_candidates(
        bundle=bundle,
        selected_spec=bundle.visualizations[0],
    )

    assert [item.id for item in ordered] == ["viz_1", "viz_2", "viz_3"]


@pytest.mark.asyncio
async def test_generate_geogebra_visualization_or_fallback_returns_execution_payload(monkeypatch):
    transport = FakeTransport(
        json_by_model={
            settings.gemini.model_vizcoder: json.dumps({
                "title": "边界距离",
                "preferred_geogebra_app": "geometry",
                "execution_mode": "command_only",
                "math_meaning_summary": "用圆与线段明确展示距离是到边界而不是到区域。",
                "object_naming_convention": "Use short English labels such as O, A, B, d.",
                "commands": [
                    {"step": 1, "purpose": "Create circle center", "command": "O=(0,0)"},
                    {"step": 2, "purpose": "Create boundary point", "command": "A=(3,0)"},
                    {"step": 3, "purpose": "Create circle boundary", "command": "c=Circle(O,A)"},
                    {"step": 4, "purpose": "Create external point", "command": "P=(4,2)"},
                    {"step": 5, "purpose": "Create foot line", "command": "d=Segment(P,A)"}
                ],
                "property_commands": [
                    {"step": 6, "purpose": "Show the segment prominently", "command": "SetLineThickness(d,4)"}
                ],
                "interaction_objects": [],
                "optional_script": {
                    "needed": False,
                    "script_type": "none",
                    "reason": "",
                    "target_object": "",
                    "trigger": "none",
                    "script_body": ""
                },
                "expected_created_objects": [
                    {"name": "O", "type": "point", "role": "center"},
                    {"name": "A", "type": "point", "role": "boundary_point"},
                    {"name": "c", "type": "circle_boundary", "role": "reference"},
                    {"name": "P", "type": "point", "role": "external_point"},
                    {"name": "d", "type": "segment", "role": "distance_segment"}
                ],
                "consistency_checks": ["The segment ends on the circle boundary."],
                "fallback_used": False,
                "fallback_reason": "",
                "implementation_notes": ["No script is needed for this construction."]
            })
        }
    )
    client = GeminiClient(transport)
    spec = schemas.VisualizationSpec.model_validate(_spec_bundle_payload()["visualizations"][0])

    async def _ok(payload, *, spec=None, timeout_s=20.0):
        return GeoGebraValidationReport(ok=True, render_ms=12.5)

    monkeypatch.setattr(
        "app.services.geogebra_codegen_service.validate_geogebra_execution_payload",
        _ok,
    )

    result = await generate_geogebra_visualization_or_fallback(llm=client, spec=spec)

    assert result.execution_payload is not None
    assert result.execution_payload.preferred_geogebra_app == "geometry"
    assert result.execution_payload.execution_mode == "command_only"
    assert [row.command for row in result.execution_payload.commands][:2] == ["O=(0,0)", "A=(3,0)"]
    assert result.error_summary is None


@pytest.mark.asyncio
async def test_geogebra_execution_payload_static_validator_accepts_small_payload():
    payload = schemas.GeoGebraExecutionPayload.model_validate({
        "title": "边界距离",
        "preferred_geogebra_app": "geometry",
        "execution_mode": "command_only",
        "math_meaning_summary": "用圆与线段明确展示距离是到边界而不是到区域。",
        "object_naming_convention": "Use short English labels such as O, A, B, d.",
        "commands": [
            {"step": 1, "purpose": "Create circle center", "command": "O=(0,0)"},
            {"step": 2, "purpose": "Create boundary point", "command": "A=(3,0)"},
            {"step": 3, "purpose": "Create circle boundary", "command": "c=Circle(O,A)"},
            {"step": 4, "purpose": "Create external point", "command": "P=(4,2)"},
            {"step": 5, "purpose": "Create distance segment", "command": "d=Segment(P,A)"},
        ],
        "property_commands": [
            {"step": 6, "purpose": "Style distance segment", "command": "SetLineThickness(d,4)"}
        ],
        "interaction_objects": [],
        "optional_script": {
            "needed": False,
            "script_type": "none",
            "reason": "",
            "target_object": "",
            "trigger": "none",
            "script_body": "",
        },
        "expected_created_objects": [
            {"name": "O", "type": "point", "role": "center"},
            {"name": "A", "type": "point", "role": "boundary_point"},
            {"name": "c", "type": "circle_boundary", "role": "reference"},
            {"name": "P", "type": "point", "role": "external_point"},
            {"name": "d", "type": "segment", "role": "distance_segment"},
        ],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": [],
    })

    report = await validate_geogebra_execution_payload(payload)

    assert report.ok is True
    assert report.render_ms is None
    assert report.validation_mode == "static"


@pytest.mark.asyncio
async def test_geogebra_execution_payload_rejects_geometry_contract_motion_drift():
    spec_payload = _spec_bundle_payload()["visualizations"][0]
    spec_payload["math_definition"]["objects"].extend([
        {
            "name": "t",
            "type": "slider_parameter",
            "definition": "motion driver",
            "role": "driver",
            "must_exist_before_animation": True,
        },
        {
            "name": "P",
            "type": "moving_point",
            "definition": "moving point",
            "role": "observed point",
            "must_exist_before_animation": False,
        },
    ])
    spec_payload["geogebra_plan"]["requires_slider"] = True
    spec_payload["interaction_and_animation"] = {
        "has_animation": True,
        "animation_driver": "slider",
        "animation_description": "Move P with slider t",
        "animation_duration_ms": 3000,
        "parameters": [
            {
                "name": "t",
                "type": "number",
                "range": {"min": -2, "max": 2, "step": 0.5},
                "default_value": 0,
                "meaning": "horizontal position",
            }
        ],
        "user_interactions": [
            {"interaction_type": "move_slider", "target": "t", "purpose": "move P"}
        ],
        "animation_sequence": ["Move P"],
        "stopping_condition_or_final_state": "P stops at slider value",
    }
    spec_payload["geometry_contract"] = {
        "core_objects": [
            {"name": "P", "type": "moving_point", "role": "observed point", "must_be_visible": True},
            {"name": "c", "type": "circle_boundary", "role": "target boundary", "must_be_visible": True},
        ],
        "motion": {
            "driver": "t",
            "moving_object": "P",
            "path_type": "line",
            "path_definition": "P moves horizontally",
            "sample_values": [-2, 0, 2],
            "expected_positions_description": "P changes x-coordinate when t changes",
        },
        "invariants": [
            {"type": "boundary_of", "objects": ["c"], "description": "c remains the target boundary"}
        ],
        "student_checkpoints": [
            {"state": "start", "observation": "P starts left"},
            {"state": "middle", "observation": "P reaches the middle"},
            {"state": "end", "observation": "P ends right"},
        ],
        "must_not_change_meaning": ["Do not make P static"],
    }
    spec = schemas.VisualizationSpec.model_validate(spec_payload)
    payload = schemas.GeoGebraExecutionPayload.model_validate({
        "title": "漂移测试",
        "preferred_geogebra_app": "geometry",
        "execution_mode": "command_only",
        "math_meaning_summary": "Payload creates P but does not bind it to t.",
        "object_naming_convention": "Use short English labels.",
        "commands": [
            {"step": 1, "purpose": "[core] Create slider", "command": "t=Slider(-2,2,0.5)"},
            {"step": 2, "purpose": "[core] Create center", "command": "O=(0,0)"},
            {"step": 3, "purpose": "[core] Create boundary point", "command": "A=(1,0)"},
            {"step": 4, "purpose": "[core] Create circle", "command": "c=Circle(O,A)"},
            {"step": 5, "purpose": "[core] Create static point", "command": "P=(1,1)"},
        ],
        "property_commands": [],
        "interaction_objects": [{"name": "t", "type": "slider", "purpose": "drive P"}],
        "optional_script": {
            "needed": False,
            "script_type": "none",
            "reason": "",
            "target_object": "",
            "trigger": "none",
            "script_body": "",
        },
        "expected_created_objects": [
            {"name": "t", "type": "slider_parameter", "role": "core driver"},
            {"name": "c", "type": "circle_boundary", "role": "core boundary"},
            {"name": "P", "type": "point", "role": "core moving point"},
        ],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": [],
    })

    with pytest.raises(GeoGebraValidationError) as exc:
        await validate_geogebra_execution_payload(payload, spec=spec.model_dump(mode="json"))

    assert any(item["kind"] == "geometry_motion_not_driver_bound" for item in exc.value.violations)


def test_geogebra_execution_payload_schema_accepts_harmless_llm_enum_mistakes():
    payload = schemas.GeoGebraExecutionPayload.model_validate({
        "title": "容错测试",
        "preferred_geogebra_app": "geometry",
        "execution_mode": "command_only",
        "math_meaning_summary": "小的枚举错误不应导致整张图丢失。",
        "object_naming_convention": "Use short English labels.",
        "commands": [{"step": 1, "purpose": "Create point", "command": "P=(0,0)"}],
        "property_commands": [],
        "interaction_objects": [
            {"name": "P", "type": "point", "purpose": "LLM confused math point with UI control"}
        ],
        "optional_script": {
            "needed": False,
            "script_type": "",
            "reason": "",
            "target_object": "",
            "trigger": "",
            "script_body": "",
        },
        "expected_created_objects": [{"name": "P", "type": "point", "role": "reference"}],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": [],
    })

    assert payload.interaction_objects[0].type == "none"
    assert payload.optional_script.script_type == "none"
    assert payload.optional_script.trigger == "none"


def test_geogebra_execution_payload_draft_normalizes_string_commands_and_expected_objects():
    spec = schemas.VisualizationSpec.model_validate(_spec_bundle_payload()["visualizations"][0])
    payload = normalize_geogebra_execution_payload_draft(
        {
            "title": "草稿容错",
            "preferred_geogebra_app": "",
            "commands": [
                "O=(0,0)",
                "A=(3,0)",
                "c=Circle(O,A)",
                {"step": "4", "purpose": "Create measured point", "command": "P=(4,2)"},
            ],
            "property_commands": ["SetLineThickness(c,4)"],
            "expected_created_objects": [],
        },
        spec=spec,
    )

    assert [row.command for row in payload.commands] == [
        "O=(0,0)",
        "A=(3,0)",
        "c=Circle(O,A)",
        "P=(4,2)",
    ]
    assert all(row.purpose.startswith("[core]") for row in payload.commands)
    assert payload.property_commands[0].command == "SetLineThickness(c,4)"
    assert payload.optional_script.needed is False
    assert [row.name for row in payload.expected_created_objects][:3] == ["O", "A", "c"]


def test_geogebra_execution_payload_draft_normalizes_slider_metadata_and_multiline_commands():
    spec = schemas.VisualizationSpec.model_validate(_spec_bundle_payload()["visualizations"][0])
    payload = normalize_geogebra_execution_payload_draft(
        {
            "title": "参数范围",
            "preferred_geogebra_app": "geometry",
            "commands": [
                {"step": 1, "purpose": "[core] Create slider", "command": "t=Slider(-4,1,0.02)"},
                {"step": 2, "purpose": "[core] Create point", "command": "T=(t,0)"},
                {
                    "step": 3,
                    "purpose": "[support] Conditional visibility should be dropped",
                    "command": "SetConditionToShowObject(T,t>0)",
                },
                {
                    "step": 4,
                    "purpose": "[support] Define interval endpoints",
                    "command": "kmin=abs(t)\nkmax=t+2",
                },
            ],
            "property_commands": [
                {"step": 1, "purpose": "Initialize slider", "command": "SetValue(t,0)"},
                {"step": 2, "purpose": "Style point", "command": "SetPointSize(T,6)"},
                {"step": 3, "purpose": "Conditional visibility", "command": "SetConditionToShowObject(T,t>0)"},
            ],
            "interaction_objects": [
                {
                    "type": "slider",
                    "name": "t",
                    "min": -4,
                    "max": 1,
                    "step": 0.02,
                    "default": 0,
                    "caption": "参数t",
                },
            ],
            "expected_created_objects": [
                {"name": "support:T", "type": "point", "role": "moving center"}
            ],
        },
        spec=spec,
    )

    assert [row.command for row in payload.commands] == [
        "t=Slider(-4,1,0.02)",
        "T=(t,0)",
        "kmin=abs(t)",
        "kmax=t+2",
    ]
    assert [row.command for row in payload.property_commands] == ["SetPointSize(T,6)"]
    assert [item.model_dump() for item in payload.interaction_objects] == [
        {"name": "t", "type": "slider", "purpose": "参数t"}
    ]
    assert payload.expected_created_objects[0].name == "T"
    assert payload.expected_created_objects[0].role.startswith("support:")


@pytest.mark.asyncio
async def test_geogebra_execution_sanitizer_salvages_small_codegen_mistakes():
    raw_payload = {
        "title": "轨迹法",
        "preferred_geogebra_app": "geometry",
        "execution_mode": "command_only",
        "math_meaning_summary": "把点C的运动转成中点M的轨迹。",
        "object_naming_convention": "Use short English labels.",
        "commands": [
            {"step": 1, "purpose": "Create slider", "command": "beta = Slider(0, 2*pi, 0.02)"},
            {"step": 2, "purpose": "Create base point", "command": "A = (-1, sqrt(3))"},
            {"step": 3, "purpose": "Create moving point", "command": "C = A + (cos(beta), sin(beta))"},
        ],
        "property_commands": [
            {"step": idx, "purpose": "Styling", "command": "ShowLabel(C, true)"}
            for idx in range(1, 18)
        ],
        "interaction_objects": [
            {"name": "C", "type": "point", "purpose": "LLM confused math point with UI control"},
            {"name": "beta", "type": "slider", "purpose": "Move C"},
        ],
        "optional_script": {
            "needed": False,
            "script_type": "",
            "reason": "",
            "target_object": "",
            "trigger": "",
            "script_body": "",
        },
        "expected_created_objects": [
            {"name": "A", "type": "point", "role": "base"},
            {"name": "C", "type": "point", "role": "moving"},
            {"name": "beta", "type": "numeric", "role": "parameter"},
        ],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": [],
    }

    sanitized = sanitize_geogebra_execution_payload_with_report(raw_payload)
    commands = [row.command for row in sanitized.payload.commands]

    assert sanitized.rewrite_map == {"beta": "param_beta"}
    assert "C = (x(A) + cos(param_beta), y(A) + sin(param_beta))" in commands
    assert len(sanitized.payload.property_commands) == 16
    assert [obj.name for obj in sanitized.payload.interaction_objects] == ["param_beta"]
    assert sanitized.payload.optional_script.script_type == "none"
    assert sanitized.payload.expected_created_objects[-1].name == "param_beta"

    report = await validate_geogebra_execution_payload(sanitized.payload)
    assert report.ok is True


@pytest.mark.asyncio
async def test_geogebra_execution_sanitizer_drops_non_core_expected_text_objects():
    raw_payload = {
        "title": "文本容错",
        "preferred_geogebra_app": "geometry",
        "execution_mode": "command_only",
        "math_meaning_summary": "文本对象缺失不应阻止核心几何图展示。",
        "object_naming_convention": "Use short English labels.",
        "commands": [
            {"step": 1, "purpose": "Create point", "command": "A=(0,0)"},
            {"step": 2, "purpose": "Create text", "command": "k_text=Text(\"k=1\", (0,1))"},
        ],
        "property_commands": [],
        "interaction_objects": [],
        "optional_script": {
            "needed": False,
            "script_type": "none",
            "reason": "",
            "target_object": "",
            "trigger": "none",
            "script_body": "",
        },
        "expected_created_objects": [
            {"name": "A", "type": "point", "role": "core point"},
            {"name": "k_text", "type": "text", "role": "display label"},
        ],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": [],
    }

    sanitized = sanitize_geogebra_execution_payload_with_report(raw_payload)

    assert [row.name for row in sanitized.payload.expected_created_objects] == ["A"]
    report = await validate_geogebra_execution_payload(sanitized.payload)
    assert report.ok is True


@pytest.mark.asyncio
async def test_geogebra_execution_sanitizer_rewrites_intersection_list_elements():
    raw_payload = {
        "title": "交点容错",
        "preferred_geogebra_app": "geometry",
        "execution_mode": "command_only",
        "math_meaning_summary": "两个圆的交点应直接命名，避免列表 Element 形式在 Apps API 中丢失名称。",
        "object_naming_convention": "Use short English labels.",
        "commands": [
            {"step": 1, "purpose": "Create center", "command": "A=(0,0)"},
            {"step": 2, "purpose": "Create center", "command": "B=(1,0)"},
            {"step": 3, "purpose": "Create circle", "command": "c1=Circle(A,1)"},
            {"step": 4, "purpose": "Create circle", "command": "c2=Circle(B,1)"},
            {"step": 5, "purpose": "Create intersection list", "command": "pts=Intersect(c1,c2)"},
            {"step": 6, "purpose": "Name first point", "command": "P1=Element(pts,1)"},
            {"step": 7, "purpose": "Name second point", "command": "P2=Element(pts,2)"},
            {"step": 8, "purpose": "Measure", "command": "k=Distance(P1,P2)"},
        ],
        "property_commands": [],
        "interaction_objects": [],
        "optional_script": {
            "needed": False,
            "script_type": "none",
            "reason": "",
            "target_object": "",
            "trigger": "none",
            "script_body": "",
        },
        "expected_created_objects": [
            {"name": "P1", "type": "point", "role": "intersection"},
            {"name": "P2", "type": "point", "role": "intersection"},
            {"name": "k", "type": "numeric", "role": "measurement"},
        ],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": [],
    }

    sanitized = sanitize_geogebra_execution_payload_with_report(raw_payload)
    commands = [row.command for row in sanitized.payload.commands]

    assert "pts=Intersect(c1,c2)" not in commands
    assert "P1 = Intersect(c1, c2, 1)" in commands
    assert "P2 = Intersect(c1, c2, 2)" in commands
    assert [row.name for row in sanitized.payload.expected_created_objects] == ["P1", "P2", "k"]
    report = await validate_geogebra_execution_payload(sanitized.payload)
    assert report.ok is True


@pytest.mark.asyncio
async def test_geogebra_execution_sanitizer_marks_support_expected_objects_by_command_tier():
    raw_payload = {
        "title": "辅助测量容错",
        "preferred_geogebra_app": "geometry",
        "execution_mode": "command_only",
        "math_meaning_summary": "核心图不应被辅助数值拖垮。",
        "object_naming_convention": "Use short English labels.",
        "commands": [
            {"step": 1, "purpose": "[core] Create point T", "command": "T=(0,0)"},
            {"step": 2, "purpose": "[core] Create point K", "command": "K=(4,0)"},
            {"step": 3, "purpose": "[core] Create main segment", "command": "segTK=Segment(T,K)"},
            {"step": 4, "purpose": "[support] Measure optional minimum k", "command": "kmin=Distance(T,K)"},
            {"step": 5, "purpose": "[annotation] Add optional label", "command": "info=Text(\"说明\",(0,1))"},
        ],
        "property_commands": [],
        "interaction_objects": [],
        "optional_script": {
            "needed": False,
            "script_type": "none",
            "reason": "",
            "target_object": "",
            "trigger": "none",
            "script_body": "",
        },
        "expected_created_objects": [
            {"name": "support:kmin", "type": "numeric", "role": "可能 k 的最小值"},
            {"name": "info", "type": "text", "role": "说明文字"},
        ],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": [],
    }

    sanitized = sanitize_geogebra_execution_payload_with_report(raw_payload)
    expected = {row.name: row.role for row in sanitized.payload.expected_created_objects}

    assert expected["T"].startswith("core:")
    assert expected["kmin"].startswith("support:")
    assert "support:kmin" not in expected
    assert "info" not in expected
    report = await validate_geogebra_execution_payload(sanitized.payload)
    assert report.ok is True


@pytest.mark.asyncio
async def test_geogebra_execution_sanitizer_drops_support_conditional_object_creation():
    raw_payload = {
        "title": "方向范围容错",
        "preferred_geogebra_app": "classic",
        "execution_mode": "command_only",
        "math_meaning_summary": "核心正方形和测量应保留，辅助阴影不能拖垮整幅图。",
        "object_naming_convention": "Use short English labels.",
        "commands": [
            {"step": 1, "purpose": "[core] Create A", "command": "A=(0,0)"},
            {"step": 2, "purpose": "[core] Create B", "command": "B=(6,0)"},
            {"step": 3, "purpose": "[core] Create C", "command": "C=(6,6)"},
            {"step": 4, "purpose": "[core] Create D", "command": "D=(0,6)"},
            {"step": 5, "purpose": "[core] Create square", "command": "S=Polygon(A,B,C,D)"},
            {
                "step": 6,
                "purpose": "[support] Create optional region overlay",
                "command": (
                    "Rmid=If(t==0,"
                    "Polygon((0,0),(6,0),(6,2),(0,2)),"
                    "Polygon((0,0),(2,0),(2,6),(0,6)))"
                ),
            },
        ],
        "property_commands": [],
        "interaction_objects": [],
        "optional_script": {
            "needed": False,
            "script_type": "none",
            "reason": "",
            "target_object": "",
            "trigger": "none",
            "script_body": "",
        },
        "expected_created_objects": [
            {"name": "S", "type": "polygon", "role": "core square"},
            {"name": "Rmid", "type": "polygon", "role": "middle strip overlay"},
        ],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": [],
    }

    sanitized = sanitize_geogebra_execution_payload_with_report(raw_payload)
    commands = [row.command for row in sanitized.payload.commands]
    expected_roles = {row.name: row.role for row in sanitized.payload.expected_created_objects}

    assert all(not command.startswith("Rmid=") for command in commands)
    assert expected_roles["S"].startswith("core:")
    assert expected_roles["Rmid"].startswith("support:")
    report = await validate_geogebra_execution_payload(sanitized.payload)
    assert report.ok is True


@pytest.mark.asyncio
async def test_geogebra_execution_sanitizer_moves_property_creations_into_commands():
    raw_payload = {
        "title": "属性命令误放创建命令",
        "preferred_geogebra_app": "geometry",
        "execution_mode": "command_only",
        "math_meaning_summary": "把误放在 property_commands 的对象创建本地搬回 commands。",
        "object_naming_convention": "Use short English labels.",
        "commands": [
            {"step": 1, "purpose": "[core] Create center", "command": "O=(0,0)"},
            {"step": 2, "purpose": "[core] Create boundary point", "command": "A=(3,0)"},
        ],
        "property_commands": [
            {"step": 1, "purpose": "Accidentally create circle", "command": "c=Circle(O,A)"},
            {"step": 2, "purpose": "Style migrated circle", "command": "SetLineThickness(c,4)"},
        ],
        "interaction_objects": [],
        "optional_script": {
            "needed": False,
            "script_type": "none",
            "reason": "",
            "target_object": "",
            "trigger": "none",
            "script_body": "",
        },
        "expected_created_objects": [
            {"name": "O", "type": "point", "role": "core center"},
            {"name": "c", "type": "circle", "role": "circle from bad property command"},
        ],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": [],
    }

    sanitized = sanitize_geogebra_execution_payload_with_report(raw_payload)

    assert "c=Circle(O,A)" in [row.command for row in sanitized.payload.commands]
    assert [row.command for row in sanitized.payload.property_commands] == ["SetLineThickness(c,4)"]
    assert sanitized.payload.expected_created_objects[-1].role.startswith("support:")
    report = await validate_geogebra_execution_payload(sanitized.payload)
    assert report.ok is True


@pytest.mark.asyncio
async def test_geogebra_execution_sanitizer_rewrites_support_abs_distance_measurements():
    raw_payload = {
        "title": "数值表达式容错",
        "preferred_geogebra_app": "geometry",
        "execution_mode": "command_only",
        "math_meaning_summary": "辅助 k 值失败时核心点线仍应展示。",
        "object_naming_convention": "Use short English labels.",
        "commands": [
            {"step": 1, "purpose": "[core] Create point T", "command": "T=(0,0)"},
            {"step": 2, "purpose": "[core] Create point K", "command": "K=(4,0)"},
            {"step": 3, "purpose": "[core] Create main segment", "command": "segTK=Segment(T,K)"},
            {
                "step": 4,
                "purpose": "[support] Measure minimum k",
                "command": "kmin=Abs(Distance(T,K)-2*sqrt(3))",
            },
            {
                "step": 5,
                "purpose": "[support] Measure maximum k",
                "command": "kmax=Distance(T,K)+2*sqrt(3)",
            },
        ],
        "property_commands": [],
        "interaction_objects": [],
        "optional_script": {
            "needed": False,
            "script_type": "none",
            "reason": "",
            "target_object": "",
            "trigger": "none",
            "script_body": "",
        },
        "expected_created_objects": [
            {"name": "T", "type": "point", "role": "core point"},
            {"name": "segTK", "type": "segment", "role": "core segment"},
            {"name": "kmin", "type": "numeric", "role": "可能 k 的最小值"},
        ],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": [],
    }

    sanitized = sanitize_geogebra_execution_payload_with_report(raw_payload)
    commands = [row.command for row in sanitized.payload.commands]
    expected_roles = {row.name: row.role for row in sanitized.payload.expected_created_objects}

    assert "dTK=Distance(T,K)" in commands
    assert "gap=2*sqrt(3)" in commands
    assert "kmin=abs(dTK-gap)" in commands
    assert "kmax=dTK+gap" in commands
    assert expected_roles["kmin"].startswith("support:")
    report = await validate_geogebra_execution_payload(sanitized.payload)
    assert report.ok is True


@pytest.mark.asyncio
async def test_geogebra_execution_payload_static_validator_rejects_large_payload():
    payload = schemas.GeoGebraExecutionPayload.model_validate({
        "title": "过大的可视化",
        "preferred_geogebra_app": "geometry",
        "execution_mode": "command_only",
        "math_meaning_summary": "命令过多，应要求简化。",
        "object_naming_convention": "Use short English labels.",
        "commands": [
            {"step": idx, "purpose": f"Create point {idx}", "command": f"P{idx}=({idx},0)"}
            for idx in range(1, 18)
        ],
        "property_commands": [],
        "interaction_objects": [],
        "optional_script": {
            "needed": False,
            "script_type": "none",
            "reason": "",
            "target_object": "",
            "trigger": "none",
            "script_body": "",
        },
        "expected_created_objects": [
            {"name": "P1", "type": "point", "role": "first point"}
        ],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": [],
    })

    with pytest.raises(GeoGebraValidationError) as exc:
        await validate_geogebra_execution_payload(payload)

    assert any(item["kind"] == "command_budget" for item in exc.value.violations)


@pytest.mark.asyncio
async def test_generate_geogebra_visualization_or_fallback_degrades_on_missing_expected_object(monkeypatch):
    transport = FakeTransport(
        json_by_model={
            settings.gemini.model_vizcoder: json.dumps({
                "title": "边界距离",
                "preferred_geogebra_app": "geometry",
                "execution_mode": "command_only",
                "math_meaning_summary": "说明边界距离。",
                "object_naming_convention": "Use short English labels.",
                "commands": [
                    {"step": 1, "purpose": "Create center", "command": "O=(0,0)"}
                ],
                "property_commands": [],
                "interaction_objects": [],
                "optional_script": {
                    "needed": False,
                    "script_type": "none",
                    "reason": "",
                    "target_object": "",
                    "trigger": "none",
                    "script_body": ""
                },
                "expected_created_objects": [
                    {"name": "missing_obj", "type": "point", "role": "should_fail_binding"}
                ],
                "consistency_checks": [],
                "fallback_used": False,
                "fallback_reason": "",
                "implementation_notes": []
            })
        }
    )
    client = GeminiClient(transport)
    spec = schemas.VisualizationSpec.model_validate(_spec_bundle_payload()["visualizations"][0])

    async def _should_not_run(payload, *, spec=None, timeout_s=20.0):
        raise AssertionError("runtime validator should not be called when local payload binding validation fails")

    monkeypatch.setattr(
        "app.services.geogebra_codegen_service.validate_geogebra_execution_payload",
        _should_not_run,
    )

    result = await generate_geogebra_visualization_or_fallback(llm=client, spec=spec)

    assert result.execution_payload is None
    assert result.error_summary is not None
    assert "expected created object" in result.error_summary


@pytest.mark.asyncio
async def test_generate_geogebra_visualization_repairs_static_failure(monkeypatch):
    initial_payload = {
        "title": "边界距离",
        "preferred_geogebra_app": "geometry",
        "execution_mode": "command_only",
        "math_meaning_summary": "说明边界距离。",
        "object_naming_convention": "Use short English labels.",
        "commands": [
            {"step": 1, "purpose": "Create center", "command": "O=(0,0)"}
        ],
        "property_commands": [],
        "interaction_objects": [],
        "optional_script": {
            "needed": False,
            "script_type": "none",
            "reason": "",
            "target_object": "",
            "trigger": "none",
            "script_body": ""
        },
        "expected_created_objects": [
            {"name": "missing_obj", "type": "point", "role": "bad binding"}
        ],
        "consistency_checks": [],
        "fallback_used": False,
        "fallback_reason": "",
        "implementation_notes": []
    }
    repaired_payload = {
        **initial_payload,
        "commands": [
            {"step": 1, "purpose": "Create center", "command": "O=(0,0)"},
            {"step": 2, "purpose": "Create repaired point", "command": "P=(1,0)"},
        ],
        "expected_created_objects": [
            {"name": "P", "type": "point", "role": "repaired core point"}
        ],
    }
    transport = _SequencedJsonTransport([initial_payload, repaired_payload])
    client = GeminiClient(transport)
    spec = schemas.VisualizationSpec.model_validate(_spec_bundle_payload()["visualizations"][0])

    async def _ok(payload, *, spec=None, timeout_s=20.0):
        return GeoGebraValidationReport(ok=True, render_ms=9.0)

    monkeypatch.setattr(
        "app.services.geogebra_codegen_service.validate_geogebra_execution_payload",
        _ok,
    )

    result = await generate_geogebra_visualization_or_fallback(llm=client, spec=spec)

    assert result.execution_payload is not None
    assert result.repair_attempted is True
    assert result.repaired is True
    assert result.error_summary is None
    assert [row.command for row in result.execution_payload.commands] == ["O=(0,0)", "P=(1,0)"]
    assert len(transport.calls) == 2
