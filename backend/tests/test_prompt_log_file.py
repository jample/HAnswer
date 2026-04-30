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
    LLMError,
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
    response_log_path = tmp_path / "llm_responses.jsonl"
    parser = PromptRegistry.get("parser")
    messages = parser.build_multimodal(b"abc", "image/png", subject_hint="math")
    client = GeminiClient(
        FakeTransport({settings.gemini.model_parser: json.dumps(_VALID_PARSED, ensure_ascii=False)}),
        prompt_logger=JsonlPromptLogger(str(log_path), str(response_log_path)),
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
    assert row["response_content"] is None
    assert row["response_preview"] == json.dumps(_VALID_PARSED, ensure_ascii=False)
    image_part = row["messages"][-1]["parts"][1]["inline_data"]
    assert image_part["mime_type"] == "image/png"
    assert image_part["image_name"] == "sample-question.png"
    assert "data" not in image_part
    assert "YWJj" not in log_path.read_text(encoding="utf-8")
    response_rows = [json.loads(line) for line in response_log_path.read_text(encoding="utf-8").splitlines()]
    assert len(response_rows) == 1
    response_row = response_rows[0]
    assert response_row["request_kind"] == "structured"
    assert response_row["response_content"] == json.dumps(_VALID_PARSED, ensure_ascii=False)
    assert response_row["response_preview"] == json.dumps(_VALID_PARSED, ensure_ascii=False)


@pytest.mark.asyncio
async def test_embed_prompt_log_records_texts(tmp_path):
    log_path = tmp_path / "llm_prompts.jsonl"
    response_log_path = tmp_path / "llm_responses.jsonl"
    client = GeminiClient(
        FakeTransport(),
        prompt_logger=JsonlPromptLogger(str(log_path), str(response_log_path)),
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
    assert row["response_content"] is None
    assert not response_log_path.exists()


class _DeniedEmbedTransport(FakeTransport):
    async def embed(self, *, model, texts, task_type=None) -> list[list[float]]:
        raise RuntimeError("Access denied: model is not authorized")


@pytest.mark.asyncio
async def test_embed_prompt_log_wraps_provider_errors_as_llm_error(tmp_path):
    log_path = tmp_path / "llm_prompts.jsonl"
    response_log_path = tmp_path / "llm_responses.jsonl"
    client = GeminiClient(
        _DeniedEmbedTransport(),
        prompt_logger=JsonlPromptLogger(str(log_path), str(response_log_path)),
    )

    with pytest.raises(LLMError, match="Access denied"):
        await client.embed(
            ["title: 几何 | text: 建立索引"],
            model=settings.openai.model_embed,
            prompt_context=PromptLogContext(phase_description="建立索引"),
        )

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["request_kind"] == "embed"
    assert row["status"] == "error"
    assert "not authorized" in row["error"]
    assert not response_log_path.exists()


@pytest.mark.asyncio
async def test_prompt_log_persists_full_response_content_without_truncating_it(tmp_path):
    log_path = tmp_path / "llm_prompts.jsonl"
    response_log_path = tmp_path / "llm_responses.jsonl"
    parser = PromptRegistry.get("parser")
    long_question = "几何题" * 3000
    long_payload = {
        **_VALID_PARSED,
        "question_text": long_question,
    }
    raw = json.dumps(long_payload, ensure_ascii=False)
    client = GeminiClient(
        FakeTransport({settings.gemini.model_parser: raw}),
        prompt_logger=JsonlPromptLogger(str(log_path), str(response_log_path)),
    )

    parsed = await client.call_structured(
        template=parser,
        model=settings.gemini.model_parser,
        model_cls=ParsedQuestion,
        template_kwargs={"raw_ocr": "dummy"},
    )

    assert parsed.question_text == long_question
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["response_content"] is None
    assert row["response_preview"].endswith("...[truncated]")
    response_rows = [json.loads(line) for line in response_log_path.read_text(encoding="utf-8").splitlines()]
    assert len(response_rows) == 1
    response_row = response_rows[0]
    assert response_row["response_content"] == raw
    assert response_row["response_preview"].endswith("...[truncated]")
    assert len(response_row["response_content"]) > len(response_row["response_preview"])
