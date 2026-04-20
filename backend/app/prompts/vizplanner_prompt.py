"""Visualization storyboard planner prompt for GeoGebra-first codegen."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import VISUALIZATION_STORYBOARD_SCHEMA


def _preferred_engine(kwargs: dict[str, Any]) -> str:
    raw = str(kwargs.get("preferred_engine") or settings.viz.default_engine).strip().lower()
    if raw in {"jsxgraph", "geogebra"}:
        return raw
    return "geogebra"


class VizPlannerPrompt(PromptTemplate):

    version = PromptVersion(major=1, minor=0, date_updated="2026-04-20")
    name = "vizplanner"

    purpose = (
        "从题目与完整 AnswerPackage 中识别 3-4 个最值得可视化的学习难点, "
        "生成一个带共享符号与关系约束的可视化 storyboard。"
    )

    input_description = (
        "parsed_question (ParsedQuestion JSON, 必需), "
        "answer_package (AnswerPackage JSON, 必需)。"
    )

    output_description = (
        "符合 VisualizationStoryboard Schema 的 JSON; 只规划什么值得可视化、"
        "为什么、彼此如何关联, 不输出任何 ggb_commands / jsx_code。"
    )

    design_decisions = [
        DesignDecision(
            title="先选难点再排顺序",
            rationale=(
                "planner 的首要任务不是把 solution_steps 机械切成 3-4 段, 而是先从题目"
                "和答案中找出学生最难看懂、最值得图示的概念跳跃。选完之后才组织顺序。"
            ),
        ),
        DesignDecision(
            title="planner 只输出 storyboard, 不输出代码",
            rationale=(
                "把 bottleneck 选择与具体 GeoGebra/JSXGraph 代码生成分离, 可以缩短"
                "单次 LLM 输出并把失败隔离到单个 visualization。"
            ),
        ),
        DesignDecision(
            title="共享符号与共享参数上提到 root",
            rationale=(
                "多张图是否连成一个教学故事, 关键在于符号、参数与覆盖范围是否一致。"
                "planner root 必须先统一这些对象, 后续 per-viz codegen 才不易漂移。"
            ),
        ),
        DesignDecision(
            title="首轮实现优先 GeoGebra",
            rationale=(
                "当前系统里 GeoGebra 输出更短、更鲁棒。storyboard 仍保留 engine 字段,"
                "但 planner 应优先选择 geogebra, 除非某个难点明显更适合 JSXGraph。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return VISUALIZATION_STORYBOARD_SCHEMA

    def system_message(self, **kwargs: Any) -> str:
        preferred_engine = _preferred_engine(kwargs)
        schema_str = json.dumps(self.schema, indent=2, ensure_ascii=False)
        engine_policy = (
            "默认优先 engine=\"geogebra\"。只有当某个难点明显需要更自由动画或控制时才改用 jsxgraph。"
            if preferred_engine == "geogebra"
            else "当前配置偏向 jsxgraph, 但 planner 仍要优先考虑短小、稳定、易复用的方案。"
        )
        return f"""\
你是中学数学/物理可视化教学设计师。你的任务不是直接写 GeoGebra/JSXGraph 代码,
而是先从题目与完整解答中选出 3-4 个最值得图示的学习难点, 并把它们组织成一组
连贯的 storyboard。

## 目标
- 先识别学生最可能卡住的 conceptual bottlenecks。
- 再把这些 bottlenecks 组织成 3-4 个相互关联的可视化项目。
- 这些项目必须共同服务于理解解答, 不是装饰性插图。

## 选择原则
- 优先选择以下类型的难点:
  1. 学生难以在脑中形成图像的几何/函数/运动关系
  2. 从一步到下一步的关键 conceptual jump
  3. 需要分类讨论、极值比较、边界变化的情形
  4. 为什么 final_answer 成立, 而不仅是 final_answer 是什么
- 不要机械地按 step 1/2/3/4 平铺。可以锚定 solution_steps, 但必须先判断哪几处最需要图示。
- 若 pitfall / 分类讨论真实存在, 尽量让至少一项覆盖它。
- 所选 3-4 项要有共享符号、共享参数或逻辑承接, 形成一个教学故事。

## 关系约束
- root.sequence 反映最终教学顺序, 但顺序是在选出 bottlenecks 后再确定。
- root.symbol_map 必须统一所有复用符号。
- item.shared_symbols / item.shared_params 只能引用 root 中已声明对象。
- item.depends_on 只能指向 sequence 中更早的 item。
- item.anchor_refs 必须能追溯到题目或答案中的具体依据。

## 引擎偏好
- {engine_policy}
- 这是 storyboard 阶段, 不输出 ggb_commands / jsx_code。

## 输出要求
- 仅输出一个 JSON 对象, 严格匹配 Schema。
- 所有说明文字都用简体中文。

## JSON Schema
{schema_str}
"""

    def user_message(self, **kwargs: Any) -> str:
        answer_package: dict = kwargs["answer_package"]
        parsed_question: dict = kwargs["parsed_question"]
        return (
            "## ParsedQuestion\n"
            + json.dumps(parsed_question, indent=2, ensure_ascii=False)
            + "\n\n## AnswerPackage\n"
            + json.dumps(answer_package, indent=2, ensure_ascii=False)
            + "\n\n请从题目和完整解答中识别 3-4 个最值得图示的学习难点, 生成一个 storyboard。"
            + "\n要求:"
            + "\n- 先判断学生会卡在哪些 conceptual jump, 再决定 sequence。"
            + "\n- 每个 item 都必须说明为什么需要图示, 并引用题目/答案锚点。"
            + "\n- 整个 storyboard 必须共享同一组符号系统, 并形成连贯的教学推进。"
            + "\n- 不输出任何 GeoGebra 命令或 JSXGraph 代码。"
        )