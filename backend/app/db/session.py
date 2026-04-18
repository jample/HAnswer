"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

# NullPool: new asyncpg connection per session. Keeps the local-first
# single-user deployment simple and — crucially — isolates connections
# from pytest-asyncio's per-test event loops so no connection is ever
# reused across a closed loop.
engine = create_async_engine(
    settings.postgres.dsn,
    pool_pre_ping=True,
    future=True,
    poolclass=NullPool,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with SessionLocal() as s:
        yield s
