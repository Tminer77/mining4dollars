"""The event recording and querying use cases."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from m4d.domain.errors import ConflictError, NotFoundError
from m4d.domain.events import EventFilter, NewEvent, SystemEvent
from m4d.domain.pagination import Cursor, Page, normalise_page_size, take_page
from m4d.domain.ports import Clock, UnitOfWork

__all__ = ["EventService", "RecordResult"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecordResult:
    """The outcome of a record request.

    ``was_created`` lets the API answer ``201`` for a genuine write and ``200``
    for a replayed one, so a client can tell the difference between "we stored
    this" and "we already had it".
    """

    event: SystemEvent
    was_created: bool


class EventService:
    """Use cases over the system event log.

    Depends only on ports, so it can be exercised with in-memory fakes.
    """

    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def record(self, request: NewEvent) -> RecordResult:
        """Record ``request``, honouring its idempotency key if it has one.

        A producer that retries after a network timeout must not create a
        duplicate. Two things can go wrong, and both are handled:

        1. The retry arrives after the original committed. The pre-check finds
           it and returns it.
        2. Two concurrent requests carry the same key. The pre-check misses, one
           insert wins, and the loser recovers the winner's row. Correctness
           rests on the unique index, not on the pre-check.
        """
        event = request.materialise(now=self._clock.now())

        async with self._uow_factory() as uow:
            if request.idempotency_key is not None:
                existing = await uow.events.find_by_idempotency_key(request.idempotency_key)
                if existing is not None:
                    logger.info(
                        "event replayed",
                        extra={"event_id": str(existing.id), "kind": existing.kind},
                    )
                    return RecordResult(event=existing, was_created=False)

            try:
                stored = await uow.events.add(event)
            except ConflictError:
                # Lost the race described above. The winner's row is now
                # visible, so return it instead of failing the caller.
                if request.idempotency_key is None:
                    raise
                winner = await uow.events.find_by_idempotency_key(request.idempotency_key)
                if winner is None:  # pragma: no cover - implies the index vanished
                    raise
                return RecordResult(event=winner, was_created=False)

            await uow.commit()

        logger.info(
            "event recorded",
            extra={
                "event_id": str(stored.id),
                "source": stored.source,
                "kind": stored.kind,
                "severity": stored.severity.value,
            },
        )
        return RecordResult(event=stored, was_created=True)

    async def get(self, event_id: UUID) -> SystemEvent:
        """Return one event.

        Raises:
            NotFoundError: if no event has that id.
        """
        async with self._uow_factory() as uow:
            event = await uow.events.get(event_id)
        if event is None:
            raise NotFoundError("Event", event_id)
        return event

    async def list(
        self,
        *,
        filters: EventFilter | None = None,
        cursor_token: str | None = None,
        limit: int | None = None,
    ) -> Page[SystemEvent]:
        """Return one page of events, newest first."""
        page_size = normalise_page_size(limit)
        cursor = Cursor.decode(cursor_token) if cursor_token else None

        async with self._uow_factory() as uow:
            # Over-fetch by exactly one. Its presence proves another page exists
            # without a second COUNT query, which on an append-only table would
            # be a full scan.
            rows = await uow.events.list_page(
                filters=filters or EventFilter(),
                after=cursor,
                limit=page_size + 1,
            )

        return take_page(
            rows,
            page_size,
            position=lambda event: Cursor(occurred_at=event.occurred_at, id=event.id),
        )
