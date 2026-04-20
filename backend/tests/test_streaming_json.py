"""Tests for the incremental JSON streaming parser and the
GeminiClient.call_structured_streaming + solver_service streaming
integration.
"""

from __future__ import annotations

import json

import pytest

from app.config import settings
from app.services.streaming_json import TopLevelStreamParser


def _stream_in_chunks(text: str, sizes: list[int]) -> list[tuple[str, object]]:
    parser = TopLevelStreamParser()
    out: list[tuple[str, object]] = []
    i = 0
    for n in sizes:
        chunk = text[i : i + n]
        i += n
        for pair in parser.feed(chunk):
            out.append(pair)
    # flush any remainder
    if i < len(text):
        for pair in parser.feed(text[i:]):
            out.append(pair)
    return out


def test_parser_emits_simple_object_in_order():
    obj = {"a": 1, "b": "two", "c": [1, 2], "d": {"x": True}, "e": None}
    text = json.dumps(obj)
    out = _stream_in_chunks(text, [1, 2, 3, 5, 8, 13, 21, 34, 55, 89])
    assert [k for k, _ in out] == ["a", "b", "c", "d", "e"]
    assert dict(out) == obj


def test_parser_handles_unicode_and_escapes():
    obj = {
        "title": "中文标题",
        "math": "x \\le y \"quoted\"",
        "list": ["α", "β", "γ"],
    }
    text = json.dumps(obj, ensure_ascii=False)
    out = _stream_in_chunks(text, [3] * 50)
    assert dict(out) == obj


def test_parser_emits_value_for_each_top_level_key_only_once():
    text = json.dumps({"a": 1, "b": 2, "c": 3})
    parser = TopLevelStreamParser()
    out: list[tuple[str, object]] = list(parser.feed(text))
    assert [k for k, _ in out] == ["a", "b", "c"]
    # Re-feeding empty string should not re-emit
    out2 = list(parser.feed(""))
    assert out2 == []


def test_parser_handles_byte_by_byte_streaming():
    text = json.dumps({"a": [1, 2, 3], "b": {"x": 1, "y": 2}})
    parser = TopLevelStreamParser()
    out: list[tuple[str, object]] = []
    for ch in text:
        out.extend(parser.feed(ch))
    assert dict(out) == {"a": [1, 2, 3], "b": {"x": 1, "y": 2}}


# ── GeminiClient streaming integration ──────────────────────────────


@pytest.mark.asyncio
async def test_call_structured_streaming_yields_tuples_then_model():
    from pydantic import BaseModel

    from app.prompts.base import PromptTemplate
    from app.services.llm_client import FakeTransport, GeminiClient

    class _Model(BaseModel):
        a: int
        b: str

    class _Tpl(PromptTemplate):
        name = "test"
        version = "v0"
        purpose = "test"
        input_description = "x"
        output_description = "y"
        design_decisions: list = []

        def system_message(self, **kwargs):
            return "sys"

        def user_message(self, **kwargs):
            return "x"

        @property
        def schema(self):
            return {"type": "object"}

    transport = FakeTransport(
        json_by_model={"m": json.dumps({"a": 1, "b": "hi"})}
    )
    client = GeminiClient(transport)

    items: list = []
    async for item in client.call_structured_streaming(
        template=_Tpl(),
        model="m",
        model_cls=_Model,
    ):
        items.append(item)

    # Expect (key, value) tuples followed by a validated model.
    assert ("a", 1) in items
    assert ("b", "hi") in items
    assert isinstance(items[-1], _Model)
    assert items[-1].a == 1 and items[-1].b == "hi"


