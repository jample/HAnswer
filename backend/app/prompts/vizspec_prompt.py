"""Visualization specification planning prompt for HAVizNew Stage 1."""

from __future__ import annotations

import json
from typing import Any

from app.prompts._audience import curriculum_boundary_block
from app.prompts._pydantic_contract import summarize_pydantic_contract
from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import VISUALIZATION_SPEC_BUNDLE_SCHEMA
from app.schemas.visualization_spec import VisualizationSpecBundle


class VizSpecPrompt(PromptTemplate):
    version = PromptVersion(major=1, minor=3, date_updated="2026-04-22")
    name = "vizspec"

    purpose = (
        "把题目与解答转成 3 个高教学价值、结构完整、可直接用于后续 GeoGebra 执行载荷生成的 "
        "VisualizationSpec 候选。"
    )

    input_description = (
        "original_problem (原题文本或结构化题面), answer_package (已确认的教学型答案), "
        "teaching_preference (可选补充要求)。"
    )

    output_description = (
        "符合 VisualizationSpecBundle Schema 的 JSON。只输出规格，不输出任何 GeoGebra 命令、JavaScript、"
        "HTML 或伪代码。"
    )

    design_decisions = [
        DesignDecision(
            title="先规格化, 再代码生成",
            rationale=(
                "把数学对象、关系、约束、参数和观察结论先独立建模, 才能让 Stage 2 在不猜测"
                "题意的前提下生成稳定的 GeoGebra 指令。"
            ),
        ),
        DesignDecision(
            title="强制暴露歧义和保守解释",
            rationale=(
                "诸如 'distance to the circle' 这类措辞如果不在 Stage 1 明确成 boundary 还是 disk, "
                "Stage 2 很容易在数学意义上跑偏。"
            ),
        ),
        DesignDecision(
            title="候选数量固定为 3 个",
            rationale=(
                "HAVizNew 当前要求覆盖 3 个关键教学瓶颈, 并且至少 1 个 recommended。固定数量"
                "让后续逐图 GeoGebra 生成和前端多图渲染保持一致。"
            ),
        ),
        DesignDecision(
            title="要求实现级 fallback 指引",
            rationale=(
                "Stage 2 需要在动画、区域并集、trace 等复杂场景里优雅降级, 所以 Stage 1 必须"
                "把 fallback_if_animation_is_too_complex 明确写出来。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return VISUALIZATION_SPEC_BUNDLE_SCHEMA

    def fewshot_examples(self, **kwargs: Any) -> list[dict]:
        example_user = {
            "original_problem": {
                "subject": "math",
                "grade_band": "junior",
                "question_text": "点到圆的距离如何理解？",
            },
            "answer_package": {
                "question_understanding": {"restated_question": "澄清距离定义"},
                "solution_steps": [{"step_index": 1, "statement": "先区分边界与区域。"}],
            },
        }
        example_bundle = {
            "task_summary": {
                "source_math_topic": "geometry",
                "source_problem_type": "circle distance",
                "core_learning_goal": "Clarify boundary distance",
            },
            "visualizations": [
                {
                    "id": "viz_example_1",
                    "title": "Boundary distance with slider",
                    "priority": 1,
                    "teaching_value": "high",
                    "recommended": True,
                    "visualization_type": "measurement_demo",
                    "preferred_geogebra_app": "geometry",
                    "pedagogical_purpose": "Show that distance is measured to the boundary",
                    "when_to_use": "When students confuse circle boundary and disk region",
                    "mathematical_claim_being_shown": "Distance is measured to the circle boundary",
                    "student_observation_goal": ["Observe the shortest segment landing on the boundary"],
                    "source_dependency": {"depends_on_solution_steps": ["clarify boundary meaning"], "depends_on_assumptions": []},
                    "math_definition": {
                        "objects": [
                            {"name": "O", "type": "point", "definition": "circle center", "role": "reference", "must_exist_before_animation": True},
                            {"name": "c", "type": "circle_boundary", "definition": "circle boundary", "role": "distance target", "must_exist_before_animation": True},
                            {"name": "P", "type": "moving_point", "definition": "observed point", "role": "measured point", "must_exist_before_animation": False}
                        ],
                        "relations": [{"relation_type": "distance", "description": "Measure the shortest distance from P to c"}],
                        "constraints": [{"name": "motion", "expression_in_plain_math": "P=(t,2)", "meaning": "P moves horizontally"}],
                        "key_formulas": [{"formula": "d(P,c)=|OP-r|", "purpose": "connect motion to analytic distance"}]
                    },
                    "geogebra_plan": {
                        "object_creation_strategy": "command_only",
                        "recommended_command_families": ["geometry"],
                        "requires_slider": True,
                        "requires_trace": False,
                        "requires_locus": False,
                        "requires_region_shading": False,
                        "requires_sequence_or_list_generation": False,
                        "requires_minimal_script": False,
                        "script_reason_if_needed": "",
                    },
                    "visual_design": {
                        "coordinate_system": {"needed": True, "type": "cartesian_2d", "suggested_viewport": {"xmin": -5, "xmax": 5, "ymin": -5, "ymax": 5}, "reason": "Clear geometry view"},
                        "visible_objects": ["O", "c", "P"],
                        "highlighted_objects": ["c", "P"],
                        "optional_hidden_helper_objects": [],
                        "labels_to_show": ["O", "P"],
                        "measurements_to_show": ["d(P,c)"],
                        "region_or_trace_display": {"needed": False, "type": "boundary_only", "description": "Keep attention on the boundary"}
                    },
                    "interaction_and_animation": {
                        "has_animation": True,
                        "animation_driver": "slider",
                        "animation_description": "Move P with one slider",
                        "animation_duration_ms": 2500,
                        "parameters": [{"name": "t", "type": "number", "range": {"min": -4, "max": 4, "step": 0.5}, "default_value": 0, "meaning": "horizontal position of P"}],
                        "user_interactions": [{"interaction_type": "move_slider", "target": "t", "purpose": "compare distance values"}],
                        "animation_sequence": ["show circle boundary", "move P", "update the shortest segment"],
                        "stopping_condition_or_final_state": "Slider pauses at the chosen position"
                    },
                    "expected_result": {
                        "final_visual_outcome": "Boundary, moving point, and shortest segment stay visible",
                        "mathematical_conclusion_visible_to_student": "The measured distance lands on the circle boundary",
                        "common_misinterpretations_to_avoid": ["Do not shade the disk if the target is the boundary"]
                    },
                    "implementation_guidance": {
                        "preferred_rendering_strategy": "Use one slider-driven point and one dynamically updated segment",
                        "preferred_geogebra_object_naming_style": "Use short English labels such as O, c, P, t",
                        "simplifications_allowed": ["Keep the guide line implicit"],
                        "things_that_must_not_be_omitted": ["The circle boundary", "The shortest segment"],
                        "things_that_must_not_be_invented": ["A filled disk"],
                        "fallback_if_animation_is_too_complex": "Use three static point positions with the shortest segment drawn in each"
                    },
                    "consistency_checks": ["The rendered target remains the boundary rather than the region"],
                    "ambiguities": [],
                    "renderability_assessment": {
                        "clarity_score": 92,
                        "math_completeness_score": 90,
                        "implementation_stability_score": 88,
                        "overall_readiness": "ready"
                    }
                }
            ]
        }
        second = json.loads(json.dumps(example_bundle["visualizations"][0], ensure_ascii=False))
        second.update({
            "id": "viz_example_2",
            "title": "Boundary versus disk comparison",
            "priority": 2,
            "recommended": False,
            "visualization_type": "comparison_overlay",
            "pedagogical_purpose": "Compare the circumference with the filled disk interpretation",
            "mathematical_claim_being_shown": "The boundary and filled disk are different target sets",
            "when_to_use": "After students see the boundary-distance definition",
        })
        third = json.loads(json.dumps(example_bundle["visualizations"][0], ensure_ascii=False))
        third.update({
            "id": "viz_example_3",
            "title": "Distance formula sweep",
            "priority": 3,
            "recommended": False,
            "visualization_type": "function_plot",
            "preferred_geogebra_app": "graphing",
            "pedagogical_purpose": "Connect the geometric distance to the formula d(P,c)=|OP-r|",
            "mathematical_claim_being_shown": "The measured distance changes with OP according to |OP-r|",
            "when_to_use": "When moving from geometric observation to calculation",
        })
        example_bundle["visualizations"].extend([second, third])
        return [
            {"role": "user", "content": "Example input\n" + json.dumps(example_user, ensure_ascii=False, indent=2)},
            {"role": "assistant", "content": json.dumps(example_bundle, ensure_ascii=False, indent=2)},
        ]

    def system_message(self, **kwargs: Any) -> str:
        contract_block = summarize_pydantic_contract(VisualizationSpecBundle)
        return """You are a senior mathematical visualization designer for an educational software product for middle school students.

Your task is NOT to write code.
Your task is to convert a math solution, explanation, or teaching content into exactly three high-value visualization specifications that are mathematically precise, instructionally useful, and implementation-ready.

The output of this step will later be used by another model to generate a GeoGebra execution payload.
Therefore, your specification must be complete, explicit, and unambiguous enough for direct GeoGebra implementation.

## Primary Goal
Given a math problem solution or explanation, identify the three most pedagogically valuable visualization ideas, and produce a detailed visualization specification for each one.

The specification must make the mathematical meaning, objects, parameters, constraints, animation logic, expected visual result, and teaching purpose fully explicit.

## Important Rules
1. Do NOT output GeoGebra commands, JSXGraph code, JavaScript code, HTML, or pseudo-code.
2. Do NOT output JavaScript code in any form.
3. Do NOT output JSXGraph code in any form.
4. Do NOT output vague design ideas such as \"show this dynamically\" without defining exactly what moves, what changes, and what students should observe.
5. Do NOT assume undefined objects, hidden constraints, or unstated mathematical facts.
6. If the source content is ambiguous, explicitly identify the ambiguity and propose the most conservative mathematically valid interpretation.
7. Prefer mathematically correct, teachable, and visually clear designs over flashy or overly complex animations.
8. The visualizations are for middle school math learning, so they should emphasize conceptual clarity, not visual decoration.
9. If an animation is unnecessary, choose a static or lightly interactive visualization instead.
10. Every visualization must have a clear mathematical purpose and an observable conclusion.
11. The content must be self-contained so that a separate model can convert it into GeoGebra commands and minimal script without needing to infer missing meaning.
12. If there are multiple possible visualizations, choose the ones with the highest teaching value and the clearest GeoGebra implementation path.
13. Respect the subject and grade band in the source problem as a hard curriculum boundary.
    If the source is for junior students, the visualization must explain the answer using Junior High School knowledge and language only.
    Do not rely on Senior High School concepts to explain a Junior High School problem.
14. For geometry visualizations involving motion, traces, loci, regions, or construction steps, fill `geometry_contract`.
    The contract must name the core objects, motion driver, moving object, intended path, sample values, invariants, and start/middle/end observations.
    This contract is binding for Stage 2; do not use vague motion such as "point moves around" without specifying the path and invariant.

## What to Analyze
From the provided math solution or explanation, determine:
- What is the core mathematical idea?
- What are the critical objects, relations, or transformations?
- What is difficult for a student to understand from text alone?
- What can be made visually obvious through a diagram, animation, parameter sweep, trace, region shading, construction steps, comparison, or measurement?
- What is the most stable and implementable GeoGebra-oriented way to visualize it?

## Output Format
Return valid JSON only.
Do not include markdown fences.
Do not include explanatory text outside JSON.

Follow the provided response schema exactly.

""" + contract_block + """

## Ambiguity Handling
If the source says something like \"distance to the circle\" or \"point moves around it\", do not leave it vague.
You must explicitly decide whether \"circle\" means the circumference/boundary or the filled disk/region, and whether the moving object is a point, a disk, a trace, or another construction.
If the source does not resolve this, mark it in the ambiguities field and choose the most conservative mathematically coherent interpretation.

## Selection Policy
Produce exactly 3 visualizations.
The three specs should cover distinct teaching bottlenecks or stages of the solution. Do not duplicate the same diagram with cosmetic changes.

## visualization_type selection guide
- `static_diagram`: a fixed diagram is enough; no continuous change is needed.
- `construction_steps`: the teaching value comes from revealing a construction order.
- `parametric_animation`: one or more parameters should drive a continuous change.
- `locus_trace`: the student should observe the path traced by a moving object.
- `region_shading`: a region, boundary, or inequality set must be visually distinguished.
- `comparison_overlay`: two or more cases should stay visible for comparison.
- `measurement_demo`: the key idea is an explicit measured quantity.
- `function_plot`: the core object is a function graph and its behavior.

## GeoGebra design rules
- Set `preferred_geogebra_app` to geometry, graphing, or classic based on the dominant object type.
- Fill `teaching_value` on every visualization: use exactly `high` or `medium`.
- Fill `geogebra_plan` honestly: prefer command_only designs and require script only when command-based construction is not stable enough.
- In `geogebra_plan`, the script flag field name is exactly `requires_minimal_script`; never output `requires_script`.
- `recommended_command_families` may use only geometry, transformation, list, logic, locus, scripting, conic, or function.
- Prefer slider-driven or step-driven interaction over fragile autoplay logic.
- Use `preferred_geogebra_object_naming_style` to reinforce short, stable ASCII object names.

## geometry_contract rules
- Include `geometry_contract` for `locus_trace`, `parametric_animation`, `region_shading`, `construction_steps`, `measurement_demo`, and any geometry visualization where a point or shape changes.
- `core_objects` should include only mathematically essential objects such as the moving point, target curve/shape, trace/locus, important segment, or measurement object.
- `motion.driver` must match a parameter or object name from the spec.
- `motion.moving_object` must match a `core_objects[].name`.
- `motion.path_type` must be one of none, line, segment, circle, circle_boundary, function_graph, locus, region_boundary, or free_parameter.
- If `motion.path_type` is not none, provide at least two numeric `sample_values`; prefer three values representing start/middle/end.
- Use `invariants` for observable facts that must remain true, such as on_curve, collinear, parallel, perpendicular, equal_distance, fixed_distance, ratio, midpoint, angle_equal, angle_measure, area_equal, tangent, symmetric_about, or transformed_from.
- `student_checkpoints` must say what a student should observe at start, middle, and end when motion exists.
- `must_not_change_meaning` should state common semantic mistakes, such as replacing a circle boundary with a filled disk or replacing a locus with an unrelated static point.

## priority and recommendation calibration
- `priority=1` means the most pedagogically valuable candidate in the bundle.
- `priority=2` means the next most important candidate that adds depth, contrast, or a problem-specific application.
- `priority=3` means the third key candidate that completes the teaching sequence.
- Recommend a candidate only when it is both instructionally strong and implementable with stable Stage 2 code.
- Exactly one visualization should have `recommended=true` unless every candidate has `overall_readiness="needs_revision"`.

## renderability_assessment calibration
- `clarity_score`: 90+ means the mathematical interpretation is explicit; 70-89 means minor ambiguity remains.
- `math_completeness_score`: 90+ means the necessary objects, constraints, and conclusion are all present.
- `implementation_stability_score`: 90+ means straightforward command-based GeoGebra; 70-89 means moderate engineering care is needed.
- `overall_readiness`: use `ready` only when the candidate is both mathematically explicit and implementation-stable; use `mostly_ready` for minor gaps; use `needs_revision` when the model still expects downstream guessing.

## parameter design rules
- For numeric parameters, use `range.min`, `range.max`, and `range.step` directly.
- `default_value` must already be a JSON number or boolean, never a symbolic string such as `pi/4`.
- Prefer a single slider or a small parameter set over decorative or redundant controls.
"""

    def user_message(self, **kwargs: Any) -> str:
        original_problem = kwargs.get("original_problem")
        answer_package = kwargs["answer_package"]
        teaching_preference = str(kwargs.get("teaching_preference") or "").strip()
        return (
            "## Original problem\n"
            + json.dumps(original_problem, indent=2, ensure_ascii=False)
            + "\n\n"
            + curriculum_boundary_block(original_problem, language="en")
            + "\n\n## Solved explanation / answer package\n"
            + json.dumps(answer_package, indent=2, ensure_ascii=False)
            + (
                "\n\n## Teaching preference\n" + teaching_preference
                if teaching_preference
                else ""
            )
            + "\n\nGenerate the JSON specification only. Produce exactly 3 visualizations."
        )
