"""Stage review guidance tests."""

from __future__ import annotations

import hashlib
import uuid

import pytest

from app.db import models
from app.services.stage_review_service import build_stage_user_guidance


def _question(marker: str) -> models.Question:
    return models.Question(
        parsed_json={
            "subject": "math",
            "grade_band": "junior",
            "topic_path": ["几何", "圆"],
            "question_text": marker,
            "given": [],
            "find": [],
            "diagram_description": "",
            "difficulty": 2,
            "tags": [],
            "confidence": 0.9,
        },
        answer_package_json={"method_pattern": {"name_cn": "弦心距"}},
        subject="math",
        grade_band="junior",
        difficulty=2,
        dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
        seen_count=1,
        status="review_solve",
    )


@pytest.mark.asyncio
async def test_build_stage_user_guidance_combines_upstream_and_rerun_notes(session):
    marker = f"stage-guidance-{uuid.uuid4().hex[:8]}"
    q = _question(marker)
    session.add(q)
    await session.flush()

    session.add(models.QuestionStageReview(
        question_id=q.id,
        stage="parsed",
        review_status="confirmed",
        artifact_version=1,
        run_count=1,
        summary_json={},
        refs_json={},
        review_note="这是面向初中生的题目。",
    ))
    session.add(models.QuestionStageReview(
        question_id=q.id,
        stage="solving",
        review_status="rejected",
        artifact_version=1,
        run_count=1,
        summary_json={},
        refs_json={},
        review_note="请减少高中技巧，步骤写得更细。",
    ))
    await session.flush()

    guidance = await build_stage_user_guidance(
        session,
        question_id=q.id,
        target_stage="solving",
    )

    assert "请严格遵守以下用户补充要求" in guidance
    assert "[来自parsed阶段的用户要求]" in guidance
    assert "这是面向初中生的题目。" in guidance
    assert "[本次solving阶段重跑要求]" in guidance
    assert "请减少高中技巧，步骤写得更细。" in guidance
