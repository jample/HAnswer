"""Vector store abstraction (§3.4 + §5.5.2).

Two implementations:
  - `MilvusVectorStore` — production, talks to pymilvus.
  - `InMemoryVectorStore` — deterministic cosine/IP scan for tests and
    environments without a Milvus instance.

Both speak the same `VectorStore` protocol so callers never branch on
backend.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Protocol

from app.config import settings


@dataclass
class Hit:
    ref_id: str                 # PG id string (question/pattern/kp)
    score: float                # higher is better (IP / cosine)
    subject: str = ""
    grade_band: str = ""
    difficulty: int = 0         # only used for q_emb


class VectorStore(Protocol):
    async def upsert(
        self,
        collection: str,
        *,
        ref_id: str,
        vector: list[float],
        subject: str,
        grade_band: str,
        difficulty: int = 0,
    ) -> None: ...

    async def search(
        self,
        collection: str,
        *,
        vector: list[float],
        k: int = 30,
        subject: str | None = None,
        grade_band: str | None = None,
    ) -> list[Hit]: ...

    # Sparse route (M5 multi-route retrieval, §3.4). Implementations
    # may no-op if they don't support sparse; callers check via
    # `supports_sparse`.
    supports_sparse: bool

    async def upsert_sparse(
        self,
        collection: str,
        *,
        ref_id: str,
        sparse: dict[int, float],
        subject: str,
        grade_band: str,
        difficulty: int = 0,
    ) -> None: ...

    async def search_sparse(
        self,
        collection: str,
        *,
        sparse: dict[int, float],
        k: int = 30,
        subject: str | None = None,
        grade_band: str | None = None,
    ) -> list[Hit]: ...


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v)) or 1.0


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (_norm(a) * _norm(b))


def _sparse_dot(a: dict[int, float], b: dict[int, float]) -> float:
    """Inner product of two sparse vectors.

    We intentionally use plain IP rather than cosine — BM25 weights are
    already IDF-normalized and comparing queries against documents with
    cosine would wash out rare-term importance.
    """
    if not a or not b:
        return 0.0
    # Iterate over the shorter vector.
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(h, 0.0) for h, w in a.items())


class InMemoryVectorStore:
    """Deterministic backend for tests.

    Identifies rows by (collection, ref_id); re-upsert on the same
    ref_id replaces the vector (mirrors Milvus upsert semantics).
    """

    supports_sparse: bool = True

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, dict]] = {
            "q_emb": {},
            "pattern_emb": {},
            "kp_emb": {},
        }
        self._sparse: dict[str, dict[str, dict]] = {
            "q_emb": {},
            "pattern_emb": {},
            "kp_emb": {},
        }

    async def upsert(
        self,
        collection: str,
        *,
        ref_id: str,
        vector: list[float],
        subject: str,
        grade_band: str,
        difficulty: int = 0,
    ) -> None:
        bucket = self._rows.setdefault(collection, {})
        bucket[ref_id] = {
            "vector": list(vector),
            "subject": subject,
            "grade_band": grade_band,
            "difficulty": difficulty,
        }

    async def search(
        self,
        collection: str,
        *,
        vector: list[float],
        k: int = 30,
        subject: str | None = None,
        grade_band: str | None = None,
    ) -> list[Hit]:
        bucket = self._rows.get(collection, {})
        hits: list[Hit] = []
        for ref_id, row in bucket.items():
            if subject and row["subject"] != subject:
                continue
            if grade_band and row["grade_band"] != grade_band:
                continue
            hits.append(Hit(
                ref_id=ref_id,
                score=_cosine(vector, row["vector"]),
                subject=row["subject"],
                grade_band=row["grade_band"],
                difficulty=row["difficulty"],
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]


    async def upsert_sparse(
        self,
        collection: str,
        *,
        ref_id: str,
        sparse: dict[int, float],
        subject: str,
        grade_band: str,
        difficulty: int = 0,
    ) -> None:
        bucket = self._sparse.setdefault(collection, {})
        bucket[ref_id] = {
            "sparse": dict(sparse),
            "subject": subject,
            "grade_band": grade_band,
            "difficulty": difficulty,
        }

    async def search_sparse(
        self,
        collection: str,
        *,
        sparse: dict[int, float],
        k: int = 30,
        subject: str | None = None,
        grade_band: str | None = None,
    ) -> list[Hit]:
        bucket = self._sparse.get(collection, {})
        hits: list[Hit] = []
        for ref_id, row in bucket.items():
            if subject and row["subject"] != subject:
                continue
            if grade_band and row["grade_band"] != grade_band:
                continue
            score = _sparse_dot(sparse, row["sparse"])
            if score <= 0.0:
                continue
            hits.append(Hit(
                ref_id=ref_id, score=score,
                subject=row["subject"], grade_band=row["grade_band"],
                difficulty=row["difficulty"],
            ))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]


class MilvusVectorStore:
    """Production backend — pymilvus client (lazy-initialized)."""

    _RAW_FIELDS = {
        "q_emb": ("ref_pg_id",),
        "pattern_emb": ("pattern_id",),
        "kp_emb": ("kp_id",),
    }
    _SPARSE_SUFFIX = "_sparse"           # companion collections
    supports_sparse: bool = True

    def __init__(self, client=None) -> None:  # type: ignore[no-untyped-def]
        self._client = client

    def _get_client(self):  # type: ignore[no-untyped-def]
        if self._client is None:
            from pymilvus import MilvusClient  # imported lazily
            uri = f"http://{settings.milvus.host}:{settings.milvus.port}"
            self._client = MilvusClient(uri=uri, db_name=settings.milvus.database)
        return self._client

    @staticmethod
    def _id_field(collection: str) -> str:
        return MilvusVectorStore._RAW_FIELDS[collection][0]

    async def upsert(
        self,
        collection: str,
        *,
        ref_id: str,
        vector: list[float],
        subject: str,
        grade_band: str,
        difficulty: int = 0,
    ) -> None:
        client = self._get_client()
        row = {
            self._id_field(collection): ref_id,
            "subject": subject,
            "grade_band": grade_band,
            "vector": vector,
        }
        if collection == "q_emb":
            row["difficulty"] = difficulty
        # pymilvus MilvusClient.upsert is synchronous; run in thread so
        # async callers don't stall the event loop.
        import asyncio
        await asyncio.to_thread(client.upsert, collection_name=collection, data=[row])

    async def search(
        self,
        collection: str,
        *,
        vector: list[float],
        k: int = 30,
        subject: str | None = None,
        grade_band: str | None = None,
    ) -> list[Hit]:
        client = self._get_client()
        filters: list[str] = []
        if subject:
            filters.append(f'subject == "{subject}"')
        if grade_band:
            filters.append(f'grade_band == "{grade_band}"')
        expr = " && ".join(filters) if filters else None

        import asyncio
        result = await asyncio.to_thread(
            client.search,
            collection_name=collection,
            data=[vector],
            limit=k,
            filter=expr,
            output_fields=list(self._RAW_FIELDS[collection]) + [
                "subject", "grade_band",
            ] + (["difficulty"] if collection == "q_emb" else []),
        )
        hits: list[Hit] = []
        for row in (result[0] if result else []):
            entity = row.get("entity", row)
            hits.append(Hit(
                ref_id=str(entity.get(self._id_field(collection), "")),
                score=float(row.get("distance", row.get("score", 0.0))),
                subject=str(entity.get("subject", "")),
                grade_band=str(entity.get("grade_band", "")),
                difficulty=int(entity.get("difficulty", 0) or 0),
            ))
        return hits

    # ── Sparse route (Milvus 2.4+ SPARSE_INVERTED_INDEX) ──────────

    async def upsert_sparse(
        self,
        collection: str,
        *,
        ref_id: str,
        sparse: dict[int, float],
        subject: str,
        grade_band: str,
        difficulty: int = 0,
    ) -> None:
        client = self._get_client()
        row = {
            self._id_field(collection): ref_id,
            "subject": subject,
            "grade_band": grade_band,
            "sparse_vector": sparse,
        }
        if collection == "q_emb":
            row["difficulty"] = difficulty
        import asyncio
        await asyncio.to_thread(
            client.upsert,
            collection_name=collection + self._SPARSE_SUFFIX,
            data=[row],
        )

    async def search_sparse(
        self,
        collection: str,
        *,
        sparse: dict[int, float],
        k: int = 30,
        subject: str | None = None,
        grade_band: str | None = None,
    ) -> list[Hit]:
        client = self._get_client()
        filters: list[str] = []
        if subject:
            filters.append(f'subject == "{subject}"')
        if grade_band:
            filters.append(f'grade_band == "{grade_band}"')
        expr = " && ".join(filters) if filters else None

        import asyncio
        result = await asyncio.to_thread(
            client.search,
            collection_name=collection + self._SPARSE_SUFFIX,
            data=[sparse],
            limit=k,
            filter=expr,
            anns_field="sparse_vector",
            output_fields=list(self._RAW_FIELDS[collection]) + [
                "subject", "grade_band",
            ] + (["difficulty"] if collection == "q_emb" else []),
        )
        hits: list[Hit] = []
        for row in (result[0] if result else []):
            entity = row.get("entity", row)
            hits.append(Hit(
                ref_id=str(entity.get(self._id_field(collection), "")),
                score=float(row.get("distance", row.get("score", 0.0))),
                subject=str(entity.get("subject", "")),
                grade_band=str(entity.get("grade_band", "")),
                difficulty=int(entity.get("difficulty", 0) or 0),
            ))
        return hits


# ── Dependency-injection singleton ───────────────────────────────────

_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = MilvusVectorStore()
    return _store


def set_vector_store(store: VectorStore | None) -> None:
    """Test hook."""
    global _store
    _store = store


def reset_vector_store() -> None:
    set_vector_store(None)


__all__: Iterable[str] = [
    "Hit", "VectorStore", "InMemoryVectorStore", "MilvusVectorStore",
    "get_vector_store", "set_vector_store", "reset_vector_store",
]
