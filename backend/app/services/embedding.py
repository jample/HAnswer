"""Embedding service (§3.6.2 step 4, §5.4).

Two dense-embedding providers share a `DenseEmbedder` protocol:

  - `EmbeddingService` — Gemini embedding API (default).
  - `BGEM3DenseEmbedder` — local BAAI/bge-m3 dense head (optional dep).

Both normalize output to `list[float]` so callers never branch on
backend. The factory `get_embedding_service()` picks the right one
based on `settings.retrieval.embedder`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.config import settings
from app.services.bge_m3_runtime import get_bge_m3_model
from app.services.llm_client import GeminiClient


class DenseEmbedder(Protocol):
    async def embed_one(self, text: str) -> list[float]: ...
    async def embed_many(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dim(self) -> int: ...


@dataclass
class EmbeddingService:
    """Gemini-backed dense embedder (default)."""

    llm: GeminiClient

    async def embed_one(self, text: str) -> list[float]:
        (vec,) = await self.llm.embed(
            [text],
            model=settings.gemini.model_embed,
            task_type="RETRIEVAL_QUERY",
        )
        return list(vec)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = await self.llm.embed(
            texts,
            model=settings.gemini.model_embed,
            task_type="RETRIEVAL_DOCUMENT",
        )
        return [list(v) for v in vecs]

    @property
    def dim(self) -> int:
        return settings.gemini.embed_dim


class BGEM3DenseEmbedder:
    """Local BAAI/bge-m3 dense embedder — lazy-imports FlagEmbedding."""

    def __init__(self, model=None) -> None:  # type: ignore[no-untyped-def]
        self._model = model

    def _get_model(self):  # type: ignore[no-untyped-def]
        if self._model is None:
            self._model = get_bge_m3_model()
        return self._model

    async def embed_one(self, text: str) -> list[float]:
        rows = await self.embed_many([text])
        return rows[0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        import asyncio

        def _run() -> list[list[float]]:
            model = self._get_model()
            out = model.encode(
                texts, return_dense=True, return_sparse=False,
                return_colbert_vecs=False,
            )
            return [list(map(float, v)) for v in out["dense_vecs"]]

        return await asyncio.to_thread(_run)

    @property
    def dim(self) -> int:
        return settings.retrieval.bge_m3_dense_dim


def build_dense_embedder(llm: GeminiClient) -> DenseEmbedder:
    """Factory used by routers; picks provider per `retrieval.embedder`."""
    if settings.retrieval.embedder == "bge-m3":
        return BGEM3DenseEmbedder()
    return EmbeddingService(llm=llm)
