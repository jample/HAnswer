"""Solver service tests (M3).

Real PostgreSQL + FakeTransport that returns a canned AnswerPackage JSON.
Verifies:
  - SSE events are emitted in the §6 order.
  - Persistence writes question.answer_package_json, section rows, step rows.
  - Re-running generate_answer is idempotent (prior rows wiped).
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import models, repo
from app.services.ingest_service import ingest_image
from app.services.llm_client import FakeTransport, GeminiClient
from app.services.solver_service import generate_answer


_PARSED = {
    "subject": "math",
    "grade_band": "senior",
    "topic_path": ["代数"],
    "question_text": "Solve x^2 - 5x + 6 = 0.",
    "given": ["二次方程 x^2 - 5x + 6 = 0"],
    "find": ["x 的所有实数解"],
    "diagram_description": "",
    "difficulty": 2,
    "tags": ["二次方程"],
    "confidence": 0.95,
}


_ANSWER_PACKAGE = {
    "question_understanding": {
        "restated_question": "求解一元二次方程 $x^2 - 5x + 6 = 0$",
        "givens": ["系数 a=1, b=-5, c=6"],
        "unknowns": ["x 的两个根"],
        "implicit_conditions": ["x 为实数"],
    },
    "key_points_of_question": [
        "识别为一元二次方程",
        "可尝试因式分解",
    ],
    "solution_steps": [
        {
            "step_index": 1,
            "statement": "因式分解",
            "rationale": "将 x^2 - 5x + 6 分解为 (x-2)(x-3)",
            "formula": "x^2 - 5x + 6 = (x-2)(x-3)",
            "why_this_step": "因式分解把方程化为两因子之积为零",
            "viz_ref": "",
        },
        {
            "step_index": 2,
            "statement": "求根",
            "rationale": "令每个因子为零",
            "formula": "x=2 \\text{ or } x=3",
            "why_this_step": "积为零则至少一因子为零",
            "viz_ref": "",
        },
    ],
    "key_points_of_answer": ["x=2 与 x=3 是两个实根"],
    "method_pattern": {
        "pattern_id_suggested": "new:代数>一元二次方程>因式分解法",
        "name_cn": "因式分解法",
        "when_to_use": "系数较小且可整因式分解的一元二次方程",
        "general_procedure": ["观察系数", "尝试十字相乘", "令因子为零"],
        "pitfalls": ["忽略系数正负号"],
    },
    "similar_questions": [
        {"statement": "解 x^2 - 7x + 12 = 0", "answer_outline": "(x-3)(x-4)=0 → x=3,4",
         "same_pattern": True, "difficulty_delta": 0},
        {"statement": "解 x^2 + x - 6 = 0", "answer_outline": "(x+3)(x-2)=0",
         "same_pattern": True, "difficulty_delta": 0},
        {"statement": "解 2x^2 - 5x + 2 = 0", "answer_outline": "十字相乘 (2x-1)(x-2)",
         "same_pattern": True, "difficulty_delta": 1},
    ],
    "knowledge_points": [
        {"node_ref": "new:代数>方程>一元二次方程", "weight": 0.9},
        {"node_ref": "new:代数>因式分解", "weight": 0.6},
    ],
    "self_check": [
        "把 x=2 代回原方程验证",
        "把 x=3 代回原方程验证",
    ],
}


async def _seed_question(session) -> str:
    """Create a real Question row via the ingest path (with a FakeTransport)."""
    parser_llm = GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_parser: json.dumps(_PARSED)}
    ))
    result = await ingest_image(
        session, data=b"\x89PNGsolver-seed-bytes",
        mime="image/png", llm=parser_llm,
    )
    return result.question.id


def _solver_llm() -> GeminiClient:
    return GeminiClient(FakeTransport(
        json_by_model={settings.gemini.model_solver: json.dumps(_ANSWER_PACKAGE)}
    ))


@pytest.mark.asyncio
async def test_solver_emits_events_in_spec_order(session, tmp_image_dir):
    qid = await _seed_question(session)
    llm = _solver_llm()

    names = [ev.name async for ev in generate_answer(session, question_id=qid, llm=llm)]

    # §6: question_understanding → key_points_of_question → solution_step×N →
    # key_points_of_answer → method_pattern → similar_questions →
    # knowledge_points → self_check (viz is a separate stream).
    expected_prefix = ["question_understanding", "key_points_of_question"]
    assert names[:2] == expected_prefix
    # Two solution_step events.
    step_idxs = [i for i, n in enumerate(names) if n == "solution_step"]
    assert len(step_idxs) == 2
    # Tail order.
    assert names[-5:] == [
        "key_points_of_answer",
        "method_pattern",
        "similar_questions",
        "knowledge_points",
        "self_check",
    ]


@pytest.mark.asyncio
async def test_solver_persists_package_and_rows(session, tmp_image_dir):
    qid = await _seed_question(session)
    llm = _solver_llm()

    # Drain the generator.
    async for _ in generate_answer(session, question_id=qid, llm=llm):
        pass

    q = await repo.get_question(session, qid)
    assert q is not None
    assert q.status == "answered"
    assert q.answer_package_json is not None
    assert q.answer_package_json["method_pattern"]["name_cn"] == "因式分解法"

    sections = (await session.execute(
        select(models.AnswerPackageSection.section)
        .where(models.AnswerPackageSection.question_id == qid)
    )).scalars().all()
    # 2 non-step sections before + 2 steps + 5 after = 9 rows.
    assert sum(1 for s in sections if s == "solution_step") == 2
    assert "question_understanding" in sections
    assert "self_check" in sections

    step_count = (await session.execute(
        select(models.SolutionStepRow).where(models.SolutionStepRow.question_id == qid)
    )).scalars().all()
    assert len(step_count) == 2


@pytest.mark.asyncio
async def test_solver_rerun_is_idempotent(session, tmp_image_dir):
    qid = await _seed_question(session)
    llm = _solver_llm()

    async for _ in generate_answer(session, question_id=qid, llm=llm):
        pass
    async for _ in generate_answer(session, question_id=qid, llm=llm):
        pass

    # Row counts must not have doubled.
    sections = (await session.execute(
        select(models.AnswerPackageSection)
        .where(models.AnswerPackageSection.question_id == qid)
    )).scalars().all()
    steps = (await session.execute(
        select(models.SolutionStepRow).where(models.SolutionStepRow.question_id == qid)
    )).scalars().all()
    assert len(steps) == 2
    # Expect exactly 9 section rows (2 + 2 + 5).
    assert len(sections) == 9
