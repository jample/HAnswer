"""Knowledge router tests (M6)."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import models
from app.db.session import session_scope
from app.main import app


@pytest.mark.asyncio
async def test_promote_and_reject_flow():
    """Create a pending KP directly, then hit /promote + /reject via HTTP."""
    marker = f"knowrouter-{uuid.uuid4().hex[:8]}"
    async with session_scope() as s:
        kp = models.KnowledgePoint(
            name_cn=marker,
            path_cached=f"临时>{marker}",
            subject="math",
            grade_band="junior",
            status="pending",
            seen_count=1,
        )
        s.add(kp)
        await s.flush()
        kp_id = str(kp.id)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/api/knowledge/promote",
                json={"kind": "kp", "id": kp_id},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "live"

            # Reject removes it entirely.
            r = await c.post(
                "/api/knowledge/reject",
                json={"kind": "kp", "id": kp_id},
            )
            assert r.status_code == 200

        # Verify it's really gone.
        async with session_scope() as s:
            gone = (await s.execute(
                select(models.KnowledgePoint).where(models.KnowledgePoint.name_cn == marker)
            )).scalar_one_or_none()
            assert gone is None
    finally:
        # Defensive cleanup if the reject step didn't run.
        async with session_scope() as s:
            await s.execute(
                delete(models.KnowledgePoint).where(models.KnowledgePoint.name_cn == marker)
            )


@pytest.mark.asyncio
async def test_merge_repoints_links():
    """Merge a pending pattern into a live one; check links move over."""
    marker = f"mergerouter-{uuid.uuid4().hex[:8]}"
    dedup = f"{marker}-dedup"
    async with session_scope() as s:
        pending = models.MethodPatternRow(
            name_cn=f"{marker}-pending",
            subject="math", grade_band="senior",
            when_to_use="临时", procedure_json=[],
            status="pending", seen_count=3,
        )
        live = models.MethodPatternRow(
            name_cn=f"{marker}-live",
            subject="math", grade_band="senior",
            when_to_use="规范",
            procedure_json=["step"], status="live", seen_count=10,
        )
        q = models.Question(
            parsed_json={
                "subject": "math", "grade_band": "senior",
                "topic_path": [], "question_text": marker,
                "given": [], "find": [], "diagram_description": "",
                "difficulty": 2, "tags": [], "confidence": 0.9,
            },
            subject="math", grade_band="senior", difficulty=2,
            dedup_hash=dedup, status="parsed",
        )
        s.add_all([pending, live, q])
        await s.flush()
        s.add(models.QuestionPatternLink(
            question_id=q.id, pattern_id=pending.id, weight=1.0,
        ))
        await s.flush()
        pending_id, live_id, qid = str(pending.id), str(live.id), q.id

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/api/knowledge/merge",
                json={"kind": "pattern", "from_id": pending_id, "into_id": live_id},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["into"]["seen_count"] == 13  # 10 + 3

        async with session_scope() as s:
            gone = await s.get(models.MethodPatternRow, uuid.UUID(pending_id))
            assert gone is None
            links = (await s.execute(
                select(models.QuestionPatternLink).where(
                    models.QuestionPatternLink.question_id == qid
                )
            )).scalars().all()
            assert len(links) == 1
            assert str(links[0].pattern_id) == live_id
    finally:
        async with session_scope() as s:
            await s.execute(delete(models.QuestionPatternLink).where(
                models.QuestionPatternLink.question_id == qid
            ))
            await s.execute(delete(models.Question).where(models.Question.id == qid))
            await s.execute(delete(models.MethodPatternRow).where(
                models.MethodPatternRow.name_cn.in_(
                    [f"{marker}-pending", f"{marker}-live"]
                )
            ))
