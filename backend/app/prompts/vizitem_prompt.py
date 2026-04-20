"""Per-item visualization codegen prompt for storyboard-driven generation."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
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


class VizItemPrompt(PromptTemplate):

    version = PromptVersion(major=1, minor=0, date_updated="2026-04-20")
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
                "把 3-4 张图拆成独立调用后, 单次输出更短、更稳定, 某一张失败时也不会"
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
                "storyboard 的价值在于 3-4 张图读起来像一个教学故事。符号和参数漂移"
                "会直接破坏这种连贯性。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return VISUALIZATION_SCHEMA

    def system_message(self, **kwargs: Any) -> str:
        preferred_engine = _preferred_engine(kwargs)
        storyboard_item = kwargs["storyboard_item"]
        schema_str = json.dumps(self.schema, indent=2, ensure_ascii=False)
        allow = ", ".join(ALLOWED_GLOBALS)
        forbid = ", ".join(FORBIDDEN_GLOBALS)
        item_engine = str(storyboard_item.get("engine") or preferred_engine).strip().lower()
        engine_policy = (
            '当前这一项默认应输出 engine="geogebra"。除非 storyboard_item 明确要求 jsxgraph, '
            "否则不要切换。"
            if item_engine != "jsxgraph"
            else '当前这一项 storyboard 已明确指定 engine="jsxgraph"。'
        )
        return f"""\
你是中学数学/物理可视化代码生成师。现在 storyboard 已经先行确定, 你的任务只剩下:
把 **一个** storyboard item 落成 **一个** 可运行 visualization JSON。

## 硬约束
- 只输出一个 Visualization JSON 对象, 不要再输出 visualizations 数组。
- `id` 必须与 storyboard_item.id 完全一致。
- 这次只覆盖当前 storyboard_item, 不要顺带把其他 item 混进同一张图。
- `learning_goal` 必须服务于 storyboard_item.learning_goal_cn。
- `caption_cn` 必须落地 storyboard_item.caption_outline_cn。
- 复用 storyboard root 中的共享符号与共享参数, 不要擅自改名。

## 引擎策略
- {engine_policy}
- 默认渲染偏好仍是 GeoGebra-first。
- 若输出 GeoGebra: `ggb_commands` 非空, `jsx_code` 为空字符串。
- 若输出 JSXGraph: `jsx_code` 非空, `ggb_commands` 为空数组。

## GeoGebra 要求
- 一行一个命令, 不要写 ggbApplet 前缀。
- 视图设置放进 ggb_settings, 不要写到 ggb_commands。
- 不要输出 SetValue(...) / SetConditionToShowObject(...) / Line(ax+by=c) 包装。
- 若 item.shared_params 非空, 优先复用同名 Slider / toggle。

## JSXGraph 要求
- jsx_code 只写函数体本身。
- 仅可使用全局: {allow}
- 严禁使用: {forbid}

## 输出格式
- 仅输出一个 JSON 对象, 严格匹配下方 Schema。
- 不要附加解释文字。

## GeoGebra cheatsheet
{GGB_CHEATSHEET}

## JSXGraph cheatsheet
{H_CHEATSHEET}

## JSON Schema
{schema_str}
"""

    def user_message(self, **kwargs: Any) -> str:
        parsed_question: dict = kwargs["parsed_question"]
        answer_package: dict = kwargs["answer_package"]
        storyboard: dict = kwargs["storyboard"]
        storyboard_item: dict = kwargs["storyboard_item"]
        previous_items: list[dict] = list(kwargs.get("previous_items") or [])
        return (
            "## ParsedQuestion\n"
            + json.dumps(parsed_question, indent=2, ensure_ascii=False)
            + "\n\n## AnswerPackage\n"
            + json.dumps(answer_package, indent=2, ensure_ascii=False)
            + "\n\n## Storyboard Root\n"
            + json.dumps(storyboard, indent=2, ensure_ascii=False)
            + "\n\n## Current Storyboard Item\n"
            + json.dumps(storyboard_item, indent=2, ensure_ascii=False)
            + "\n\n## Previous Storyboard Items\n"
            + json.dumps(previous_items, indent=2, ensure_ascii=False)
            + "\n\n请严格把当前 storyboard_item 落成一个 visualization。"
            + "\n要求:"
            + "\n- `id` 必须等于 storyboard_item.id。"
            + "\n- `title_cn` 尽量保持 storyboard_item.title_cn 的表述。"
            + "\n- `learning_goal` 直接服务于 storyboard_item.learning_goal_cn。"
            + "\n- `caption_cn` 明确回扣当前 item 对应的解答锚点。"
            + "\n- 若 storyboard_item.shared_symbols / shared_params 非空, 必须复用。"
            + "\n- 默认优先 GeoGebra, 并让图形直接解释当前 bottleneck。"
            + "\n- 不要把整个题目所有内容塞进同一张图。"
        )