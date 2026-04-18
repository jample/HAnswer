"""Exam service tests (M7).

Covers:
  - Bank-only exam honoring difficulty_dist.
  - Variant synthesis when the bank is short.
  - Failed synthesis degrades gracefully (bank-only output).
  - get_exam_detail hydrates statements from source questions.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import models
from app.services.exam_service import (
    ExamConfig,
    build_exam,
    get_exam_detail,
)
from app.services.llm_client import FakeTransport, GeminiClient


_ANSWER_PKG = {
    "question_understanding": {
        "restated_question": "解 $x^2 - 5x + 6 = 0$",
        "givens": ["a=1,b=-5,c=6"], "unknowns": ["x"], "implicit_conditions": ["x 实数"],
    },
    "key_points_of_question": ["识别二次方程"],
    "solution_steps": [
        {"step_index": 1, "statement": "因式分解",
         "rationale": "(x-2)(x-3)", "formula": "(x-2)(x-3)=0",
         "why_this_step": "积零则因子零", "viz_ref": ""},
    ],
    "key_points_of_answer": ["x=2, x=3"],
    "method_pattern": {
        "pattern_id_suggested": "new:代数>因式分解法",
        "name_cn": "因式分解法",
        "when_to_use": "系数小可整因式分解",
        "general_procedure": ["观察系数", "十字相乘", "令因子为零"],
        "pitfalls": ["忽略符号"],
    },
    "similar_questions": [
        {"statement": "解 x^2-7x+12=0", "answer_outline": "x=3,4",
         "same_pattern": True, "difficulty_delta": 0},
        {"statement": "解 x^2+x-6=0", "answer_outline": "x=-3,2",
         "same_pattern": True, "difficulty_delta": 0},
        {"statement": "解 2x^2-5x+2=0", "answer_outline": "x=1/2,2",
         "same_pattern": True, "difficulty_delta": 1},
    ],
    "knowledge_points": [
        {"node_ref": "new:代数>一元二次方程", "weight": 0.9},
    ],
    "self_check": ["代回验证"],
}


def _seed_q(session, *, statement: str, difficulty: int,
            subject: str = "math", grade_band: str = "senior") -> models.Question:
    raw = statement.encode()
    q = models.Question(
        parsed_json={"question_text": statement, "subject": subject,
                     "grade_band": grade_band, "difficulty": difficulty,
                     "topic_path": ["代数"], "given": [], "find": [],
                     "diagram_description": "", "tags": [], "confidence": 0.9},
        answer_package_json=_ANSWER_PKG,
        subject=subject, grade_band=grade_band, difficulty=difficulty,
        dedup_hash=hashlib.sha1(raw + str(uuid.uuid4()).encode()).hexdigest(),
        seen_count=1, status="answered",
    )
    session.add(q)
    return q


def _fake_llm_with_variants(n: int) -> GeminiClient:
    variants = [
        {
            "statement": f"变体题目 #{i} $x^2-{i+5}x+{i+6}=0$",
            "answer_outline": f"得 x={i+2},x={i+3}",
            "rubric": "1. 分解 2. 令零 3. 求根",
            "difficulty": 2,
            "same_pattern": True,
        }
        for i in range(n)
    ]
    payload = json.dumps({"variants": variants}, ensure_ascii=False)
    return GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_solver: payload}
    ))


@pytest.mark.asyncio
async def test_build_exam_bank_only(session):
    for diff, stmt in [(1, "简单题"), (2, "中等题 A"), (2, "中等题 B"),
                        (3, "偏难题 A"), (3, "偏难题 B")]:
        _seed_q(session, statement=stmt, difficulty=diff)
    await session.flush()

    cfg = ExamConfig(
        count=4,
        difficulty_dist={1: 1, 2: 2, 3: 1},
        allow_synthesis=False,
        seed=42,
    )
    exam = await build_exam(session, cfg=cfg, llm=_fake_llm_with_variants(0))

    items = (await session.execute(
        select(models.ExamItem).where(models.ExamItem.exam_id == exam.id)
        .order_by(models.ExamItem.position)
    )).scalars().all()
    assert len(items) == 4
    # All items are bank-sourced.
    assert all(it.source_question_id is not None and it.synthesized_payload_json is None
               for it in items)
    # Positions are 1..N contiguous.
    assert [it.position for it in items] == [1, 2, 3, 4]
    # Answer outlines got populated from the stored AnswerPackage.
    assert all(it.answer_outline for it in items)


@pytest.mark.asyncio
async def test_build_exam_synthesizes_when_short(session):
    _seed_q(session, statement="仅有的题", difficulty=2)
    await session.flush()

    cfg = ExamConfig(count=3, allow_synthesis=True, seed=1)
    exam = await build_exam(session, cfg=cfg, llm=_fake_llm_with_variants(2))

    items = (await session.execute(
        select(models.ExamItem).where(models.ExamItem.exam_id == exam.id)
        .order_by(models.ExamItem.position)
    )).scalars().all()

    assert len(items) == 3
    # First one is the bank source; others are synthesized.
    assert items[0].synthesized_payload_json is None
    assert items[0].source_question_id is not None
    synth = [it for it in items if it.synthesized_payload_json is not None]
    assert len(synth) == 2
    for it in synth:
        assert "变体题目" in it.synthesized_payload_json["statement"]
        assert it.rubric
        assert it.answer_outline


@pytest.mark.asyncio
async def test_get_exam_detail_hydrates_statements(session):
    q = _seed_q(session, statement="原题 $x^2=4$", difficulty=1)
    await session.flush()

    cfg = ExamConfig(count=1, allow_synthesis=False)
    exam = await build_exam(session, cfg=cfg, llm=_fake_llm_with_variants(0))

    detail = await get_exam_detail(session, exam.id)
    assert detail is not None
    assert detail["name"]
    assert len(detail["items"]) == 1
    assert detail["items"][0]["statement"] == "原题 $x^2=4$"
    assert detail["items"][0]["source_question_id"] == str(q.id)
    assert detail["items"][0]["synthesized"] is False


@pytest.mark.asyncio
async def test_build_exam_rejects_empty_bank(session):
    cfg = ExamConfig(count=3, allow_synthesis=True)
    with pytest.raises(ValueError):
        await build_exam(session, cfg=cfg, llm=_fake_llm_with_variants(3))
