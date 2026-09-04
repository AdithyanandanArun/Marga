"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(
    database_url: str,
    *,
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_pre_ping: bool = True,
    echo: bool = False,
) -> AsyncEngine:
    """Create an async SQLAlchemy engine.

    For PostgreSQL use ``postgresql+asyncpg://...``.
    For testing with SQLite use ``sqlite+aiosqlite://...``.
    """
    kwargs: dict = {
        "echo": echo,
        "pool_pre_ping": pool_pre_ping,
    }
    # SQLite does not support pool_size / max_overflow
    if not database_url.startswith("sqlite"):
        kwargs["pool_size"] = pool_size
        kwargs["max_overflow"] = max_overflow

    return create_async_engine(database_url, **kwargs)


def _session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session scoped to a single unit of work.

    Usage::

        async with get_session(engine) as session:
            session.add(row)
            await session.commit()
    """
    factory = _session_factory(engine)
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
