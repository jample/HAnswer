"""Answer resume endpoint test (M8)."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.db import models
from app.db.session import session_scope
from app.main import app


@pytest.mark.asyncio
async def test_resume_returns_stored_sections_and_viz():
    marker = f"resume-{uuid.uuid4().hex[:8]}"

    async with session_scope() as s:
        q = models.Question(
            parsed_json={"question_text": marker, "topic_path": []},
            answer_package_json={"method_pattern": {"name_cn": "因式分解法"}},
            subject="math", grade_band="senior", difficulty=2,
            dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
            seen_count=1, status="answered",
        )
        s.add(q)
        await s.flush()
        qid = q.id

        s.add(models.AnswerPackageSection(
            question_id=qid, section="question_understanding",
            payload_json={"restated_question": "foo"},
        ))
        s.add(models.AnswerPackageSection(
            question_id=qid, section="method_pattern",
            payload_json={"name_cn": "因式分解法"},
        ))
        s.add(models.VisualizationRow(
            question_id=qid, viz_ref="viz-1", title="T", caption="C",
            learning_goal="G", helpers_used_json=[], jsx_code="// js",
            params_json=[], animation_json=None,
        ))

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get(f"/api/answer/{qid}/resume")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["question_id"] == str(qid)
            assert body["complete"] is True
            sections = [s["section"] for s in body["sections"]]
            assert "question_understanding" in sections
            assert "method_pattern" in sections
            assert len(body["visualizations"]) == 1
            assert body["visualizations"][0]["id"] == "viz-1"
    finally:
        async with session_scope() as s:
            await s.execute(
                delete(models.VisualizationRow).where(models.VisualizationRow.question_id == qid)
            )
            await s.execute(
                delete(models.AnswerPackageSection)
                .where(models.AnswerPackageSection.question_id == qid)
            )
            await s.execute(delete(models.Question).where(models.Question.id == qid))
            await s.commit()


@pytest.mark.asyncio
async def test_resume_404_on_missing_question():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"/api/answer/{uuid.uuid4()}/resume")
        assert r.status_code == 404
