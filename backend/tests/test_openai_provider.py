from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import get_settings, settings
from app.services import llm_deps
from app.services.embedding_deps import get_embedding_client, set_embedding_client
from app.services.llm_client import LLMError, TransientLLMError
from app.services.openai_transport import OpenAIResponsesTransport, _looks_transient_error


class _FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text='{"ok": true}',
            usage=SimpleNamespace(input_tokens=3, output_tokens=4),
        )


class _FakeEmbeddings:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=0, embedding=[3.0, 4.0]),
                SimpleNamespace(index=1, embedding=[0.0, 5.0]),
            ]
        )


class _FakeAsyncOpenAI:
    created: list[dict] = []
    last: _FakeAsyncOpenAI | None = None

    def __init__(self, **kwargs) -> None:
        self.created.append(kwargs)
        self.responses = _FakeResponses()
        self.embeddings = _FakeEmbeddings()
        _FakeAsyncOpenAI.last = self


class _DeniedEmbeddingError(Exception):
    status_code = 403


class _DeniedEmbeddings:
    async def create(self, **kwargs):
        raise _DeniedEmbeddingError("Access denied: model is not authorized")


class _DeniedEmbeddingAsyncOpenAI:
    def __init__(self, **kwargs) -> None:
        self.responses = _FakeResponses()
        self.embeddings = _DeniedEmbeddings()


class _MisroutedEmbeddings:
    async def create(self, **kwargs):
        return SimpleNamespace(object="response", model="gpt-5.4")


class _MisroutedEmbeddingAsyncOpenAI:
    def __init__(self, **kwargs) -> None:
        self.responses = _FakeResponses()
        self.embeddings = _MisroutedEmbeddings()


