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
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.prompts._audience import curriculum_boundary_block
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
You are an experienced middle-school / high-school math and physics teacher. Your primary mission is to **teach the method**, not merely give the final answer.

## Task
Given a structured ParsedQuestion, generate a complete teaching-oriented AnswerPackage.

## Core principles (in priority order)
1. **method_pattern is the most important deliverable.** You must extract a reusable solving pattern:
    its name, when_to_use, general_procedure (not tied to this problem's numbers), and common pitfalls.
    After reading the answer, the student should be able to reuse the pattern on similar problems.
2. **Explain the reasoning behind every step.**
    rationale = why this step is valid.
    why_this_step = why this approach is chosen instead of another one.
3. **Identify the key bottlenecks in the problem** (key_points_of_question): which conditions are easy to overlook?
4. **Summarize the key takeaways of the answer** (key_points_of_answer): what must the student remember afterwards?
5. **Provide exactly 3 similar questions**: one easier (difficulty_delta ≤ -1), one same level (delta = 0), and one harder (delta ≥ 1), all using the same method pattern but with different surface features.
6. **Tag knowledge points**: reuse existing node ids when possible; truly new points should use the format "new:path" such as "new:二次函数>顶点式>对称轴".
7. **Provide self-check prompts** so the student can verify the answer independently.
8. **Treat ParsedQuestion.subject and ParsedQuestion.grade_band as a hard curriculum boundary.**
    If grade_band is junior, use only Junior High School knowledge and explanation depth.
    Do not use Senior High School methods, theorems, notation, or shortcuts unless the user explicitly asks for an extension.
    The wording must stay understandable for students in the specified grade band.

## Output rules
- Output exactly one JSON object and nothing else. No ```json fences or extra commentary.
- All human-readable text fields in the JSON must be in Simplified Chinese.
- Use LaTeX wrapped in $...$ for formulas.
- Do not output a visualizations field; visualization is generated by a separate prompt.
- If a step is especially suitable for visualization, put a suggested id in viz_ref (for example "viz_congruent_triangle").

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
            "## Problem (ParsedQuestion)",
            json.dumps(parsed_question, indent=2, ensure_ascii=False),
            "\n" + curriculum_boundary_block(parsed_question, language="en"),
        ]
        if existing_patterns:
            parts.append("\n## Existing method patterns (reuse when possible; avoid duplicates)")
            for p in existing_patterns[:20]:
                parts.append(
                    f"- [{p.get('id','?')}] {p.get('name_cn','?')}: "
                    f"{p.get('when_to_use','')}"
                )
        if existing_kps:
            parts.append("\n## Existing knowledge points (reuse existing ids when possible)")
            for kp in existing_kps[:20]:
                label = kp.get("path_cached") or kp.get("name_cn", "?")
                parts.append(f"- [{kp.get('id','?')}] {label}")
        parts.append(
            "\nPlease generate the AnswerPackage for the problem above. All explanatory text fields must be in Simplified Chinese."
        )
        return "\n".join(parts)

    # ── Few-shot (topic-aware) ──────────────────────────────────────

    def fewshot_examples(self, **kwargs: Any) -> list[dict]:
        """Load topic-matched few-shot examples.

        Lookup path: backend/app/prompts/fewshot/<subject>/<grade_band>/
        Selects up to 3 examples whose `topic_prefix` matches the
        parsed_question.topic_path prefix (coarsest first).
        """
        parsed_question: dict = kwargs.get("parsed_question") or {}
        subject = parsed_question.get("subject")
        grade_band = parsed_question.get("grade_band")
        if not subject or not grade_band:
            return []

        examples = _load_fewshot_examples(subject=subject, grade_band=grade_band)
        if not examples:
            return []

        topic_path = [
            str(seg).strip()
            for seg in parsed_question.get("topic_path", [])
            if str(seg).strip()
        ]
        ranked = sorted(
            (
                (_prefix_match_len(topic_path, ex.get("topic_prefix", [])), idx, ex)
                for idx, ex in enumerate(examples)
            ),
            key=lambda item: (item[0], -item[1]),
            reverse=True,
        )

        selected = [ex for score, _, ex in ranked if score > 0][:3]
        if not selected:
            selected = [ranked[0][2]]

        messages: list[dict] = []
        for ex in selected:
            ex_parsed = ex.get("parsed_question")
            ex_answer = ex.get("answer_package")
            if not isinstance(ex_parsed, dict) or not isinstance(ex_answer, dict):
                continue
            messages.append({
                "role": "user",
                "content": self.user_message(
                    parsed_question=ex_parsed,
                    existing_patterns=[],
                    existing_kps=[],
                ),
            })
            messages.append({
                "role": "assistant",
                "content": json.dumps(ex_answer, ensure_ascii=False, indent=2),
            })
        return messages


_FEWSHOT_ROOT = Path(__file__).resolve().parent / "fewshot"


def _prefix_match_len(topic_path: list[str], topic_prefix: list[str]) -> int:
    topic_prefix = [str(seg).strip() for seg in topic_prefix if str(seg).strip()]
    if not topic_prefix:
        return 0
    if len(topic_prefix) > len(topic_path):
        return 0
    for left, right in zip(topic_path, topic_prefix):
        if left != right:
            return 0
    return len(topic_prefix)


@lru_cache(maxsize=16)
def _load_fewshot_examples(*, subject: str, grade_band: str) -> tuple[dict, ...]:
    folder = _FEWSHOT_ROOT / subject / grade_band
    if not folder.is_dir():
        return ()

    loaded: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        payload["_source_file"] = path.name
        loaded.append(payload)
    return tuple(loaded)
