"""Audience and curriculum-boundary helpers for prompt conditioning."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_SUBJECT_LABELS = {
    "math": {"en": "Mathematics", "zh": "数学"},
    "physics": {"en": "Physics", "zh": "物理"},
}

_GRADE_BAND_LABELS = {
    "junior": {
        "stage_en": "Junior High School",
        "stage_zh": "初中",
        "rule_en": (
            "Use only Junior High School knowledge, methods, notation, and explanation depth. "
            "Do not solve with Senior High School knowledge unless the user explicitly asks for an extension. "
            "The explanation must be understandable to Junior High School students."
        ),
        "rule_zh": (
            "只使用初中阶段的知识、方法、记号和讲解深度。除非用户明确要求拓展，"
            "否则不要调用高中知识来解题。解释必须让初中生能够直接理解。"
        ),
    },
    "senior": {
        "stage_en": "Senior High School",
        "stage_zh": "高中",
        "rule_en": (
            "Target Senior High School students. Use Senior High School knowledge and explanation depth, "
            "but do not assume university-level mathematics or physics unless the user explicitly asks for it."
        ),
        "rule_zh": (
            "面向高中学生。可以使用高中阶段的知识和讲解深度，但除非用户明确要求，"
            "不要默认使用大学层面的数学或物理知识。"
        ),
    },
}


def _normalized_question(data: Mapping[str, Any] | None) -> tuple[str, str]:
    payload = data or {}
    subject = str(payload.get("subject") or "unknown").strip().lower()
    grade_band = str(payload.get("grade_band") or "unknown").strip().lower()
    return subject, grade_band


def curriculum_boundary_block(data: Mapping[str, Any] | None, *, language: str) -> str:
    subject, grade_band = _normalized_question(data)
    subject_labels = _SUBJECT_LABELS.get(subject, {"en": subject or "unknown", "zh": subject or "未知"})
    band = _GRADE_BAND_LABELS.get(grade_band)

    if language == "zh":
        lines = [
            "## 受众与课程边界",
            f"- 学科: {subject} ({subject_labels['zh']})",
            f"- 学段: {grade_band}",
        ]
        if band:
            lines.append(f"- 目标学生: {band['stage_zh']}学生")
            lines.append(f"- 硬性要求: {band['rule_zh']}")
        else:
            lines.append("- 硬性要求: 严格按给定学段控制知识范围和讲解难度，不要跨到更高学段。")
        return "\n".join(lines)

    lines = [
        "## Audience / curriculum boundary",
        f"- Subject: {subject} ({subject_labels['en']})",
        f"- Grade band: {grade_band}",
    ]
    if band:
        lines.append(f"- Teaching audience: {band['stage_en']} students")
        lines.append(f"- Hard rule: {band['rule_en']}")
    else:
        lines.append(
            "- Hard rule: Respect the provided grade band as a curriculum boundary and avoid using higher-stage knowledge by default."
        )
    return "\n".join(lines)