"""Unit of work over a SQLAlchemy session."""

from __future__ import annotations

import logging
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from m4d.db.repositories.endpoints import SqlAlchemyEndpointRepository
from m4d.db.repositories.events import SqlAlchemyEventRepository
from m4d.db.repositories.findings import SqlAlchemyFindingRepository
from m4d.db.repositories.plans import SqlAlchemyPlanRepository
from m4d.db.repositories.scans import SqlAlchemyScanRepository

__all__ = ["SqlAlchemyUnitOfWork"]

logger = logging.getLogger(__name__)


class SqlAlchemyUnitOfWork:
    """A transactional scope exposing the repositories that share it.

    Implements :class:`~m4d.domain.ports.UnitOfWork`. Every repository reached
    through one instance runs on the same session, so their writes commit or
    roll back together.

    Usage::

        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            await uow.events.add(event)
            await uow.commit()

    Leaving the block without committing rolls back. That default is chosen so
    that a forgotten commit is a visibly missing write in a test, never a
    partially applied change in production.
    """

    events: SqlAlchemyEventRepository
    endpoints: SqlAlchemyEndpointRepository
    scans: SqlAlchemyScanRepository
    findings: SqlAlchemyFindingRepository
    plans: SqlAlchemyPlanRepository

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    @property
    def session(self) -> AsyncSession:
        """The active session.

        Raises:
            RuntimeError: if accessed outside the ``async with`` block.
        """
        if self._session is None:
            msg = "The unit of work has not been entered."
            raise RuntimeError(msg)
        return self._session

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        self._session = self._session_factory()
        self.events = SqlAlchemyEventRepository(self._session)
        self.endpoints = SqlAlchemyEndpointRepository(self._session)
        self.scans = SqlAlchemyScanRepository(self._session)
        self.findings = SqlAlchemyFindingRepository(self._session)
        self.plans = SqlAlchemyPlanRepository(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self._session
        if session is None:  # pragma: no cover - defensive
            return
        try:
            # Unconditional: a no-op after an explicit commit, and the safety
            # net for every path that did not reach one.
            await session.rollback()
        finally:
            await session.close()
            self._session = None

    async def commit(self) -> None:
        """Durably apply the work done in this scope."""
        await self.session.commit()

    async def rollback(self) -> None:
        """Discard the work done in this scope."""
        await self.session.rollback()
