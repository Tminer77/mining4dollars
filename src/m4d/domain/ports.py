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

from m4d.domain.coins import Coin, Pool
from m4d.domain.events import EventFilter, SystemEvent
from m4d.domain.pagination import Cursor
from m4d.domain.quotes import Quote
from m4d.domain.workers import Worker

__all__ = [
    "Clock",
    "CoinRepository",
    "EventRepository",
    "PoolRepository",
    "QuoteRepository",
    "UnitOfWork",
    "WorkerRepository",
]


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


@runtime_checkable
class CoinRepository(Protocol):
    """Persistence for mineable coins."""

    async def add(self, coin: Coin) -> Coin:
        """Persist ``coin`` and return it."""
        ...

    async def get(self, coin_id: UUID) -> Coin | None:
        """Return the coin with ``coin_id``, or ``None``."""
        ...

    async def find_by_ticker(self, ticker: str) -> Coin | None:
        """Return the coin with ``ticker``, or ``None``."""
        ...

    async def list_all(self) -> Sequence[Coin]:
        """Return every listed coin, ticker ascending."""
        ...


@runtime_checkable
class PoolRepository(Protocol):
    """Persistence for pool endpoints."""

    async def add(self, pool: Pool) -> Pool:
        """Persist ``pool`` and return it."""
        ...

    async def get(self, pool_id: UUID) -> Pool | None:
        """Return the pool with ``pool_id``, or ``None``."""
        ...

    async def list_all(self) -> Sequence[Pool]:
        """Return every pool, name ascending."""
        ...


@runtime_checkable
class WorkerRepository(Protocol):
    """Persistence for mining workers, including capabilities and assignment."""

    async def add(self, worker: Worker) -> Worker:
        """Persist ``worker`` and return it."""
        ...

    async def get(self, worker_id: UUID) -> Worker | None:
        """Return the worker with ``worker_id``, or ``None``."""
        ...

    async def find_by_name(self, name: str) -> Worker | None:
        """Return the worker with ``name``, or ``None``."""
        ...

    async def save(self, worker: Worker) -> Worker:
        """Replace the stored worker (capabilities and assignment included)."""
        ...

    async def list_page(self, *, after: Cursor | None, limit: int) -> Sequence[Worker]:
        """Return up to ``limit`` workers after ``after``.

        Ordered by ``(created_at DESC, id DESC)``.
        """
        ...

    async def list_all(self) -> Sequence[Worker]:
        """Return the whole fleet, name ascending."""
        ...


@runtime_checkable
class QuoteRepository(Protocol):
    """Persistence for market quotes."""

    async def add(self, quote: Quote) -> Quote:
        """Persist ``quote`` and return it."""
        ...

    async def latest_per_coin(self) -> Sequence[Quote]:
        """Return the newest quote for each coin that has one."""
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
        """The event repository enrolled in this transaction."""
        ...

    @property
    def coins(self) -> CoinRepository:
        """The coin catalog enrolled in this transaction."""
        ...

    @property
    def pools(self) -> PoolRepository:
        """The pool catalog enrolled in this transaction."""
        ...

    @property
    def workers(self) -> WorkerRepository:
        """The worker inventory enrolled in this transaction."""
        ...

    @property
    def quotes(self) -> QuoteRepository:
        """The market quotes enrolled in this transaction."""
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
