0. 整体流水线设计：
0.1 整体流水线流程：
Stage 1 spec JSON

    ↓

Stage 2 payload JSON

    ↓

read payload.commands[]

    ↓

for each command:

    ggbApplet.evalCommandGetLabels(command)

    log success/failure + created labels

    ↓

apply payload.property_commands[]

    ↓

if payload.optional_script.needed:

    attach minimal script

    ↓

optional animation / trace controls

0.2 最终提取GeoGebra内容模式：

最终可执行内容就应该从 Step 2 的 response 里取。
但不是从 response 里“随便找一段文本”，而是从 Stage 2 的结构化字段中提取。
最终的 GeoGebra commands / script 就从 Step 2 的 response 中提取；但必须从结构化字段提取，而不是从自由文本里猜。

最稳的做法是让 Stage 2 返回这种结构：

* preferred_geogebra_app
* execution_mode
* commands[]
* property_commands[]
* interaction_objects[]
* optional_script
* expected_created_objects[]

然后你的程序按顺序处理：

1). 从 commands[] 里取创建命令

这些是最核心的构造命令。
用 evalCommandGetLabels 或 evalCommand 逐条执行。GeoGebra 官方说明 evalCommand 支持多条命令用 \n 分隔，但从工程上你更适合逐条执行，因为这样更容易定位失败点。evalCommandGetLabels 还能返回实际创建的标签。 

2). 从 property_commands[] 里取样式/属性设置

这些可以是：

* 再次用 command 形式执行
* 或映射到 API 方法，例如 setColor、setVisible、setTrace、setLabelVisible 等。GeoGebra Apps API 提供了这些对象状态设置接口。 

3). 从 optional_script 里取脚本

只有当 optional_script.needed = true 时才使用。
而且这部分要非常克制，因为脚本复杂度更高；GeoGebra 支持 GGBScript 和 JavaScript，但你现在的最佳策略仍然应该是“能不用脚本就不用脚本”。GeoGebra 手册明确有 scripting 体系，Apps API 也提供了动画与对象状态控制接口。 


1. 1st Step prompt for GeoGebra — Generate a Complete GeoGebra Visualization Specification:

下面是 GeoGebra 版 Prompt 1 对应的内容。
目标是让第一阶段输出稳定、完整，并且能直接供第二阶段“生成 GeoGebra payload”使用。

我按这几个原则设计：

* 保留你前面两阶段链路需要的核心字段
* 强化 GeoGebra 相关字段
* 不做过重抽象
* 便于 model_validate_json(...)
* 便于后续筛选 recommended visualization
  
