"""SolverPrompt — ParsedQuestion → AnswerPackage (§7.2.2).

PURPOSE
    Given a ParsedQuestion, generate a teaching-oriented AnswerPackage
    whose primary deliverable is the method_pattern (not the numeric
    answer).

OPTIMIZATION
    1. `SolverPrompt().preview(parsed_question={...})`
    2. `SolverPrompt().explain()` — read design rationale.
    3. Modify; bump `minor`; validate with 20 golden AnswerPackage samples (§11.1).
"""

from __future__ import annotations

import json
from typing import Any

from app.prompts.base import DesignDecision, PromptTemplate, PromptVersion
from app.prompts.schemas import ANSWER_PACKAGE_SCHEMA


class SolverPrompt(PromptTemplate):

    version = PromptVersion(major=1, minor=0, date_updated="2026-04-17")
    name = "solver"

    purpose = (
        "根据 ParsedQuestion 生成完整教学型答案包 (AnswerPackage)。"
        "核心产出是 method_pattern 与分步教学, 而非数值答案。"
    )

    input_description = (
        "parsed_question (ParsedQuestion JSON, 必需)。"
        "可选: existing_patterns (已有方法模式列表, 鼓励复用), "
        "existing_kps (已有知识点列表, 鼓励复用现有 id)。"
    )

    output_description = (
        "严格符合 AnswerPackage JSON Schema 的 JSON 对象 (不含 visualizations; "
        "可视化由 VizCoder prompt 单独生成)。"
    )

    design_decisions = [
        DesignDecision(
            title="教师优先, 解题其次",
            rationale=(
                "系统消息明确'先教方法再给答案', 并将 method_pattern 列为核心原则 #1, "
                "让 LLM 把注意力放在通用方法归纳而非数值计算。"
            ),
            alternatives_considered=[
                "先解题再提取模式 — LLM 倾向于复述步骤, 模式质量下降",
                "两次调用 (解题 + 归纳) — token 成本翻倍且一致性差",
            ],
        ),
        DesignDecision(
            title="why_this_step 字段",
            rationale=(
                "每步除了 rationale(为什么成立) 还有 why_this_step(为什么选这个方法), "
                "这是可迁移推理能力的教学关键。"
            ),
        ),
        DesignDecision(
            title="3 道同类题",
            rationale=(
                "similar_questions 固定 3 道: 偏易/同难度/偏难 (difficulty_delta ∈ [-2,2]), "
                "同一方法模式变换表面特征, 形成难度梯度。"
            ),
        ),
        DesignDecision(
            title="复用已有 pattern/kp",
            rationale=(
                "通过 existing_patterns/existing_kps 注入上下文, 让 LLM 引用已命名的 id, "
                "减少重复的 pending 节点; 真新模式则用 pattern_id_suggested 建议新 UUID。"
            ),
        ),
        DesignDecision(
            title="self_check 自查提示",
            rationale="培养学生自主验证答案习惯 (代入/量纲/特殊值), 而非盲信。",
        ),
        DesignDecision(
            title="不生成 visualizations",
            rationale=(
                "拆分到独立 VizCoder prompt, 因为 (1) Solver 输出已长, 加入 JSXGraph "
                "代码超出注意力; (2) VizCoder 需要专门的安全指令和 H 库 cheatsheet; "
                "(3) 分离后可独立重试/A-B 测试。"
            ),
        ),
    ]

    @property
    def schema(self) -> dict:
        return ANSWER_PACKAGE_SCHEMA

    # ── System ──────────────────────────────────────────────────────

    def system_message(self, **kwargs: Any) -> str:
        """System prompt.

        Structure:
          1. Role: teaching teacher.
          2. Task.
          3. Core principles (ordered by priority — method_pattern first).
          4. Format rules.
          5. Schema verbatim.

        Knobs to tune:
          - Emphasis balance between method_pattern and solution_steps.
          - Similar-questions difficulty distribution wording.
          - Tone (currently 严谨但亲切).
        """
        schema_str = json.dumps(self.schema, indent=2, ensure_ascii=False)
        return f"""\
你是一位教学经验丰富的中学数理教师。你的核心使命是**教方法**, 而不仅仅是给答案。

## 任务
根据学生提交的结构化题目 (ParsedQuestion), 生成完整的教学型答案包 (AnswerPackage)。

## 核心原则 (按优先级)
1. **method_pattern 是最重要的产出。** 必须归纳出可复用的解题方法模式:
   名称、适用场景 (when_to_use)、通用步骤 (general_procedure, 不依赖本题数据)、
   常见陷阱 (pitfalls)。学生看完后应能用这个模式解决同类题。
2. **每步解释"为什么这样想"**: rationale = 这一步为什么成立;
   why_this_step = 为什么选这个方法而非其他。
3. **指出题目的关键难点** (key_points_of_question): 哪些条件容易被忽略?
4. **总结答案的核心洞见** (key_points_of_answer): 学生做完后必须记住什么?
5. **提供 3 道同类题**: 一易 (difficulty_delta ≤ -1)、一同 (delta = 0)、
   一难 (delta ≥ 1), 使用相同方法模式但变换表面特征。
6. **标注知识点**: 尽量引用已有 node id; 新知识点用 "new:路径" 格式
   (如 "new:二次函数>顶点式>对称轴")。
7. **给自查提示**: 帮助学生自主验证答案。

## 格式
- 仅输出一个 JSON 对象, 不包含 ```json 标记或解释文字。
- 公式用 LaTeX 并以 $ 包裹。
- 简体中文, 严谨但不晦涩。
- 不要输出 visualizations 字段 (由独立模块生成)。
- 如果某一步适合配合可视化, 在 viz_ref 写建议 id (如 "viz_congruent_triangle"),
  可视化模块会据此生成对应 JSXGraph。

## JSON Schema
{schema_str}
"""

    # ── User ────────────────────────────────────────────────────────

    def user_message(self, **kwargs: Any) -> str:
        """User prompt.

        kwargs:
          parsed_question (dict, REQUIRED): the ParsedQuestion JSON.
          existing_patterns (list[dict]|None): [{id, name_cn, when_to_use}, ...]
          existing_kps (list[dict]|None): [{id, name_cn, path_cached}, ...]

        Knobs to tune:
          - How many existing patterns/kps to inject (currently top 20).
          - Reuse instruction phrasing.
        """
        parsed_question: dict = kwargs["parsed_question"]
        existing_patterns: list[dict] | None = kwargs.get("existing_patterns")
        existing_kps: list[dict] | None = kwargs.get("existing_kps")

        parts: list[str] = [
            "## 题目 (ParsedQuestion)",
            json.dumps(parsed_question, indent=2, ensure_ascii=False),
        ]
        if existing_patterns:
            parts.append("\n## 已有方法模式 (优先复用, 避免创建重复项)")
            for p in existing_patterns[:20]:
                parts.append(
                    f"- [{p.get('id','?')}] {p.get('name_cn','?')}: "
                    f"{p.get('when_to_use','')}"
                )
        if existing_kps:
            parts.append("\n## 已有知识点 (优先引用已有 id)")
            for kp in existing_kps[:20]:
                label = kp.get("path_cached") or kp.get("name_cn", "?")
                parts.append(f"- [{kp.get('id','?')}] {label}")
        parts.append("\n请根据上述题目生成 AnswerPackage。")
        return "\n".join(parts)

    # ── Few-shot (topic-aware) ──────────────────────────────────────

    def fewshot_examples(self, **kwargs: Any) -> list[dict]:
        """Load topic-matched few-shot examples.

        Lookup path: backend/app/prompts/fewshot/<subject>/<grade_band>/
        Selects up to 3 examples whose `topic_prefix` matches the
        parsed_question.topic_path prefix (coarsest first).

        TODO(M2+): implement file loader. For now returns [].
        """
        return []
