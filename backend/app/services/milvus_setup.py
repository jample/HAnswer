"""Milvus collection bootstrap (§5.5.2).

Dense collections cover legacy question/pattern/kp search plus the
whole-question / whole-answer / pedagogical-unit routes. Each dense
collection has a companion sparse `*_sparse` collection with a
SPARSE_INVERTED_INDEX holding BM25 / bge-m3 lexical weights (requires
Milvus 2.4+).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pymilvus import CollectionSchema, DataType, FieldSchema, MilvusClient

from app.config import settings

log = logging.getLogger(__name__)


@dataclass
class BootstrapReport:
    expected_dense_dim: int
    created_dense: list[str] = field(default_factory=list)
    recreated_dense: list[str] = field(default_factory=list)
    created_sparse: list[str] = field(default_factory=list)
    recreated_sparse: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any((
            self.created_dense,
            self.recreated_dense,
            self.created_sparse,
            self.recreated_sparse,
        ))


def _base_fields(extra_scalar: list[FieldSchema]) -> list[FieldSchema]:
    dim = settings.retrieval_dense_dim
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
    "question_full_emb": CollectionSchema(
        fields=_base_fields([
            FieldSchema(name="question_id", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="difficulty", dtype=DataType.INT64),
        ]),
        description="Whole-question embeddings (canonical question vector after q_emb retirement)",
    ),
    "answer_full_emb": CollectionSchema(
        fields=_base_fields([
            FieldSchema(name="question_id", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="difficulty", dtype=DataType.INT64),
        ]),
        description="Whole-answer embeddings",
    ),
    "retrieval_unit_emb": CollectionSchema(
        fields=_base_fields([
            FieldSchema(name="retrieval_unit_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="unit_kind", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="difficulty", dtype=DataType.INT64),
        ]),
        description="Pedagogical retrieval-unit embeddings",
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
    "question_full_emb_sparse": CollectionSchema(
        fields=_sparse_fields([
            FieldSchema(name="question_id", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="difficulty", dtype=DataType.INT64),
        ]),
        description="Whole-question sparse lexical",
    ),
    "answer_full_emb_sparse": CollectionSchema(
        fields=_sparse_fields([
            FieldSchema(name="question_id", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="difficulty", dtype=DataType.INT64),
        ]),
        description="Whole-answer sparse lexical",
    ),
    "retrieval_unit_emb_sparse": CollectionSchema(
        fields=_sparse_fields([
            FieldSchema(name="retrieval_unit_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="unit_kind", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="difficulty", dtype=DataType.INT64),
        ]),
        description="Pedagogical retrieval-unit sparse lexical",
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


def _extract_vector_dim(desc: dict | None) -> int | None:
    if not isinstance(desc, dict):
        return None
    for field in desc.get("fields", []):
        if field.get("name") != "vector":
            continue
        params = field.get("params")
        if isinstance(params, dict) and "dim" in params:
            try:
                return int(params["dim"])
            except (TypeError, ValueError):
                return None
        for key in ("dim", "dimension"):
            if key in field:
                try:
                    return int(field[key])
                except (TypeError, ValueError):
                    return None
    return None


def _create_dense_collection(client: MilvusClient, name: str, schema: CollectionSchema) -> None:
    client.create_collection(collection_name=name, schema=schema)
    dense_index = client.prepare_index_params()
    dense_index.add_index(
        field_name="vector",
        index_name="vector_idx",
        **_INDEX,
    )
    client.create_index(
        collection_name=name,
        index_params=dense_index,
    )
    client.load_collection(name)


def _create_sparse_collection(client: MilvusClient, name: str, schema: CollectionSchema) -> None:
    client.create_collection(collection_name=name, schema=schema)
    sparse_index = client.prepare_index_params()
    sparse_index.add_index(
        field_name="sparse_vector",
        index_name="sparse_idx",
        **_SPARSE_INDEX,
    )
    client.create_index(
        collection_name=name,
        index_params=sparse_index,
    )
    client.load_collection(name)


def get_client() -> MilvusClient:
    uri = f"http://{settings.milvus.host}:{settings.milvus.port}"
    return MilvusClient(uri=uri, db_name=settings.milvus.database)


def ensure_collections(
    *,
    recreate_dense_on_dim_mismatch: bool | None = None,
    force_recreate_dense: bool = False,
    recreate_sparse: bool = False,
) -> BootstrapReport:
    client = get_client()
    recreate_dense = (
        settings.milvus.recreate_dense_on_dim_mismatch
        if recreate_dense_on_dim_mismatch is None
        else recreate_dense_on_dim_mismatch
    )
    expected_dim = settings.retrieval_dense_dim
    report = BootstrapReport(expected_dense_dim=expected_dim)
    for name, schema in COLLECTIONS.items():
        if client.has_collection(name):
            if force_recreate_dense:
                log.warning("milvus: dropping and recreating dense collection %s", name)
                client.drop_collection(collection_name=name)
                _create_dense_collection(client, name, schema)
                report.recreated_dense.append(name)
                continue
            desc = client.describe_collection(collection_name=name)
            actual_dim = _extract_vector_dim(desc)
            if actual_dim is not None and actual_dim != expected_dim:
                msg = (
                    f"milvus collection {name} has dense dim {actual_dim}, "
                    f"but the active embedder requires {expected_dim}. "
                    "Recreate dense collections before serving traffic."
                )
                if not recreate_dense:
                    raise RuntimeError(msg)
                log.warning("%s Dropping and recreating %s.", msg, name)
                client.drop_collection(collection_name=name)
                _create_dense_collection(client, name, schema)
                report.recreated_dense.append(name)
                continue
            log.info("milvus: %s exists", name)
            client.load_collection(name)
            continue

        log.info("milvus: creating %s", name)
        _create_dense_collection(client, name, schema)
        report.created_dense.append(name)

    for name, schema in SPARSE_COLLECTIONS.items():
        if client.has_collection(name):
            if recreate_sparse:
                log.warning("milvus: dropping and recreating sparse collection %s", name)
                client.drop_collection(collection_name=name)
                _create_sparse_collection(client, name, schema)
                report.recreated_sparse.append(name)
                continue
            log.info("milvus: %s exists", name)
            client.load_collection(name)
            continue
        log.info("milvus: creating %s", name)
        _create_sparse_collection(client, name, schema)
        report.created_sparse.append(name)

    return report


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
        "expected_dense_dim": settings.retrieval_dense_dim,
        "active_embedder": settings.retrieval.embedder,
        "collections": {},
        "missing": [],
        "dense_dim_mismatches": {},
    }
    for name in expected:
        if not client.has_collection(name):
            report["missing"].append(name)
            continue
        try:
            stats = client.get_collection_stats(collection_name=name)
            if name in COLLECTIONS:
                desc = client.describe_collection(collection_name=name)
                actual_dim = _extract_vector_dim(desc)
                if actual_dim is not None and actual_dim != settings.retrieval_dense_dim:
                    report["dense_dim_mismatches"][name] = {
                        "expected": settings.retrieval_dense_dim,
                        "actual": actual_dim,
                    }
        except Exception as e:  # pragma: no cover - defensive
            stats = {"error": str(e)}
        report["collections"][name] = stats
    return report


async def ensure_collections_async() -> BootstrapReport:
    """Async wrapper for FastAPI lifespan — offloads pymilvus to a thread."""
    import asyncio
    return await asyncio.to_thread(ensure_collections)


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="HAnswer Milvus bootstrap / doctor")
    parser.add_argument("--doctor", action="store_true",
                        help="Print current Milvus state instead of creating collections.")
    parser.add_argument(
        "--recreate-dense",
        action="store_true",
        help="Drop and recreate dense Milvus collections when their vector dim mismatches the active embedder.",
    )
    parser.add_argument(
        "--recreate-sparse",
        action="store_true",
        help="Drop and recreate all sparse Milvus collections before bootstrap.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    if args.doctor:
        print(json.dumps(doctor(), indent=2, ensure_ascii=False, default=str))
    else:
        ensure_collections(
            recreate_dense_on_dim_mismatch=args.recreate_dense,
            force_recreate_dense=args.recreate_dense,
            recreate_sparse=args.recreate_sparse,
        )
        print("Milvus collections ready.")
