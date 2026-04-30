"""Per-item visualization codegen prompt for storyboard-driven generation."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.prompts._audience import curriculum_boundary_block
from app.prompts._viz_compact import (
    compact_answer_for_item,
    compact_previous_items,
    compact_question,
    compact_storyboard_root,
)
from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import VISUALIZATION_SCHEMA
from app.prompts.vizcoder_prompt import (
    ALLOWED_GLOBALS,
    FORBIDDEN_GLOBALS,
    GGB_CHEATSHEET,
    H_CHEATSHEET,
)


def _preferred_engine(kwargs: dict[str, Any]) -> str:
    raw = str(kwargs.get("preferred_engine") or settings.viz.default_engine).strip().lower()
    if raw in {"jsxgraph", "geogebra"}:
        return raw
    return "geogebra"


def _resolved_engine(kwargs: dict[str, Any]) -> str:
    storyboard_item = kwargs.get("storyboard_item") or {}
    item_engine = str(storyboard_item.get("engine") or "").strip().lower()
    if item_engine in {"jsxgraph", "geogebra"}:
        return item_engine
    return _preferred_engine(kwargs)


class VizItemPrompt(PromptTemplate):

    version = PromptVersion(major=1, minor=2, date_updated="2026-04-22")
    name = "vizitem"

    purpose = (
        "基于已经确定的 VisualizationStoryboardItem, 只生成一个可落地的可视化对象。"
    )

    input_description = (
        "parsed_question (ParsedQuestion JSON), answer_package (AnswerPackage JSON), "
        "storyboard (VisualizationStoryboard JSON), storyboard_item (single item JSON)。"
    )

    output_description = (
        "符合 Visualization Schema 的单个 JSON 对象; 默认优先 GeoGebra, 仅在"
        " storyboard_item 明确要求或确实更合适时才使用 JSXGraph。"
    )

    design_decisions = [
        DesignDecision(
            title="一次只生成一个 visualization",
            rationale=(
                "把 2 张图拆成独立调用后, 单次输出更短、更稳定, 某一张失败时也不会"
                "拖垮整组可视化。"
            ),
        ),
        DesignDecision(
            title="必须服从 storyboard, 不重新选题",
            rationale=(
                "planner 已经决定了真正要解释的 bottleneck。per-item codegen 不能"
                "再次改写教学重点, 只能把当前 item 落实成可运行的图。"
            ),
        ),
        DesignDecision(
            title="默认 GeoGebra-first",
            rationale=(
                "当前目标是让 plan-first 架构先在 GeoGebra 上稳定落地。只有在当前"
                " item 明确更适合 JSXGraph 时, 才允许切换引擎。"
            ),
        ),
        DesignDecision(
            title="共享符号和共享参数必须复用",
            rationale=(
                "storyboard 的价值在于 2 张图读起来像一个教学故事。符号和参数漂移"
                "会直接破坏这种连贯性。"
            ),
        ),
        DesignDecision(
            title="压缩上下文, 降低 token 与故障率",
            rationale=(
                "实际日志显示单次 vizitem 调用约 13K 输入 token, 其中绝大部分是与本"
                "图无关的 AnswerPackage 子段 (similar_questions / key_points_of_answer / "
                "self_check) 与重复的 JSON Schema (Schema 已经通过 response_schema 通"
                "道传给 Gemini)。这里只保留与当前 storyboard_item 真正相关的题面、"
                "锚定步骤、共享符号/参数, 并按 engine 选择 cheatsheet, 把单次输入压"
                "到约 3K-4K token。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return VISUALIZATION_SCHEMA

    def fewshot_examples(self, **kwargs: Any) -> list[dict]:
        example_user = {
            "storyboard_item": {
                "id": "viz-example-1",
                "title_cn": "交点建立",
                "learning_goal_cn": "看清交点与函数图像的对应关系",
                "engine": "geogebra",
                "shared_symbols": ["A", "B"],
                "shared_params": ["t"],
            }
        }
        example_output = {
            "id": "viz-example-1",
            "title_cn": "交点建立",
            "caption_cn": "突出抛物线与 x 轴交点 A、B 的位置。",
            "learning_goal": "看清交点与函数图像的对应关系",
            "interactive_hints": ["拖动参数观察交点变化"],
            "helpers_used": [],
            "engine": "geogebra",
            "jsx_code": "",
            "ggb_commands": [
                "t=Slider(-2,2,0.1)",
                "f(x)=x^2-2*x+t",
                "A=Intersect(f,xAxis,1)",
                "B=Intersect(f,xAxis,2)"
            ],
            "ggb_settings": {"app_name": "graphing", "grid_visible": True, "axes_visible": True},
            "params": [{"name": "t", "label_cn": "参数 t", "kind": "slider", "min": -2, "max": 2, "step": 0.1, "default": 0}],
            "animation": None,
        }
        return [
            {
                "role": "user",
                "content": "Example storyboard item\n" + json.dumps(example_user, ensure_ascii=False, indent=2),
            },
            {"role": "assistant", "content": json.dumps(example_output, ensure_ascii=False, indent=2)},
        ]

    def system_message(self, **kwargs: Any) -> str:
        engine = _resolved_engine(kwargs)
        allow = ", ".join(ALLOWED_GLOBALS)
        forbid = ", ".join(FORBIDDEN_GLOBALS)
        engine_policy = (
            'This item should default to engine="geogebra". Do not switch unless storyboard_item explicitly requires jsxgraph.'
            if engine != "jsxgraph"
            else 'This storyboard item explicitly requires engine="jsxgraph".'
        )
        engine_preference = (
            "The overall rendering preference is GeoGebra-first."
            if engine != "jsxgraph"
            else "This item uses JSXGraph. Generate JSXGraph code only."
        )
        if engine == "geogebra":
            engine_block = (
                "## GeoGebra requirements\n"
                "- One command per line; do not write a ggbApplet prefix.\n"
                "- Put view settings in ggb_settings, not in ggb_commands.\n"
                "- Do not output wrappers such as SetValue(...), SetConditionToShowObject(...), or Line(ax+by=c).\n"
                "- If item.shared_params is non-empty, reuse same-name sliders / toggles whenever possible.\n"
                "- jsx_code must be an empty string.\n\n"
                f"## GeoGebra cheatsheet\n{GGB_CHEATSHEET}"
            )
        else:
            engine_block = (
                "## JSXGraph requirements\n"
                "- jsx_code must contain only the function body itself.\n"
                f"- Allowed globals only: {allow}\n"
                f"- Forbidden: {forbid}\n"
                "- ggb_commands must be an empty array.\n\n"
                f"## JSXGraph cheatsheet\n{H_CHEATSHEET}"
            )
        # Note: JSON output schema is enforced via the Gemini API's
        # `response_json_schema` channel, so we do NOT duplicate the full
        # schema in the system prompt (that was ~1.5K wasted tokens).
        return f"""\
    You are a math / physics visualization code generator for middle-school and high-school lessons. The storyboard has already been decided. Your only remaining task is to turn **one** storyboard item into **one** runnable visualization JSON object.

    ## Hard constraints
    - Output exactly one Visualization JSON object. Do not output a visualizations array.
    - `id` must match storyboard_item.id exactly.
    - Cover only the current storyboard_item. Do not mix other items into the same visualization.
    - `learning_goal` must directly serve storyboard_item.learning_goal_cn.
    - `caption_cn` must realize storyboard_item.caption_outline_cn.
    - Reuse shared symbols and shared parameters from the storyboard root. Do not rename them arbitrarily.
        - Respect parsed_question.subject and parsed_question.grade_band as a hard curriculum boundary.
            If grade_band is junior, the figure and explanations must stay within Junior High School knowledge and wording.
            Do not explain a junior problem with Senior High School concepts.

    ## Engine policy
