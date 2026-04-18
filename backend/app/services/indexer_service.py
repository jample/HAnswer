"""Pedagogical retrieval index builder.

Stage-1 implementation is deterministic: it derives retrieval-friendly
representations from `ParsedQuestion` + `AnswerPackage` without another
LLM call on the critical path. The result is a dual representation:

  - full-fidelity whole-question / whole-answer texts
  - semantically meaningful pedagogical units (`method`, `step`,
    `question_focus`, `answer_focus`, `extension`, `keyword_profile`)

These are stored in PG and embedded into Milvus for hybrid retrieval.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import QuestionRetrievalProfile, RetrievalUnitRow
from app.schemas import AnswerPackage, ParsedQuestion, PedagogicalIndexProfile, RetrievalUnit

_STAGE_RE = re.compile(r"(初一|初二|初三|高一|高二|高三)")

_NOVELTY_RULES = {
    "新定义": ["新定义", "定义新", "新运算", "新规则"],
    "阅读理解": ["阅读理解", "材料题", "信息题"],
    "多问": ["(1)", "（1）", "(2)", "（2）", "第1问", "第2问"],
    "动点": ["动点", "点P运动", "点在圆上运动"],
    "参数题": ["参数", "m的取值", "k的取值", "a的范围"],
}

_OBJECT_RULES = {
    "圆": ["圆", "圆O", "圆上"],
    "抛物线": ["抛物线"],
    "三角形": ["三角形", "△"],
    "四边形": ["四边形", "平行四边形", "菱形", "矩形"],
    "函数": ["函数", "图像"],
    "数列": ["数列"],
    "向量": ["向量"],
    "电路": ["电路", "电阻", "电流", "电压"],
    "磁场": ["磁场", "电磁感应"],
    "运动学": ["位移", "速度", "加速度", "匀加速"],
    "力学": ["受力", "动量", "能量", "碰撞"],
}

_TARGET_RULES = {
    "最值": ["最值", "最大值", "最小值"],
    "证明": ["证明"],
    "面积": ["面积"],
    "轨迹": ["轨迹"],
    "表达式": ["表达式", "解析式", "函数关系式"],
    "范围": ["取值范围", "范围", "取值"],
    "根与解": ["根", "解方程", "求解"],
}

_CONDITION_RULES = {
    "平行": ["平行"],
    "垂直": ["垂直"],
    "相切": ["相切"],
    "中点": ["中点"],
    "全等": ["全等"],
    "相似": ["相似"],
    "守恒": ["守恒"],
    "匀加速": ["匀加速"],
    "导数": ["导数"],
    "数形结合": ["数形结合"],
}


@dataclass
class IndexBuildResult:
    profile: PedagogicalIndexProfile
    units: list[RetrievalUnit]


def _contains_any(text: str, needles: list[str]) -> bool:
    return any(n in text for n in needles)


def _detect_labels(text: str, rules: dict[str, list[str]]) -> list[str]:
    return [label for label, needles in rules.items() if _contains_any(text, needles)]


def _dedupe_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = str(item).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def render_question_full_text(parsed: ParsedQuestion) -> str:
    parts: list[str] = [
        f"学科: {parsed.subject}",
        f"学段: {parsed.grade_band}",
    ]
    if parsed.topic_path:
        parts.append(f"知识路径: {' > '.join(parsed.topic_path)}")
    if parsed.tags:
        parts.append(f"标签: {' / '.join(parsed.tags)}")
    parts.append(f"题目: {parsed.question_text}")
    if parsed.given:
        parts.append("已知: " + "；".join(parsed.given))
    if parsed.find:
        parts.append("求解: " + "；".join(parsed.find))
    if parsed.diagram_description:
        parts.append("图形描述: " + parsed.diagram_description)
    return "\n".join(parts)


def render_answer_full_text(pkg: AnswerPackage) -> str:
    parts: list[str] = [
        "题目理解:",
        pkg.question_understanding.restated_question,
    ]
    if pkg.key_points_of_question:
        parts.append("题目关键点: " + "；".join(pkg.key_points_of_question))
    if pkg.solution_steps:
        parts.append("解题步骤:")
        for step in pkg.solution_steps:
            line = f"{step.step_index}. {step.statement}；原理: {step.rationale}"
            if step.why_this_step:
                line += f"；为何这样做: {step.why_this_step}"
            if step.formula:
                line += f"；公式: {step.formula}"
            parts.append(line)
    if pkg.key_points_of_answer:
        parts.append("答案关键点: " + "；".join(pkg.key_points_of_answer))
    parts.append(
        "方法模式: "
        + pkg.method_pattern.name_cn
        + "；适用: "
        + pkg.method_pattern.when_to_use
    )
    if pkg.method_pattern.general_procedure:
        parts.append("通用步骤: " + "；".join(pkg.method_pattern.general_procedure))
    if pkg.method_pattern.pitfalls:
        parts.append("常见陷阱: " + "；".join(pkg.method_pattern.pitfalls))
    if pkg.self_check:
        parts.append("自查: " + "；".join(pkg.self_check))
    return "\n".join(parts)


def _build_extension_text(pkg: AnswerPackage) -> str:
    if not pkg.similar_questions:
        return ""
    lines = ["扩展思路:"]
    for i, item in enumerate(pkg.similar_questions, start=1):
        lines.append(
            f"{i}. {item.statement}；大纲: {item.answer_outline}；难度变化: {item.difficulty_delta}"
        )
    if pkg.method_pattern.general_procedure:
        lines.append("迁移时保持方法骨架: " + "；".join(pkg.method_pattern.general_procedure))
    return "\n".join(lines)


def build_pedagogical_index(
    *,
    parsed: ParsedQuestion,
    package: AnswerPackage,
) -> IndexBuildResult:
    source_text = "\n".join([
        render_question_full_text(parsed),
        render_answer_full_text(package),
        package.method_pattern.name_cn,
    ])
    stage_match = _STAGE_RE.search(source_text)
    textbook_stage = stage_match.group(1) if stage_match else ""

    novelty_flags = _detect_labels(source_text, _NOVELTY_RULES)
    object_entities = _detect_labels(source_text, _OBJECT_RULES)
    target_types = _detect_labels(source_text, _TARGET_RULES)
    condition_signals = _detect_labels(source_text, _CONDITION_RULES)

    lexical_aliases: list[str] = []
    lexical_aliases.extend(parsed.topic_path)
    lexical_aliases.extend(parsed.tags)
    lexical_aliases.extend(object_entities)
    lexical_aliases.extend(target_types)
    lexical_aliases.extend(condition_signals)
    lexical_aliases.extend(novelty_flags)
    lexical_aliases.append(package.method_pattern.name_cn)
    if "新定义" in novelty_flags:
        lexical_aliases.extend(["新定义题", "定义新运算", "定义新规则"])
    if "圆" in object_entities and "最值" in target_types:
        lexical_aliases.extend(["圆的最值", "圆上最值", "与圆有关的最值"])
    if parsed.grade_band == "junior":
        lexical_aliases.append("初中")
    if parsed.grade_band == "senior":
        lexical_aliases.append("高中")
    if textbook_stage:
        lexical_aliases.append(textbook_stage)

    question_full_text = render_question_full_text(parsed)
    answer_full_text = render_answer_full_text(package)
    method_text = "\n".join([
        package.method_pattern.name_cn,
        package.method_pattern.when_to_use,
        *package.method_pattern.general_procedure,
        *package.method_pattern.pitfalls,
    ])
    step_texts = [
        "\n".join(filter(None, [
            f"步骤 {step.step_index}: {step.statement}",
            f"原理: {step.rationale}",
            f"为何这样做: {step.why_this_step}",
            f"公式: {step.formula}" if step.formula else "",
        ]))
        for step in package.solution_steps
    ]
    extension_text = _build_extension_text(package)

    profile = PedagogicalIndexProfile(
        subject=parsed.subject,
        grade_band=parsed.grade_band,
        textbook_stage=textbook_stage,
        topic_path=list(parsed.topic_path),
        novelty_flags=_dedupe_keep_order(novelty_flags),
        object_entities=_dedupe_keep_order(object_entities),
        target_types=_dedupe_keep_order(target_types),
        condition_signals=_dedupe_keep_order(condition_signals),
        question_focus=_dedupe_keep_order(list(package.key_points_of_question)),
        answer_focus=_dedupe_keep_order(list(package.key_points_of_answer) + list(package.self_check)),
        method_labels=_dedupe_keep_order([package.method_pattern.name_cn, *parsed.topic_path]),
        extension_ideas=_dedupe_keep_order(
            [item.statement for item in package.similar_questions]
            + [item.answer_outline for item in package.similar_questions]
        ),
        pitfalls=_dedupe_keep_order(list(package.method_pattern.pitfalls)),
        lexical_aliases=_dedupe_keep_order(lexical_aliases),
        query_texts={
            "question_full_text": question_full_text,
            "answer_full_text": answer_full_text,
            "method_text": method_text,
            "step_texts": step_texts,
            "extension_text": extension_text,
        },
    )

    units: list[RetrievalUnit] = [
        RetrievalUnit(
            unit_kind="question_focus",
            title="题目关键点",
            text="；".join(profile.question_focus),
            keywords=_dedupe_keep_order(profile.object_entities + profile.target_types + profile.condition_signals),
            weight=0.82,
            source_section="key_points_of_question",
        ),
        RetrievalUnit(
            unit_kind="answer_focus",
            title="答案关键点",
            text="；".join(profile.answer_focus),
            keywords=_dedupe_keep_order(profile.target_types + profile.method_labels),
            weight=0.82,
            source_section="key_points_of_answer",
        ),
        RetrievalUnit(
            unit_kind="method",
            title=package.method_pattern.name_cn,
            text=method_text,
            keywords=_dedupe_keep_order(profile.method_labels + profile.pitfalls),
            weight=0.94,
            source_section="method_pattern",
        ),
    ]
    for step in package.solution_steps:
        units.append(RetrievalUnit(
            unit_kind="step",
            title=f"步骤 {step.step_index}",
            text="\n".join(filter(None, [
                step.statement,
                step.rationale,
                step.why_this_step,
                step.formula,
            ])),
            keywords=_dedupe_keep_order(profile.method_labels + profile.condition_signals),
            weight=0.72,
            source_section="solution_steps",
        ))
    if extension_text:
        units.append(RetrievalUnit(
            unit_kind="extension",
            title="扩展思路",
            text=extension_text,
            keywords=_dedupe_keep_order(profile.method_labels + profile.target_types),
            weight=0.68,
            source_section="similar_questions",
        ))
    units.append(RetrievalUnit(
        unit_kind="keyword_profile",
        title="关键词画像",
        text="；".join(_dedupe_keep_order(
            profile.lexical_aliases
            + profile.novelty_flags
            + profile.object_entities
            + profile.target_types
            + profile.condition_signals
        )),
        keywords=_dedupe_keep_order(profile.lexical_aliases),
        weight=0.88,
        source_section="index_profile",
    ))
    units = [u for u in units if u.text.strip()]
    return IndexBuildResult(profile=profile, units=units)


async def persist_pedagogical_index(
    session: AsyncSession,
    *,
    question_id: uuid.UUID,
    profile: PedagogicalIndexProfile,
    units: list[RetrievalUnit],
) -> list[RetrievalUnitRow]:
    await session.execute(
        delete(RetrievalUnitRow).where(RetrievalUnitRow.question_id == question_id)
    )
    await session.execute(
        delete(QuestionRetrievalProfile).where(QuestionRetrievalProfile.question_id == question_id)
    )

    session.add(QuestionRetrievalProfile(
        question_id=question_id,
        profile_json=profile.model_dump(mode="json"),
    ))
    await session.flush()

    rows: list[RetrievalUnitRow] = []
    for unit in units:
        row = RetrievalUnitRow(
            question_id=question_id,
            unit_kind=unit.unit_kind,
            title=unit.title,
            text=unit.text,
            keywords_json=list(unit.keywords),
            weight=float(unit.weight),
            source_section=unit.source_section,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return rows


async def load_retrieval_units(
    session: AsyncSession, *, question_id: uuid.UUID,
) -> list[RetrievalUnitRow]:
    return list((await session.execute(
        select(RetrievalUnitRow)
        .where(RetrievalUnitRow.question_id == question_id)
        .order_by(RetrievalUnitRow.created_at, RetrievalUnitRow.title)
    )).scalars().all())