def test_embedding_config_uses_emb_env_names(monkeypatch):
    monkeypatch.setenv("EMB_URL", "https://embed-env.test/v1")
    monkeypatch.setenv("EMB_API_KEY", "embed-env-key")
    monkeypatch.setenv("EMBEDDING_ENDPOINT", "https://old-env.test/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "old-env-key")
    get_settings.cache_clear()
    try:
        loaded = get_settings()
    finally:
        get_settings.cache_clear()

    assert loaded.embedding.endpoint == "https://embed-env.test/v1"
    assert loaded.embedding.api_key == "embed-env-key"


@pytest.mark.asyncio
async def test_openai_transport_uses_env_backed_base_url_and_responses(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    monkeypatch.setattr(settings.openai, "api_key", "test-key")
    monkeypatch.setattr(settings.openai, "base_url", "https://example.test/v1")

    transport = OpenAIResponsesTransport()

    assert _FakeAsyncOpenAI.created[-1] == {
        "api_key": "test-key",
        "base_url": "https://example.test/v1",
    }

    raw, ptok, ctok = await transport.generate_json(
        model="gpt-5.4-pro",
        messages=[
            {"role": "system", "content": "You are AI"},
            {
                "role": "user",
                "parts": [
                    {"text": "parse this"},
                    {"inline_data": {"mime_type": "image/png", "data": "AAAA"}},
                ],
            },
        ],
        response_schema={"type": "object"},
        timeout_s=10,
    )
    call = _FakeAsyncOpenAI.last.responses.calls[-1]
    assert raw == '{"ok": true}'
    assert (ptok, ctok) == (3, 4)
    assert call["model"] == "gpt-5.4-pro"
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["input"][1]["content"][1]["type"] == "input_image"
    assert call["input"][1]["content"][1]["image_url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_openai_embeddings_use_configured_dimensions_and_normalize(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    monkeypatch.setattr(settings.openai, "api_key", "test-key")
    monkeypatch.setattr(settings.openai, "embed_dim", 2)

    transport = OpenAIResponsesTransport()
    vectors = await transport.embed(model="text-embedding-3-large", texts=["a", "b"])

    call = _FakeAsyncOpenAI.last.embeddings.calls[-1]
    assert call["dimensions"] == 2
    assert vectors == [[0.6, 0.8], [0.0, 1.0]]


@pytest.mark.asyncio
async def test_embedding_client_uses_dedicated_openai_config(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    monkeypatch.setattr(settings.embedding, "provider", "openai")
    monkeypatch.setattr(settings.embedding, "api_key", "embed-key")
    monkeypatch.setattr(settings.embedding, "endpoint", "https://embed.test/v1")
    monkeypatch.setattr(settings.embedding, "model", "text-embedding-3-small")
    monkeypatch.setattr(settings.embedding, "dimensions", 7)
    set_embedding_client(None)
    try:
        client = get_embedding_client()
        await client.embed(["a", "b"])
    finally:
        set_embedding_client(None)

    assert _FakeAsyncOpenAI.created[-1] == {
        "api_key": "embed-key",
        "base_url": "https://embed.test/v1",
    }
    call = _FakeAsyncOpenAI.last.embeddings.calls[-1]
    assert call["model"] == "text-embedding-3-small"
    assert call["dimensions"] == 7


@pytest.mark.asyncio
async def test_embedding_client_normalizes_azure_deployment_endpoint(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    monkeypatch.setattr(settings.embedding, "provider", "openai")
    monkeypatch.setattr(settings.embedding, "api_key", "embed-key")
    monkeypatch.setattr(
        settings.embedding,
        "endpoint",
        "https://yaoai.openai.azure.com/openai/deployments/"
        "text-embedding-3-large/embeddings?api-version=2023-05-15",
    )
    monkeypatch.setattr(settings.embedding, "model", "text-embedding-3-large")
    monkeypatch.setattr(settings.embedding, "dimensions", 1536)
    set_embedding_client(None)
    try:
        client = get_embedding_client()
        await client.embed(["hello"])
    finally:
        set_embedding_client(None)

    assert _FakeAsyncOpenAI.created[-1] == {
        "api_key": "embed-key",
        "base_url": "https://yaoai.openai.azure.com/openai/v1/",
    }
    call = _FakeAsyncOpenAI.last.embeddings.calls[-1]
    assert call["model"] == "text-embedding-3-large"


@pytest.mark.asyncio
async def test_openai_embedding_permission_error_is_llm_error(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _DeniedEmbeddingAsyncOpenAI)
    monkeypatch.setattr(settings.openai, "api_key", "test-key")

    transport = OpenAIResponsesTransport()

    with pytest.raises(LLMError, match="OpenAI embedding request failed"):
        await transport.embed(model="text-embedding-3-large", texts=["a"])


@pytest.mark.asyncio
async def test_openai_embedding_non_embedding_response_is_llm_error(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _MisroutedEmbeddingAsyncOpenAI)
    monkeypatch.setattr(settings.openai, "api_key", "test-key")

    transport = OpenAIResponsesTransport()

    with pytest.raises(LLMError, match="did not return embedding data"):
        await transport.embed(model="text-embedding-v3", texts=["a"])


def test_llm_deps_selects_openai_without_gemini_fallback(monkeypatch):
    class _OpenAITransport:
        pass

    class _GeminiTransport:
        def __init__(self) -> None:
            raise AssertionError("Gemini transport should not be constructed")

    monkeypatch.setattr(settings.llm, "provider", "openai")
    monkeypatch.setattr(llm_deps, "OpenAIResponsesTransport", _OpenAITransport)
    monkeypatch.setattr(llm_deps, "GoogleGeminiTransport", _GeminiTransport)
    llm_deps.set_llm_client(None)
    try:
        client = llm_deps.get_llm_client()
        assert isinstance(client.transport, _OpenAITransport)
    finally:
        llm_deps.set_llm_client(None)


def test_openai_transport_treats_remote_protocol_close_as_transient():
    import httpx

    exc = httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body "
        "(incomplete chunked read)"
    )

    assert _looks_transient_error(exc)


@pytest.mark.asyncio
async def test_openai_stream_protocol_error_is_mapped_to_transient(monkeypatch):
    import httpx
    import openai

    class _ProtocolErrorStream:
        def __init__(self) -> None:
            self.index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.index == 0:
                self.index += 1
                return SimpleNamespace(type="response.output_text.delta", delta='{"ok"')
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body "
                "(incomplete chunked read)"
            )

    class _StreamingResponses:
        async def create(self, **kwargs):
            assert kwargs["stream"] is True
            return _ProtocolErrorStream()

    class _StreamingAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = _StreamingResponses()

    monkeypatch.setattr(openai, "AsyncOpenAI", _StreamingAsyncOpenAI)
    monkeypatch.setattr(settings.openai, "api_key", "test-key")

    transport = OpenAIResponsesTransport()
    chunks: list[str] = []

    with pytest.raises(TransientLLMError):
        async for chunk in transport.generate_json_stream_iter(
            model="gpt-5.4-pro",
            messages=[{"role": "user", "content": "x"}],
            response_schema={"type": "object"},
            timeout_s=10,
        ):
            chunks.append(chunk.text)

    assert chunks == ['{"ok"']
