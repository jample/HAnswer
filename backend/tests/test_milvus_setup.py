from __future__ import annotations

import pytest

from app.config import settings
from app.services import milvus_setup


class _FakeIndexParams:
    def add_index(self, **kwargs) -> None:
        self.kwargs = kwargs


class _FakeMilvusClient:
    def __init__(self, existing: dict[str, int] | None = None) -> None:
        self.existing = dict(existing or {})
        self.created: list[str] = []
        self.dropped: list[str] = []
        self.loaded: list[str] = []

    def has_collection(self, name: str) -> bool:
        return name in self.existing

    def describe_collection(self, collection_name: str):
        dim = self.existing[collection_name]
        return {
            "fields": [
                {"name": "vector", "params": {"dim": dim}},
            ]
        }

    def create_collection(self, collection_name: str, schema) -> None:
        self.created.append(collection_name)
        self.existing[collection_name] = settings.retrieval_dense_dim

    def prepare_index_params(self):
        return _FakeIndexParams()

    def create_index(self, collection_name: str, index_params) -> None:
        return None

    def load_collection(self, name: str | None = None, collection_name: str | None = None) -> None:
        self.loaded.append(name or collection_name or "")

    def drop_collection(self, collection_name: str) -> None:
        self.dropped.append(collection_name)
        self.existing.pop(collection_name, None)

    def get_collection_stats(self, collection_name: str):
        return {"row_count": 0}


@pytest.mark.asyncio
async def test_admin_config_exposes_active_dense_dim():
    assert settings.retrieval_dense_dim in {settings.gemini.embed_dim, settings.retrieval.bge_m3_dense_dim}


def test_milvus_setup_raises_on_dense_dim_mismatch(monkeypatch):
    old_embedder = settings.retrieval.embedder
    try:
        settings.retrieval.embedder = "bge-m3"
        fake = _FakeMilvusClient(existing={name: 768 for name in milvus_setup.COLLECTIONS})
        fake.existing.update({name: 0 for name in milvus_setup.SPARSE_COLLECTIONS})
        monkeypatch.setattr(milvus_setup, "get_client", lambda: fake)
        with pytest.raises(RuntimeError, match="dense dim 768"):
            milvus_setup.ensure_collections(recreate_dense_on_dim_mismatch=False)
    finally:
        settings.retrieval.embedder = old_embedder


def test_milvus_setup_recreates_dense_mismatch(monkeypatch):
    old_embedder = settings.retrieval.embedder
    try:
        settings.retrieval.embedder = "bge-m3"
        fake = _FakeMilvusClient(existing={name: 768 for name in milvus_setup.COLLECTIONS})
        fake.existing.update({name: 0 for name in milvus_setup.SPARSE_COLLECTIONS})
        monkeypatch.setattr(milvus_setup, "get_client", lambda: fake)
        milvus_setup.ensure_collections(recreate_dense_on_dim_mismatch=True)
        assert set(fake.dropped) == set(milvus_setup.COLLECTIONS)
        assert set(fake.created) == set(milvus_setup.COLLECTIONS)
    finally:
        settings.retrieval.embedder = old_embedder


def test_milvus_setup_force_recreates_dense(monkeypatch):
    fake = _FakeMilvusClient(existing={name: settings.retrieval_dense_dim for name in milvus_setup.COLLECTIONS})
    fake.existing.update({name: 0 for name in milvus_setup.SPARSE_COLLECTIONS})
    monkeypatch.setattr(milvus_setup, "get_client", lambda: fake)
    milvus_setup.ensure_collections(force_recreate_dense=True)
    assert set(fake.dropped) == set(milvus_setup.COLLECTIONS)


def test_milvus_doctor_reports_dense_dim_mismatch(monkeypatch):
    old_embedder = settings.retrieval.embedder
    try:
        settings.retrieval.embedder = "bge-m3"
        fake = _FakeMilvusClient(existing={name: 768 for name in milvus_setup.COLLECTIONS})
        fake.existing.update({name: 0 for name in milvus_setup.SPARSE_COLLECTIONS})
        monkeypatch.setattr(milvus_setup, "get_client", lambda: fake)
        report = milvus_setup.doctor()
        assert report["expected_dense_dim"] == settings.retrieval.bge_m3_dense_dim
        assert report["dense_dim_mismatches"]["q_emb"]["actual"] == 768
    finally:
        settings.retrieval.embedder = old_embedder


def test_milvus_setup_recreates_sparse_when_requested(monkeypatch):
    fake = _FakeMilvusClient(existing={name: 0 for name in milvus_setup.SPARSE_COLLECTIONS})
    monkeypatch.setattr(milvus_setup, "get_client", lambda: fake)
    milvus_setup.ensure_collections(recreate_sparse=True)
    assert set(fake.dropped) == set(milvus_setup.SPARSE_COLLECTIONS)
