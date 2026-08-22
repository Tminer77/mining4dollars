"""Endpoint persistence backed by PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from m4d.db.integrity import translate_integrity_error
from m4d.db.keyset import after_cursor
from m4d.db.tables import EndpointRow
from m4d.domain.endpoints import Endpoint, EndpointFilter, EndpointStatus
from m4d.domain.errors import NotFoundError
from m4d.domain.pagination import Cursor

__all__ = ["SqlAlchemyEndpointRepository"]

_UNIQUE = {"uq_endpoint_hostname": "An endpoint with this hostname is already enrolled."}


def _to_domain(row: EndpointRow) -> Endpoint:
    """Translate a persistence row into a domain entity."""
    return Endpoint(
        id=row.id,
        hostname=row.hostname,
        platform=row.platform,
        role=row.role,
        status=row.status,
        agent_version=row.agent_version,
        labels=dict(row.labels),
        last_seen_at=row.last_seen_at,
        registered_at=row.registered_at,
        quarantine_reason=row.quarantine_reason,
    )


def _apply_fields(row: EndpointRow, endpoint: Endpoint) -> None:
    """Copy domain fields onto an existing row."""
    row.hostname = endpoint.hostname
    row.platform = endpoint.platform
    row.role = endpoint.role
    row.status = endpoint.status
    row.agent_version = endpoint.agent_version
    row.labels = dict(endpoint.labels)
    row.last_seen_at = endpoint.last_seen_at
    row.registered_at = endpoint.registered_at
    row.quarantine_reason = endpoint.quarantine_reason


class SqlAlchemyEndpointRepository:
    """Implements :class:`~m4d.domain.ports.EndpointRepository` over a session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, endpoint: Endpoint) -> Endpoint:
        """Stage ``endpoint`` for insertion."""
        row = EndpointRow(id=endpoint.id)
        _apply_fields(row, endpoint)
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(
                exc, unique_indexes=_UNIQUE, check_prefix="ck_endpoint_"
            ) from exc
        return _to_domain(row)

    async def save(self, endpoint: Endpoint) -> Endpoint:
        """Replace the persisted row for ``endpoint``."""
        row = await self._session.get(EndpointRow, endpoint.id)
        if row is None:
            raise NotFoundError("Endpoint", endpoint.id)
        _apply_fields(row, endpoint)
        await self._session.flush()
        return _to_domain(row)

    async def get(self, endpoint_id: UUID) -> Endpoint | None:
        """Return the endpoint with ``endpoint_id``, or ``None``."""
        row = await self._session.get(EndpointRow, endpoint_id)
        return None if row is None else _to_domain(row)

    async def find_by_hostname(self, hostname: str) -> Endpoint | None:
        """Return the endpoint enrolled under ``hostname``, or ``None``."""
        statement = select(EndpointRow).where(EndpointRow.hostname == hostname)
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return None if row is None else _to_domain(row)

    async def list_page(
        self,
        *,
        filters: EndpointFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[Endpoint]:
        """Return up to ``limit`` endpoints, most recently seen first."""
        statement = _apply_filters(select(EndpointRow), filters)
        if after is not None:
            statement = statement.where(
                after_cursor(EndpointRow.last_seen_at, EndpointRow.id, after)
            )
        statement = statement.order_by(
            EndpointRow.last_seen_at.desc(), EndpointRow.id.desc()
        ).limit(limit)
        rows = (await self._session.execute(statement)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def count(self, *, status: EndpointStatus | None = None) -> int:
        """Return how many endpoints match ``status``, or the fleet size."""
        statement = select(func.count()).select_from(EndpointRow)
        if status is not None:
            statement = statement.where(EndpointRow.status == status)
        return int((await self._session.execute(statement)).scalar_one())


def _apply_filters(
    statement: Select[tuple[EndpointRow]], filters: EndpointFilter
) -> Select[tuple[EndpointRow]]:
    """Attach the WHERE clauses implied by ``filters``."""
    if filters.status is not None:
        statement = statement.where(EndpointRow.status == filters.status)
    if filters.role is not None:
        statement = statement.where(EndpointRow.role == filters.role)
    if filters.platform is not None:
        statement = statement.where(EndpointRow.platform == filters.platform)
    if filters.hostname is not None:
        statement = statement.where(EndpointRow.hostname == filters.hostname)
    return statement
