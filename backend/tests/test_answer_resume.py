"""Answer resume endpoint test (M8)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import models
from app.db.session import session_scope
from app.main import _ResumePollingAccessFilter, app
from app.services import answer_job_service


def test_resume_access_filter_drops_successful_resume_polls():
    filt = _ResumePollingAccessFilter()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:58870",
            "GET",
            "/api/answer/abc/resume?solution_id=def",
            "1.1",
            200,
        ),
        exc_info=None,
    )
    assert filt.filter(record) is False


def test_resume_access_filter_keeps_other_access_logs():
    filt = _ResumePollingAccessFilter()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:58870",
            "GET",
            "/api/answer/abc/start",
            "1.1",
            200,
        ),
        exc_info=None,
    )
    assert filt.filter(record) is True


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
            learning_goal="G", helpers_used_json=[], engine="geogebra", jsx_code="",
            spec_json={"id": "spec-1", "recommended": True},
            execution_payload_json={
                "title": "T",
                "preferred_geogebra_app": "geometry",
                "execution_mode": "command_only",
                "math_meaning_summary": "Resume test payload",
                "object_naming_convention": "Use short English labels",
                "commands": [{"step": 1, "purpose": "Create point", "command": "A=(0,0)"}],
                "property_commands": [],
                "interaction_objects": [],
                "optional_script": {
                    "needed": False,
                    "script_type": "none",
                    "reason": "",
                    "target_object": "",
                    "trigger": "none",
                    "script_body": "",
                },
                "expected_created_objects": [{"name": "A", "type": "point", "role": "point"}],
                "consistency_checks": [],
                "fallback_used": False,
                "fallback_reason": "",
                "implementation_notes": [],
            },
            degraded=False,
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
            assert body["visualizations"][0]["spec_json"] == {"id": "spec-1", "recommended": True}
            assert body["visualizations"][0]["execution_payload"]["commands"] == [
                {"step": 1, "purpose": "Create point", "command": "A=(0,0)"}
            ]
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
async def test_resume_returns_visualization_plan_from_current_solution():
    marker = f"resume-vizplan-{uuid.uuid4().hex[:8]}"

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
            answer_package_json={"method_pattern": {"name_cn": "图像法"}},
            subject="math", grade_band="senior", difficulty=2,
            dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
            seen_count=1, status="review_viz",
        )
        s.add(q)
        await s.flush()
        qid = q.id
        s.add(models.QuestionSolution(
            question_id=qid,
            ordinal=1,
            title="解法 1",
            is_current=True,
            status="review_viz",
            answer_package_json={"method_pattern": {"name_cn": "图像法"}},
            visualization_plan_json={"task_summary": {"source_math_topic": "function"}},
            visualizations_json=[{"id": "viz-1", "title_cn": "交点示意"}],
            sediment_json=None,
            stage_reviews_json={
                "visualizing": {
                    "stage": "visualizing",
                    "review_status": "pending",
                    "artifact_version": 1,
                    "run_count": 1,
                    "summary": {"visualization_count": 1},
                    "refs": {},
                    "review_note": "",
                    "reviewed_at": None,
                    "updated_at": None,
                }
            },
        ))

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get(f"/api/answer/{qid}/resume")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["visualization_plan"] == {"task_summary": {"source_math_topic": "function"}}
            assert "storyboard" not in body
            assert body["solutions"][0]["has_visualization_plan"] is True
    finally:
        async with session_scope() as s:
            await s.execute(
                delete(models.QuestionSolution).where(models.QuestionSolution.question_id == qid)
            )
            await s.execute(
                delete(models.QuestionStageReview)
                .where(models.QuestionStageReview.question_id == qid)
            )
            await s.execute(delete(models.Question).where(models.Question.id == qid))
            await s.commit()


@pytest.mark.asyncio
async def test_resume_prefers_visualization_rows_over_solution_cache_when_both_exist():
    marker = f"resume-viz-rows-{uuid.uuid4().hex[:8]}"

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
            answer_package_json={"method_pattern": {"name_cn": "图像法"}},
            subject="math", grade_band="senior", difficulty=2,
            dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
            seen_count=1, status="review_viz",
        )
        s.add(q)
        await s.flush()
        qid = q.id

        s.add(models.QuestionSolution(
            question_id=qid,
            ordinal=1,
            title="解法 1",
            is_current=True,
            status="review_viz",
            answer_package_json={"method_pattern": {"name_cn": "图像法"}},
            visualization_plan_json={"task_summary": {"source_math_topic": "function"}},
            visualizations_json=[{"id": "cached-viz", "title_cn": "缓存图"}],
            sediment_json=None,
            stage_reviews_json={},
        ))
        s.add(models.VisualizationRow(
            question_id=qid,
            viz_ref="row-viz",
            title="行存图",
            caption="来自表",
            learning_goal="优先读取表数据",
            interactive_hints_json=["来自表"],
            helpers_used_json=[],
            engine="geogebra",
            jsx_code="",
            execution_payload_json={
                "title": "行存图",
                "preferred_geogebra_app": "geometry",
                "execution_mode": "command_only",
                "math_meaning_summary": "Row-backed payload",
                "object_naming_convention": "Use short English labels",
                "commands": [{"step": 1, "purpose": "Create point", "command": "A=(0,0)"}],
                "property_commands": [],
                "interaction_objects": [],
                "optional_script": {
                    "needed": False,
                    "script_type": "none",
                    "reason": "",
                    "target_object": "",
                    "trigger": "none",
                    "script_body": "",
                },
                "expected_created_objects": [{"name": "A", "type": "point", "role": "point"}],
                "consistency_checks": [],
                "fallback_used": False,
                "fallback_reason": "",
                "implementation_notes": [],
            },
            degraded=False,
            spec_json=None,
            params_json=[],
            animation_json=None,
        ))
        await s.commit()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get(f"/api/answer/{qid}/resume")
            assert r.status_code == 200, r.text
            body = r.json()
            assert [item["id"] for item in body["visualizations"]] == ["row-viz"]
            assert body["visualizations"][0]["interactive_hints"] == ["来自表"]
            assert body["visualizations"][0]["execution_payload"]["preferred_geogebra_app"] == "geometry"
    finally:
        async with session_scope() as s:
            await s.execute(
                delete(models.VisualizationRow).where(models.VisualizationRow.question_id == qid)
            )
            await s.execute(
                delete(models.QuestionSolution).where(models.QuestionSolution.question_id == qid)
            )
            await s.execute(delete(models.Question).where(models.Question.id == qid))
            await s.commit()


@pytest.mark.asyncio
async def test_resume_restores_job_state_from_persisted_status_after_restart():
    marker = f"resume-status-{uuid.uuid4().hex[:8]}"

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
            seen_count=1, status="solving",
        )
        s.add(q)
        await s.flush()
        qid = q.id

        s.add(models.AnswerPackageSection(
            question_id=qid,
            section="status",
            payload_json={
                "stage": "solving",
                "message": "正在调用 Gemini 生成完整教学型答案，复杂题可能需要几十秒。",
                "call_index": 2,
                "total_calls": 4,
                "label": "生成解答",
            },
        ))

    answer_job_service._states.clear()
    answer_job_service._tasks.clear()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get(f"/api/answer/{qid}/resume")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["job"]["running"] is False
            assert body["job"]["stage"] == "solving"
            assert body["job"]["message"] == "正在调用 Gemini 生成完整教学型答案，复杂题可能需要几十秒。"
            assert body["job"]["call_index"] == 2
    finally:
        async with session_scope() as s:
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
async def test_job_state_reports_not_running_after_review_state_even_before_task_cleanup():
    qid = uuid.uuid4()
    solution_id = uuid.uuid4()
    key = answer_job_service._job_key(qid, solution_id)
    blocker = asyncio.Event()
    task = asyncio.create_task(blocker.wait())
    answer_job_service._states[key] = answer_job_service.JobState(
        question_id=str(qid),
        solution_id=str(solution_id),
        stage="visualizing",
        call_index=3,
        label="生成可视化",
        message="可视化已生成，等待人工确认。",
        done=True,
    )
    answer_job_service._tasks[key] = task

    try:
        async with session_scope() as s:
            job = await answer_job_service.get_answer_job_state(
                s,
                qid,
                solution_id,
            )

        assert job["done"] is True
        assert job["running"] is False
        assert job["stage"] == "visualizing"
    finally:
        answer_job_service._states.pop(key, None)
        answer_job_service._tasks.pop(key, None)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


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