''' Text of 1st Step Prompt  Sample

You are a senior mathematical visualization designer for an educational software product for middle school students.

Your task is NOT to write GeoGebra commands or code.
Your task is to convert a math solution, explanation, or teaching content into one or two high-value GeoGebra-ready visualization specifications that are mathematically precise, instructionally useful, and implementation-ready.

The output of this step will later be used by another model to generate GeoGebra commands and, only if necessary, minimal GeoGebra scripting.
Therefore, your specification must be complete, explicit, and unambiguous enough for direct GeoGebra implementation.

## Primary Goal
Given a math problem solution or explanation, identify the 1-2 most pedagogically valuable visualization ideas, and produce a detailed visualization specification for each one.

The specification must make the mathematical meaning, GeoGebra object plan, parameters, constraints, animation logic, expected visual result, and teaching purpose fully explicit.

## Important Rules
1. Do NOT output GeoGebra commands, JavaScript, GGBScript, HTML, or pseudo-code.
2. Do NOT output vague design ideas such as "show this dynamically" without defining exactly what moves, what changes, and what students should observe.
3. Do NOT assume undefined objects, hidden constraints, or unstated mathematical facts.
4. If the source content is ambiguous, explicitly identify the ambiguity and propose the most conservative mathematically valid interpretation.
5. Prefer mathematically correct, teachable, and visually clear designs over flashy or overly complex animations.
6. The visualizations are for middle school math learning, so they should emphasize conceptual clarity, not visual decoration.
7. If animation is unnecessary, choose a static or lightly interactive visualization instead.
8. Every visualization must have a clear mathematical purpose and an observable conclusion.
9. The content must be self-contained so that a separate model can convert it into GeoGebra commands without needing to infer missing meaning.
10. If there are multiple possible visualizations, choose the ones with the highest teaching value and the clearest GeoGebra implementation path.
11. Prefer command-based GeoGebra constructions over script-heavy designs whenever possible.
12. Prefer slider-driven or step-driven interaction over fragile or overly elaborate animation logic.

## GeoGebra-Oriented Design Requirements
For each visualization, explicitly determine:
- Which GeoGebra app is most appropriate: geometry, graphing, or classic
- Which mathematical objects must be created
- Which objects are free objects, dependent objects, auxiliary objects, moving objects, traced objects, shaded regions, or labels
- Whether the visualization can be implemented using GeoGebra commands alone
- Whether it requires a slider, trace, locus, sequence, transformation, or region shading
- Whether minimal scripting is needed, and if so, why
- What the stable fallback is if the animation or dynamic effect is too complex

## What to Analyze
From the provided math solution or explanation, determine:
- What is the core mathematical idea?
- What are the critical objects, relations, or transformations?
- What is difficult for a student to understand from text alone?
- What can be made visually obvious through a diagram, animation, slider, trace, locus, region shading, construction steps, comparison, or measurement?
- What is the most stable and implementable GeoGebra-oriented way to visualize it?

## Output Format
Return valid JSON only.
Do not include markdown fences.
Do not include explanatory text outside JSON.

Use the following schema exactly:

{
  "task_summary": {
    "source_math_topic": "string",
    "source_problem_type": "string",
    "core_learning_goal": "string"
  },
  "visualizations": [
    {
      "id": "viz_1",
      "title": "string",
      "priority": 1,
      "teaching_value": "high | medium",
      "recommended": true,
      "visualization_type": "static_diagram | construction_steps | parametric_animation | locus_trace | region_shading | comparison_overlay | measurement_demo | function_plot",
      "preferred_geogebra_app": "geometry | graphing | classic",
      "pedagogical_purpose": "string",
      "when_to_use": "string",
      "mathematical_claim_being_shown": "string",
      "student_observation_goal": [
        "string"
      ],
      "source_dependency": {
        "depends_on_solution_steps": [
          "string"
        ],
        "depends_on_assumptions": [
          "string"
        ]
      },
      "math_definition": {
        "objects": [
          {
            "name": "string",
            "type": "point | line | segment | ray | circle | circle_boundary | polygon | function_graph | region | angle | label | slider_parameter | moving_point | traced_object | auxiliary_object | locus | list_object",
            "definition": "string",
            "role": "string",
            "must_exist_before_animation": true
          }
        ],
        "relations": [
          {
            "relation_type": "distance | intersection | perpendicular | parallel | equality | ratio | midpoint | collinear | on_curve | inside_region | boundary_of | symmetric_about | transformed_from | angle_measure | area_relation | function_relation | locus_condition",
            "description": "string"
          }
        ],
        "constraints": [
          {
            "name": "string",
            "expression_in_plain_math": "string",
            "meaning": "string"
          }
        ],
        "key_formulas": [
          {
            "formula": "string",
            "purpose": "string"
          }
        ]
      },
      "geogebra_plan": {
        "object_creation_strategy": "command_only | mostly_commands_with_minimal_script | requires_script",
        "recommended_command_families": [
          "geometry",
          "transformation",
          "list",
          "logic",
          "scripting",
          "conic",
          "function"
        ],
        "requires_slider": true,
        "requires_trace": true,
        "requires_locus": false,
        "requires_region_shading": true,
        "requires_sequence_or_list_generation": false,
        "requires_minimal_script": false,
        "script_reason_if_needed": "string"
      },
      "visual_design": {
        "coordinate_system": {
          "needed": true,
          "type": "cartesian_2d | geometry_plane",
          "suggested_viewport": {
            "xmin": "number",
            "xmax": "number",
            "ymin": "number",
            "ymax": "number"
          },
          "reason": "string"
        },
        "visible_objects": [
          "string"
        ],
        "highlighted_objects": [
          "string"
        ],
        "optional_hidden_helper_objects": [
          "string"
        ],
        "labels_to_show": [
          "string"
        ],
        "measurements_to_show": [
          "string"
        ],
        "region_or_trace_display": {
          "needed": true,
          "type": "none | trace | shaded_region | moving_overlay | boundary_only | stepwise_reveal",
          "description": "string"
        }
      },
      "interaction_and_animation": {
        "has_animation": true,
        "animation_driver": "none | slider | moving_point | parameter_t | step_index",
        "animation_description": "string",
        "parameters": [
          {
            "name": "string",
            "type": "number | angle | integer_step | boolean",
            "range_or_values": "string",
            "default_value": "string",
            "meaning": "string"
          }
        ],
        "user_interactions": [
          {
            "interaction_type": "drag | play_pause | step_forward | step_backward | toggle_visibility | move_slider",
            "target": "string",
            "purpose": "string"
          }
        ],
        "animation_sequence": [
          "string"
        ],
        "stopping_condition_or_final_state": "string"
      },
      "expected_result": {
        "final_visual_outcome": "string",
        "mathematical_conclusion_visible_to_student": "string",
        "common_misinterpretations_to_avoid": [
          "string"
        ]
      },
      "implementation_guidance": {
        "preferred_rendering_strategy": "string",
        "preferred_geogebra_object_naming_style": "Use short English labels such as A, B, C, O, P, t, r, c1, c2, f, g",
        "simplifications_allowed": [
          "string"
        ],
        "things_that_must_not_be_omitted": [
          "string"
        ],
        "things_that_must_not_be_invented": [
          "string"
        ],
        "fallback_if_animation_is_too_complex": "string"
      },
      "consistency_checks": [
        "string"
      ],
      "ambiguities": [
        {
          "issue": "string",
          "impact": "low | medium | high",
          "preferred_resolution": "string"
        }
      ],
      "renderability_assessment": {
        "clarity_score": 0,
        "math_completeness_score": 0,
        "implementation_stability_score": 0,
        "overall_readiness": "ready | mostly_ready | needs_revision"
      }
    }
  ]
}

## Scoring Guidance
For the three scores in renderability_assessment, use integers from 0 to 100.

## Quality Requirements
A good output must:
- define exactly what is being visualized
- define the mathematical objects explicitly
- identify all important parameters and ranges
- specify whether a reference object is a boundary, region, graph, segment, or locus
- state what the student should observe
- state the final mathematical conclusion the visualization should reveal
- avoid implementation-sensitive ambiguity
- be directly usable by a second model for GeoGebra command generation

## Ambiguity Handling
If the source says something like "distance to the circle" or "point moves around it", do not leave it vague.
You must explicitly decide whether "circle" means the circumference/boundary or the filled disk/region, and whether the moving object is a point, a disk, a trace, or another construction.
If the source does not resolve this, mark it in the ambiguities field and choose the most conservative mathematically coherent interpretation.

## GeoGebra Suitability Policy
Prefer visualizations that map cleanly to standard GeoGebra objects and commands.
If a visualization would require excessive scripting or brittle procedural logic, choose a simpler but mathematically faithful design.

## Selection Policy
Produce at most 2 visualizations.
Choose fewer if only one visualization is truly valuable and stable.

## Input
You will receive:
- the original problem if available
- the solved answer or explanation
- possibly additional teaching context

Generate the JSON specification only.

''' Text of 1st Step Prompt  Sample

