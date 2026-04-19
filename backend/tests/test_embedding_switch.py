from __future__ import annotations

import pytest

from app.services.embedding import EmbeddingService
from app.services.gemini_transport import GoogleGeminiTransport
from app.services.llm_client import FakeTransport, GeminiClient


class _TaskTypeTransport(FakeTransport):
    def __init__(self) -> None:
        super().__init__()
        self.embed_calls: list[dict] = []

    async def embed(self, *, model, texts, task_type=None):
        self.embed_calls.append(
            {"model": model, "texts": list(texts), "task_type": task_type},
        )
        return [[0.1, 0.2] for _ in texts]


@pytest.mark.asyncio
async def test_embedding_service_uses_v2_task_prefixes():
    """gemini-embedding-2-preview: task_type is None, text gets prefixed."""
    transport = _TaskTypeTransport()
    llm = GeminiClient(transport)
    svc = EmbeddingService(llm=llm)

    await svc.embed_one("查询文本")
    await svc.embed_many(["文档一", "文档二"])

    # v2 model: task_type should be None (prefixes are in the text)
    assert transport.embed_calls[0]["task_type"] is None
    assert transport.embed_calls[1]["task_type"] is None
    # Query text should have search prefix
    assert transport.embed_calls[0]["texts"] == ["task: search result | query: 查询文本"]
    # Document texts should have document prefix
    assert transport.embed_calls[1]["texts"] == [
        "title: none | text: 文档一",
        "title: none | text: 文档二",
    ]


@pytest.mark.asyncio
async def test_text_embedding_004_uses_legacy_sdk_path():
    transport = GoogleGeminiTransport()

    class _Legacy:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def configure(self, **kwargs) -> None:
            self.calls.append({"configure": kwargs})

        def embed_content(self, **kwargs):
            self.calls.append(kwargs)
            return {"embedding": [[1.0, 2.0], [3.0, 4.0]]}

    legacy = _Legacy()
    transport._legacy_genai = legacy

    out = await transport.embed(
        model="text-embedding-004",
        texts=["文档A", "文档B"],
        task_type="RETRIEVAL_DOCUMENT",
    )

    assert out == [[1.0, 2.0], [3.0, 4.0]]
    assert legacy.calls[1]["model"] == "models/text-embedding-004"
    assert legacy.calls[1]["task_type"] == "RETRIEVAL_DOCUMENT"


@pytest.mark.asyncio
async def test_text_embedding_004_falls_back_when_unavailable():
    transport = GoogleGeminiTransport()

    async def _boom(*, model, texts, task_type):
        raise RuntimeError("404 not found for embedContent")

    class _Resp:
        def __init__(self) -> None:
            self.embeddings = [type("Emb", (), {"values": [0.5, 0.6]})()]

    class _Models:
        async def embed_content(self, **kwargs):
            return _Resp()

    class _Aio:
        def __init__(self) -> None:
            self.models = _Models()

    class _Client:
        def __init__(self) -> None:
            self.aio = _Aio()

    transport._legacy_embed = _boom  # type: ignore[method-assign]
    transport._client = _Client()  # type: ignore[assignment]

    out = await transport.embed(
        model="text-embedding-004",
        texts=["查询"],
        task_type="RETRIEVAL_QUERY",
    )

    # Fallback path goes through the v2 transport which renormalizes the
    # MRL-truncated prefix; expect L2 norm ~= 1.
    assert len(out) == 1 and len(out[0]) == 2
    norm = (out[0][0] ** 2 + out[0][1] ** 2) ** 0.5
    assert abs(norm - 1.0) < 1e-6
    assert transport._text_embedding_004_available is False
