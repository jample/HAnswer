"""Knowledge detail + admin config endpoint tests (gap fill)."""

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
async def test_kp_detail_lists_related_questions():
    marker = f"kpd-{uuid.uuid4().hex[:8]}"
    async with session_scope() as s:
        kp = models.KnowledgePoint(
            name_cn=marker, path_cached=f"测试>{marker}",
            subject="math", grade_band="senior", status="live", seen_count=2,
        )
        s.add(kp)
        await s.flush()
        kp_id = kp.id

        q = models.Question(
            parsed_json={"question_text": f"{marker}-题面", "topic_path": []},
            answer_package_json=None,
            subject="math", grade_band="senior", difficulty=2,
            dedup_hash=hashlib.sha1(marker.encode()).hexdigest(),
            seen_count=1, status="answered",
        )
        s.add(q)
        await s.flush()
        qid = q.id

        s.add(models.QuestionKPLink(question_id=qid, kp_id=kp_id, weight=0.9))

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get(f"/api/knowledge/kp/{kp_id}/detail")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["kp"]["name_cn"] == marker
            qs = body["questions"]
            assert any(x["question_id"] == str(qid) for x in qs)
            found = next(x for x in qs if x["question_id"] == str(qid))
            assert abs(found["weight"] - 0.9) < 1e-6

            # 404 for unknown kp
            r404 = await c.get(f"/api/knowledge/kp/{uuid.uuid4()}/detail")
            assert r404.status_code == 404
    finally:
        async with session_scope() as s:
            await s.execute(delete(models.QuestionKPLink).where(
                models.QuestionKPLink.kp_id == kp_id))
            await s.execute(delete(models.Question).where(models.Question.id == qid))
            await s.execute(delete(models.KnowledgePoint).where(
                models.KnowledgePoint.id == kp_id))
            await s.commit()


@pytest.mark.asyncio
async def test_admin_config_masks_secrets():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/admin/config")
        assert r.status_code == 200, r.text
        body = r.json()

        g = body["gemini"]
        assert "api_key_masked" in g
        assert "api_key" not in g          # never leak cleartext
        assert "model_parser" in g and "model_solver" in g
        assert "model_vizcoder" in g and "model_embed" in g

        # DSN password (if any) must not appear in cleartext.
        assert "dsn_masked" in body["postgres"]
        assert "password" not in body["postgres"]["dsn_masked"].lower()

        # Retrieval config echoed; required keys present.
        r_cfg = body["retrieval"]
        for k in ("embedder", "sparse_encoder", "multi_route", "rrf_k", "active_dense_dim"):
            assert k in r_cfg

        viz_cfg = body["viz"]
        assert viz_cfg["default_engine"] in {"jsxgraph", "geogebra"}