1. 2nd Step prompt for GeoGebra — Convert GeoGebra Visualization Specification into Executable GeoGebra Output：

这个提示词的目标是：

把 Prompt 1 生成的 specification，转换为适合 GeoGebra Engine 执行的内容。

这里我建议你让第二阶段输出 结构化 GeoGebra payload，而不是单纯一大段命令文本。因为这样更利于你程序执行、调试和回退。

GeoGebra 官方文档显示 commands 用于创建和修改对象；脚本是顺序执行的；还支持 scripting commands 分类。 

''' Text of 2nd Step Prompt  Sample
You are a senior mathematical visualization designer for an educational software product for middle school students.

Your task is NOT to write GeoGebra commands or code.
Your task is to convert a math solution, explanation, or teaching content into one or two high-value GeoGebra-ready visualization specifications that are mathematically precise, instructionally useful, and implementation-ready.

The output of this step will later be used by another model to generate GeoGebra commands and, only if necessary, minimal GeoGebra scripting.
Therefore, your specification must be complete, explicit, and unambiguous enough for direct GeoGebra implementation.

## Primary Goal
Given a math problem solution or explanation, identify the 1-2 most pedagogically valuable visualization ideas, and produce a detailed visualization specification for each one.

The specification must make the mathematical meaning, GeoGebra object plan, parameters, constraints, animation logic, expected visual result, and teaching purpose fully explicit.

## Important Rules
1. Do NOT output GeoGebra commands, JavaScript, GGBScript, HTML, or pseudo-code.
2. Do NOT output vague design ideas such as "show this dynamically" without defining exactly what moves, what changes, and what students should observe.
3. Do NOT assume undefined objects, hidden constraints, or unstated mathematical facts.
4. If the source content is ambiguous, explicitly identify the ambiguity and propose the most conservative mathematically valid interpretation.
5. Prefer mathematically correct, teachable, and visually clear designs over flashy or overly complex animations.
6. The visualizations are for middle school math learning, so they should emphasize conceptual clarity, not visual decoration.
7. If animation is unnecessary, choose a static or lightly interactive visualization instead.
8. Every visualization must have a clear mathematical purpose and an observable conclusion.
9. The content must be self-contained so that a separate model can convert it into GeoGebra commands without needing to infer missing meaning.
10. If there are multiple possible visualizations, choose the ones with the highest teaching value and the clearest GeoGebra implementation path.
11. Prefer command-based GeoGebra constructions over script-heavy designs whenever possible.
12. Prefer slider-driven or step-driven interaction over fragile or overly elaborate animation logic.

## GeoGebra-Oriented Design Requirements
For each visualization, explicitly determine:
- Which GeoGebra app is most appropriate: geometry, graphing, or classic
- Which mathematical objects must be created
- Which objects are free objects, dependent objects, auxiliary objects, moving objects, traced objects, shaded regions, or labels
- Whether the visualization can be implemented using GeoGebra commands alone
- Whether it requires a slider, trace, locus, sequence, transformation, or region shading
- Whether minimal scripting is needed, and if so, why
- What the stable fallback is if the animation or dynamic effect is too complex

## What to Analyze
From the provided math solution or explanation, determine:
- What is the core mathematical idea?
- What are the critical objects, relations, or transformations?
- What is difficult for a student to understand from text alone?
- What can be made visually obvious through a diagram, animation, slider, trace, locus, region shading, construction steps, comparison, or measurement?
- What is the most stable and implementable GeoGebra-oriented way to visualize it?

## Output Format
Return valid JSON only.
Do not include markdown fences.
Do not include explanatory text outside JSON.

Use the following schema exactly:

{
  "task_summary": {
    "source_math_topic": "string",
    "source_problem_type": "string",
    "core_learning_goal": "string"
  },
  "visualizations": [
    {
      "id": "viz_1",
      "title": "string",
      "priority": 1,
      "teaching_value": "high | medium",
      "recommended": true,
      "visualization_type": "static_diagram | construction_steps | parametric_animation | locus_trace | region_shading | comparison_overlay | measurement_demo | function_plot",
      "preferred_geogebra_app": "geometry | graphing | classic",
      "pedagogical_purpose": "string",
      "when_to_use": "string",
      "mathematical_claim_being_shown": "string",
      "student_observation_goal": [
        "string"
      ],
      "source_dependency": {
        "depends_on_solution_steps": [
          "string"
        ],
        "depends_on_assumptions": [
          "string"
        ]
      },
      "math_definition": {
        "objects": [
          {
            "name": "string",
            "type": "point | line | segment | ray | circle | circle_boundary | polygon | function_graph | region | angle | label | slider_parameter | moving_point | traced_object | auxiliary_object | locus | list_object",
            "definition": "string",
            "role": "string",
            "must_exist_before_animation": true
          }
        ],
        "relations": [
          {
            "relation_type": "distance | intersection | perpendicular | parallel | equality | ratio | midpoint | collinear | on_curve | inside_region | boundary_of | symmetric_about | transformed_from | angle_measure | area_relation | function_relation | locus_condition",
            "description": "string"
          }
        ],
        "constraints": [
          {
            "name": "string",
            "expression_in_plain_math": "string",
            "meaning": "string"
          }
        ],
        "key_formulas": [
          {
            "formula": "string",
            "purpose": "string"
          }
        ]
      },
      "geogebra_plan": {
        "object_creation_strategy": "command_only | mostly_commands_with_minimal_script | requires_script",
        "recommended_command_families": [
          "geometry",
          "transformation",
          "list",
          "logic",
          "scripting",
          "conic",
          "function"
        ],
        "requires_slider": true,
        "requires_trace": true,
        "requires_locus": false,
        "requires_region_shading": true,
        "requires_sequence_or_list_generation": false,
        "requires_minimal_script": false,
        "script_reason_if_needed": "string"
      },
      "visual_design": {
        "coordinate_system": {
          "needed": true,
          "type": "cartesian_2d | geometry_plane",
          "suggested_viewport": {
            "xmin": "number",
            "xmax": "number",
            "ymin": "number",
            "ymax": "number"
          },
          "reason": "string"
        },
        "visible_objects": [
          "string"
        ],
        "highlighted_objects": [
          "string"
        ],
        "optional_hidden_helper_objects": [
          "string"
        ],
        "labels_to_show": [
          "string"
        ],
        "measurements_to_show": [
          "string"
        ],
        "region_or_trace_display": {
          "needed": true,
          "type": "none | trace | shaded_region | moving_overlay | boundary_only | stepwise_reveal",
          "description": "string"
        }
      },
      "interaction_and_animation": {
        "has_animation": true,
        "animation_driver": "none | slider | moving_point | parameter_t | step_index",
        "animation_description": "string",
        "parameters": [
          {
            "name": "string",
            "type": "number | angle | integer_step | boolean",
            "range_or_values": "string",
            "default_value": "string",
            "meaning": "string"
          }
        ],
        "user_interactions": [
          {
            "interaction_type": "drag | play_pause | step_forward | step_backward | toggle_visibility | move_slider",
            "target": "string",
            "purpose": "string"
          }
        ],
        "animation_sequence": [
          "string"
        ],
        "stopping_condition_or_final_state": "string"
      },
      "expected_result": {
        "final_visual_outcome": "string",
        "mathematical_conclusion_visible_to_student": "string",
        "common_misinterpretations_to_avoid": [
          "string"
        ]
      },
      "implementation_guidance": {
        "preferred_rendering_strategy": "string",
        "preferred_geogebra_object_naming_style": "Use short English labels such as A, B, C, O, P, t, r, c1, c2, f, g",
        "simplifications_allowed": [
          "string"
        ],
        "things_that_must_not_be_omitted": [
          "string"
        ],
        "things_that_must_not_be_invented": [
          "string"
        ],
        "fallback_if_animation_is_too_complex": "string"
      },
      "consistency_checks": [
        "string"
      ],
      "ambiguities": [
        {
          "issue": "string",
          "impact": "low | medium | high",
          "preferred_resolution": "string"
        }
      ],
      "renderability_assessment": {
        "clarity_score": 0,
        "math_completeness_score": 0,
        "implementation_stability_score": 0,
        "overall_readiness": "ready | mostly_ready | needs_revision"
      }
    }
  ]
}

## Scoring Guidance
For the three scores in renderability_assessment, use integers from 0 to 100.

## Quality Requirements
A good output must:
- define exactly what is being visualized
- define the mathematical objects explicitly
- identify all important parameters and ranges
- specify whether a reference object is a boundary, region, graph, segment, or locus
- state what the student should observe
- state the final mathematical conclusion the visualization should reveal
- avoid implementation-sensitive ambiguity
- be directly usable by a second model for GeoGebra command generation

## Ambiguity Handling
If the source says something like "distance to the circle" or "point moves around it", do not leave it vague.
You must explicitly decide whether "circle" means the circumference/boundary or the filled disk/region, and whether the moving object is a point, a disk, a trace, or another construction.
If the source does not resolve this, mark it in the ambiguities field and choose the most conservative mathematically coherent interpretation.

## GeoGebra Suitability Policy
Prefer visualizations that map cleanly to standard GeoGebra objects and commands.
If a visualization would require excessive scripting or brittle procedural logic, choose a simpler but mathematically faithful design.

## Selection Policy
Produce at most 2 visualizations.
Choose fewer if only one visualization is truly valuable and stable.

## Input
You will receive:
- the original problem if available
- the solved answer or explanation
- possibly additional teaching context

Generate the JSON specification only.

''' Text of 2nd Step Prompt  Sample