- {engine_policy}
    - {engine_preference}

{engine_block}

    ## Output rules
    - Output exactly one JSON object whose field names / types / enums strictly match response_schema.
    - All human-readable text fields in the output must be in Simplified Chinese.
    - Do not add any explanation outside the JSON.
"""

    def user_message(self, **kwargs: Any) -> str:
        parsed_question: dict = kwargs["parsed_question"]
        answer_package: dict = kwargs["answer_package"]
        storyboard: dict = kwargs["storyboard"]
        storyboard_item: dict = kwargs["storyboard_item"]
        previous_items: list[dict] = list(kwargs.get("previous_items") or [])

        question_brief = compact_question(parsed_question)
        answer_brief = compact_answer_for_item(answer_package, storyboard_item=storyboard_item)
        storyboard_brief = compact_storyboard_root(storyboard, item=storyboard_item)
        previous_brief = compact_previous_items(previous_items)

        return (
            "## QuestionBrief (trimmed problem statement + diagram description)\n"
            + json.dumps(question_brief, indent=2, ensure_ascii=False)
            + "\n\n"
            + curriculum_boundary_block(parsed_question, language="en")
            + "\n\n## AnswerContext (only the step / pattern data relevant to this item)\n"
            + json.dumps(answer_brief, indent=2, ensure_ascii=False)
            + "\n\n## StoryboardRoot (trimmed theme / shared symbols / params / sequence)\n"
            + json.dumps(storyboard_brief, indent=2, ensure_ascii=False)
            + "\n\n## CurrentStoryboardItem (the full item that must be implemented now)\n"
            + json.dumps(storyboard_item, indent=2, ensure_ascii=False)
            + "\n\n## PreviousItemsBrief (already covered items; do not duplicate them)\n"
            + json.dumps(previous_brief, indent=2, ensure_ascii=False)
            + "\n\nPlease turn CurrentStoryboardItem into exactly one visualization."
            + "\nRequirements:"
            + "\n- `id` must equal storyboard_item.id."
            + "\n- `title_cn` should stay as close as possible to storyboard_item.title_cn."
            + "\n- `learning_goal` must directly serve storyboard_item.learning_goal_cn."
            + "\n- `caption_cn` must clearly tie back to the answer anchor for this item."
            + "\n- If storyboard_item.shared_symbols / shared_params is non-empty, you must reuse them."
            + "\n- Prefer GeoGebra by default and make the figure directly explain the current bottleneck."
            + "\n- Do not try to cram the entire problem into a single figure."
            + "\n- All human-readable output text must be in Simplified Chinese."
        )
