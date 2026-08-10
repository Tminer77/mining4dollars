"""In-memory implementations of the domain ports.

Their existence is the point of the port abstraction: the service layer can be
exercised exhaustively, including its concurrency handling, with no database and
no I/O. If these fakes were hard to write, the ports would be badly drawn.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from uuid import UUID

from m4d.domain.errors import ConflictError
from m4d.domain.events import EventFilter, EventSeverity, SystemEvent
from m4d.domain.pagination import Cursor

__all__ = ["FakeEventRepository", "FakeUnitOfWork"]


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


class FakeUnitOfWork:
    """A unit of work that records how it was used.

    ``committed`` and ``rolled_back`` let tests assert that a service actually
    closed its transaction, which is the failure the real implementation is
    designed to make visible.
    """

    def __init__(self, repository: FakeEventRepository | None = None) -> None:
        self.events = repository or FakeEventRepository()
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
