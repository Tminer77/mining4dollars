"""Use-case behaviour, exercised with in-memory ports only."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import uuid4

import pytest

from m4d.domain.errors import ConflictError, NotFoundError
from m4d.domain.events import EventFilter, EventSeverity, NewEvent, SystemEvent
from m4d.services.clock import FrozenClock
from m4d.services.events import EventService
from tests.unit.fakes import FakeEventRepository, FakeUnitOfWork

NOW = dt.datetime(2026, 8, 10, 12, 0, tzinfo=dt.UTC)


@dataclass(frozen=True)
class Harness:
    """A service plus the fakes behind it, so tests can inspect both."""

    service: EventService
    repository: FakeEventRepository
    units: list[FakeUnitOfWork]


def build_harness(repository: FakeEventRepository | None = None) -> Harness:
    """Wire an :class:`EventService` onto in-memory ports."""
    repo = repository or FakeEventRepository()
    units: list[FakeUnitOfWork] = []

    def factory() -> FakeUnitOfWork:
        unit = FakeUnitOfWork(repo)
        units.append(unit)
        return unit

    return Harness(
        service=EventService(uow_factory=factory, clock=FrozenClock(NOW)),
        repository=repo,
        units=units,
    )


@pytest.fixture
def harness() -> Harness:
    return build_harness()


class TestRecord:
    async def test_stores_the_event(self, harness: Harness) -> None:
        result = await harness.service.record(NewEvent(source="api", kind="a.b"))

        assert result.was_created is True
        assert result.event.source == "api"
        assert harness.repository.by_id[result.event.id] == result.event

    async def test_commits_the_transaction(self, harness: Harness) -> None:
        """A write that is never committed is the bug this asserts against."""
        await harness.service.record(NewEvent(source="api", kind="a.b"))
        assert harness.units[0].committed is True

    async def test_stamps_time_from_the_injected_clock(self, harness: Harness) -> None:
        result = await harness.service.record(NewEvent(source="api", kind="a.b"))
        assert result.event.recorded_at == NOW

    async def test_preserves_the_payload(self, harness: Harness) -> None:
        result = await harness.service.record(
            NewEvent(source="api", kind="a.b", payload={"nested": {"count": 3}})
        )
        assert result.event.payload == {"nested": {"count": 3}}


class TestIdempotency:
    async def test_replay_returns_the_original(self, harness: Harness) -> None:
        first = await harness.service.record(
            NewEvent(source="api", kind="a.b", idempotency_key="k1")
        )
        second = await harness.service.record(
            NewEvent(source="api", kind="a.b", idempotency_key="k1")
        )

        assert second.was_created is False
        assert second.event.id == first.event.id

    async def test_replay_does_not_duplicate(self, harness: Harness) -> None:
        for _ in range(3):
            await harness.service.record(NewEvent(source="api", kind="a.b", idempotency_key="k1"))
        assert len(harness.repository.by_id) == 1

    async def test_distinct_keys_create_distinct_events(self, harness: Harness) -> None:
        await harness.service.record(NewEvent(source="api", kind="a.b", idempotency_key="k1"))
        await harness.service.record(NewEvent(source="api", kind="a.b", idempotency_key="k2"))
        assert len(harness.repository.by_id) == 2

    async def test_unkeyed_events_are_never_deduplicated(self, harness: Harness) -> None:
        """Without a key the producer has not asked for de-duplication."""
        await harness.service.record(NewEvent(source="api", kind="a.b"))
        await harness.service.record(NewEvent(source="api", kind="a.b"))
        assert len(harness.repository.by_id) == 2

    async def test_recovers_when_a_concurrent_writer_wins(self) -> None:
        """The pre-check can miss; correctness must rest on the unique index.

        This reproduces the interleaving where two requests carry the same key,
        both pre-checks find nothing, and one insert then loses.
        """

        class RacingRepository(FakeEventRepository):
            """Hides the existing row from the first lookup only."""

            def __init__(self) -> None:
                super().__init__()
                self.lookups = 0

            async def find_by_idempotency_key(self, key: str) -> SystemEvent | None:
                self.lookups += 1
                if self.lookups == 1:
                    return None  # the concurrent writer has not committed yet
                return await super().find_by_idempotency_key(key)

        repository = RacingRepository()
        winner = NewEvent(source="api", kind="a.b", idempotency_key="k1").materialise(now=NOW)
        await repository.add(winner)

        harness = build_harness(repository)
        result = await harness.service.record(
            NewEvent(source="api", kind="a.b", idempotency_key="k1")
        )

        assert result.was_created is False
        assert result.event.id == winner.id

    async def test_conflict_without_a_key_propagates(self) -> None:
        """A conflict we cannot explain must not be silently swallowed."""

        class AlwaysConflicts(FakeEventRepository):
            async def add(self, event: SystemEvent) -> SystemEvent:
                raise ConflictError("boom")

        harness = build_harness(AlwaysConflicts())
        with pytest.raises(ConflictError):
            await harness.service.record(NewEvent(source="api", kind="a.b"))


class TestGet:
    async def test_returns_a_stored_event(self, harness: Harness) -> None:
        created = await harness.service.record(NewEvent(source="api", kind="a.b"))
        assert (await harness.service.get(created.event.id)).id == created.event.id

    async def test_raises_for_an_unknown_id(self, harness: Harness) -> None:
        with pytest.raises(NotFoundError) as caught:
            await harness.service.get(uuid4())
        assert caught.value.code == "not_found"


class TestList:
    async def _seed(self, harness: Harness, count: int) -> None:
        for index in range(count):
            await harness.service.record(
                NewEvent(
                    source="api" if index % 2 == 0 else "worker",
                    kind="a.b",
                    severity=EventSeverity.ERROR if index % 3 == 0 else EventSeverity.INFO,
                    occurred_at=NOW + dt.timedelta(seconds=index),
                )
            )

    async def test_returns_newest_first(self, harness: Harness) -> None:
        await self._seed(harness, 5)
        page = await harness.service.list()
        timestamps = [event.occurred_at for event in page.items]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_respects_the_limit(self, harness: Harness) -> None:
        await self._seed(harness, 10)
        page = await harness.service.list(limit=3)
        assert len(page.items) == 3
        assert page.has_more is True

    async def test_final_page_has_no_cursor(self, harness: Harness) -> None:
        await self._seed(harness, 3)
        page = await harness.service.list(limit=10)
        assert len(page.items) == 3
        assert page.next_cursor is None

    async def test_cursor_walk_visits_every_event_exactly_once(self, harness: Harness) -> None:
        """The property that matters: no gaps, no repeats, across page edges."""
        await self._seed(harness, 10)

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(10):  # bounded so a broken cursor cannot loop forever
            page = await harness.service.list(limit=3, cursor_token=cursor)
            seen.extend(str(event.id) for event in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break

        assert cursor is None
        assert len(seen) == 10
        assert len(set(seen)) == 10

    async def test_filters_by_source(self, harness: Harness) -> None:
        await self._seed(harness, 6)
        page = await harness.service.list(filters=EventFilter(source="worker"))
        assert {event.source for event in page.items} == {"worker"}

    async def test_filters_by_minimum_severity(self, harness: Harness) -> None:
        await self._seed(harness, 6)
        page = await harness.service.list(filters=EventFilter(min_severity=EventSeverity.ERROR))
        assert all(event.severity.is_actionable for event in page.items)

    async def test_time_bounds_are_exclusive(self, harness: Harness) -> None:
        await self._seed(harness, 5)
        page = await harness.service.list(
            filters=EventFilter(occurred_after=NOW, occurred_before=NOW + dt.timedelta(seconds=4))
        )
        offsets = sorted((event.occurred_at - NOW).total_seconds() for event in page.items)
        assert offsets == [1.0, 2.0, 3.0]

    async def test_empty_store_returns_an_empty_page(self, harness: Harness) -> None:
        page = await harness.service.list()
        assert page.items == ()
        assert page.next_cursor is None
