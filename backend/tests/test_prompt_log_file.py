from __future__ import annotations

import json

import pytest

from app.config import settings
from app.prompts import PromptRegistry
from app.schemas import ParsedQuestion
from app.services.llm_client import (
    FakeTransport,
    GeminiClient,
    JsonlPromptLogger,
    PromptLogContext,
)

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


@pytest.mark.asyncio
async def test_structured_prompt_log_sanitizes_inline_image(tmp_path):
    log_path = tmp_path / "llm_prompts.jsonl"
    parser = PromptRegistry.get("parser")
    messages = parser.build_multimodal(b"abc", "image/png", subject_hint="math")
    client = GeminiClient(
        FakeTransport({settings.gemini.model_parser: json.dumps(_VALID_PARSED, ensure_ascii=False)}),
        prompt_logger=JsonlPromptLogger(str(log_path)),
    )

    parsed = await client.call_structured(
        template=parser,
        model=settings.gemini.model_parser,
        model_cls=ParsedQuestion,
        messages_override=messages,
        prompt_context=PromptLogContext(
            phase_description="解析题目",
            image_names=["sample-question.png"],
        ),
    )

    assert parsed.subject == "math"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["request_kind"] == "structured"
    assert row["phase_description"] == "解析题目"
    assert row["image_names"] == ["sample-question.png"]
    image_part = row["messages"][-1]["parts"][1]["inline_data"]
    assert image_part["mime_type"] == "image/png"
    assert image_part["image_name"] == "sample-question.png"
    assert "data" not in image_part
    assert "YWJj" not in log_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_embed_prompt_log_records_texts(tmp_path):
    log_path = tmp_path / "llm_prompts.jsonl"
    client = GeminiClient(
        FakeTransport(),
        prompt_logger=JsonlPromptLogger(str(log_path)),
    )

    vectors = await client.embed(
        ["title: 几何 | text: 已知圆心角求弦长"],
        model=settings.gemini.model_embed,
        prompt_context=PromptLogContext(
            phase_description="建立索引",
            question_id="q-1",
            solution_id="s-1",
        ),
    )

    assert len(vectors) == 1
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["request_kind"] == "embed"
    assert row["phase_description"] == "建立索引"
    assert row["question_id"] == "q-1"
    assert row["solution_id"] == "s-1"
    assert row["texts"] == ["title: 几何 | text: 已知圆心角求弦长"]