@pytest.mark.asyncio
async def test_call_structured_streaming_falls_back_on_validation_error():
    from pydantic import BaseModel

    from app.prompts.base import PromptTemplate
    from app.services.llm_client import FakeTransport, GeminiClient

    class _Model(BaseModel):
        a: int
        b: str

    class _Tpl(PromptTemplate):
        name = "test"
        version = "v0"
        purpose = "test"
        input_description = "x"
        output_description = "y"
        design_decisions: list = []

        def system_message(self, **kwargs):
            return "sys"

        def user_message(self, **kwargs):
            return "x"

        @property
        def schema(self):
            return {"type": "object"}

    # First (streaming) call returns a JSON missing required field "b".
    # Repair fallback (call_structured / generate_json) returns a valid one.
    bad = json.dumps({"a": 1})
    good = json.dumps({"a": 1, "b": "ok"})

    class _Toggling(FakeTransport):
        def __init__(self):
            super().__init__()
            self._calls = 0

        async def generate_json(self, *, model, messages, response_schema, timeout_s):
            self._calls += 1
            return good, 0, 0

        async def generate_json_stream_iter(
            self, *, model, messages, response_schema, timeout_s,
        ):
            from app.services.llm_client import StreamChunk
            yield StreamChunk(text=bad)

    old_repairs = settings.llm.max_repair_attempts
    settings.llm.max_repair_attempts = 1
    try:
        transport = _Toggling()
        client = GeminiClient(transport)
        items: list = []
        async for item in client.call_structured_streaming(
            template=_Tpl(),
            model="m",
            model_cls=_Model,
        ):
            items.append(item)
    finally:
        settings.llm.max_repair_attempts = old_repairs

    # The streaming path emits (key, value) tuples for "a" only, then
    # falls back to bulk repair which yields a validated model.
    assert isinstance(items[-1], _Model)
    assert items[-1].a == 1 and items[-1].b == "ok"


@pytest.mark.asyncio
async def test_call_structured_streaming_falls_back_on_transient_stream_error():
    from pydantic import BaseModel

    from app.prompts.base import PromptTemplate
    from app.services.llm_client import FakeTransport, GeminiClient, StreamChunk, TransientLLMError

    class _Model(BaseModel):
        a: int
        b: str

    class _Tpl(PromptTemplate):
        name = "test"
        version = "v0"
        purpose = "test"
        input_description = "x"
        output_description = "y"
        design_decisions: list = []

        def system_message(self, **kwargs):
            return "sys"

        def user_message(self, **kwargs):
            return "x"

        @property
        def schema(self):
            return {"type": "object"}

    good = json.dumps({"a": 1, "b": "ok"})

    class _Flaky(FakeTransport):
        def __init__(self):
            super().__init__()
            self.bulk_calls = 0

        async def generate_json(self, *, model, messages, response_schema, timeout_s):
            self.bulk_calls += 1
            return good, 0, 0

        async def generate_json_stream_iter(
            self, *, model, messages, response_schema, timeout_s,
        ):
            yield StreamChunk(text='{"a":1')
            raise TransientLLMError("stream stalled")

    old_retries = settings.llm.max_retries
    settings.llm.max_retries = 1
    try:
        transport = _Flaky()
        client = GeminiClient(transport)
        items: list = []
        async for item in client.call_structured_streaming(
            template=_Tpl(),
            model="m",
            model_cls=_Model,
        ):
            items.append(item)
    finally:
        settings.llm.max_retries = old_retries

    assert transport.bulk_calls == 1
    assert isinstance(items[-1], _Model)
    assert items[-1].a == 1 and items[-1].b == "ok"


@pytest.mark.asyncio
async def test_call_structured_stream_flag_falls_back_to_non_stream_on_transient_error():
    from pydantic import BaseModel

    from app.prompts.base import PromptTemplate
    from app.services.llm_client import FakeTransport, GeminiClient, TransientLLMError

    class _Model(BaseModel):
        a: int
        b: str

    class _Tpl(PromptTemplate):
        name = "test"
        version = "v0"
        purpose = "test"
        input_description = "x"
        output_description = "y"
        design_decisions: list = []

        def system_message(self, **kwargs):
            return "sys"

        def user_message(self, **kwargs):
            return "x"

        @property
        def schema(self):
            return {"type": "object"}

    class _Transport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.stream_calls = 0
            self.bulk_calls = 0

        async def generate_json_stream(self, *, model, messages, response_schema, timeout_s):
            self.stream_calls += 1
            raise TransientLLMError("stream timeout")

        async def generate_json(self, *, model, messages, response_schema, timeout_s):
            self.bulk_calls += 1
            return json.dumps({"a": 1, "b": "ok"}), 0, 0

    old_retries = settings.llm.max_retries
    settings.llm.max_retries = 1
    try:
        client = GeminiClient(_Transport())
        parsed = await client.call_structured(
            template=_Tpl(),
            model="m",
            model_cls=_Model,
            stream=True,
        )
    finally:
        settings.llm.max_retries = old_retries

    assert parsed.a == 1
    assert parsed.b == "ok"


