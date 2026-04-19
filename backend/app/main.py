"""FastAPI application entry point (§5.3).

Mounts routers from §6 API design. On startup the lifespan hook
auto-bootstraps Milvus collections (dense + sparse companions for M5)
if `milvus.auto_bootstrap` is true and the server is reachable — this
removes the long-standing footgun where a fresh `docker compose up`
leaves an empty Milvus instance until someone remembers to run the
setup script manually.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import admin, answer, dialog, ingest, knowledge, practice, retrieve
from app.services.milvus_setup import BootstrapReport, ensure_collections_async

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")
log = logging.getLogger(__name__)


def _should_rebuild_retrieval(bootstrap: BootstrapReport | None) -> bool:
    if bootstrap is None:
        return False
    if not settings.milvus.auto_reindex_on_bootstrap_change:
        return False
    return bootstrap.changed


async def _rebuild_retrieval_from_pg() -> None:
    from app.db.session import session_scope
    from app.services.embedding import build_dense_embedder
    from app.services.llm_deps import get_llm_client
    from app.services.reindex_service import rebuild_retrieval_indexes
    from app.services.sparse_encoder import get_sparse_encoder
    from app.services.vector_store import get_vector_store

    llm = get_llm_client()
    embedder = build_dense_embedder(llm)
    sparse = get_sparse_encoder()
    vector_store = get_vector_store()

    async with session_scope() as session:
        stats = await rebuild_retrieval_indexes(
            session,
            embedding=embedder,
            vector_store=vector_store,
            sparse_encoder=sparse,
        )
    log.info(
        "Milvus retrieval rebuild complete (%s questions, %s patterns, %s kps)",
        stats.indexed_questions,
        stats.indexed_patterns,
        stats.indexed_kps,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    bootstrap_report: BootstrapReport | None = None
    rebuilt_retrieval = False
    if settings.milvus.auto_bootstrap:
        try:
            bootstrap_report = await ensure_collections_async()
        except Exception as e:  # noqa: BLE001
            # Non-fatal: unit tests and air-gapped runs should still come up.
            log.warning(
                "milvus auto-bootstrap skipped (%s). Run "
                "`python -m app.services.milvus_setup --doctor` to diagnose.",
                e,
            )

    if _should_rebuild_retrieval(bootstrap_report):
        try:
            await _rebuild_retrieval_from_pg()
            rebuilt_retrieval = True
        except Exception as e:  # noqa: BLE001
            log.warning("Milvus retrieval rebuild skipped (%s)", e)

    # Warm up BM25 corpus stats from PostgreSQL so the sparse route
    # has proper IDF values from the first query (not degenerate TF).
    if not rebuilt_retrieval:
        try:
            from app.db.session import session_scope
            from app.services.sparse_encoder import warmup_bm25_from_db
            async with session_scope() as session:
                await warmup_bm25_from_db(session)
            log.info("BM25 corpus warm-up complete")
        except Exception as e:  # noqa: BLE001
            log.warning("BM25 warm-up skipped (%s)", e)

    try:
        from app.services.answer_job_service import recover_inflight_answer_jobs

        recovered = await recover_inflight_answer_jobs()
        if recovered:
            log.info("Recovered %s in-flight answer job(s)", recovered)
    except Exception as e:  # noqa: BLE001
        log.warning("answer-job recovery skipped (%s)", e)

    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="HAnswer API",
        version="0.1.0",
        description="Learning companion backend — math & physics.",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(ingest.router)
    app.include_router(answer.router)
    app.include_router(answer.questions_router)
    app.include_router(dialog.router)
    app.include_router(retrieve.router)
    app.include_router(retrieve.questions_list_router)
    app.include_router(practice.router)
    app.include_router(knowledge.router)
    app.include_router(admin.router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
