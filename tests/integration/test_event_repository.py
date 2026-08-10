"""Repository behaviour against real PostgreSQL."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from m4d.db.engine import Database
from m4d.db.uow import SqlAlchemyUnitOfWork
from m4d.domain.errors import ConflictError
from m4d.domain.events import EventFilter, EventSeverity, NewEvent, SystemEvent
from m4d.domain.pagination import Cursor

pytestmark = pytest.mark.integration

NOW = dt.datetime(2026, 8, 10, 12, 0, tzinfo=dt.UTC)


def build(
    *,
    source: str = "api",
    kind: str = "a.b",
    severity: EventSeverity = EventSeverity.INFO,
    offset_seconds: int = 0,
    payload: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> SystemEvent:
    """Create a materialised event for insertion."""
    return NewEvent(
        source=source,
        kind=kind,
        severity=severity,
        payload=payload or {},
        occurred_at=NOW + dt.timedelta(seconds=offset_seconds),
        idempotency_key=idempotency_key,
    ).materialise(now=NOW + dt.timedelta(seconds=offset_seconds))


async def seed(uow: SqlAlchemyUnitOfWork, events: list[SystemEvent]) -> None:
    """Persist ``events`` in one transaction."""
    async with uow:
        for event in events:
            await uow.events.add(event)
        await uow.commit()


class TestRoundTrip:
    async def test_stores_and_reloads_an_event(self, uow: SqlAlchemyUnitOfWork) -> None:
        event = build()
        await seed(uow, [event])

        async with uow:
            loaded = await uow.events.get(event.id)

        assert loaded is not None
        assert loaded.id == event.id
        assert loaded.source == "api"
        assert loaded.severity is EventSeverity.INFO

    async def test_preserves_a_nested_payload(self, uow: SqlAlchemyUnitOfWork) -> None:
        """JSONB must round-trip structure, not just a flat string blob."""
        payload = {"run": {"id": 7, "ok": True, "tags": ["a", "b"], "ratio": 1.5, "none": None}}
        event = build(payload=payload)
        await seed(uow, [event])

        async with uow:
            loaded = await uow.events.get(event.id)

        assert loaded is not None
        assert loaded.payload == payload

    async def test_preserves_timezone_aware_timestamps(self, uow: SqlAlchemyUnitOfWork) -> None:
        event = build()
        await seed(uow, [event])

        async with uow:
            loaded = await uow.events.get(event.id)

        assert loaded is not None
        assert loaded.occurred_at.tzinfo is not None
        assert loaded.occurred_at == event.occurred_at

    async def test_returns_none_for_an_unknown_id(self, uow: SqlAlchemyUnitOfWork) -> None:
        async with uow:
            assert await uow.events.get(uuid4()) is None


class TestTransactions:
    async def test_uncommitted_work_is_discarded(self, uow: SqlAlchemyUnitOfWork) -> None:
        """Leaving the block without committing must not persist anything."""
        event = build()
        async with uow:
            await uow.events.add(event)
            # deliberately no commit

        async with uow:
            assert await uow.events.get(event.id) is None

    async def test_explicit_rollback_discards(self, uow: SqlAlchemyUnitOfWork) -> None:
        event = build()
        async with uow:
            await uow.events.add(event)
            await uow.rollback()

        async with uow:
            assert await uow.events.get(event.id) is None

    async def test_session_is_unavailable_outside_the_block(
        self, uow: SqlAlchemyUnitOfWork
    ) -> None:
        with pytest.raises(RuntimeError, match="not been entered"):
            _ = uow.session


class TestIdempotency:
    async def test_duplicate_key_raises_a_domain_conflict(self, uow: SqlAlchemyUnitOfWork) -> None:
        """The driver error must be translated, not leaked to the service."""
        await seed(uow, [build(idempotency_key="k1")])

        async with uow:
            with pytest.raises(ConflictError):
                await uow.events.add(build(idempotency_key="k1", offset_seconds=1))

    async def test_transaction_survives_a_conflict(self, uow: SqlAlchemyUnitOfWork) -> None:
        """The SAVEPOINT must leave the outer transaction usable.

        This is what lets the service recover by reading the winning row rather
        than failing the caller.
        """
        await seed(uow, [build(idempotency_key="k1")])

        async with uow:
            with pytest.raises(ConflictError):
                await uow.events.add(build(idempotency_key="k1", offset_seconds=1))

            recovered = await uow.events.find_by_idempotency_key("k1")
            assert recovered is not None

    async def test_many_null_keys_do_not_collide(self, uow: SqlAlchemyUnitOfWork) -> None:
        """The unique index is partial, so unkeyed events are unconstrained."""
        await seed(uow, [build(offset_seconds=index) for index in range(5)])

        async with uow:
            page = await uow.events.list_page(filters=EventFilter(), after=None, limit=10)
        assert len(page) == 5

    async def test_lookup_by_key(self, uow: SqlAlchemyUnitOfWork) -> None:
        event = build(idempotency_key="k1")
        await seed(uow, [event])

        async with uow:
            found = await uow.events.find_by_idempotency_key("k1")
            assert found is not None
            assert found.id == event.id
            assert await uow.events.find_by_idempotency_key("absent") is None


class TestListing:
    async def test_orders_newest_first(self, uow: SqlAlchemyUnitOfWork) -> None:
        await seed(uow, [build(offset_seconds=index) for index in range(5)])

        async with uow:
            page = await uow.events.list_page(filters=EventFilter(), after=None, limit=10)

        assert [event.occurred_at for event in page] == sorted(
            (event.occurred_at for event in page), reverse=True
        )

    async def test_keyset_pagination_covers_ties(self, uow: SqlAlchemyUnitOfWork) -> None:
        """Identical timestamps are normal under bulk ingest.

        Without the id tiebreaker the ordering is not total and rows at a page
        boundary are skipped or repeated. All ten events share one timestamp.
        """
        await seed(uow, [build(offset_seconds=0) for _ in range(10)])

        seen: set[str] = set()
        cursor = None
        async with uow:
            for _ in range(10):
                rows = await uow.events.list_page(filters=EventFilter(), after=cursor, limit=3)
                if not rows:
                    break
                seen.update(str(row.id) for row in rows)
                last = rows[-1]
                cursor = Cursor(occurred_at=last.occurred_at, id=last.id)

        assert len(seen) == 10

    async def test_filters_by_source(self, uow: SqlAlchemyUnitOfWork) -> None:
        await seed(uow, [build(source="api"), build(source="worker", offset_seconds=1)])

        async with uow:
            page = await uow.events.list_page(
                filters=EventFilter(source="worker"), after=None, limit=10
            )

        assert [event.source for event in page] == ["worker"]

    async def test_filters_by_minimum_severity(self, uow: SqlAlchemyUnitOfWork) -> None:
        await seed(
            uow,
            [
                build(severity=EventSeverity.DEBUG, offset_seconds=0),
                build(severity=EventSeverity.WARNING, offset_seconds=1),
                build(severity=EventSeverity.CRITICAL, offset_seconds=2),
            ],
        )

        async with uow:
            page = await uow.events.list_page(
                filters=EventFilter(min_severity=EventSeverity.WARNING), after=None, limit=10
            )

        assert {event.severity for event in page} == {
            EventSeverity.WARNING,
            EventSeverity.CRITICAL,
        }

    async def test_time_bounds_are_exclusive(self, uow: SqlAlchemyUnitOfWork) -> None:
        await seed(uow, [build(offset_seconds=index) for index in range(5)])

        async with uow:
            page = await uow.events.list_page(
                filters=EventFilter(
                    occurred_after=NOW,
                    occurred_before=NOW + dt.timedelta(seconds=4),
                ),
                after=None,
                limit=10,
            )

        offsets = sorted((event.occurred_at - NOW).total_seconds() for event in page)
        assert offsets == [1.0, 2.0, 3.0]

    async def test_limit_is_honoured(self, uow: SqlAlchemyUnitOfWork) -> None:
        await seed(uow, [build(offset_seconds=index) for index in range(10)])

        async with uow:
            page = await uow.events.list_page(filters=EventFilter(), after=None, limit=4)

        assert len(page) == 4


class TestDatabaseConstraints:
    """The database is the last line of defence against writes from outside."""

    async def test_rejects_an_unknown_severity(self, database: Database) -> None:
        async with database.engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    text(
                        "INSERT INTO system_event "
                        "(id, source, kind, severity, payload, occurred_at, recorded_at) "
                        "VALUES (gen_random_uuid(), 'x', 'y', 'bogus', '{}'::jsonb, now(), now())"
                    )
                )

    async def test_rejects_a_blank_source(self, database: Database) -> None:
        async with database.engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    text(
                        "INSERT INTO system_event "
                        "(id, source, kind, severity, payload, occurred_at, recorded_at) "
                        "VALUES (gen_random_uuid(), '   ', 'y', 'info', '{}'::jsonb, now(), now())"
                    )
                )

    async def test_rejects_recording_before_occurrence(self, database: Database) -> None:
        """A backfill that inverts the two timestamps would corrupt lag metrics."""
        async with database.engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    text(
                        "INSERT INTO system_event "
                        "(id, source, kind, severity, payload, occurred_at, recorded_at) "
                        "VALUES (gen_random_uuid(), 'x', 'y', 'info', '{}'::jsonb, "
                        "now(), now() - interval '1 hour')"
                    )
                )