@pytest.mark.asyncio
async def test_call_structured_stream_flag_does_not_fallback_when_retries_disabled():
    from pydantic import BaseModel

    from app.prompts.base import PromptTemplate
    from app.services.llm_client import FakeTransport, GeminiClient, TransientLLMError

    class _Model(BaseModel):
        a: int
        b: str

    class _Tpl(PromptTemplate):
        name = "test"
        version = "v0"
        purpose = "test"
        input_description = "x"
        output_description = "y"
        design_decisions: list = []

        def system_message(self, **kwargs):
            return "sys"

        def user_message(self, **kwargs):
            return "x"

        @property
        def schema(self):
            return {"type": "object"}

    class _Transport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.stream_calls = 0
            self.bulk_calls = 0

        async def generate_json_stream(self, *, model, messages, response_schema, timeout_s):
            self.stream_calls += 1
            raise TransientLLMError("stream timeout")

        async def generate_json(self, *, model, messages, response_schema, timeout_s):
            self.bulk_calls += 1
            return json.dumps({"a": 1, "b": "ok"}), 0, 0

    old_retries = settings.llm.max_retries
    settings.llm.max_retries = 0
    try:
        transport = _Transport()
        client = GeminiClient(transport)
        with pytest.raises(TransientLLMError):
            await client.call_structured(
                template=_Tpl(),
                model="m",
                model_cls=_Model,
                stream=True,
            )
    finally:
        settings.llm.max_retries = old_retries

    assert transport.stream_calls == 1
    assert transport.bulk_calls == 0


@pytest.mark.asyncio
async def test_call_structured_retries_configured_number_before_success():
    from pydantic import BaseModel

    from app.prompts.base import PromptTemplate
    from app.services.llm_client import FakeTransport, GeminiClient, TransientLLMError

    class _Model(BaseModel):
        a: int

    class _Tpl(PromptTemplate):
        name = "test"
        version = "v0"
        purpose = "test"
        input_description = "x"
        output_description = "y"
        design_decisions: list = []

        def system_message(self, **kwargs):
            return "sys"

        def user_message(self, **kwargs):
            return "x"

        @property
        def schema(self):
            return {"type": "object"}

    class _Transport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.calls_count = 0

        async def generate_json(self, *, model, messages, response_schema, timeout_s):
            self.calls_count += 1
            if self.calls_count <= settings.llm.max_retries:
                raise TransientLLMError("503 UNAVAILABLE This model is currently experiencing high demand.")
            return json.dumps({"a": 1}), 0, 0

    transport = _Transport()
    client = GeminiClient(transport)
    parsed = await client.call_structured(
        template=_Tpl(),
        model="m",
        model_cls=_Model,
        stream=False,
    )

    assert parsed.a == 1
    assert transport.calls_count == settings.llm.max_retries + 1


@pytest.mark.asyncio
async def test_call_structured_streaming_does_not_repair_when_disabled():
    from pydantic import BaseModel

    from app.prompts.base import PromptTemplate
    from app.services.llm_client import FakeTransport, GeminiClient, StreamChunk, LLMError

    class _Model(BaseModel):
        a: int
        b: str

    class _Tpl(PromptTemplate):
        name = "test"
        version = "v0"
        purpose = "test"
        input_description = "x"
        output_description = "y"
        design_decisions: list = []

        def system_message(self, **kwargs):
            return "sys"

        def user_message(self, **kwargs):
            return "x"

        @property
        def schema(self):
            return {"type": "object"}

    class _Transport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.bulk_calls = 0

        async def generate_json(self, *, model, messages, response_schema, timeout_s):
            self.bulk_calls += 1
            return json.dumps({"a": 1, "b": "ok"}), 0, 0

        async def generate_json_stream_iter(self, *, model, messages, response_schema, timeout_s):
            yield StreamChunk(text=json.dumps({"a": 1}))

    old_repairs = settings.llm.max_repair_attempts
    settings.llm.max_repair_attempts = 0
    try:
        transport = _Transport()
        client = GeminiClient(transport)
        with pytest.raises(LLMError, match="streaming output failed validation"):
            async for _ in client.call_structured_streaming(
                template=_Tpl(),
                model="m",
                model_cls=_Model,
            ):
                pass
    finally:
        settings.llm.max_repair_attempts = old_repairs

    assert transport.bulk_calls == 0
