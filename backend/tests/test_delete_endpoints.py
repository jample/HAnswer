from __future__ import annotations

import hashlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import models
from app.db.session import session_scope
from app.main import app
from app.routers import answer as answer_router
from app.services import answer_job_service
from app.services.solution_ref_service import encode_solution_ref
from app.services.vector_store import InMemoryVectorStore


def _parsed_payload(marker: str) -> dict:
    return {
        "subject": "math",
        "grade_band": "senior",
        "topic_path": ["代数", "函数"],
        "question_text": marker,
        "given": [],
        "find": [],
        "diagram_description": "",
        "difficulty": 3,
        "tags": [],
        "confidence": 0.9,
    }


async def _cleanup(question_id: uuid.UUID | None, solution_ids: list[uuid.UUID] | None = None) -> None:
    if question_id is None:
        return
    async with session_scope() as s:
        if solution_ids:
            await s.execute(delete(models.QuestionRetrievalProfile).where(
                models.QuestionRetrievalProfile.solution_id.in_(solution_ids)
            ))
            await s.execute(delete(models.RetrievalUnitRow).where(
                models.RetrievalUnitRow.solution_id.in_(solution_ids)
            ))
            await s.execute(delete(models.QuestionSolution).where(
                models.QuestionSolution.id.in_(solution_ids)
            ))
        await s.execute(delete(models.VisualizationRow).where(
            models.VisualizationRow.question_id == question_id
        ))
        await s.execute(delete(models.QuestionStageReview).where(
            models.QuestionStageReview.question_id == question_id
        ))
        await s.execute(delete(models.QuestionRetrievalProfile).where(
            models.QuestionRetrievalProfile.question_id == question_id
        ))
        await s.execute(delete(models.RetrievalUnitRow).where(
            models.RetrievalUnitRow.question_id == question_id
        ))
        await s.execute(delete(models.Question).where(models.Question.id == question_id))


