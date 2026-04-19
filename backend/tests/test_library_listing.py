"""Library listing filters and readiness gates."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.db import models
from app.db.session import session_scope
from app.main import app


def _parsed_payload(text: str) -> dict:
    return {
        "subject": "math",
        "grade_band": "senior",
        "topic_path": ["代数", "函数"],
        "question_text": text,
        "given": [],
        "find": [],
        "diagram_description": "",
        "difficulty": 3,
        "tags": [],
        "confidence": 0.9,
    }


@pytest.mark.asyncio
async def test_list_questions_only_returns_learning_ready_items_and_supports_facets():
    marker = f"lib-{uuid.uuid4().hex[:8]}"

    async with session_scope() as s:
        pattern = models.MethodPatternRow(
            name_cn=f"{marker}-待定系数法",
            subject="math",
            grade_band="senior",
            when_to_use="测试",
            procedure_json=["设元"],
            pitfalls_json=[],
            status="live",
            seen_count=3,
        )
        s.add(pattern)
        await s.flush()
        pattern_id = pattern.id

        ready_q = models.Question(
            parsed_json=_parsed_payload(f"{marker} ready"),
            answer_package_json={"method_pattern": {"name_cn": pattern.name_cn}},
            subject="math",
            grade_band="senior",
            difficulty=3,
            dedup_hash=hashlib.sha1(f"{marker}-ready".encode()).hexdigest(),
            seen_count=4,
            status="answered",
        )
        pending_q = models.Question(
            parsed_json=_parsed_payload(f"{marker} pending"),
            answer_package_json={"method_pattern": {"name_cn": pattern.name_cn}},
            subject="math",
            grade_band="senior",
            difficulty=3,
            dedup_hash=hashlib.sha1(f"{marker}-pending".encode()).hexdigest(),
            seen_count=1,
            status="review_index",
        )
        s.add_all([ready_q, pending_q])
        await s.flush()
        ready_qid = ready_q.id
        pending_qid = pending_q.id

        s.add_all([
            models.QuestionPatternLink(question_id=ready_qid, pattern_id=pattern_id, weight=1.0),
            models.QuestionPatternLink(question_id=pending_qid, pattern_id=pattern_id, weight=1.0),
            models.QuestionRetrievalProfile(
                question_id=ready_qid,
                profile_json={
                    "subject": "math",
                    "grade_band": "senior",
                    "textbook_stage": "高二",
                    "topic_path": ["代数", marker, "函数"],
                    "novelty_flags": ["多问"],
                    "object_entities": [],
                    "target_types": ["最值"],
                    "condition_signals": [],
                    "question_focus": [],
                    "answer_focus": [],
                    "method_labels": [pattern.name_cn],
                    "extension_ideas": [],
                    "pitfalls": [],
                    "lexical_aliases": [marker],
                    "query_texts": {
                        "question_full_text": "q",
                        "answer_full_text": "a",
                        "method_text": "m",
                        "step_texts": [],
                        "extension_text": "",
                    },
                },
            ),
            models.QuestionRetrievalProfile(
                question_id=pending_qid,
                profile_json={
                    "subject": "math",
                    "grade_band": "senior",
                    "textbook_stage": "高二",
                    "topic_path": ["代数", marker, "函数"],
                    "novelty_flags": ["多问"],
                    "object_entities": [],
                    "target_types": ["最值"],
                    "condition_signals": [],
                    "question_focus": [],
                    "answer_focus": [],
                    "method_labels": [pattern.name_cn],
                    "extension_ideas": [],
                    "pitfalls": [],
                    "lexical_aliases": [marker],
                    "query_texts": {
                        "question_full_text": "q",
                        "answer_full_text": "a",
                        "method_text": "m",
                        "step_texts": [],
                        "extension_text": "",
                    },
                },
            ),
            models.QuestionStageReview(
                question_id=ready_qid,
                stage="parsed",
                review_status="confirmed",
                artifact_version=1,
                run_count=1,
                summary_json={},
                refs_json={},
            ),
            models.QuestionStageReview(
                question_id=ready_qid,
                stage="solving",
                review_status="confirmed",
                artifact_version=1,
                run_count=1,
                summary_json={},
                refs_json={},
            ),
            models.QuestionStageReview(
                question_id=ready_qid,
                stage="visualizing",
                review_status="confirmed",
                artifact_version=1,
                run_count=1,
                summary_json={},
                refs_json={},
            ),
            models.QuestionStageReview(
                question_id=ready_qid,
                stage="indexing",
                review_status="confirmed",
                artifact_version=1,
                run_count=1,
                summary_json={},
                refs_json={},
            ),
            models.QuestionStageReview(
                question_id=pending_qid,
                stage="parsed",
                review_status="confirmed",
                artifact_version=1,
                run_count=1,
                summary_json={},
                refs_json={},
            ),
            models.QuestionStageReview(
                question_id=pending_qid,
                stage="indexing",
                review_status="pending",
                artifact_version=1,
                run_count=1,
                summary_json={},
                refs_json={},
            ),
        ])

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get(
                "/api/questions",
                params={
                    "learning_ready": "true",
                    "method": pattern.name_cn,
                    "topic": marker,
                    "target_type": "最值",
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            ids = [item["question_id"] for item in body["items"]]
            assert str(ready_qid) in ids
            assert str(pending_qid) not in ids
            item = next(item for item in body["items"] if item["question_id"] == str(ready_qid))
            assert item["learning_ready"] is True
            assert item["pattern_name"] == pattern.name_cn
            assert marker in item["topic_path"]
            assert "最值" in item["target_types"]
            assert pattern.name_cn in body["facets"]["methods"]
    finally:
        async with session_scope() as s:
            await s.execute(delete(models.QuestionStageReview).where(
                models.QuestionStageReview.question_id.in_([ready_qid, pending_qid])
            ))
            await s.execute(delete(models.QuestionRetrievalProfile).where(
                models.QuestionRetrievalProfile.question_id.in_([ready_qid, pending_qid])
            ))
            await s.execute(delete(models.QuestionPatternLink).where(
                models.QuestionPatternLink.question_id.in_([ready_qid, pending_qid])
            ))
            await s.execute(delete(models.Question).where(
                models.Question.id.in_([ready_qid, pending_qid])
            ))
            await s.execute(delete(models.MethodPatternRow).where(
                models.MethodPatternRow.id == pattern_id
            ))
            await s.commit()


@pytest.mark.asyncio
async def test_list_questions_scans_beyond_old_cap_and_supports_offset_pagination():
    marker = f"lib-page-{uuid.uuid4().hex[:8]}"
    created_ids: list[uuid.UUID] = []

    async with session_scope() as s:
        base_time = datetime.now(UTC) - timedelta(hours=1)
        rows: list[models.Question] = []
        for idx in range(30):
            rows.append(models.Question(
                parsed_json=_parsed_payload(f"{marker} match {idx}"),
                answer_package_json=None,
                subject="math",
                grade_band="senior",
                difficulty=3,
                dedup_hash=hashlib.sha1(f"{marker}-match-{idx}".encode()).hexdigest(),
                seen_count=1,
                status="answered",
                created_at=base_time + timedelta(seconds=idx),
            ))
        for idx in range(100):
            rows.append(models.Question(
                parsed_json=_parsed_payload(f"other-noise-{idx}"),
                answer_package_json=None,
                subject="math",
                grade_band="senior",
                difficulty=3,
                dedup_hash=hashlib.sha1(f"{marker}-noise-{idx}".encode()).hexdigest(),
                seen_count=1,
                status="answered",
                created_at=base_time + timedelta(minutes=10, seconds=idx),
            ))
        s.add_all(rows)
        await s.flush()
        created_ids = [row.id for row in rows]

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            first = await c.get(
                "/api/questions",
                params={
                    "q": marker,
                    "learning_ready": "false",
                    "limit": 20,
                    "offset": 0,
                },
            )
            assert first.status_code == 200, first.text
            first_body = first.json()
            assert first_body["count"] == 20
            assert first_body["total_count"] == 30
            assert first_body["has_more"] is True
            assert first_body["next_offset"] == 20
            assert all(marker in item["question_text"] for item in first_body["items"])

            second = await c.get(
                "/api/questions",
                params={
                    "q": marker,
                    "learning_ready": "false",
                    "limit": 20,
                    "offset": 20,
                },
            )
            assert second.status_code == 200, second.text
            second_body = second.json()
            assert second_body["count"] == 10
            assert second_body["total_count"] == 30
            assert second_body["has_more"] is False
            assert second_body["next_offset"] is None
            assert all(marker in item["question_text"] for item in second_body["items"])
    finally:
        async with session_scope() as s:
            await s.execute(delete(models.Question).where(models.Question.id.in_(created_ids)))
            await s.commit()