3. GeoGebra 版 Prompt 1 对应的 Pydantic schema

目标是让第一阶段输出稳定、完整，并且能直接供第二阶段“生成 GeoGebra payload”使用

''' Python Code Sample 

from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field, conint, model_validator


# ----------------------------
# Top-level summary
# ----------------------------

class TaskSummary(BaseModel):
    source_math_topic: str = Field(..., description="Main topic, e.g. geometry, coordinate geometry, function")
    source_problem_type: str = Field(..., description="Problem type, e.g. distance set, dynamic geometry, similarity proof")
    core_learning_goal: str = Field(..., description="Main student learning objective")


class SourceDependency(BaseModel):
    depends_on_solution_steps: List[str] = Field(default_factory=list)
    depends_on_assumptions: List[str] = Field(default_factory=list)


# ----------------------------
# Math definition
# ----------------------------

class MathObjectSpec(BaseModel):
    name: str = Field(..., description="Short stable object name, e.g. A, B, O, P, t, c1")
    type: Literal[
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
    definition: str = Field(..., description="Plain mathematical definition")
    role: str = Field(..., description="Why this object exists in the visualization")
    must_exist_before_animation: bool = True


class RelationSpec(BaseModel):
    relation_type: Literal[
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
    description: str


class ConstraintSpec(BaseModel):
    name: str
    expression_in_plain_math: str
    meaning: str


class FormulaSpec(BaseModel):
    formula: str
    purpose: str


class MathDefinitionSpec(BaseModel):
    objects: List[MathObjectSpec] = Field(default_factory=list)
    relations: List[RelationSpec] = Field(default_factory=list)
    constraints: List[ConstraintSpec] = Field(default_factory=list)
    key_formulas: List[FormulaSpec] = Field(default_factory=list)


# ----------------------------
# GeoGebra-oriented plan
# ----------------------------

class GeoGebraPlan(BaseModel):
    object_creation_strategy: Literal[
        "command_only",
        "mostly_commands_with_minimal_script",
        "requires_script",
    ]
    recommended_command_families: List[Literal[
        "geometry",
        "transformation",
        "list",
        "logic",
        "scripting",
        "conic",
        "function",
    ]] = Field(default_factory=list)

    requires_slider: bool = False
    requires_trace: bool = False
    requires_locus: bool = False
    requires_region_shading: bool = False
    requires_sequence_or_list_generation: bool = False
    requires_minimal_script: bool = False
    script_reason_if_needed: str = ""


# ----------------------------
# Visual design
# ----------------------------

class SuggestedViewport(BaseModel):
    xmin: float
    xmax: float
    ymin: float
    ymax: float

    @model_validator(mode="after")
    def validate_bounds(self) -> "SuggestedViewport":
        if self.xmin >= self.xmax:
            raise ValueError("xmin must be less than xmax")
        if self.ymin >= self.ymax:
            raise ValueError("ymin must be less than ymax")
        return self


class CoordinateSystemSpec(BaseModel):
    needed: bool
    type: Literal["cartesian_2d", "geometry_plane"]
    suggested_viewport: SuggestedViewport
    reason: str


class RegionOrTraceDisplaySpec(BaseModel):
    needed: bool
    type: Literal[
        "none",
        "trace",
        "shaded_region",
        "moving_overlay",
        "boundary_only",
        "stepwise_reveal",
    ]
    description: str


class VisualDesignSpec(BaseModel):
    coordinate_system: CoordinateSystemSpec
    visible_objects: List[str] = Field(default_factory=list)
    highlighted_objects: List[str] = Field(default_factory=list)
    optional_hidden_helper_objects: List[str] = Field(default_factory=list)
    labels_to_show: List[str] = Field(default_factory=list)
    measurements_to_show: List[str] = Field(default_factory=list)
    region_or_trace_display: RegionOrTraceDisplaySpec


# ----------------------------
# Interaction / animation
# ----------------------------

class ParameterSpec(BaseModel):
    name: str
    type: Literal["number", "angle", "integer_step", "boolean"]
    range_or_values: str
    default_value: str
    meaning: str


class UserInteractionSpec(BaseModel):
    interaction_type: Literal[
        "drag",
        "play_pause",
        "step_forward",
        "step_backward",
        "toggle_visibility",
        "move_slider",
    ]
    target: str
    purpose: str


class InteractionAndAnimationSpec(BaseModel):
    has_animation: bool
    animation_driver: Literal["none", "slider", "moving_point", "parameter_t", "step_index"]
    animation_description: str
    parameters: List[ParameterSpec] = Field(default_factory=list)
    user_interactions: List[UserInteractionSpec] = Field(default_factory=list)
    animation_sequence: List[str] = Field(default_factory=list)
    stopping_condition_or_final_state: str

    @model_validator(mode="after")
    def validate_animation_consistency(self) -> "InteractionAndAnimationSpec":
        if self.has_animation:
            if self.animation_driver == "none":
                raise ValueError("animation_driver cannot be 'none' when has_animation is True")
            if not self.animation_sequence:
                raise ValueError("animation_sequence is required when has_animation is True")
        else:
            if self.animation_driver != "none":
                raise ValueError("animation_driver must be 'none' when has_animation is False")
        return self


# ----------------------------
# Expected result / implementation
# ----------------------------

class ExpectedResultSpec(BaseModel):
    final_visual_outcome: str
    mathematical_conclusion_visible_to_student: str
    common_misinterpretations_to_avoid: List[str] = Field(default_factory=list)


class ImplementationGuidanceSpec(BaseModel):
    preferred_rendering_strategy: str
    preferred_geogebra_object_naming_style: str = (
        "Use short English labels such as A, B, C, O, P, t, r, c1, c2, f, g"
    )
    simplifications_allowed: List[str] = Field(default_factory=list)
    things_that_must_not_be_omitted: List[str] = Field(default_factory=list)
    things_that_must_not_be_invented: List[str] = Field(default_factory=list)
    fallback_if_animation_is_too_complex: str


class AmbiguitySpec(BaseModel):
    issue: str
    impact: Literal["low", "medium", "high"]
    preferred_resolution: str


class RenderabilityAssessment(BaseModel):
    clarity_score: conint(ge=0, le=100)
    math_completeness_score: conint(ge=0, le=100)
    implementation_stability_score: conint(ge=0, le=100)
    overall_readiness: Literal["ready", "mostly_ready", "needs_revision"]


# ----------------------------
# VisualizationSpec
# ----------------------------

class VisualizationSpec(BaseModel):
    id: str
    title: str
    priority: int
    teaching_value: Literal["high", "medium"]
    recommended: bool

    visualization_type: Literal[
        "static_diagram",
        "construction_steps",
        "parametric_animation",
        "locus_trace",
        "region_shading",
        "comparison_overlay",
        "measurement_demo",
        "function_plot",
    ]

    preferred_geogebra_app: Literal["geometry", "graphing", "classic"]

    pedagogical_purpose: str
    when_to_use: str
    mathematical_claim_being_shown: str
    student_observation_goal: List[str] = Field(default_factory=list)

    source_dependency: SourceDependency
    math_definition: MathDefinitionSpec
    geogebra_plan: GeoGebraPlan
    visual_design: VisualDesignSpec
    interaction_and_animation: InteractionAndAnimationSpec
    expected_result: ExpectedResultSpec
    implementation_guidance: ImplementationGuidanceSpec
    consistency_checks: List[str] = Field(default_factory=list)
    ambiguities: List[AmbiguitySpec] = Field(default_factory=list)
    renderability_assessment: RenderabilityAssessment

    @model_validator(mode="after")
    def validate_geogebra_consistency(self) -> "VisualizationSpec":
        # Script requirement consistency
        if self.geogebra_plan.requires_minimal_script and not self.geogebra_plan.script_reason_if_needed.strip():
            raise ValueError("script_reason_if_needed is required when requires_minimal_script is True")

        if (
            self.geogebra_plan.object_creation_strategy == "command_only"
            and self.geogebra_plan.requires_minimal_script
        ):
            raise ValueError("command_only strategy cannot require minimal script")

        # Locus / trace / region hints should broadly agree with visual display or animation intent
        display_type = self.visual_design.region_or_trace_display.type

        if self.geogebra_plan.requires_trace and display_type not in {"trace", "moving_overlay", "boundary_only", "stepwise_reveal"}:
            raise ValueError("requires_trace is inconsistent with region_or_trace_display.type")

        if self.geogebra_plan.requires_region_shading and display_type not in {"shaded_region", "moving_overlay", "boundary_only"}:
            raise ValueError("requires_region_shading is inconsistent with region_or_trace_display.type")

        # Animation consistency
        if self.geogebra_plan.requires_slider:
            has_slider_param = any(obj.type == "slider_parameter" for obj in self.math_definition.objects) or any(
                p.type in {"number", "angle", "integer_step", "boolean"}
                for p in self.interaction_and_animation.parameters
            )
            if not has_slider_param:
                raise ValueError("requires_slider=True but no slider-like parameter/object is defined")

        return self


# ----------------------------
# Bundle
# ----------------------------

class VisualizationSpecBundle(BaseModel):
    task_summary: TaskSummary
    visualizations: List[VisualizationSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_bundle(self) -> "VisualizationSpecBundle":
        if not self.visualizations:
            raise ValueError("At least one visualization is required")

        recommended_count = sum(1 for v in self.visualizations if v.recommended)
        if recommended_count == 0:
            raise ValueError("At least one visualization must have recommended=True")

        if recommended_count > 1:
            # Allowed if you want, but for most pipelines exactly one recommended result is easier.
            # Change this to `pass` if you want multiple recommendations.
            raise ValueError("Only one visualization should have recommended=True")

        return self

''' Python Code Sample