@pytest.mark.asyncio
async def test_delete_question_endpoint_removes_solution_vectors_and_question(monkeypatch):
    marker = f"delete-question-{uuid.uuid4().hex[:8]}"
    question_id: uuid.UUID | None = None
    solution_id: uuid.UUID | None = None
    vs = InMemoryVectorStore()
    monkeypatch.setattr(answer_router, "get_vector_store", lambda: vs)

    try:
        async with session_scope() as s:
            question = models.Question(
                parsed_json=_parsed_payload(marker),
                answer_package_json={"method_pattern": {"name_cn": "待定系数法"}},
                subject="math",
                grade_band="senior",
                difficulty=3,
                dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
                seen_count=1,
                status="answered",
            )
            s.add(question)
            await s.flush()
            question_id = question.id

            solution = models.QuestionSolution(
                question_id=question_id,
                ordinal=1,
                title="解法 1",
                is_current=True,
                status="answered",
                answer_package_json={"method_pattern": {"name_cn": "待定系数法"}},
                visualizations_json=[],
                sediment_json={"retrieval_unit_ids": []},
                stage_reviews_json={},
            )
            s.add(solution)
            await s.flush()
            solution_id = solution.id

            unit = models.RetrievalUnitRow(
                question_id=question_id,
                solution_id=solution_id,
                unit_kind="method",
                title="方法",
                text="方法文本",
                keywords_json=[],
                weight=1.0,
                source_section="method",
            )
            s.add_all([
                models.QuestionRetrievalProfile(
                    question_id=question_id,
                    solution_id=solution_id,
                    profile_json={"query_texts": {"question_full_text": "q", "answer_full_text": "a"}},
                ),
                unit,
            ])
            await s.flush()

            solution_ref = encode_solution_ref(question_id=question_id, solution_id=solution_id)
            await vs.upsert(
                "question_full_emb",
                ref_id=solution_ref,
                vector=[1.0, 0.0],
                subject="math",
                grade_band="senior",
                difficulty=3,
            )
            await vs.upsert(
                "answer_full_emb",
                ref_id=solution_ref,
                vector=[1.0, 0.0],
                subject="math",
                grade_band="senior",
                difficulty=3,
            )
            await vs.upsert(
                "retrieval_unit_emb",
                ref_id=str(unit.id),
                vector=[1.0, 0.0],
                subject="math",
                grade_band="senior",
                difficulty=3,
                unit_kind="method",
            )

        answer_job_service._states[answer_job_service._job_key(question_id, solution_id)] = answer_job_service.JobState(
            question_id=str(question_id),
            solution_id=str(solution_id),
            stage="indexing",
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(f"/api/questions/{question_id}/delete")
            assert r.status_code == 200, r.text

        assert solution_ref not in vs._rows["question_full_emb"]
        assert solution_ref not in vs._rows["answer_full_emb"]
        assert answer_job_service._job_key(question_id, solution_id) not in answer_job_service._states

        async with session_scope() as s:
            assert await s.get(models.Question, question_id) is None
    finally:
        answer_job_service._states.clear()
        answer_job_service._tasks.clear()
        await _cleanup(question_id, [solution_id] if solution_id else None)


@pytest.mark.asyncio
async def test_delete_solution_endpoint_switches_current_solution_and_clears_vectors(monkeypatch):
    marker = f"delete-solution-{uuid.uuid4().hex[:8]}"
    question_id: uuid.UUID | None = None
    old_solution_id: uuid.UUID | None = None
    new_solution_id: uuid.UUID | None = None
    vs = InMemoryVectorStore()
    monkeypatch.setattr(answer_router, "get_vector_store", lambda: vs)

    try:
        async with session_scope() as s:
            question = models.Question(
                parsed_json=_parsed_payload(marker),
                answer_package_json={"method_pattern": {"name_cn": "旧解法"}},
                subject="math",
                grade_band="senior",
                difficulty=3,
                dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
                seen_count=1,
                status="answered",
            )
            s.add(question)
            await s.flush()
            question_id = question.id

            old_solution = models.QuestionSolution(
                question_id=question_id,
                ordinal=1,
                title="解法 1",
                is_current=True,
                status="answered",
                answer_package_json={"method_pattern": {"name_cn": "旧解法"}},
                visualizations_json=[{"id": "viz-old"}],
                sediment_json={"retrieval_unit_ids": []},
                stage_reviews_json={
                    "indexing": {
                        "stage": "indexing",
                        "review_status": "confirmed",
                        "artifact_version": 1,
                        "run_count": 1,
                    },
                },
            )
            new_solution = models.QuestionSolution(
                question_id=question_id,
                ordinal=2,
                title="解法 2",
                is_current=False,
                status="review_solve",
                answer_package_json={"method_pattern": {"name_cn": "新解法"}},
                visualizations_json=[],
                sediment_json=None,
                stage_reviews_json={},
            )
            s.add_all([old_solution, new_solution])
            await s.flush()
            old_solution_id = old_solution.id
            new_solution_id = new_solution.id

            unit = models.RetrievalUnitRow(
                question_id=question_id,
                solution_id=old_solution_id,
                unit_kind="method",
                title="方法",
                text="方法文本",
                keywords_json=[],
                weight=1.0,
                source_section="method",
            )
            s.add_all([
                unit,
                models.VisualizationRow(
                    question_id=question_id,
                    viz_ref="viz-old",
                    title="旧图",
                    caption="旧图",
                    learning_goal="旧图",
                    helpers_used_json=[],
                    jsx_code="",
                    ggb_commands_json=[],
                    params_json=[],
                ),
            ])
            await s.flush()

            old_ref = encode_solution_ref(question_id=question_id, solution_id=old_solution_id)
            await vs.upsert(
                "question_full_emb",
                ref_id=old_ref,
                vector=[1.0, 0.0],
                subject="math",
                grade_band="senior",
                difficulty=3,
            )
            await vs.upsert(
                "answer_full_emb",
                ref_id=old_ref,
                vector=[1.0, 0.0],
                subject="math",
                grade_band="senior",
                difficulty=3,
            )
            await vs.upsert(
                "retrieval_unit_emb",
                ref_id=str(unit.id),
                vector=[1.0, 0.0],
                subject="math",
                grade_band="senior",
                difficulty=3,
                unit_kind="method",
            )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(f"/api/questions/{question_id}/solutions/{old_solution_id}/delete")
            assert r.status_code == 200, r.text

        async with session_scope() as s:
            refreshed_question = await s.get(models.Question, question_id)
            refreshed_new_solution = await s.get(models.QuestionSolution, new_solution_id)
            assert await s.get(models.QuestionSolution, old_solution_id) is None
            assert refreshed_question is not None
            assert refreshed_new_solution is not None
            assert refreshed_new_solution.is_current is True
            assert refreshed_question.answer_package_json == refreshed_new_solution.answer_package_json
            assert refreshed_question.status == refreshed_new_solution.status
            viz_rows = (await s.execute(
                select(models.VisualizationRow).where(models.VisualizationRow.question_id == question_id)
            )).scalars().all()
            assert viz_rows == []

        assert old_ref not in vs._rows["question_full_emb"]
        assert old_ref not in vs._rows["answer_full_emb"]
    finally:
        await _cleanup(question_id, [sid for sid in [old_solution_id, new_solution_id] if sid is not None])


@pytest.mark.asyncio
async def test_clear_solution_index_endpoint_downgrades_status_and_removes_vectors(monkeypatch):
    marker = f"clear-index-{uuid.uuid4().hex[:8]}"
    question_id: uuid.UUID | None = None
    solution_id: uuid.UUID | None = None
    vs = InMemoryVectorStore()
    monkeypatch.setattr(answer_router, "get_vector_store", lambda: vs)

    try:
        async with session_scope() as s:
            question = models.Question(
                parsed_json=_parsed_payload(marker),
                answer_package_json={"method_pattern": {"name_cn": "索引前"}},
                subject="math",
                grade_band="senior",
                difficulty=3,
                dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
                seen_count=1,
                status="answered",
            )
            s.add(question)
            await s.flush()
            question_id = question.id

            solution = models.QuestionSolution(
                question_id=question_id,
                ordinal=1,
                title="解法 1",
                is_current=True,
                status="answered",
                answer_package_json={"method_pattern": {"name_cn": "索引前"}},
                visualizations_json=[],
                sediment_json={"retrieval_unit_ids": []},
                stage_reviews_json={
                    "visualizing": {
                        "stage": "visualizing",
                        "review_status": "confirmed",
                        "artifact_version": 1,
                        "run_count": 1,
                    },
                    "indexing": {
                        "stage": "indexing",
                        "review_status": "confirmed",
                        "artifact_version": 1,
                        "run_count": 1,
                    },
                },
            )
            s.add(solution)
            await s.flush()
            solution_id = solution.id

            s.add(models.QuestionStageReview(
                question_id=question_id,
                stage="indexing",
                review_status="confirmed",
                artifact_version=1,
                run_count=1,
                summary_json={},
                refs_json={"solution_id": str(solution_id)},
            ))
            unit = models.RetrievalUnitRow(
                question_id=question_id,
                solution_id=solution_id,
                unit_kind="method",
                title="方法",
                text="方法文本",
                keywords_json=[],
                weight=1.0,
                source_section="method",
            )
            s.add_all([
                unit,
                models.QuestionRetrievalProfile(
                    question_id=question_id,
                    solution_id=solution_id,
                    profile_json={"query_texts": {"question_full_text": "q", "answer_full_text": "a"}},
                ),
            ])
            await s.flush()

            solution_ref = encode_solution_ref(question_id=question_id, solution_id=solution_id)
            await vs.upsert(
                "question_full_emb",
                ref_id=solution_ref,
                vector=[1.0, 0.0],
                subject="math",
                grade_band="senior",
                difficulty=3,
            )
            await vs.upsert(
                "answer_full_emb",
                ref_id=solution_ref,
                vector=[1.0, 0.0],
                subject="math",
                grade_band="senior",
                difficulty=3,
            )
            await vs.upsert(
                "retrieval_unit_emb",
                ref_id=str(unit.id),
                vector=[1.0, 0.0],
                subject="math",
                grade_band="senior",
                difficulty=3,
                unit_kind="method",
            )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(f"/api/questions/{question_id}/solutions/{solution_id}/index/clear")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["index_cleared"]["retrieval_units_deleted"] == 1
            assert body["index_cleared"]["retrieval_profiles_deleted"] == 1

        async with session_scope() as s:
            refreshed_question = await s.get(models.Question, question_id)
            refreshed_solution = await s.get(models.QuestionSolution, solution_id)
            assert refreshed_question is not None
            assert refreshed_solution is not None
            assert refreshed_solution.sediment_json is None
            assert refreshed_solution.status == "review_viz"
            assert "indexing" not in (refreshed_solution.stage_reviews_json or {})
            assert refreshed_question.status == "review_viz"
            review = (await s.execute(
                select(models.QuestionStageReview)
                .where(models.QuestionStageReview.question_id == question_id)
                .where(models.QuestionStageReview.stage == "indexing")
            )).scalar_one_or_none()
            assert review is None

        assert solution_ref not in vs._rows["question_full_emb"]
        assert solution_ref not in vs._rows["answer_full_emb"]
    finally:
        await _cleanup(question_id, [solution_id] if solution_id else None)
