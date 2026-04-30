"""VariantSynthPrompt — method-pattern-preserving variants (M7, §3.5, §7.2).

PURPOSE
    Given a source question + its method pattern, synthesize N new
    questions that REUSE the same pattern but vary surface features
    (numbers, named objects, context). Used to top up practice exams
    when the local bank doesn't have enough same-pattern questions.

OPTIMIZATION
    1. `VariantSynthPrompt().preview(source=..., count=3)`
    2. `VariantSynthPrompt().explain()`
    3. Modify; bump minor; verify against practice exam eval set.
"""

from __future__ import annotations

import json
from typing import Any

from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import VARIANT_LIST_SCHEMA


class VariantSynthPrompt(PromptTemplate):

    version = PromptVersion(major=1, minor=0, date_updated="2026-04-17")
    name = "variant_synth"

    purpose = (
        "在题库不足时合成新题: 保留 method_pattern (方法模式/解题套路), "
        "改变数值/具体对象/情境, 以便形成同质不同表的练习题。"
    )

    input_description = (
        "source (源题目 JSON: statement, subject, grade_band, difficulty, "
        "pattern_name, pattern_procedure, knowledge_points), "
        "count (需要生成的变体数量, 1-10), "
        "difficulty_target (可选目标难度 1-5, 不给则默认接近源题)。"
    )

    output_description = (
        "严格符合 VariantList JSON Schema 的 JSON 对象 (variants 数组)。"
        "每个变体必须: 使用相同方法模式; 改变表面数字/对象; "
        "提供 answer_outline 与评分 rubric。"
    )

    design_decisions = [
        DesignDecision(
            title="same_pattern 硬约束",
            rationale=(
                "系统消息三次强调'不得更换方法模式', schema 中 same_pattern 必为 true, "
                "否则变体失去练习价值 — 学生练的是模式, 不是表面题面。"
            ),
            alternatives_considered=[
                "允许近似模式 — LLM 倾向于偏移到更简单的模式, 难度失真",
                "无 pattern 约束的自由出题 — 退化为随机题库, 与检索已覆盖的功能重复",
            ],
        ),
        DesignDecision(
            title="显式注入 pattern 的 general_procedure",
            rationale=(
                "把源题 method_pattern.general_procedure 直接写进 user_message, "
                "LLM 依此生成变体, 比仅给模式名称 (如 '配方法') 稳定得多。"
            ),
        ),
        DesignDecision(
            title="评分 rubric 必填",
            rationale=(
                "练习卷需要学生自测, rubric 明确得分点/易错点, "
                "比仅给 answer_outline 更贴近教学场景。"
            ),
        ),
        DesignDecision(
            title="不生成可视化",
            rationale=(
                "练习题侧重计算和方法应用; 可视化成本高且可有可无, "
                "保留在 `/q/[id]` 精讲视图中。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return VARIANT_LIST_SCHEMA

    def system_message(self, **kwargs: Any) -> str:
        return (
            "You are a math / physics problem writer for Chinese middle-school and high-school students. Your task is to synthesize N variants of the given source problem while preserving the same method_pattern.\n\n"
            "Hard requirements:\n"
            "  1. Every variant must still use the source problem's general_procedure.\n"
            "  2. Change at least two of the following: numbers, named objects, or surface scenario/context.\n"
            "  3. Every variant must include answer_outline and rubric.\n"
            "  4. Use LaTeX wrapped in $...$ for formulas.\n"
            "  5. All human-readable text fields in the JSON must be in Simplified Chinese.\n"
            "  6. Output only JSON matching the schema below. Do not add any explanation.\n\n"
            f"{json.dumps(self.schema, ensure_ascii=False, indent=2)}"
        )

    def user_message(self, **kwargs: Any) -> str:
        source = kwargs["source"]
        count = int(kwargs.get("count", 3))
        difficulty_target = kwargs.get("difficulty_target")
        return (
            "Source problem information:\n"
            f"{json.dumps(source, ensure_ascii=False, indent=2)}\n\n"
            f"Please generate {count} variants. For every variant, same_pattern must be true.\n"
            + (
                f"Target difficulty: {difficulty_target} (1-5)."
                if difficulty_target is not None else
                "Difficulty may vary within ±1 of the source problem."
            )
            + "\nAll human-readable output text should be in Simplified Chinese."
        )
