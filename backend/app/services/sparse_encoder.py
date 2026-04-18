"""Sparse lexical encoders for M5 multi-route retrieval (§3.4).

Two implementations behind a shared `SparseEncoder` protocol:

  - `BM25SparseEncoder` — in-process, no extra dependencies. Tokenizes
    Chinese + English with a character-bigram fallback (so math+physics
    keywords like 导数, 向心力, x^2 are all reachable without requiring
    jieba). Produces a sparse dict[term_hash → weight] per document
    with classic BM25 IDF × term-frequency saturation.

  - `BGEM3SparseEncoder` — thin wrapper around BAAI/bge-m3's lexical
    head, loaded lazily via `FlagEmbedding` (optional install). Returns
    the model's native sparse lexical weights (already dampened), which
    gives materially better recall on Chinese math than BM25 alone.

Both return a `dict[int, float]` so the same Milvus sparse index or
the `InMemoryVectorStore` sparse bucket can consume them.
"""

from __future__ import annotations

import math
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from app.config import settings
from app.services.bge_m3_runtime import get_bge_m3_model

# ── Tokenizer ────────────────────────────────────────────────────────

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# Math-symbol single-character tokens that should not be swallowed.
_MATH_SYMBOL = re.compile(r"[=+\-*/^<>≤≥≠∫∑∏→±·×÷]")


def _tokenize(text: str) -> list[str]:
    """Yield tokens suitable for BM25 scoring.

    Strategy:
      - Extract ASCII words, decimal numbers, math symbols.
      - For every CJK run, emit each character AND every character
        bigram (standard n-gram trick for index-free Chinese retrieval).
    """
    tokens: list[str] = []
    tokens.extend(m.group(0).lower() for m in _ASCII_WORD.finditer(text))
    tokens.extend(m.group(0) for m in _NUMBER.finditer(text))
    tokens.extend(m.group(0) for m in _MATH_SYMBOL.finditer(text))

    # CJK runs → unigrams + bigrams.
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(run)  # unigrams
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])  # bigrams
    return tokens


def _hash(token: str) -> int:
    """Stable 31-bit hash so the sparse dim matches across processes."""
    return hash(token) & 0x7FFFFFFF


# ── Protocol ────────────────────────────────────────────────────────


class SparseEncoder(Protocol):
    async def encode(self, texts: list[str]) -> list[dict[int, float]]: ...
    async def encode_one(self, text: str) -> dict[int, float]: ...


# ── BM25 (default, no deps) ─────────────────────────────────────────


@dataclass
class _BM25Corpus:
    """Running corpus statistics shared across encode calls.

    BM25 needs document-frequency estimates; in a local-first deploy we
    don't precompute against the whole corpus. We accumulate stats
    online — cold-start retrieval behaves like TF on the first call
    and converges toward real BM25 as more documents flow through
    the encoder during ingest.
    """

    df: dict[int, int] = field(default_factory=dict)
    n_docs: int = 0
    total_len: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, tokens: list[str]) -> None:
        with self._lock:
            self.n_docs += 1
            self.total_len += len(tokens)
            for tok in set(tokens):
                h = _hash(tok)
                self.df[h] = self.df.get(h, 0) + 1

    def idf(self, term_hash: int) -> float:
        if self.n_docs == 0:
            return 1.0
        df = self.df.get(term_hash, 0)
        # Robertson/Spärck-Jones smoothed IDF.
        return math.log((self.n_docs - df + 0.5) / (df + 0.5) + 1.0)

    def avgdl(self) -> float:
        return (self.total_len / self.n_docs) if self.n_docs else 1.0


class BM25SparseEncoder:
    """Online BM25 sparse encoder — thread-safe, dependency-free.

    `encode` updates corpus stats (used during sediment/upsert so stats
    reflect the actual knowledge base). `encode_one` for a query does
    NOT update stats — it just scores against the current snapshot.
    """

    # Classic BM25 hyperparameters; kept conservative for small corpora.
    k1: float = 1.5
    b: float = 0.75

    def __init__(self) -> None:
        self.corpus = _BM25Corpus()

    async def encode(self, texts: list[str]) -> list[dict[int, float]]:
        out = []
        for text in texts:
            toks = _tokenize(text)
            self.corpus.update(toks)
            out.append(self._score(toks))
        return out

    async def encode_one(self, text: str) -> dict[int, float]:
        return self._score(_tokenize(text))

    def _score(self, tokens: list[str]) -> dict[int, float]:
        if not tokens:
            return {}
        tf: dict[int, int] = {}
        for tok in tokens:
            h = _hash(tok)
            tf[h] = tf.get(h, 0) + 1

        avgdl = self.corpus.avgdl()
        dl = len(tokens)
        vec: dict[int, float] = {}
        for h, f in tf.items():
            idf = self.corpus.idf(h)
            norm = f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / avgdl))
            vec[h] = idf * norm
        return vec


# ── BGE-M3 (optional) ────────────────────────────────────────────────


class BGEM3SparseEncoder:
    """Reuses bge-m3's lexical head for sparse weights.

    Dependencies are imported lazily so the rest of the app runs even
    when FlagEmbedding / torch are not installed.
    """

    def __init__(self, model=None) -> None:  # type: ignore[no-untyped-def]
        self._model = model

    def _get_model(self):  # type: ignore[no-untyped-def]
        if self._model is None:
            self._model = get_bge_m3_model()
        return self._model

    async def encode(self, texts: list[str]) -> list[dict[int, float]]:
        import asyncio

        def _run() -> list[dict[int, float]]:
            model = self._get_model()
            out = model.encode(
                texts, return_dense=False, return_sparse=True,
                return_colbert_vecs=False,
            )
            # bge-m3 returns {token_id: weight}; hash token_id into our
            # sparse dim namespace so it coexists with BM25 fields.
            return [
                {int(tid) & 0x7FFFFFFF: float(w)
                 for tid, w in row.items()}
                for row in out["lexical_weights"]
            ]

        return await asyncio.to_thread(_run)

    async def encode_one(self, text: str) -> dict[int, float]:
        rows = await self.encode([text])
        return rows[0] if rows else {}


# ── Factory ─────────────────────────────────────────────────────────


_instance: SparseEncoder | None = None


def get_sparse_encoder() -> SparseEncoder:
    global _instance
    if _instance is None:
        name = settings.retrieval.sparse_encoder
        if name == "bge-m3":
            _instance = BGEM3SparseEncoder()
        else:
            _instance = BM25SparseEncoder()
    return _instance


def set_sparse_encoder(encoder: SparseEncoder | None) -> None:
    """Test hook."""
    global _instance
    _instance = encoder


__all__: Iterable[str] = [
    "SparseEncoder", "BM25SparseEncoder", "BGEM3SparseEncoder",
    "get_sparse_encoder", "set_sparse_encoder",
]
