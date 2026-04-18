"""Admin LLM cost router test (M8)."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.db import models
from app.db.session import session_scope
from app.main import app


@pytest.mark.asyncio
async def test_llm_cost_aggregates():
    marker = f"lc-{uuid.uuid4().hex[:8]}"
    rows = [
        models.LLMCall(
            task=marker, prompt_version="v1.0",
            model="gemini-2.0-flash",
            prompt_tokens=100, completion_tokens=200, cost_usd=0.0010,
            latency_ms=300, status="ok",
        ),
        models.LLMCall(
            task=marker, prompt_version="v1.0",
            model="gemini-2.0-flash",
            prompt_tokens=50, completion_tokens=60, cost_usd=0.0004,
            latency_ms=500, status="repaired",
        ),
        models.LLMCall(
            task=marker, prompt_version="v1.0",
            model="gemini-2.0-flash",
            prompt_tokens=10, completion_tokens=0, cost_usd=0.0,
            latency_ms=100, status="error", error="boom",
        ),
    ]

    async with session_scope() as s:
        for r in rows:
            s.add(r)
        await s.flush()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/api/admin/llm-cost?days=7")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["window_days"] == 7
            # Totals include ALL rows in window (not just ours) — so just
            # assert our marker's aggregate is present in `by_prompt`.
            mine = [p for p in body["by_prompt"] if p["task"] == marker]
            assert len(mine) == 1
            agg = mine[0]
            assert agg["calls"] == 3
            assert agg["prompt_tokens"] == 160
            assert agg["completion_tokens"] == 260
            assert abs(agg["cost_usd"] - 0.0014) < 1e-9
            # Global totals must include at least our contribution.
            assert body["totals"]["calls"] >= 3
            assert body["totals"]["error"] >= 1
            assert body["totals"]["repaired"] >= 1
            # by_day non-empty.
            assert len(body["by_day"]) >= 1
    finally:
        async with session_scope() as s:
            await s.execute(delete(models.LLMCall).where(models.LLMCall.task == marker))
            await s.commit()