4.GeoGebra 版 Prompt 2 schema。
它的设计目标是：Stage 2 输出可直接被程序消费，不需要再做文本分析。

‘’‘ Python Code Sample

from __future__ import annotations

from typing import List, Literal
from pydantic import BaseModel, Field, model_validator


class GeoGebraCommandStep(BaseModel):
    step: int
    purpose: str
    command: str


class GeoGebraPropertyCommandStep(BaseModel):
    step: int
    purpose: str
    command: str


class InteractionObjectSpec(BaseModel):
    name: str
    type: Literal["slider", "button", "checkbox", "input_box", "none"]
    purpose: str


class OptionalScriptSpec(BaseModel):
    needed: bool
    script_type: Literal["none", "ggbscript", "javascript"]
    reason: str
    target_object: str
    trigger: Literal["none", "on_click", "on_update", "on_drag_end", "on_load"]
    script_body: str

    @model_validator(mode="after")
    def validate_script_consistency(self) -> "OptionalScriptSpec":
        if not self.needed:
            if self.script_type != "none":
                raise ValueError("script_type must be 'none' when needed is False")
        else:
            if self.script_type == "none":
                raise ValueError("script_type cannot be 'none' when needed is True")
            if not self.reason.strip():
                raise ValueError("reason is required when script is needed")
        return self


