"""Visualization storyboard planner prompt for GeoGebra-first codegen."""

from __future__ import annotations

import json
from typing import Any

from app.prompts._audience import curriculum_boundary_block
from app.config import settings
from app.prompts._viz_compact import compact_answer_for_planner, compact_question
from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import VISUALIZATION_STORYBOARD_SCHEMA


def _preferred_engine(kwargs: dict[str, Any]) -> str:
    raw = str(kwargs.get("preferred_engine") or settings.viz.default_engine).strip().lower()
    if raw in {"jsxgraph", "geogebra"}:
        return raw
    return "geogebra"


class VizPlannerPrompt(PromptTemplate):

    version = PromptVersion(major=1, minor=2, date_updated="2026-04-22")
    name = "vizplanner"

    purpose = (
        "从题目与完整 AnswerPackage 中识别 1-2 个最值得可视化的学习难点, "
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
                "planner 的首要任务不是把 solution_steps 机械切成两段, 而是先从题目"
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

    def fewshot_examples(self, **kwargs: Any) -> list[dict]:
        example_user = {
            "parsed_question": {"subject": "math", "grade_band": "junior", "question_text": "通过图像理解抛物线最值"},
            "answer_package": {"solution_steps": [{"step_index": 1, "statement": "先看交点，再看顶点。"}]},
        }
        example_storyboard = {
            "theme_cn": "从交点到顶点的图像过渡",
            "selection_rationale_cn": "先把对象建立出来，再过渡到最值判断。",
            "symbol_map": [{"symbol": "A", "meaning_cn": "左侧交点"}, {"symbol": "V", "meaning_cn": "顶点"}],
            "shared_params": [{"name": "t", "label_cn": "参数 t", "kind": "slider", "min": -2, "max": 2, "step": 0.1, "default": 0}],
            "coverage_summary": [],
            "sequence": ["viz-1", "viz-2"],
            "items": [
                {"id": "viz-1", "title_cn": "交点建立", "anchor_refs": [{"kind": "question_given", "ref": "given:0"}], "difficulty_reason_cn": "题设条件先要落到图上", "student_confusion_risk": "high", "conceptual_jump_cn": "从文字到对象", "why_visualization_needed_cn": "先建立交点对象", "learning_goal_cn": "认出交点位置", "engine": "geogebra", "shared_symbols": ["A"], "shared_params": ["t"], "depends_on": [], "relation_to_prev_cn": "", "relation_to_next_cn": "为顶点判断做铺垫", "caption_outline_cn": "先看交点如何出现", "geo_target_cn": "抛物线与 x 轴交点"},
                {"id": "viz-2", "title_cn": "顶点比较", "anchor_refs": [{"kind": "solution_step", "ref": "1"}], "difficulty_reason_cn": "最值依赖顶点位置", "student_confusion_risk": "medium", "conceptual_jump_cn": "从交点到顶点", "why_visualization_needed_cn": "把最值判断显性化", "learning_goal_cn": "理解顶点与最值", "engine": "geogebra", "shared_symbols": ["V"], "shared_params": ["t"], "depends_on": ["viz-1"], "relation_to_prev_cn": "承接交点图", "relation_to_next_cn": "", "caption_outline_cn": "再看顶点如何决定最值", "geo_target_cn": "抛物线顶点"}
            ]
        }
        return [
            {"role": "user", "content": "Example compact input\n" + json.dumps(example_user, ensure_ascii=False, indent=2)},
            {"role": "assistant", "content": json.dumps(example_storyboard, ensure_ascii=False, indent=2)},
        ]

    def system_message(self, **kwargs: Any) -> str:
        preferred_engine = _preferred_engine(kwargs)
        engine_policy = (
                        "Prefer engine=\"geogebra\" by default. Switch to jsxgraph only when a bottleneck clearly needs freer animation or interaction control."
            if preferred_engine == "geogebra"
                        else "The current config prefers jsxgraph, but the planner should still favor short, stable, reusable plans."
        )
        return f"""\
    You are a math / physics visualization lesson designer for middle-school and high-school students. Your job is not to write GeoGebra / JSXGraph code yet. First, choose the 2 most visualization-worthy learning bottlenecks from the problem and its full answer, then organize them into one coherent storyboard.

## Goal
- First identify the conceptual bottlenecks where students are most likely to get stuck.
- Then organize those bottlenecks into 1 or 2 related visualization items.
- The items must collectively help students understand the answer, not act as decorative illustrations.

## Selection principles
- Prioritize bottlenecks of these types:
    1. geometric / functional / motion relationships that are hard to visualize mentally
    2. key conceptual jumps from one step to the next
    3. case splits, extrema comparisons, or boundary changes
    4. why the final answer is true, not just what the final answer is
- Respect parsed_question.subject and parsed_question.grade_band as a hard curriculum boundary.
    If grade_band is junior, do not use Senior High School knowledge to explain or motivate the storyboard.
    The storyboard should help the specified grade band understand the answer with age-appropriate concepts.
- Do not mechanically flatten the answer into step 1 / 2 / 3 / 4. You may anchor items to solution_steps, but first judge which parts truly deserve visualization.
- If a pitfall or case split is real and ranks among the top two bottlenecks, cover it explicitly.
- The 2 chosen items should share symbols, parameters, or logic so they read like one teaching story.

## Relationship constraints
- root.sequence is the final teaching order, but decide the order only after selecting bottlenecks.
- root.symbol_map must be the single source of truth for all shared symbols.
- Each symbol_map entry must declare exactly one atomic symbol: use `P`, `Q`, `P'`, `Q'`, `r`, `r'`; do not combine symbols into one entry such as `P, Q`, `P'/Q'`, or `T as center, r as radius`.
- Every symbol used in item.shared_symbols must be declared individually in root.symbol_map.
- item.shared_symbols / item.shared_params may only reference objects declared at the root.
- item.depends_on may only point to items that appear earlier in sequence.
- item.anchor_refs must trace back to concrete evidence in the problem or answer.

## Engine preference
- {engine_policy}
- This is the storyboard stage. Do not output ggb_commands or jsx_code.

## Output rules
- Output exactly one JSON object whose field names / types / enums strictly match response_schema.
- All human-readable text content in the JSON must be in Simplified Chinese.
"""

    def user_message(self, **kwargs: Any) -> str:
        answer_package: dict = kwargs["answer_package"]
        parsed_question: dict = kwargs["parsed_question"]
        # Compact projections: drop similar_questions / key_points_of_answer /
        # self_check / knowledge_points / per-step rationale; the planner only
        # needs the question stem, diagram, given/find, solution-step
        # statements + formulas, and the method pattern with pitfalls.
        question_brief = compact_question(parsed_question)
        answer_brief = compact_answer_for_planner(answer_package)
        return (
            "## QuestionBrief (trimmed problem statement + diagram description)\n"
            + json.dumps(question_brief, indent=2, ensure_ascii=False)
            + "\n\n"
            + curriculum_boundary_block(parsed_question, language="en")
            + "\n\n## AnswerContext (trimmed to step titles / formulas + method_pattern + pitfalls)\n"
            + json.dumps(answer_brief, indent=2, ensure_ascii=False)
            + "\n\nPlease identify the 2 learning bottlenecks most worth visualizing and generate one storyboard."
            + "\nRequirements:"
            + "\n- Judge the conceptual jumps first, then decide the sequence."
            + "\n- Every item must explain why visualization is needed and cite anchors from the problem or answer."
            + "\n- The whole storyboard must share one consistent symbol system and feel like one teaching progression."
            + "\n- symbol_map must list atomic symbols one by one; do not merge multiple symbols into one entry."
            + "\n- If an item.shared_symbols entry uses a symbol, symbol_map must contain the same symbol as its own entry."
            + "\n- Do not output any GeoGebra commands or JSXGraph code."
            + "\n- All human-readable text fields in the output must be in Simplified Chinese."
        )