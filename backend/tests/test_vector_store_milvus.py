from __future__ import annotations

import pytest

from app.services.vector_store import MilvusVectorStore


class _FakeMilvusClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def delete(self, **kwargs) -> None:
        self.calls.append(("delete", kwargs))

    def insert(self, **kwargs) -> None:
        self.calls.append(("insert", kwargs))

    def flush(self, **kwargs) -> None:
        self.calls.append(("flush", kwargs))


@pytest.mark.asyncio
async def test_milvus_upsert_replaces_by_ref_id():
    client = _FakeMilvusClient()
    store = MilvusVectorStore(client=client)
    await store.upsert(
        "q_emb",
        ref_id="abc-123",
        vector=[0.1, 0.2],
        subject="math",
        grade_band="senior",
        difficulty=2,
    )
    assert client.calls[0][0] == "delete"
    assert client.calls[0][1]["collection_name"] == "q_emb"
    assert client.calls[0][1]["filter"] == 'ref_pg_id == "abc-123"'
    assert client.calls[1][0] == "insert"
    assert client.calls[1][1]["data"][0]["ref_pg_id"] == "abc-123"
    assert client.calls[2][0] == "flush"
    assert client.calls[2][1]["collection_name"] == "q_emb"


@pytest.mark.asyncio
async def test_milvus_upsert_sparse_replaces_by_ref_id():
    client = _FakeMilvusClient()
    store = MilvusVectorStore(client=client)
    await store.upsert_sparse(
        "pattern_emb",
        ref_id="pat-1",
        sparse={1: 0.5},
        subject="math",
        grade_band="senior",
    )
    assert client.calls[0][0] == "delete"
    assert client.calls[0][1]["collection_name"] == "pattern_emb_sparse"
    assert client.calls[0][1]["filter"] == 'pattern_id == "pat-1"'
    assert client.calls[1][0] == "insert"
    assert client.calls[1][1]["data"][0]["sparse_vector"] == {1: 0.5}
    assert client.calls[2][0] == "flush"
    assert client.calls[2][1]["collection_name"] == "pattern_emb_sparse"
