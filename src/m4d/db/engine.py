"""Engine and session lifecycle.

Wrapped in a class rather than exposed as module-level globals: an import-time
engine connects during test collection, cannot be disposed deterministically,
and makes running two configurations in one process impossible.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from m4d.config import Settings

__all__ = ["Database"]

logger = logging.getLogger(__name__)


class Database:
    """Owns the connection pool and hands out sessions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine = create_async_engine(
            str(settings.database_url),
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            # Verifies a pooled connection before handing it out. Costs one
            # round trip; avoids the stale-connection errors that follow a
            # database restart or an idle-timeout kill by a proxy.
            pool_pre_ping=True,
            connect_args={
                "timeout": settings.db_connect_timeout_seconds,
                "server_settings": {
                    "application_name": settings.app_name,
                    # A server-side ceiling on any single statement. Without it
                    # one pathological query holds a pool slot indefinitely and
                    # the outage spreads to every other request.
                    "statement_timeout": str(settings.db_statement_timeout_ms),
                },
            },
        )
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        """The underlying engine. Intended for migrations and diagnostics."""
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Factory producing new sessions bound to this engine."""
        return self._session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session and guarantee it is closed."""
        async with self._session_factory() as session:
            yield session

    async def check(self) -> None:
        """Round-trip the database.

        Raises whatever the driver raises on failure; readiness checks turn that
        into an unhealthy response rather than swallowing it here.
        """
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        """Close every pooled connection."""
        await self._engine.dispose()
        logger.info("database connection pool disposed")
