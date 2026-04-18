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
from app.routers import admin, answer, ingest, knowledge, practice, retrieve
from app.services.milvus_setup import ensure_collections_async

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if settings.milvus.auto_bootstrap:
        try:
            await ensure_collections_async()
        except Exception as e:  # noqa: BLE001
            # Non-fatal: unit tests and air-gapped runs should still come up.
            log.warning(
                "milvus auto-bootstrap skipped (%s). Run "
                "`python -m app.services.milvus_setup --doctor` to diagnose.",
                e,
            )
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
