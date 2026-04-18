"""Answer resume endpoint test (M8)."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy import select

from app.db import models
from app.db.session import session_scope
from app.main import app


@pytest.mark.asyncio
async def test_resume_returns_stored_sections_and_viz():
    marker = f"resume-{uuid.uuid4().hex[:8]}"

    async with session_scope() as s:
        q = models.Question(
            parsed_json={
                "subject": "math",
                "grade_band": "senior",
                "topic_path": [],
                "question_text": marker,
                "given": [],
                "find": [],
                "diagram_description": "",
                "difficulty": 2,
                "tags": [],
                "confidence": 0.9,
            },
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
            assert any(item["stage"] == "parsed" for item in body["stage_reviews"])
    finally:
        async with session_scope() as s:
            await s.execute(
                delete(models.VisualizationRow).where(models.VisualizationRow.question_id == qid)
            )
            await s.execute(
                delete(models.AnswerPackageSection)
                .where(models.AnswerPackageSection.question_id == qid)
            )
            await s.execute(
                delete(models.QuestionStageReview)
                .where(models.QuestionStageReview.question_id == qid)
            )
            await s.execute(delete(models.Question).where(models.Question.id == qid))
            await s.commit()


@pytest.mark.asyncio
async def test_resume_404_on_missing_question():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"/api/answer/{uuid.uuid4()}/resume")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_confirm_indexing_marks_question_answered():
    marker = f"confirm-index-{uuid.uuid4().hex[:8]}"

    async with session_scope() as s:
        q = models.Question(
            parsed_json={
                "subject": "math",
                "grade_band": "senior",
                "topic_path": [],
                "question_text": marker,
                "given": [],
                "find": [],
                "diagram_description": "",
                "difficulty": 2,
                "tags": [],
                "confidence": 0.9,
            },
            answer_package_json={"method_pattern": {"name_cn": "因式分解法"}},
            subject="math", grade_band="senior", difficulty=2,
            dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
            seen_count=1, status="review_index",
        )
        s.add(q)
        await s.flush()
        qid = q.id

        s.add(models.QuestionStageReview(
            question_id=qid,
            stage="indexing",
            review_status="pending",
            artifact_version=1,
            run_count=1,
            summary_json={"retrieval_unit_count": 3},
            refs_json={"question_id": str(qid)},
        ))

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                f"/api/answer/{qid}/stages/indexing/confirm",
                json={"note": "确认入库，但后续推荐时按初中生题目处理。"},
            )
            assert r.status_code == 200, r.text

        async with session_scope() as s:
            refreshed = await s.get(models.Question, qid)
            assert refreshed is not None
            assert refreshed.status == "answered"
            review = (await s.execute(
                select(models.QuestionStageReview)
                .where(models.QuestionStageReview.question_id == qid)
                .where(models.QuestionStageReview.stage == "indexing")
            )).scalar_one()
            assert review.review_status == "confirmed"
            assert review.review_note == "确认入库，但后续推荐时按初中生题目处理。"
    finally:
        async with session_scope() as s:
            await s.execute(
                delete(models.QuestionStageReview)
                .where(models.QuestionStageReview.question_id == qid)
            )
            await s.execute(delete(models.Question).where(models.Question.id == qid))
            await s.commit()


@pytest.mark.asyncio
async def test_confirm_stage_allows_clearing_existing_review_note():
    marker = f"clear-note-{uuid.uuid4().hex[:8]}"

    async with session_scope() as s:
        q = models.Question(
            parsed_json={
                "subject": "math",
                "grade_band": "senior",
                "topic_path": [],
                "question_text": marker,
                "given": [],
                "find": [],
                "diagram_description": "",
                "difficulty": 2,
                "tags": [],
                "confidence": 0.9,
            },
            answer_package_json=None,
            subject="math", grade_band="senior", difficulty=2,
            dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
            seen_count=1, status="review_parse",
        )
        s.add(q)
        await s.flush()
        qid = q.id

        s.add(models.QuestionStageReview(
            question_id=qid,
            stage="parsed",
            review_status="pending",
            artifact_version=1,
            run_count=1,
            summary_json={"question_text": marker},
            refs_json={"question_id": str(qid)},
            review_note="旧要求",
        ))

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                f"/api/answer/{qid}/stages/parsed/confirm",
                json={"note": ""},
            )
            assert r.status_code == 200, r.text

        async with session_scope() as s:
            review = (await s.execute(
                select(models.QuestionStageReview)
                .where(models.QuestionStageReview.question_id == qid)
                .where(models.QuestionStageReview.stage == "parsed")
            )).scalar_one()
            assert review.review_status == "confirmed"
            assert review.review_note == ""
    finally:
        async with session_scope() as s:
            await s.execute(
                delete(models.QuestionStageReview)
                .where(models.QuestionStageReview.question_id == qid)
            )
            await s.execute(delete(models.Question).where(models.Question.id == qid))
            await s.commit()


@pytest.mark.asyncio
async def test_create_solution_endpoint_creates_current_solution():
    marker = f"create-solution-{uuid.uuid4().hex[:8]}"

    async with session_scope() as s:
        q = models.Question(
            parsed_json={
                "subject": "math",
                "grade_band": "senior",
                "topic_path": [],
                "question_text": marker,
                "given": [],
                "find": [],
                "diagram_description": "",
                "difficulty": 2,
                "tags": [],
                "confidence": 0.9,
            },
            answer_package_json=None,
            subject="math", grade_band="senior", difficulty=2,
            dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
            seen_count=1, status="review_parse",
        )
        s.add(q)
        await s.flush()
        qid = q.id

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(f"/api/questions/{qid}/solutions", json={})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["question_id"] == str(qid)
            assert body["solution"]["ordinal"] == 1
            assert body["solution"]["is_current"] is True
    finally:
        async with session_scope() as s:
            await s.execute(
                delete(models.QuestionSolution)
                .where(models.QuestionSolution.question_id == qid)
            )
            await s.execute(delete(models.Question).where(models.Question.id == qid))
            await s.commit()
