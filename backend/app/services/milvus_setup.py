"""Milvus collection bootstrap (§5.5.2).

Dense collections `q_emb`, `pattern_emb`, `kp_emb` use HNSW + IP at the
configured `embed_dim`. For M5 multi-route retrieval each of them also
has a companion sparse collection `*_sparse` with a SPARSE_INVERTED_INDEX
holding BM25 / bge-m3 lexical weights (requires Milvus 2.4+).
"""

from __future__ import annotations

import logging

from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient

from app.config import settings

log = logging.getLogger(__name__)


def _base_fields(extra_scalar: list[FieldSchema]) -> list[FieldSchema]:
    dim = settings.gemini.embed_dim
    return [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        *extra_scalar,
        FieldSchema(name="subject", dtype=DataType.VARCHAR, max_length=16),
        FieldSchema(name="grade_band", dtype=DataType.VARCHAR, max_length=16),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
    ]


def _sparse_fields(extra_scalar: list[FieldSchema]) -> list[FieldSchema]:
    return [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        *extra_scalar,
        FieldSchema(name="subject", dtype=DataType.VARCHAR, max_length=16),
        FieldSchema(name="grade_band", dtype=DataType.VARCHAR, max_length=16),
        FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
    ]


COLLECTIONS = {
    "q_emb": CollectionSchema(
        fields=_base_fields([
            FieldSchema(name="ref_pg_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="difficulty", dtype=DataType.INT64),
        ]),
        description="Question text embeddings",
    ),
    "pattern_emb": CollectionSchema(
        fields=_base_fields([
            FieldSchema(name="pattern_id", dtype=DataType.VARCHAR, max_length=64),
        ]),
        description="Method pattern embeddings",
    ),
    "kp_emb": CollectionSchema(
        fields=_base_fields([
            FieldSchema(name="kp_id", dtype=DataType.VARCHAR, max_length=64),
        ]),
        description="Knowledge point embeddings",
    ),
}

SPARSE_COLLECTIONS = {
    "q_emb_sparse": CollectionSchema(
        fields=_sparse_fields([
            FieldSchema(name="ref_pg_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="difficulty", dtype=DataType.INT64),
        ]),
        description="Question sparse lexical (BM25 / bge-m3)",
    ),
    "pattern_emb_sparse": CollectionSchema(
        fields=_sparse_fields([
            FieldSchema(name="pattern_id", dtype=DataType.VARCHAR, max_length=64),
        ]),
        description="Pattern sparse lexical",
    ),
    "kp_emb_sparse": CollectionSchema(
        fields=_sparse_fields([
            FieldSchema(name="kp_id", dtype=DataType.VARCHAR, max_length=64),
        ]),
        description="KP sparse lexical",
    ),
}

_INDEX = {"index_type": "HNSW", "metric_type": "IP", "params": {"M": 16, "efConstruction": 200}}
_SPARSE_INDEX = {"index_type": "SPARSE_INVERTED_INDEX", "metric_type": "IP", "params": {}}


def get_client() -> MilvusClient:
    uri = f"http://{settings.milvus.host}:{settings.milvus.port}"
    return MilvusClient(uri=uri, db_name=settings.milvus.database)


def ensure_collections() -> None:
    client = get_client()
    for name, schema in COLLECTIONS.items():
        if client.has_collection(name):
            log.info("milvus: %s exists", name)
        else:
            log.info("milvus: creating %s", name)
            client.create_collection(collection_name=name, schema=schema)
            client.create_index(
                collection_name=name,
                index_params=[{**_INDEX, "field_name": "vector", "index_name": "vector_idx"}],
            )
            client.load_collection(name)

    for name, schema in SPARSE_COLLECTIONS.items():
        if client.has_collection(name):
            log.info("milvus: %s exists", name)
            continue
        log.info("milvus: creating %s", name)
        client.create_collection(collection_name=name, schema=schema)
        client.create_index(
            collection_name=name,
            index_params=[{
                **_SPARSE_INDEX,
                "field_name": "sparse_vector",
                "index_name": "sparse_idx",
            }],
        )
        client.load_collection(name)


def doctor() -> dict:
    """Report current Milvus state — collection list + row counts.

    Intended for operators: after `docker compose up -d`, run
    `python -m app.services.milvus_setup --doctor` to verify the
    backend can talk to Milvus and the schema is in place.
    """
    client = get_client()
    expected = list(COLLECTIONS.keys()) + list(SPARSE_COLLECTIONS.keys())
    report: dict = {
        "uri": f"http://{settings.milvus.host}:{settings.milvus.port}",
        "database": settings.milvus.database,
        "collections": {},
        "missing": [],
    }
    for name in expected:
        if not client.has_collection(name):
            report["missing"].append(name)
            continue
        try:
            stats = client.get_collection_stats(collection_name=name)
        except Exception as e:  # pragma: no cover - defensive
            stats = {"error": str(e)}
        report["collections"][name] = stats
    return report


async def ensure_collections_async() -> None:
    """Async wrapper for FastAPI lifespan — offloads pymilvus to a thread."""
    import asyncio
    await asyncio.to_thread(ensure_collections)


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="HAnswer Milvus bootstrap / doctor")
    parser.add_argument("--doctor", action="store_true",
                        help="Print current Milvus state instead of creating collections.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.doctor:
        print(json.dumps(doctor(), indent=2, ensure_ascii=False, default=str))
    else:
        ensure_collections()
        print("Milvus collections ready.")
