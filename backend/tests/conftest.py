"""Shared pytest fixtures.

Tests run against the **real local PostgreSQL** (§0, Appendix B).
Each test gets an `AsyncSession` bound to a SAVEPOINT that is rolled
back at teardown, so the DB stays clean while still exercising the
Postgres dialect (JSONB, UUID, CHECK constraints).

Assumes `alembic upgrade head` has been run once against the configured DSN.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


@pytest_asyncio.fixture
async def engine():
    """Function-scoped async engine to avoid cross-loop reuse under pytest-asyncio."""
    eng = create_async_engine(settings.postgres.dsn, pool_pre_ping=True, future=True)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    """Per-test session with SAVEPOINT-based rollback.

    Pattern: open a connection, begin an outer transaction, bind a session
    to it, and roll everything back at teardown. The service layer's inner
    commits are captured as nested SAVEPOINTs via `join_transaction_mode`.
    """
    async with engine.connect() as conn:
        outer = await conn.begin()
        maker = async_sessionmaker(
            bind=conn,
            expire_on_commit=False,
            class_=AsyncSession,
            join_transaction_mode="create_savepoint",
        )
        async with maker() as s:
            try:
                yield s
            finally:
                await outer.rollback()


@pytest.fixture
def tmp_image_dir(monkeypatch):
    """Isolate disk writes from app.services.ingest_service."""
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(settings.storage, "image_dir", d, raising=False)
        yield Path(d)
