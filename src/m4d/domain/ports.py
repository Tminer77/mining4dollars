"""Ports: the interfaces the domain requires of the outside world.

These are :class:`~typing.Protocol` definitions, so implementations satisfy them
structurally and never import the domain to inherit from it. Dependencies point
inward; the database layer knows about the domain, and the domain knows only
about these shapes.

The practical payoff is that services are unit-testable against a dictionary
without a database, and that swapping storage does not touch business logic.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from types import TracebackType
from typing import Protocol, runtime_checkable
from uuid import UUID

from m4d.domain.events import EventFilter, SystemEvent
from m4d.domain.pagination import Cursor

__all__ = ["Clock", "EventRepository", "UnitOfWork"]


@runtime_checkable
class EventRepository(Protocol):
    """Persistence for :class:`~m4d.domain.events.SystemEvent`.

    Note the absence of update and delete: the event log is append-only.
    """

    async def add(self, event: SystemEvent) -> SystemEvent:
        """Persist ``event`` and return it."""
        ...

    async def get(self, event_id: UUID) -> SystemEvent | None:
        """Return the event with ``event_id``, or ``None``."""
        ...

    async def find_by_idempotency_key(self, key: str) -> SystemEvent | None:
        """Return the event previously recorded under ``key``, or ``None``."""
        ...

    async def list_page(
        self,
        *,
        filters: EventFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[SystemEvent]:
        """Return up to ``limit`` events matching ``filters``, after ``after``.

        Ordered by ``(occurred_at DESC, id DESC)`` — newest first.
        """
        ...


class UnitOfWork(Protocol):
    """A transactional boundary over one or more repositories.

    Services declare what must succeed or fail together; they do not manage
    sessions, connections, or commits. Exiting the context without an explicit
    :meth:`commit` rolls back, so a forgotten commit loses work loudly in tests
    rather than half-writing in production.
    """

    @property
    def events(self) -> EventRepository:
        """The event repository enrolled in this transaction.

        A read-only property rather than a plain attribute so the type is
        covariant: an implementation may expose a concrete
        ``SqlAlchemyEventRepository`` here and still satisfy the port. A mutable
        attribute would be invariant and reject every real implementation.
        """
        ...

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None:
        """Durably apply everything done in this unit of work."""
        ...

    async def rollback(self) -> None:
        """Discard everything done in this unit of work."""
        ...


class Clock(Protocol):
    """A source of the current time.

    Injected so that time-dependent behaviour is deterministic under test.
    """

    def now(self) -> dt.datetime:
        """Return the current timezone-aware UTC time."""
        ...
