"""Ingest pipeline tests (M2, §3.1).

Uses the real local PostgreSQL (§Appendix B) plus FakeTransport so the
Parser side never hits Gemini. Each test rolls back via the SAVEPOINT
pattern in `tests/conftest.py`.

Prereq: `alembic upgrade head` run once against the configured DSN.
"""

from __future__ import annotations

import json

import pytest

from app.config import settings
from app.schemas import ParsedQuestion
from app.services.ingest_service import edit_parsed, ingest_image, rescan_question
from app.services.llm_client import FakeTransport, GeminiClient


_VALID_PARSED = {
    "subject": "math",
    "grade_band": "senior",
    "topic_path": ["几何", "圆"],
    "question_text": "已知点 A(-1, √3), ⊙O 半径 1, 求…",
    "given": ["A=(-1,√3)", "r=1"],
    "find": ["B 是否为 A 关于 ⊙O 的 √3-平移点"],
    "diagram_description": "坐标系中给出单位圆",
    "difficulty": 4,
    "tags": ["新定义", "平移"],
    "confidence": 0.8,
}


def _llm_with(parsed: dict) -> GeminiClient:
    transport = FakeTransport(
        json_by_model={settings.gemini.model_parser: json.dumps(parsed)}
    )
    return GeminiClient(transport)


@pytest.mark.asyncio
async def test_ingest_uses_streaming_parser_when_enabled(session, tmp_image_dir):
    llm = _llm_with(_VALID_PARSED)
    old_flag = settings.llm.stream_parser_json
    settings.llm.stream_parser_json = True
    try:
        await ingest_image(
            session,
            data=b"stream-parser-bytes",
            mime="image/jpeg",
            llm=llm,
            subject_hint="math",
        )
    finally:
        settings.llm.stream_parser_json = old_flag

    assert llm.transport.calls
    assert llm.transport.calls[-1].get("stream") is True


@pytest.mark.asyncio
async def test_ingest_happy_path(session, tmp_image_dir):
    llm = _llm_with(_VALID_PARSED)
    data = b"\xff\xd8\xff\xe0fake-jpeg-bytes-happy"
    result = await ingest_image(
        session, data=data, mime="image/jpeg", llm=llm, subject_hint="math",
    )
    assert not result.deduped
    assert result.question.subject == "math"
    assert result.question.grade_band == "senior"
    assert result.question.difficulty == 4
    assert result.question.dedup_hash == result.image.sha256
    assert any(tmp_image_dir.iterdir())


@pytest.mark.asyncio
async def test_ingest_dedup_on_same_image(session, tmp_image_dir):
    llm = _llm_with(_VALID_PARSED)
    data = b"\xff\xd8\xff\xe0dedup-bytes-xyz"
    first = await ingest_image(session, data=data, mime="image/png", llm=llm)
    assert not first.deduped
    second = await ingest_image(session, data=data, mime="image/png", llm=llm)
    assert second.deduped
    assert second.question.id == first.question.id
    assert second.question.seen_count == 2
    # Parser called only once (second call short-circuited).
    assert len(llm.transport.calls) == 1


@pytest.mark.asyncio
async def test_patch_valid_field(session, tmp_image_dir):
    llm = _llm_with(_VALID_PARSED)
    result = await ingest_image(
        session, data=b"patch-valid-bytes", mime="image/jpeg", llm=llm,
    )
    updated = await edit_parsed(
        session,
        question_id=result.question.id,
        patch={"difficulty": 3, "tags": ["新定义"]},
    )
    assert updated.difficulty == 3
    assert updated.parsed_json["tags"] == ["新定义"]


@pytest.mark.asyncio
async def test_patch_invalid_rejected(session, tmp_image_dir):
    llm = _llm_with(_VALID_PARSED)
    result = await ingest_image(
        session, data=b"patch-invalid-bytes", mime="image/jpeg", llm=llm,
    )
    with pytest.raises(Exception):
        # difficulty out of range must fail ParsedQuestion validation.
        await edit_parsed(
            session, question_id=result.question.id, patch={"difficulty": 9},
        )


def test_parsed_question_pydantic_roundtrip():
    pq = ParsedQuestion.model_validate(_VALID_PARSED)
    assert pq.subject == "math"
    assert pq.find[0].startswith("B")


@pytest.mark.asyncio
async def test_rescan_injects_user_guidance(session, tmp_image_dir):
    llm = _llm_with(_VALID_PARSED)
    result = await ingest_image(
        session, data=b"rescan-guidance-bytes", mime="image/jpeg", llm=llm,
    )

    llm.transport.calls.clear()
    await rescan_question(
        session,
        question_id=result.question.id,
        llm=llm,
        user_guidance="这是面向初中生的题目，请更谨慎地区分已知和所求。",
    )

    assert llm.transport.calls
    assert "messages" in llm.transport.calls[-1]
    assert "初中生" in llm.transport.calls[-1]["messages"][-1]["content"]
