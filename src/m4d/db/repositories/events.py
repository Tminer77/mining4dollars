"""Event persistence backed by PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import DateTime, Select, literal, select, tuple_
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from m4d.db.tables import SystemEventRow
from m4d.domain.errors import ConflictError, ValidationError
from m4d.domain.events import EventFilter, EventSeverity, SystemEvent
from m4d.domain.pagination import Cursor

__all__ = ["SqlAlchemyEventRepository"]

IDEMPOTENCY_INDEX = "uq_system_event_idempotency_key"


def _translate_integrity_error(exc: IntegrityError) -> Exception:
    """Map a driver-level constraint violation onto a domain error.

    Storage errors must not escape this layer as SQLAlchemy types; the service
    layer only understands the domain vocabulary.
    """
    detail = str(exc.orig)
    if IDEMPOTENCY_INDEX in detail:
        return ConflictError("An event with this idempotency key already exists.")
    if "ck_system_event_" in detail:
        # A CHECK failed, meaning bad data reached the database despite the
        # domain's own validation. Report it as invalid input, not a conflict.
        return ValidationError("The event violates a database constraint.", detail=detail)
    return exc


def _to_domain(row: SystemEventRow) -> SystemEvent:
    """Translate a persistence row into a domain entity."""
    return SystemEvent(
        id=row.id,
        source=row.source,
        kind=row.kind,
        severity=row.severity,
        payload=dict(row.payload),
        occurred_at=row.occurred_at,
        recorded_at=row.recorded_at,
        idempotency_key=row.idempotency_key,
    )


def _to_row(event: SystemEvent) -> SystemEventRow:
    """Translate a domain entity into a persistence row."""
    return SystemEventRow(
        id=event.id,
        source=event.source,
        kind=event.kind,
        severity=event.severity,
        payload=dict(event.payload),
        occurred_at=event.occurred_at,
        recorded_at=event.recorded_at,
        idempotency_key=event.idempotency_key,
    )


class SqlAlchemyEventRepository:
    """Implements :class:`~m4d.domain.ports.EventRepository` over a session.

    The repository never commits. Transaction control belongs to the unit of
    work, so that a service can compose several repository calls into one
    atomic change.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: SystemEvent) -> SystemEvent:
        """Stage ``event`` for insertion.

        Raises:
            ConflictError: if ``event.idempotency_key`` is already recorded.
            ValidationError: if the row violates a database CHECK.
        """
        row = _to_row(event)
        try:
            # A SAVEPOINT, so that a constraint violation rolls back only this
            # insert. Without it the failure poisons the whole transaction and
            # the caller cannot recover by reading the existing row.
            async with self._session.begin_nested():
                self._session.add(row)
                # Flush here so a violation is attributable to this call rather
                # than surfacing at commit time, far from its cause.
                await self._session.flush()
        except IntegrityError as exc:
            raise _translate_integrity_error(exc) from exc
        return _to_domain(row)

    async def get(self, event_id: UUID) -> SystemEvent | None:
        """Return the event with ``event_id``, or ``None``."""
        row = await self._session.get(SystemEventRow, event_id)
        return None if row is None else _to_domain(row)

    async def find_by_idempotency_key(self, key: str) -> SystemEvent | None:
        """Return the event previously recorded under ``key``, or ``None``."""
        statement = select(SystemEventRow).where(SystemEventRow.idempotency_key == key)
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return None if row is None else _to_domain(row)

    async def list_page(
        self,
        *,
        filters: EventFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[SystemEvent]:
        """Return up to ``limit`` events, newest first, after ``after``."""
        statement = self._apply_filters(select(SystemEventRow), filters)

        if after is not None:
            # Row-value comparison, not `occurred_at < x OR (= x AND id < y)`.
            # Both are correct; only this form keeps the composite index usable
            # as a single range scan.
            #
            # The bounds are wrapped in typed literals so asyncpg receives
            # `timestamptz` and `uuid` parameters rather than having to infer
            # them from an untyped placeholder.
            statement = statement.where(
                tuple_(SystemEventRow.occurred_at, SystemEventRow.id)
                < tuple_(
                    literal(after.occurred_at, DateTime(timezone=True)),
                    literal(after.id, PgUUID(as_uuid=True)),
                )
            )

        statement = statement.order_by(
            SystemEventRow.occurred_at.desc(), SystemEventRow.id.desc()
        ).limit(limit)

        rows = (await self._session.execute(statement)).scalars().all()
        return [_to_domain(row) for row in rows]

    @staticmethod
    def _apply_filters(
        statement: Select[tuple[SystemEventRow]], filters: EventFilter
    ) -> Select[tuple[SystemEventRow]]:
        """Attach the WHERE clauses implied by ``filters``."""
        if filters.source is not None:
            statement = statement.where(SystemEventRow.source == filters.source)
        if filters.kind is not None:
            statement = statement.where(SystemEventRow.kind == filters.kind)
        if filters.min_severity is not None:
            statement = statement.where(
                SystemEventRow.severity.in_(EventSeverity.at_or_above(filters.min_severity))
            )
        if filters.occurred_after is not None:
            statement = statement.where(SystemEventRow.occurred_at > filters.occurred_after)
        if filters.occurred_before is not None:
            statement = statement.where(SystemEventRow.occurred_at < filters.occurred_before)
        return statement
