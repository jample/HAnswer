"""HAVizNew Stage 2 prompt: VisualizationSpec -> GeoGebra visualization."""

from __future__ import annotations

import json
from typing import Any

from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import GEOGEBRA_EXECUTION_PAYLOAD_DRAFT_SCHEMA
from app.prompts.vizcoder_prompt import GGB_CHEATSHEET


class GeoGebraCodegenPrompt(PromptTemplate):

    version = PromptVersion(major=1, minor=0, date_updated="2026-04-22")
    name = "geogebra_codegen"

    purpose = (
        "将已选中的 VisualizationSpec 落地为一个 GeoGebra 可执行可持久化的 Visualization 对象。"
    )

    input_description = "spec (selected VisualizationSpec JSON)"

    output_description = (
        "一个符合 GeoGebraExecutionPayload Schema 的 JSON 对象，用于按阶段执行 GeoGebra 构造、属性设置与最小脚本。"
    )

    design_decisions = [
        DesignDecision(
            title="只消费单个已选规格",
            rationale=(
                "Stage 1 已经完成教学目标、展示结论与交互边界的规划。Stage 2 不能重选图，"
                "只能把当前规格稳定地翻译成 GeoGebra 命令。"
            ),
        ),
        DesignDecision(
            title="强制 GeoGebra-only 输出",
            rationale=(
                "当前迁移目标是把 HAVizNew 主流水线从 JSXGraph 切到 GeoGebra。"
                "Stage 2 不能再回退到 JSXGraph 代码。"
            ),
        ),
        DesignDecision(
            title="把教学意图优先于炫技效果",
            rationale=(
                "规格里已经明确 pedagogical_purpose、mathematical_claim_being_shown 与"
                "fallback_if_animation_is_too_complex。若动画太脆弱，应优先生成一个"
                "稳定的静态或滑块驱动构型。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return GEOGEBRA_EXECUTION_PAYLOAD_DRAFT_SCHEMA

    def fewshot_examples(self, **kwargs: Any) -> list[dict]:
        examples: list[dict] = []
        measurement_payload = {
            "title": "边界距离核心图",
            "preferred_geogebra_app": "geometry",
            "execution_mode": "command_only",
            "math_meaning_summary": "用圆、外点和距离线段说明距离落在圆边界上。",
            "object_naming_convention": "Use short English labels such as O, A, c, P, d.",
            "commands": [
                {"step": 1, "purpose": "[core] Create circle center", "command": "O=(0,0)"},
                {"step": 2, "purpose": "[core] Create boundary point", "command": "A=(3,0)"},
                {"step": 3, "purpose": "[core] Create circle boundary", "command": "c=Circle(O,A)"},
                {"step": 4, "purpose": "[core] Create measured point", "command": "P=(4,2)"},
                {"step": 5, "purpose": "[core] Create distance segment", "command": "d=Segment(P,A)"},
                {"step": 6, "purpose": "[annotation] Show distance text", "command": "txt=Text(\"距离看边界\",(1,-3))"},
            ],
            "property_commands": [
                {"step": 1, "purpose": "Style core circle", "command": "SetColor(c, 30, 120, 220)"},
                {"step": 2, "purpose": "Emphasize distance", "command": "SetLineThickness(d,4)"},
                {"step": 3, "purpose": "Show point labels", "command": "ShowLabel(P,true)"},
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
                {"name": "c", "type": "circle", "role": "core boundary"},
                {"name": "P", "type": "point", "role": "core measured point"},
                {"name": "d", "type": "segment", "role": "core distance segment"},
            ],
            "consistency_checks": ["The distance segment remains visible even if the annotation text fails."],
            "fallback_used": False,
            "fallback_reason": "",
            "implementation_notes": ["The first five commands form a complete core diagram."],
        }
        slider_payload = {
            "title": "参数变化核心图",
            "preferred_geogebra_app": "graphing",
            "execution_mode": "command_only",
            "math_meaning_summary": "用一个滑块控制二次函数开口并观察顶点。",
            "object_naming_convention": "Use short English labels such as a, f, V.",
            "commands": [
                {"step": 1, "purpose": "[core] Create coefficient slider", "command": "a=Slider(-3,3,0.5)"},
                {"step": 2, "purpose": "[core] Create function graph", "command": "f(x)=a*x^2"},
                {"step": 3, "purpose": "[core] Create vertex", "command": "V=Extremum(f)"},
            ],
            "property_commands": [
                {"step": 1, "purpose": "Emphasize vertex", "command": "SetPointSize(V,6)"},
                {"step": 2, "purpose": "Style graph", "command": "SetColor(f, 20, 130, 90)"},
            ],
            "interaction_objects": [
                {"name": "a", "type": "slider", "purpose": "Drive the quadratic coefficient"}
            ],
            "optional_script": {
                "needed": False,
                "script_type": "none",
                "reason": "",
                "target_object": "",
                "trigger": "none",
                "script_body": "",
            },
            "expected_created_objects": [
                {"name": "a", "type": "slider_parameter", "role": "core parameter"},
                {"name": "f", "type": "function_graph", "role": "core function"},
                {"name": "V", "type": "point", "role": "core vertex"},
            ],
            "consistency_checks": ["The vertex remains on the graph when a changes."],
            "fallback_used": False,
            "fallback_reason": "",
            "implementation_notes": ["Only one slider is used."],
        }
        intersection_payload = {
            "title": "交点直接命名",
            "preferred_geogebra_app": "geometry",
            "execution_mode": "command_only",
            "math_meaning_summary": "两个圆的交点直接命名，避免列表对象在 Apps API 中自动改名。",
            "object_naming_convention": "Use short English labels such as A, B, c1, c2, P1, P2.",
            "commands": [
                {"step": 1, "purpose": "[core] Create first center", "command": "A=(0,0)"},
                {"step": 2, "purpose": "[core] Create second center", "command": "B=(1,0)"},
                {"step": 3, "purpose": "[core] Create first circle", "command": "c1=Circle(A,1)"},
                {"step": 4, "purpose": "[core] Create second circle", "command": "c2=Circle(B,1)"},
                {"step": 5, "purpose": "[core] Create first intersection directly", "command": "P1=Intersect(c1,c2,1)"},
                {"step": 6, "purpose": "[core] Create second intersection directly", "command": "P2=Intersect(c1,c2,2)"},
                {"step": 7, "purpose": "[support] Measure intersection distance", "command": "k=Distance(P1,P2)"},
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
                {"name": "c1", "type": "circle", "role": "core first circle"},
                {"name": "c2", "type": "circle", "role": "core second circle"},
                {"name": "P1", "type": "point", "role": "core first intersection"},
                {"name": "P2", "type": "point", "role": "core second intersection"},
            ],
            "consistency_checks": ["Do not use pts=Intersect(c1,c2) followed by Element(pts,1)."],
            "fallback_used": False,
            "fallback_reason": "",
            "implementation_notes": ["Intersections are directly named."],
        }
        example_labels = (
            "Example: stable static geometry measurement payload.",
            "Example: stable one-slider graphing payload.",
            "Example: stable direct-intersection geometry payload.",
        )
        for label, payload in zip(example_labels, (measurement_payload, slider_payload, intersection_payload), strict=True):
            examples.append({"role": "user", "content": label})
            examples.append({
                "role": "assistant",
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
            })
        return examples

    def system_message(self, **kwargs: Any) -> str:
        return f"""\
You are generating exactly one GeoGebra execution payload from one selected VisualizationSpec.

Hard requirements:
- Output exactly one JSON object matching response_schema.
- title should stay close to spec.title.
- preferred_geogebra_app must align with spec.preferred_geogebra_app.
- execution_mode must agree with optional_script.
- All human-readable output text must be in Simplified Chinese.

GeoGebra rules:
- Separate object creation into commands[] and post-creation styling/visibility/trace work into property_commands[].
- property_commands[] must never create objects with a left-hand-side assignment such as X=...; put every object creation in commands[].
- commands[] entries must be ordered by increasing step and each command must be one complete GeoGebra command string.
- property_commands[] entries must be ordered by increasing step.
- Keep commands[] at or below 16 entries and property_commands[] at or below 16 entries.
- Prefer command_only execution. Do not use JavaScript or GGBScript in the default path.
- For command_only, optional_script must be exactly needed=false, script_type="none", trigger="none", reason="", target_object="", script_body="".
- Use command tiers in commands[].purpose. Prefix every creation command with exactly one of:
  - [core] for objects required for the main mathematical claim.
  - [support] for optional helpers, secondary cases, or extra measurements.
  - [annotation] for text and display-only objects.
- The first 5-8 commands must form a complete [core] diagram that can teach the claim even if later commands fail.
- [support] and [annotation] commands may depend on [core] objects; [core] commands must not depend on support/annotation objects.
- interaction_objects may only describe UI controls: slider, button, checkbox, input_box, or none. Never list GeoGebra points/segments/circles there.
- Each interaction_objects[] item must contain only name, type, and purpose. Do not include min, max, step, default, caption, or other UI range metadata there.
- Slider ranges/defaults belong inside the GeoGebra Slider(...) creation command, not in interaction_objects[].
- expected_created_objects must name only important [core] math objects the host should verify after command execution.
- Do not include text labels, captions, annotations, decorative display text such as k_text, or merely explanatory [support] numeric formulas in expected_created_objects.
- Include a [support] object in expected_created_objects only when the diagram becomes mathematically misleading without it, and make its role start with "support:".
- Do not hide mathematical meaning inside free-form notes; put executable work into commands/property_commands/optional_script.
- Prefer direct, stable constructions over fragile animation tricks.
- If the requested interaction is too complex, follow the spec fallback strategy with a simpler but mathematically faithful diagram.
- Avoid conditional object creation such as If(step >= n, Segment(...)).
- Avoid point-plus-vector shorthand such as A + v or Vector(B - A); use explicit coordinates or stable GeoGebra commands.
- To create a point offset from A, write C=(x(A)+dx, y(A)+dy), not C=A+(dx,dy).
- For circle/curve intersections, create named points directly with indexed Intersect commands, e.g. P1=Intersect(c1,c2,1) and P2=Intersect(c1,c2,2). Do not create an intersection list and then use Element(list, n).
- Avoid Abs(...); use lowercase abs(...). For support numeric measurements, prefer simple reusable assignments such as dTK=Distance(T,K), gap=2*sqrt(3), kmin=abs(dTK-gap), kmax=dTK+gap.
- Never put multiple GeoGebra commands in one command string; each commands[].command and property_commands[].command must be a single line.
- Do not use SetValue(...) in commands[] or property_commands[]; put initial slider values in Slider(...) instead.
- Do not use SetConditionToShowObject(...); keep important objects always visible and omit optional conditional visibility.
- If a numeric value is only explanatory, prefer a Text(...) annotation with the plain formula rather than making it a hard expected numeric object.

Geometry contract rules:
- If spec.geometry_contract exists, treat it as binding. Do not silently change the moving object, path, invariant, target shape, or student observation.
- Every geometry_contract.core_objects item with must_be_visible=true must be created by commands[] or included in expected_created_objects.
- If geometry_contract.motion.path_type is not "none", create the declared motion.driver as a stable slider parameter whenever possible.
- The declared geometry_contract.motion.moving_object must be created from the declared driver. For example, if driver is t and moving object is P, the command that creates P should reference t unless you explicitly use fallback_used=true with a faithful static fallback.
- If the spec requires trace/locus behavior, include SetTrace(...), Locus(...), or a clearly declared static fallback with representative positions.
- If the spec requires region shading, include a region/filling construction or a clearly declared boundary-only fallback if that preserves the mathematical claim.
- Do not replace circle-boundary motion with disk/region motion, or a locus with an unrelated fixed sample point.
- Put any semantic simplification in implementation_notes and set fallback_used/fallback_reason when it changes animation into static representative positions.

Reference cheatsheet:
{GGB_CHEATSHEET}
"""

    def user_message(self, **kwargs: Any) -> str:
        spec: dict[str, Any] = kwargs["spec"]
        return (
            "Below is the selected VisualizationSpec JSON.\n"
            "Turn it into exactly one GeoGebraExecutionPayload object.\n\n"
            + json.dumps(spec, ensure_ascii=False, indent=2)
            + "\n\nReminder:\n"
            + "- Keep the mathematics faithful to the spec.\n"
            + "- Prefer slider/checkbox interaction objects whose names match spec interaction parameters when possible.\n"
            + "- Put creation commands before property commands.\n"
            + "- Keep commands and styling minimal; nonessential visual polish should be omitted.\n"
            + "- If a static diagram is the more stable implementation, that is allowed if it still realizes the teaching goal.\n"
            + "- Output JSON only."
        )
