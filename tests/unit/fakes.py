"""In-memory implementations of the domain ports.

Their existence is the point of the port abstraction: the service layer can be
exercised exhaustively, including its concurrency handling, with no database and
no I/O. If these fakes were hard to write, the ports would be badly drawn.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from uuid import UUID

from m4d.domain.coins import Coin, Pool
from m4d.domain.errors import ConflictError
from m4d.domain.events import EventFilter, EventSeverity, SystemEvent
from m4d.domain.pagination import Cursor
from m4d.domain.quotes import Quote, latest_quote_per_coin
from m4d.domain.workers import Worker

__all__ = [
    "FakeCoinRepository",
    "FakeEventRepository",
    "FakePoolRepository",
    "FakeQuoteRepository",
    "FakeUnitOfWork",
    "FakeWorkerRepository",
]


class FakeEventRepository:
    """A dictionary pretending to be the event table."""

    def __init__(self) -> None:
        self.by_id: dict[UUID, SystemEvent] = {}
        self.by_key: dict[str, SystemEvent] = {}

    async def add(self, event: SystemEvent) -> SystemEvent:
        """Store ``event``, enforcing the idempotency key's uniqueness."""
        if event.idempotency_key is not None and event.idempotency_key in self.by_key:
            # Mirrors the unique index in PostgreSQL. Without this the fake
            # would be more forgiving than production and the service's race
            # handling would go untested.
            raise ConflictError("An event with this idempotency key already exists.")
        self.by_id[event.id] = event
        if event.idempotency_key is not None:
            self.by_key[event.idempotency_key] = event
        return event

    async def get(self, event_id: UUID) -> SystemEvent | None:
        return self.by_id.get(event_id)

    async def find_by_idempotency_key(self, key: str) -> SystemEvent | None:
        return self.by_key.get(key)

    async def list_page(
        self,
        *,
        filters: EventFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[SystemEvent]:
        """Apply the same ordering and filtering semantics as the real store."""
        events = sorted(
            self.by_id.values(), key=lambda event: (event.occurred_at, event.id), reverse=True
        )
        events = [event for event in events if _matches(event, filters)]

        if after is not None:
            events = [
                event
                for event in events
                if (event.occurred_at, event.id) < (after.occurred_at, after.id)
            ]

        return events[:limit]


def _matches(event: SystemEvent, filters: EventFilter) -> bool:
    """Whether ``event`` satisfies ``filters``."""
    if filters.source is not None and event.source != filters.source:
        return False
    if filters.kind is not None and event.kind != filters.kind:
        return False
    if filters.min_severity is not None and event.severity not in EventSeverity.at_or_above(
        filters.min_severity
    ):
        return False
    if filters.occurred_after is not None and event.occurred_at <= filters.occurred_after:
        return False
    return not (
        filters.occurred_before is not None and event.occurred_at >= filters.occurred_before
    )


class FakeCoinRepository:
    """A dictionary pretending to be the coin catalog."""

    def __init__(self) -> None:
        self.by_id: dict[UUID, Coin] = {}
        self.by_ticker: dict[str, Coin] = {}

    async def add(self, coin: Coin) -> Coin:
        if coin.ticker in self.by_ticker:
            raise ConflictError(f"Coin '{coin.ticker}' is already listed.")
        self.by_id[coin.id] = coin
        self.by_ticker[coin.ticker] = coin
        return coin

    async def get(self, coin_id: UUID) -> Coin | None:
        return self.by_id.get(coin_id)

    async def find_by_ticker(self, ticker: str) -> Coin | None:
        return self.by_ticker.get(ticker)

    async def list_all(self) -> Sequence[Coin]:
        return tuple(sorted(self.by_id.values(), key=lambda coin: coin.ticker))


class FakePoolRepository:
    """A dictionary pretending to be the pool catalog."""

    def __init__(self) -> None:
        self.by_id: dict[UUID, Pool] = {}

    async def add(self, pool: Pool) -> Pool:
        self.by_id[pool.id] = pool
        return pool

    async def get(self, pool_id: UUID) -> Pool | None:
        return self.by_id.get(pool_id)

    async def list_all(self) -> Sequence[Pool]:
        return tuple(sorted(self.by_id.values(), key=lambda pool: pool.name))


class FakeWorkerRepository:
    """A dictionary pretending to be the worker inventory."""

    def __init__(self) -> None:
        self.by_id: dict[UUID, Worker] = {}
        self.by_name: dict[str, Worker] = {}

    async def add(self, worker: Worker) -> Worker:
        if worker.name in self.by_name:
            raise ConflictError(f"Worker '{worker.name}' is already enrolled.")
        self.by_id[worker.id] = worker
        self.by_name[worker.name] = worker
        return worker

    async def get(self, worker_id: UUID) -> Worker | None:
        return self.by_id.get(worker_id)

    async def find_by_name(self, name: str) -> Worker | None:
        return self.by_name.get(name)

    async def save(self, worker: Worker) -> Worker:
        existing = self.by_id.get(worker.id)
        if existing is None:
            return await self.add(worker)
        if existing.name != worker.name:
            del self.by_name[existing.name]
        self.by_id[worker.id] = worker
        self.by_name[worker.name] = worker
        return worker

    async def list_page(self, *, after: Cursor | None, limit: int) -> Sequence[Worker]:
        workers = sorted(
            self.by_id.values(), key=lambda worker: (worker.created_at, worker.id), reverse=True
        )
        if after is not None:
            workers = [
                worker
                for worker in workers
                if (worker.created_at, worker.id) < (after.occurred_at, after.id)
            ]
        return workers[:limit]

    async def list_all(self) -> Sequence[Worker]:
        return tuple(sorted(self.by_id.values(), key=lambda worker: worker.name))


class FakeQuoteRepository:
    """A list pretending to be the quote table."""

    def __init__(self) -> None:
        self.items: list[Quote] = []

    async def add(self, quote: Quote) -> Quote:
        self.items.append(quote)
        return quote

    async def latest_per_coin(self) -> Sequence[Quote]:
        return tuple(latest_quote_per_coin(tuple(self.items)).values())


class FakeUnitOfWork:
    """A unit of work that records how it was used.

    ``committed`` and ``rolled_back`` let tests assert that a service actually
    closed its transaction, which is the failure the real implementation is
    designed to make visible.
    """

    def __init__(
        self,
        repository: FakeEventRepository | None = None,
        *,
        coins: FakeCoinRepository | None = None,
        pools: FakePoolRepository | None = None,
        workers: FakeWorkerRepository | None = None,
        quotes: FakeQuoteRepository | None = None,
    ) -> None:
        self.events = repository or FakeEventRepository()
        self.coins = coins or FakeCoinRepository()
        self.pools = pools or FakePoolRepository()
        self.workers = workers or FakeWorkerRepository()
        self.quotes = quotes or FakeQuoteRepository()
        self.committed = False
        self.rolled_back = False
        self.entered = 0

    async def __aenter__(self) -> FakeUnitOfWork:
        self.entered += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.committed:
            self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
