"""Async Postgres connection management via SQLAlchemy 2.0 + psycopg3.

Process-wide singleton holder for the engine + sessionmaker so repos don't
need to pass them around. `dispose_engine()` is an idempotent shutdown hook.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from quant.config import get_settings


def _to_async_url(url: str) -> str:
    """Accept both sync and async driver prefixes in env (`psycopg` == `psycopg_async`)."""
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


@dataclass
class _EngineHolder:
    engine: AsyncEngine | None = None
    sessionmaker: async_sessionmaker[AsyncSession] | None = None


_holder = _EngineHolder()


def get_engine() -> AsyncEngine:
    if _holder.engine is None:
        url = _to_async_url(get_settings().database_url)
        _holder.engine = create_async_engine(
            url,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            future=True,
        )
    return _holder.engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _holder.sessionmaker is None:
        _holder.sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _holder.sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional session context. Commits on success, rolls back on error."""
    session = get_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def dispose_engine() -> None:
    """Shutdown hook — drop the pool. Safe to call multiple times."""
    if _holder.engine is not None:
        await _holder.engine.dispose()
    _holder.engine = None
    _holder.sessionmaker = None
