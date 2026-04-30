"""Prompt Template framework tests (§11.1 verification).

Verifies for every registered prompt:
  - explain() contains ≥3 design decisions with title + rationale;
  - preview() renders without raising and includes system / user sections;
  - build() returns at least [system, user];
  - trace_tag() exposes name + version;
  - diff_preview() produces a diff when kwargs change;
  - call_structured() round-trips a valid JSON via FakeTransport;
  - call_structured() triggers the repair loop on first invalid JSON
    and succeeds on the second attempt.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.config import settings
from app.prompts import PromptRegistry
from app.prompts.solver_prompt import _load_fewshot_examples
from app.schemas import ParsedQuestion
from app.services.llm_client import FakeTransport, GeminiClient, LLMError

# ---- registry-wide invariants -----------------------------------------

def test_registry_has_core_prompts():
    names = PromptRegistry.names()
    assert {
        "dialog", "parser", "solver", "vizplanner", "vizitem", "vizcoder", "vizspec", "jsxgraph_codegen"
    }.issubset(set(names))


@pytest.mark.parametrize("name", ["dialog", "parser", "solver", "vizcoder", "vizitem"])
def test_prompt_has_design_decisions(name: str):
    t = PromptRegistry.get(name)
    assert len(t.design_decisions) >= 3, "must document ≥3 design decisions"
    for d in t.design_decisions:
        assert d.title and d.rationale, "each decision needs title + rationale"


@pytest.mark.parametrize("name", ["dialog", "parser", "solver", "vizcoder", "vizitem"])
def test_explain_is_stable(name: str):
    t = PromptRegistry.get(name)
    text = t.explain()
    assert t.name in text
    assert "DESIGN DECISIONS" in text
    # each design decision must appear in the rendered explanation
    for d in t.design_decisions:
        assert d.title in text


# ---- preview / build --------------------------------------------------

SAMPLE_KWARGS: dict[str, dict] = {
    "dialog": {
        "session_title": "二次函数追问",
        "question_context": {
            "question_id": "q-1",
            "parsed_question": {"question_text": "求抛物线顶点", "given": [], "find": []},
        },
        "summary": "用户已经理解配方法, 但不确定顶点坐标怎么读。",
        "key_facts": ["题目围绕二次函数顶点式展开"],
        "open_questions": ["顶点坐标与对称轴如何快速读取"],
        "recent_messages": [
            {"role": "user", "content": "为什么要配方?"},
            {"role": "assistant", "content": "因为这样能把式子转成顶点式。"},
        ],
        "user_message": "那顶点坐标怎么从式子里直接看出来?",
    },
    "parser": {"raw_ocr": "已知 a=3, b=4, 求斜边长。"},
    "solver": {
        "parsed_question": {
            "subject": "math",
            "grade_band": "junior",
            "stem_text": "已知 a=3, b=4, 求斜边长。",
            "givens": [{"symbol": "a", "value": "3"}, {"symbol": "b", "value": "4"}],
            "unknowns": ["c"],
            "figures": [],
            "candidate_kps": [],
        },
        "existing_patterns": [],
        "existing_kps": [],
    },
    "vizcoder": {
        "parsed_question": {"subject": "math", "grade_band": "junior", "stem_text": "直角三角形斜边长。"},
        "answer_package": {
            "question_understanding": {"restated_goal": "求 c"},
            "solution_steps": [],
            "method_pattern": None,
        },
    },
    "vizplanner": {
        "parsed_question": {
            "subject": "math",
            "grade_band": "junior",
            "question_text": "已知二次函数图像与 x 轴交点, 求最值。",
            "given": ["顶点在第一象限", "与 x 轴交于 A,B"],
            "find": ["最小值"],
        },
        "answer_package": {
            "question_understanding": {
                "restated_question": "求最值",
                "givens": [],
                "unknowns": [],
                "implicit_conditions": [],
            },
            "key_points_of_question": ["顶点位置", "与 x 轴交点关系"],
            "solution_steps": [],
            "key_points_of_answer": ["先构造函数图像", "再比较极值"],
            "method_pattern": {
                "pattern_id_suggested": "p1",
                "name_cn": "图像法",
                "when_to_use": "涉及二次函数最值",
                "general_procedure": ["画图", "找顶点"],
                "pitfalls": ["忽略定义域"],
            },
            "similar_questions": [
                {"statement": "s1", "answer_outline": "a1"},
                {"statement": "s2", "answer_outline": "a2"},
                {"statement": "s3", "answer_outline": "a3"},
            ],
            "knowledge_points": [{"node_ref": "kp:quad", "weight": 1.0}],
            "self_check": ["顶点坐标是否正确"],
        },
    },
    "vizitem": {
        "parsed_question": {
            "subject": "math",
            "grade_band": "junior",
            "question_text": "已知二次函数图像与 x 轴交点, 求最值。",
            "given": ["顶点在第一象限", "与 x 轴交于 A,B"],
            "find": ["最小值"],
        },
        "answer_package": {
            "question_understanding": {
                "restated_question": "求最值",
                "givens": [],
                "unknowns": [],
                "implicit_conditions": [],
            },
            "key_points_of_question": ["顶点位置", "与 x 轴交点关系"],
            "solution_steps": [
                {
                    "step_index": 1,
                    "statement": "画出函数草图并标出交点 A,B。",
                    "rationale": "先把文字条件变成图形对象。",
                    "formula": "",
                    "why_this_step": "交点关系决定后续最值判断",
                    "viz_ref": "viz-1",
                }
            ],
            "key_points_of_answer": ["先构造函数图像", "再比较极值"],
            "method_pattern": {
                "pattern_id_suggested": "p1",
                "name_cn": "图像法",
                "when_to_use": "涉及二次函数最值",
                "general_procedure": ["画图", "找顶点"],
                "pitfalls": ["忽略定义域"],
            },
            "similar_questions": [
                {"statement": "s1", "answer_outline": "a1"},
                {"statement": "s2", "answer_outline": "a2"},
                {"statement": "s3", "answer_outline": "a3"},
            ],
            "knowledge_points": [{"node_ref": "kp:quad", "weight": 1.0}],
            "self_check": ["顶点坐标是否正确"],
        },
        "storyboard": {
            "theme_cn": "从图像关系到最值结论",
            "selection_rationale_cn": "选择两个关键跳跃",
            "symbol_map": [{"symbol": "A", "meaning_cn": "交点 A"}],
            "shared_params": [{"name": "t", "label_cn": "参数 t", "kind": "slider", "default": 0, "min": -2, "max": 2, "step": 0.1}],
            "coverage_summary": [{"item_id": "viz-1", "summary_cn": "画出交点", "anchor_refs": [{"kind": "question_given", "ref": "given:0"}]}],
            "sequence": ["viz-1", "viz-2"],
            "items": [
                {
                    "id": "viz-1",
                    "title_cn": "交点示意",
                    "anchor_refs": [{"kind": "question_given", "ref": "given:0"}],
                    "difficulty_reason_cn": "条件不容易映射到图像。",
                    "student_confusion_risk": "high",
                    "conceptual_jump_cn": "从题设到对象",
                    "why_visualization_needed_cn": "帮助建立图像对象",
                    "learning_goal_cn": "理解交点位置",
                    "engine": "geogebra",
                    "shared_symbols": ["A"],
                    "shared_params": ["t"],
                    "depends_on": [],
                    "caption_outline_cn": "对应解答 step 1 的交点建立。",
                    "geo_target_cn": "显示抛物线和 x 轴交点",
                },
                {
                    "id": "viz-2",
                    "title_cn": "顶点比较",
                    "anchor_refs": [{"kind": "solution_step", "ref": "2"}],
                    "difficulty_reason_cn": "极值位置不直观。",
                    "student_confusion_risk": "medium",
                    "conceptual_jump_cn": "从交点到顶点",
                    "why_visualization_needed_cn": "需要补出顶点",
                    "learning_goal_cn": "理解顶点与最值",
                    "engine": "geogebra",
                    "shared_symbols": ["A"],
                    "shared_params": ["t"],
                    "depends_on": ["viz-1"],
                    "caption_outline_cn": "对应极值比较。",
                    "geo_target_cn": "显示顶点",
                },
            ],
        },
        "storyboard_item": {
            "id": "viz-1",
            "title_cn": "交点示意",
            "anchor_refs": [{"kind": "question_given", "ref": "given:0"}],
            "difficulty_reason_cn": "条件不容易映射到图像。",
            "student_confusion_risk": "high",
            "conceptual_jump_cn": "从题设到对象",
            "why_visualization_needed_cn": "帮助建立图像对象",
            "learning_goal_cn": "理解交点位置",
            "engine": "geogebra",
            "shared_symbols": ["A"],
            "shared_params": ["t"],
            "depends_on": [],
            "caption_outline_cn": "对应解答 step 1 的交点建立。",
            "geo_target_cn": "显示抛物线和 x 轴交点"
        },
        "previous_items": [],
    },
    "vizspec": {
        "original_problem": {
            "subject": "math",
            "grade_band": "junior",
            "question_text": "点 P 到圆的距离如何理解？",
            "diagram_description": "圆心 O，半径 r，点 P 在圆外移动。",
        },
        "answer_package": {
            "question_understanding": {
                "restated_question": "理解点到圆的距离",
                "givens": ["圆心 O", "半径 r"],
                "unknowns": ["距离定义"],
                "implicit_conditions": [],
            },
            "key_points_of_question": ["边界与区域的区分"],
            "solution_steps": [
                {
                    "step_index": 1,
                    "statement": "先明确圆指的是边界还是区域。",
                    "rationale": "避免距离定义歧义。",
                    "formula": "",
                    "why_this_step": "只有先澄清定义，后续图示才不会误导。",
                    "viz_ref": "viz_1",
                }
            ],
            "key_points_of_answer": ["距离默认是到边界的最短距离"],
            "method_pattern": {
                "pattern_id_suggested": "p-circle-distance",
                "name_cn": "定义澄清 + 图示法",
                "when_to_use": "对象定义容易混淆时",
                "general_procedure": ["先澄清定义", "再用图形表达最短距离"],
                "pitfalls": ["把圆误认为填充区域"],
            },
            "similar_questions": [
                {"statement": "s1", "answer_outline": "a1"},
                {"statement": "s2", "answer_outline": "a2"},
                {"statement": "s3", "answer_outline": "a3"},
            ],
            "knowledge_points": [{"node_ref": "kp:circle", "weight": 1.0}],
            "self_check": ["是否区分边界与区域"],
        },
        "teaching_preference": "Focus on conceptual clarity. Avoid decorative animation.",
    },
    "jsxgraph_codegen": {
        "spec": {
            "id": "viz_1",
            "title": "Point to circle boundary distance",
            "priority": 1,
            "recommended": True,
            "visualization_type": "locus_trace",
            "pedagogical_purpose": "Clarify distance to the circle boundary",
            "mathematical_claim_being_shown": "Distance is measured to the circle boundary",
            "math_definition": {
                "objects": [
                    {"name": "O", "type": "point", "definition": "Center", "role": "reference", "must_exist_before_animation": True},
                    {"name": "c", "type": "circle_boundary", "definition": "Boundary", "role": "distance target", "must_exist_before_animation": True},
                ]
            },
            "visual_design": {
                "coordinate_system": {
                    "needed": True,
                    "type": "cartesian_2d",
                    "suggested_viewport": {"xmin": -5, "xmax": 5, "ymin": -5, "ymax": 5},
                    "reason": "Show the circle and moving point clearly",
                },
                "visible_objects": ["O", "c"],
                "highlighted_objects": ["c"],
                "optional_hidden_helper_objects": [],
                "labels_to_show": ["O"],
                "measurements_to_show": ["d(P,c)"],
                "region_or_trace_display": {
                    "needed": True,
                    "type": "trace",
                    "description": "Show a trace of the moving point",
                },
            },
            "interaction_and_animation": {
                "has_animation": True,
                "animation_driver": "slider",
                "animation_description": "Move point P using a slider",
                "animation_duration_ms": 2500,
                "parameters": [{"name": "t", "type": "number", "range": {"min": -4, "max": 4, "step": 0.5}, "default_value": 0, "meaning": "point position"}],
                "user_interactions": [{"interaction_type": "move_slider", "target": "t", "purpose": "Explore distance change"}],
                "animation_sequence": ["Create boundary", "Move point", "Update distance segment"],
                "stopping_condition_or_final_state": "Slider at either endpoint",
            },
            "expected_result": {
                "final_visual_outcome": "Circle boundary with measured shortest segment",
                "mathematical_conclusion_visible_to_student": "Distance goes to the boundary",
                "common_misinterpretations_to_avoid": ["Do not fill the disk"],
            },
            "implementation_guidance": {
                "preferred_rendering_strategy": "Use a slider and dynamic segment",
                "simplifications_allowed": ["Use one guide line"],
                "things_that_must_not_be_omitted": ["Boundary visibility"],
                "things_that_must_not_be_invented": ["Filled disk"],
                "fallback_if_animation_is_too_complex": "Use three static point positions",
            },
            "ambiguities": [],
        },
    },
}


@pytest.mark.parametrize("name", ["dialog", "parser", "solver", "vizcoder", "vizitem"])
def test_preview_renders(name: str):
    t = PromptRegistry.get(name)
    out = t.preview(**SAMPLE_KWARGS[name])
    assert "[SYSTEM]" in out
    assert "[USER]" in out
    assert "OUTPUT SCHEMA" in out


@pytest.mark.parametrize("name", ["dialog", "parser", "solver", "vizcoder", "vizitem"])
def test_build_has_system_and_user(name: str):
    t = PromptRegistry.get(name)
    msgs = t.build(**SAMPLE_KWARGS[name])
    roles = [m["role"] for m in msgs]
    assert roles[0] == "system"
    assert roles[-1] == "user"


def test_solver_loads_curated_fewshot_examples():
    examples = _load_fewshot_examples(subject="math", grade_band="senior")
    assert examples, "expected curated few-shot examples on disk"
    assert any(ex.get("topic_prefix") == ["代数", "一元二次方程"] for ex in examples)


def test_solver_selects_topic_matched_fewshot_examples():
    t = PromptRegistry.get("solver")
    msgs = t.fewshot_examples(parsed_question={
        "subject": "math",
        "grade_band": "senior",
        "topic_path": ["代数", "一元二次方程", "因式分解"],
        "question_text": "解 $x^2-5x+6=0$",
        "given": [],
        "find": [],
        "diagram_description": "",
        "difficulty": 2,
        "tags": [],
        "confidence": 0.9,
    })
    assert len(msgs) >= 2
    assert msgs[0]["role"] == "user"
    assert "因式分解法" in msgs[1]["content"]


@pytest.mark.parametrize("name", ["dialog", "parser", "solver", "vizcoder", "vizitem"])
def test_trace_tag(name: str):
    t = PromptRegistry.get(name)
    tag = t.trace_tag()
    assert tag["prompt_name"] == name
    assert tag["prompt_version"].startswith("v")


def test_diff_preview_shows_changes():
    t = PromptRegistry.get("parser")
    diff = t.diff_preview(
        old_kwargs={"subject_hint": "math"},
        new_kwargs={"subject_hint": "physics"},
    )
    assert "-" in diff and "+" in diff


def test_solver_prompt_includes_curriculum_boundary_for_junior_students():
    t = PromptRegistry.get("solver")
    preview = t.preview(**SAMPLE_KWARGS["solver"])
    assert "Teaching audience: Junior High School students" in preview
    assert "Do not use Senior High School methods" in preview


def test_vizcoder_prompt_uses_geogebra_by_default():
    t = PromptRegistry.get("vizcoder")
    preview = t.preview(**SAMPLE_KWARGS["vizcoder"])
    assert "this is the current server-side default and should be preferred" in preview
    assert 'engine="geogebra"' in preview


def test_vizcoder_prompt_switches_with_config(monkeypatch):
    t = PromptRegistry.get("vizcoder")
    old = settings.viz.default_engine
    monkeypatch.setattr(settings.viz, "default_engine", "jsxgraph")
    try:
        preview = t.preview(**SAMPLE_KWARGS["vizcoder"])
    finally:
        monkeypatch.setattr(settings.viz, "default_engine", old)
    assert 'engine="jsxgraph"' in preview
    assert 'engine="jsxgraph" — this is the current server-side default and should be preferred.' in preview


def test_vizplanner_prompt_prefers_bottlenecks_over_fixed_steps():
    t = PromptRegistry.get("vizplanner")
    preview = t.preview(**SAMPLE_KWARGS["vizplanner"])
    assert "identify the conceptual bottlenecks where students are most likely to get stuck" in preview
    assert "Do not mechanically flatten the answer into step 1 / 2 / 3 / 4" in preview
    assert "If grade_band is junior, do not use Senior High School knowledge" in preview
    assert "Do not output ggb_commands or jsx_code" in preview
    assert "Do not output any GeoGebra commands or JSXGraph code" in preview
    assert "Each symbol_map entry must declare exactly one atomic symbol" in preview
    assert "do not combine symbols into one entry such as `P, Q`" in preview


def test_vizitem_prompt_locks_to_single_storyboard_item():
    t = PromptRegistry.get("vizitem")
    preview = t.preview(**SAMPLE_KWARGS["vizitem"])
    assert "Output exactly one Visualization JSON object" in preview
    assert "`id` must match storyboard_item.id exactly" in preview
    assert "the figure and explanations must stay within Junior High School knowledge" in preview
    assert 'This item should default to engine="geogebra"' in preview
    assert "The overall rendering preference is GeoGebra-first." in preview


def test_vizspec_prompt_bans_code_and_targets_schema_bundle():
    t = PromptRegistry.get("vizspec")
    preview = t.preview(**SAMPLE_KWARGS["vizspec"])
    assert "Do NOT output JSXGraph code" in preview
    assert "Do NOT output JavaScript code" in preview
    assert "If the source is for junior students" in preview
    assert "Teaching audience: Junior High School students" in preview
    assert "Generate the JSON specification only" in preview

    # Auto-derived schema contract block must appear with section header,
    # all literal-bearing fields, all numeric bounds, and all cross-field rules.
    assert "## Schema contract (auto-derived from Pydantic" in preview
    assert "All values listed below are exact tokens" in preview
    # Sample of literal fields covering EACH Literal alias defined in
    # app.schemas.visualization_spec — proves the auto-derivation reaches
    # them all, not just the 6 that used to be hand-listed.
    assert "teaching_value`: high | medium" in preview
    assert "visualization_type`: static_diagram | construction_steps" in preview
    assert "math_definition.objects.type`: point | line | segment" in preview
    assert "math_definition.relations.relation_type`: distance | intersection" in preview
    assert "visual_design.coordinate_system.type`: cartesian_2d | geometry_plane" in preview
    assert "region_or_trace_display.type`: none | trace | shaded_region" in preview
    assert "animation_driver`: none | slider | moving_point" in preview
    assert "parameters.type`: number | angle | integer_step | boolean" in preview
    assert "user_interactions.interaction_type`: drag | play_pause" in preview
    assert "ambiguities.impact`: low | medium | high" in preview
    assert "renderability_assessment.overall_readiness`: ready | mostly_ready | needs_revision" in preview
    assert "if parameters.type is 'number', 'angle', or 'integer_step' then range MUST be provided" in preview
    # Numeric bounds (not just enums) are also injected.
    assert "renderability_assessment.clarity_score` (integer): >= 0, <= 100" in preview
    assert "visualizations` (array): min length 3, max length 3" in preview
    # All four cross-field validator rule groups appear.
    assert "Cross-field rules" in preview
    assert "if has_animation=true then animation_driver must NOT be 'none'" in preview
    assert "suggested_viewport requires xmin < xmax" in preview
    assert "unless every visualization is needs_revision" in preview
    assert "if visualization_type='region_shading'" in preview
    assert "visualization_type selection guide" in preview
    assert "priority and recommendation calibration" in preview
    assert "renderability_assessment calibration" in preview
    assert "For numeric parameters, use `range.min`, `range.max`, and `range.step` directly." in preview


def test_vizspec_prompt_covers_every_literal_token_from_schema():
    """Drift guard: every Literal value in visualization_spec.py must
    appear in the rendered vizspec system prompt at least once."""
    from typing import Literal as _Literal
    from typing import get_args as _get_args
    from typing import get_origin as _get_origin

    from app.schemas import visualization_spec as vs_mod

    t = PromptRegistry.get("vizspec")
    preview = t.preview(**SAMPLE_KWARGS["vizspec"])
    for attr_name in dir(vs_mod):
        attr = getattr(vs_mod, attr_name)
        if _get_origin(attr) is _Literal:
            for token in _get_args(attr):
                assert (
                    str(token) in preview
                ), f"vizspec prompt missing literal token {attr_name}={token!r}"


def test_vizcoder_prompt_includes_junior_high_school_boundary():
    t = PromptRegistry.get("vizcoder")
    preview = t.preview(**SAMPLE_KWARGS["vizcoder"])
    assert "学段约束 (重要)" in preview
    assert "只使用初中阶段的知识" in preview


def test_jsxgraph_codegen_prompt_requires_code_only_function_contract():
    t = PromptRegistry.get("jsxgraph_codegen")
    preview = t.preview(**SAMPLE_KWARGS["jsxgraph_codegen"])
    assert "Output JavaScript code only" in preview
    assert "function renderVisualization(containerId, spec)" in preview
    assert "raw JavaScript only" in preview
    # Sandbox hard-constraint section must spell out the validator's
    # allow-list and forbid-list so the LLM stops emitting Date.now()
    # / new XMLHttpRequest / computed ['constructor'] patterns.
    assert "Sandbox Hard Constraints" in preview
    assert "ALLOWED globals" in preview
    assert "FORBIDDEN globals" in preview
    assert "Date" in preview and "performance" in preview
    assert "XMLHttpRequest" in preview and "WebSocket" in preview
    assert "setTimeout(\"string\", ms)" in preview
    assert '"constructor"' in preview
    assert "requestAnimationFrame" in preview
    assert "H helper reference" in preview
    assert "H.anim.loop" in preview
    assert "Critical spec fields to read first" in preview
    assert "Parameter reading pattern" in preview
    assert "Do NOT call JXG.JSXGraph.freeBoard(containerId)" in preview
    assert "Do NOT wrap the entire function body in a catch that suppresses failures" in preview
    assert "Never return an inert fallback" in preview
    assert "Success Contract" in preview
    assert "{ board, update, destroy }" in preview


@pytest.mark.parametrize("name", ["vizcoder", "vizplanner", "vizitem", "vizspec", "jsxgraph_codegen"])
def test_visualization_prompts_include_fewshot_examples(name: str):
    prompt = PromptRegistry.get(name)
    messages = prompt.fewshot_examples(**SAMPLE_KWARGS.get(name, {}))
    assert len(messages) >= 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


# ---- GeminiClient round-trip via FakeTransport -----------------------


_VALID_PARSED = {
    "subject": "math",
    "grade_band": "senior",
    "topic_path": ["几何", "三角形"],
    "question_text": "已知 a=3, b=4, 求斜边长。",
    "given": ["a=3", "b=4"],
    "find": ["c"],
    "diagram_description": "",
    "difficulty": 2,
    "tags": [],
    "confidence": 0.9,
}


def test_call_structured_happy_path():
    transport = FakeTransport(json_by_model={"gemini-2.0-flash": json.dumps(_VALID_PARSED)})
    client = GeminiClient(transport)
    parser = PromptRegistry.get("parser")
    result = asyncio.run(
        client.call_structured(
            template=parser,
            model="gemini-2.0-flash",
            model_cls=ParsedQuestion,
            template_kwargs={"raw_ocr": "已知 a=3, b=4, 求斜边长。"},
        )
    )
    assert isinstance(result, ParsedQuestion)
    assert result.find == ["c"]
    assert len(transport.calls) == 1  # no repair needed


def test_call_structured_happy_path_streaming():
    transport = FakeTransport(json_by_model={"gemini-2.0-flash": json.dumps(_VALID_PARSED)})
    client = GeminiClient(transport)
    parser = PromptRegistry.get("parser")
    result = asyncio.run(
        client.call_structured(
            template=parser,
            model="gemini-2.0-flash",
            model_cls=ParsedQuestion,
            template_kwargs={"raw_ocr": "已知 a=3, b=4, 求斜边长。"},
            stream=True,
            timeout_s=91,
        )
    )
    assert isinstance(result, ParsedQuestion)
    assert result.find == ["c"]
    assert len(transport.calls) == 1
    assert transport.calls[0]["stream"] is True


def test_call_text_happy_path():
    transport = FakeTransport(text_by_model={"gemini-2.0-flash": "function renderVisualization(containerId, spec) {}"})
    client = GeminiClient(transport)
    prompt = PromptRegistry.get("jsxgraph_codegen")
    result = asyncio.run(
        client.call_text(
            template=prompt,
            model="gemini-2.0-flash",
            template_kwargs=SAMPLE_KWARGS["jsxgraph_codegen"],
        )
    )
    assert result.startswith("function renderVisualization")
    assert len(transport.calls) == 1
    assert transport.calls[0]["text"] is True


class _RepairTransport(FakeTransport):
    """First call returns bad JSON, second returns valid."""

    def __init__(self, bad: str, good: str) -> None:
        super().__init__()
        self._responses = [bad, good]

    async def generate_json(self, *, model, messages, response_schema, timeout_s):
        self.calls.append({"model": model, "messages": messages})
        raw = self._responses.pop(0) if self._responses else "{}"
        return raw, 0, 0


def test_call_structured_repair_loop_recovers():
    transport = _RepairTransport(bad="{}", good=json.dumps(_VALID_PARSED))
    client = GeminiClient(transport)
    parser = PromptRegistry.get("parser")
    result = asyncio.run(
        client.call_structured(
            template=parser,
            model="gemini-2.0-flash",
            model_cls=ParsedQuestion,
            template_kwargs={"raw_ocr": "q"},
            min_repair_attempts=1,
        )
    )
    assert isinstance(result, ParsedQuestion)
    assert len(transport.calls) == 2  # one repair round-trip


def test_call_structured_gives_up_after_max_attempts():
    transport = FakeTransport(json_by_model={"gemini-2.0-flash": "{}"})
    client = GeminiClient(transport)
    parser = PromptRegistry.get("parser")
    with pytest.raises(LLMError):
        asyncio.run(
            client.call_structured(
                template=parser,
                model="gemini-2.0-flash",
                model_cls=ParsedQuestion,
                template_kwargs={"raw_ocr": "q"},
            )
        )
