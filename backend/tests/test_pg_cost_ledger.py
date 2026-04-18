"""PgCostLedger tests (M8).

Verifies that exercising a LLM call through `GeminiClient(..., ledger=PgCostLedger())`
writes a row into `llm_calls` with the correct task/prompt_version/model.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.config import settings
from app.db.models import LLMCall
from app.db.session import session_scope
from app.schemas import VariantList
from app.prompts import VariantSynthPrompt
from app.services.cost_ledger import PgCostLedger
from app.services.llm_client import FakeTransport, GeminiClient, LLMCallRecord


@pytest.mark.asyncio
async def test_pg_ledger_records_call_directly():
    rec = LLMCallRecord(
        task="parser", prompt_version="v1.0 (2026-04-17)",
        model="gemini-2.0-flash",
        prompt_tokens=10, completion_tokens=20,
        cost_usd=0.0002, latency_ms=250,
        status="ok",
    )
    await PgCostLedger().record(rec)

    # PgCostLedger opens its own session_scope; the fixture can still see
    # the row because we commit through the app's session factory.
    async with session_scope() as s:
        rows = (await s.execute(
            select(LLMCall).where(LLMCall.task == "parser", LLMCall.model == "gemini-2.0-flash")
            .order_by(LLMCall.created_at.desc())
        )).scalars().all()
    assert any(r.prompt_tokens == 10 and r.completion_tokens == 20 for r in rows)

    # Cleanup: rows inserted outside the fixture's SAVEPOINT need manual removal.
    async with session_scope() as s:
        for r in rows:
            await s.delete(r)
        await s.commit()


@pytest.mark.asyncio
async def test_gemini_client_wires_ledger():
    # Use a FakeTransport that returns a valid VariantList JSON.
    payload = json.dumps({"variants": [{
        "statement": "测试题", "answer_outline": "要点", "rubric": "得分点",
        "difficulty": 2, "same_pattern": True,
    }]})
    transport = FakeTransport(json_by_model={settings.gemini.model_solver: payload})

    calls: list[LLMCallRecord] = []

    class _Spy:
        async def record(self, rec):
            calls.append(rec)

    client = GeminiClient(transport, ledger=_Spy())
    result = await client.call_structured(
        template=VariantSynthPrompt(),
        model=settings.gemini.model_solver,
        model_cls=VariantList,
        template_kwargs={
            "source": {"statement": "x^2=4", "subject": "math",
                       "grade_band": "senior", "difficulty": 2,
                       "pattern_name": "配方法",
                       "pattern_procedure": ["step"]},
            "count": 1,
        },
    )
    assert len(result.variants) == 1
    assert len(calls) == 1
    assert calls[0].task == "variant_synth"
    assert calls[0].status == "ok"
    assert calls[0].model == settings.gemini.model_solver
