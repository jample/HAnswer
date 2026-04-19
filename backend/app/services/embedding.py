"""Embedding service (§3.6.2 step 4, §5.4).

Two dense-embedding providers share a `DenseEmbedder` protocol:

  - `EmbeddingService` — Gemini embedding API (default).
  - `BGEM3DenseEmbedder` — local BAAI/bge-m3 dense head (optional dep).

Both normalize output to `list[float]` so callers never branch on
backend. The factory `build_dense_embedder()` picks the right one
based on `settings.retrieval.embedder`.

`gemini-embedding-2-preview` exposes several knobs we use:

  * Per-surface ``task_type`` (CLUSTERING for ontology anchors,
    QUESTION_ANSWERING for answers, etc.) sharpens the geometry of
    each surface; v2 expresses this as a text prefix instead of an
    API parameter.
  * Real ``title`` field — v2 was trained with title/body pairs and
    uses the title as a global anchor; passing the KP path / pattern
    name beats the previous ``title: none`` placeholder.
  * Matryoshka MRL — ``output_dimensionality`` returns the truncated
    prefix and the transport renormalizes so cosine still works.

Long inputs (e.g. full answer texts that exceed the v2 input window)
are NOT silently truncated. ``embed_documents`` splits oversize text
on paragraph / sentence boundaries into windowed chunks, embeds each,
mean-pools the L2-normalized vectors, then renormalizes — yielding a
semantic centroid that loses no content.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from app.config import settings
from app.services.bge_m3_runtime import get_bge_m3_model
from app.services.llm_client import GeminiClient

log = logging.getLogger(__name__)

# Models that use text-prefixed task instructions instead of task_type param
_V2_EMBED_MODELS = {"gemini-embedding-2-preview"}

# Soft per-text limit, well under the v2 hard cap of ~2048 tokens.
# Chinese chars average ~0.6 tokens, mixed text ~0.4, so 6000 chars
# stays inside the window with comfortable headroom.
_MAX_CHARS_PER_CALL = 6000
_CHUNK_OVERLAP_CHARS = 240  # only used as a last-resort sliding window

TaskKind = Literal[
    "RETRIEVAL_QUERY",
    "RETRIEVAL_DOCUMENT",
    "QUESTION_ANSWERING",
    "SEMANTIC_SIMILARITY",
    "CLASSIFICATION",
    "CLUSTERING",
    "FACT_VERIFICATION",
]

# v2 prefixes per Gemini docs. Keys are TaskKind values.
_V2_PREFIX = {
    "RETRIEVAL_QUERY": "task: search result | query: ",
    "QUESTION_ANSWERING": "task: question answering | query: ",
    "SEMANTIC_SIMILARITY": "task: sentence similarity | query: ",
    "CLASSIFICATION": "task: classification | query: ",
    "CLUSTERING": "task: clustering | query: ",
    "FACT_VERIFICATION": "task: fact checking | query: ",
}


@dataclass
class EmbedItem:
    """One semantic surface to embed.

    ``task_kind`` selects the embedding head; ``title`` is forwarded to
    the v2 ``title:`` slot for RETRIEVAL_DOCUMENT (ignored for other
    kinds, which use ``query:`` framing instead).
    """

    text: str
    task_kind: TaskKind = "RETRIEVAL_DOCUMENT"
    title: str = ""


@dataclass
class _Chunk:
    item_idx: int
    text: str


class DenseEmbedder(Protocol):
    async def embed_one(self, text: str) -> list[float]: ...
    async def embed_many(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_documents(self, items: list[EmbedItem]) -> list[list[float]]: ...
    @property
    def dim(self) -> int: ...


# ── helpers ─────────────────────────────────────────────────────────


def _is_v2_model() -> bool:
    return settings.gemini.model_embed in _V2_EMBED_MODELS


def _format_v2_payload(*, text: str, task_kind: TaskKind, title: str) -> str:
    """Build the prompt text for v2 models."""
    if task_kind == "RETRIEVAL_DOCUMENT":
        title_slot = title.strip() or "none"
        return f"title: {title_slot} | text: {text}"
    prefix = _V2_PREFIX.get(task_kind, _V2_PREFIX["RETRIEVAL_QUERY"])
    return prefix + text


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p and p.strip()]


def _split_sentences(text: str) -> list[str]:
    # Cover Chinese + English terminators.
    pieces = re.split(r"(?<=[。！？.!?；;])\s+", text)
    return [p for p in (s.strip() for s in pieces) if p]


def _sliding_windows(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    step = max(max_chars - _CHUNK_OVERLAP_CHARS, 1)
    return [text[i : i + max_chars] for i in range(0, len(text), step)]


def _chunk_text(text: str, max_chars: int = _MAX_CHARS_PER_CALL) -> list[str]:
    """Split text without dropping content.

    Strategy (in order):
      1. If the whole text fits → return as-is.
      2. Group paragraphs greedily into ≤max_chars windows.
      3. For any single paragraph longer than max_chars, fall back to
         sentence splitting; greedily group sentences.
      4. For any single sentence longer than max_chars, fall back to a
         character-level sliding window with overlap so context is not
         lost across the seam.

    Every character of the input lands in at least one chunk.
    """
    text = text or ""
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def _flush() -> None:
        nonlocal buf, buf_len
        if buf:
            chunks.append("\n\n".join(buf))
            buf = []
            buf_len = 0

    def _add_segment(seg: str) -> None:
        nonlocal buf, buf_len
        if len(seg) > max_chars:
            _flush()
            sentences = _split_sentences(seg)
            if len(sentences) <= 1:
                # Single huge sentence — sliding window with overlap.
                for window in _sliding_windows(seg, max_chars):
                    chunks.append(window)
                return
            for piece in sentences:
                _add_segment(piece)
            return
        # +2 for the joining "\n\n"
        if buf and buf_len + len(seg) + 2 > max_chars:
            _flush()
        buf.append(seg)
        buf_len += len(seg) + (2 if buf_len else 0)

    for para in _split_paragraphs(text):
        _add_segment(para)
    _flush()

    if not chunks:
        chunks = _sliding_windows(text, max_chars)
    return chunks


def _l2_renormalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return vec
    return [v / norm for v in vec]


def _mean_pool(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    if len(vectors) == 1:
        return list(vectors[0])
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            acc[i] += x
    n = float(len(vectors))
    return _l2_renormalize([x / n for x in acc])


# ── implementations ────────────────────────────────────────────────


@dataclass
class EmbeddingService:
    """Gemini-backed dense embedder (default).

    All public methods are safe against oversize inputs: long texts are
    chunked + mean-pooled rather than truncated.
    """

    llm: GeminiClient
    _max_chars: int = _MAX_CHARS_PER_CALL

    async def embed_one(self, text: str) -> list[float]:
        # Single retrieval query — short by construction, but pool just
        # in case a caller hands us a paragraph-sized question.
        items = [EmbedItem(text=text, task_kind="RETRIEVAL_QUERY")]
        vecs = await self.embed_documents(items)
        return vecs[0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        items = [EmbedItem(text=t, task_kind="RETRIEVAL_DOCUMENT") for t in texts]
        return await self.embed_documents(items)

    async def embed_documents(self, items: list[EmbedItem]) -> list[list[float]]:
        if not items:
            return []

        # 1. Chunk every item; remember which chunks belong to which item.
        per_item_chunks: list[list[_Chunk]] = []
        all_chunks: list[_Chunk] = []
        for idx, item in enumerate(items):
            text = item.text or ""
            pieces = _chunk_text(text, self._max_chars)
            if len(pieces) > 1:
                log.info(
                    "embedding: oversize text on item %d split into %d chunks (len=%d)",
                    idx,
                    len(pieces),
                    len(text),
                )
            chunks = [_Chunk(item_idx=idx, text=p) for p in pieces]
            per_item_chunks.append(chunks)
            all_chunks.extend(chunks)

        # 2. Group chunks by task_kind so each batch maps to a single
        #    API call configuration. With v2 prefixes the title travels
        #    inside the payload, so we still only need to group by
        #    task_kind for the legacy task_type API path.
        grouped: dict[TaskKind, list[tuple[int, str]]] = {}
        v2 = _is_v2_model()
        for global_idx, chunk in enumerate(all_chunks):
            kind = items[chunk.item_idx].task_kind
            title = items[chunk.item_idx].title
            if v2:
                payload = _format_v2_payload(text=chunk.text, task_kind=kind, title=title)
            else:
                payload = chunk.text
            grouped.setdefault(kind, []).append((global_idx, payload))

        # 3. Issue one batched call per group, in parallel.
        chunk_vecs: list[list[float] | None] = [None] * len(all_chunks)

        async def _run_group(kind: TaskKind, entries: list[tuple[int, str]]) -> None:
            payloads = [e[1] for e in entries]
            vecs = await self.llm.embed(
                payloads,
                model=settings.gemini.model_embed,
                task_type=None if v2 else kind,
            )
            for (global_idx, _payload), vec in zip(entries, vecs, strict=True):
                chunk_vecs[global_idx] = list(vec)

        await asyncio.gather(*[
            _run_group(kind, entries) for kind, entries in grouped.items()
        ])

        # 4. Mean-pool chunks back into per-item vectors.
        global_index = 0
        out: list[list[float]] = []
        for chunks in per_item_chunks:
            piece_vecs: list[list[float]] = []
            for _ in chunks:
                v = chunk_vecs[global_index]
                global_index += 1
                if v is not None:
                    piece_vecs.append(v)
            out.append(_mean_pool(piece_vecs))
        return out

    @property
    def dim(self) -> int:
        return settings.gemini.embed_dim


class BGEM3DenseEmbedder:
    """Local BAAI/bge-m3 dense embedder — lazy-imports FlagEmbedding.

    bge-m3 has its own 8192-token window so chunking is rarely needed,
    but we apply the same paragraph-aware splitter for parity so the
    upstream callers don't have to branch.
    """

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
        items = [EmbedItem(text=t) for t in texts]
        return await self.embed_documents(items)

    async def embed_documents(self, items: list[EmbedItem]) -> list[list[float]]:
        if not items:
            return []
        per_item: list[list[str]] = []
        flat: list[str] = []
        for item in items:
            chunks = _chunk_text(item.text or "", _MAX_CHARS_PER_CALL)
            per_item.append(chunks)
            flat.extend(chunks)

        def _run() -> list[list[float]]:
            model = self._get_model()
            out = model.encode(
                flat,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False,
            )
            return [list(map(float, v)) for v in out["dense_vecs"]]

        flat_vecs = await asyncio.to_thread(_run)

        out_vecs: list[list[float]] = []
        idx = 0
        for chunks in per_item:
            piece_vecs = [flat_vecs[idx + i] for i in range(len(chunks))]
            idx += len(chunks)
            out_vecs.append(_mean_pool([_l2_renormalize(v) for v in piece_vecs]))
        return out_vecs

    @property
    def dim(self) -> int:
        return settings.retrieval.bge_m3_dense_dim


def build_dense_embedder(llm: GeminiClient) -> DenseEmbedder:
    """Factory used by routers; picks provider per `retrieval.embedder`."""
    if settings.retrieval.embedder == "bge-m3":
        return BGEM3DenseEmbedder()
    return EmbeddingService(llm=llm)


__all__ = [
    "DenseEmbedder",
    "EmbedItem",
    "EmbeddingService",
    "BGEM3DenseEmbedder",
    "build_dense_embedder",
]
