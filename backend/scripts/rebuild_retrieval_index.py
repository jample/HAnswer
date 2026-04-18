"""Rebuild Milvus retrieval indexes from PostgreSQL data.

Typical usage after switching Gemini embeddings -> local bge-m3:

    python -m scripts.rebuild_retrieval_index --recreate-dense
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from app.config import settings
from app.db.session import session_scope
from app.services.embedding import build_dense_embedder
from app.services.llm_deps import get_llm_client
from app.services.milvus_setup import doctor, ensure_collections
from app.services.reindex_service import rebuild_retrieval_indexes
from app.services.sparse_encoder import get_sparse_encoder
from app.services.vector_store import get_vector_store


async def _run(
    *,
    recreate_dense: bool,
    recreate_sparse: bool,
    question_ids: list[str] | None,
) -> dict:
    ensure_collections(
        recreate_dense_on_dim_mismatch=recreate_dense,
        force_recreate_dense=recreate_dense,
        recreate_sparse=recreate_sparse,
    )
    llm = get_llm_client()
    embedder = build_dense_embedder(llm)
    sparse = get_sparse_encoder()
    vector_store = get_vector_store()

    parsed_ids = [uuid.UUID(qid) for qid in (question_ids or [])]
    async with session_scope() as session:
        stats = await rebuild_retrieval_indexes(
            session,
            embedding=embedder,
            vector_store=vector_store,
            sparse_encoder=sparse,
            question_ids=parsed_ids or None,
        )
    return {
        "active_embedder": settings.retrieval.embedder,
        "active_dense_dim": settings.retrieval_dense_dim,
        "recreate_dense": recreate_dense,
        "recreate_sparse": recreate_sparse,
        "question_ids": question_ids or [],
        "indexed_questions": stats.indexed_questions,
        "indexed_patterns": stats.indexed_patterns,
        "indexed_kps": stats.indexed_kps,
        "milvus": doctor(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild HAnswer retrieval indexes in Milvus.")
    parser.add_argument(
        "--recreate-dense",
        action="store_true",
        help="Drop and recreate dense Milvus collections if their dim mismatches the active embedder.",
    )
    parser.add_argument(
        "--recreate-sparse",
        action="store_true",
        help="Drop and recreate sparse Milvus collections before rebuilding.",
    )
    parser.add_argument(
        "--question-id",
        action="append",
        dest="question_ids",
        help="Restrict rebuild to one or more specific question UUIDs.",
    )
    args = parser.parse_args()
    result = asyncio.run(
        _run(
            recreate_dense=args.recreate_dense,
            recreate_sparse=args.recreate_sparse,
            question_ids=args.question_ids,
        ),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