class ExpectedCreatedObjectSpec(BaseModel):
    name: str
    type: str
    role: str


class GeoGebraExecutionPayload(BaseModel):
    title: str
    preferred_geogebra_app: Literal["geometry", "graphing", "classic"]
    execution_mode: Literal[
        "command_only",
        "commands_plus_minimal_ggbscript",
        "commands_plus_minimal_javascript",
    ]
    math_meaning_summary: str
    object_naming_convention: str

    commands: List[GeoGebraCommandStep] = Field(default_factory=list)
    property_commands: List[GeoGebraPropertyCommandStep] = Field(default_factory=list)
    interaction_objects: List[InteractionObjectSpec] = Field(default_factory=list)
    optional_script: OptionalScriptSpec
    expected_created_objects: List[ExpectedCreatedObjectSpec] = Field(default_factory=list)

    consistency_checks: List[str] = Field(default_factory=list)
    fallback_used: bool
    fallback_reason: str
    implementation_notes: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_payload(self) -> "GeoGebraExecutionPayload":
        if not self.commands:
            raise ValueError("At least one creation command is required")

        # Ensure step numbers are increasing
        command_steps = [c.step for c in self.commands]
        if command_steps != sorted(command_steps):
            raise ValueError("commands must be ordered by step")

        property_steps = [c.step for c in self.property_commands]
        if property_steps and property_steps != sorted(property_steps):
            raise ValueError("property_commands must be ordered by step")

        # execution_mode consistency
        if self.execution_mode == "command_only" and self.optional_script.needed:
            raise ValueError("command_only execution_mode cannot include optional_script")

        if self.execution_mode != "command_only" and not self.optional_script.needed:
            raise ValueError("script-based execution_mode requires optional_script.needed=True")

        if not self.fallback_used and self.fallback_reason.strip():
            raise ValueError("fallback_reason should be empty when fallback_used is False")

        if self.fallback_used and not self.fallback_reason.strip():
            raise ValueError("fallback_reason is required when fallback_used is True")

        return self

‘’‘ python code sample