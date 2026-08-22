"""Scan persistence backed by PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from m4d.db.integrity import translate_integrity_error
from m4d.db.keyset import after_cursor
from m4d.db.tables import ScanRow
from m4d.domain.antivirus import Scan, ScanFilter, ScanStatus
from m4d.domain.errors import NotFoundError
from m4d.domain.pagination import Cursor

__all__ = ["SqlAlchemyScanRepository"]

_UNIQUE = {"uq_scan_idempotency_key": "A scan with this idempotency key already exists."}


def _to_domain(row: ScanRow) -> Scan:
    """Translate a persistence row into a domain entity."""
    return Scan(
        id=row.id,
        endpoint_id=row.endpoint_id,
        kind=row.kind,
        status=row.status,
        queued_at=row.queued_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        files_examined=row.files_examined,
        findings_count=row.findings_count,
        error_message=row.error_message,
        idempotency_key=row.idempotency_key,
    )


def _apply_fields(row: ScanRow, scan: Scan) -> None:
    """Copy domain fields onto an existing row."""
    row.endpoint_id = scan.endpoint_id
    row.kind = scan.kind
    row.status = scan.status
    row.queued_at = scan.queued_at
    row.started_at = scan.started_at
    row.completed_at = scan.completed_at
    row.files_examined = scan.files_examined
    row.findings_count = scan.findings_count
    row.error_message = scan.error_message
    row.idempotency_key = scan.idempotency_key


class SqlAlchemyScanRepository:
    """Implements :class:`~m4d.domain.ports.ScanRepository` over a session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, scan: Scan) -> Scan:
        """Stage ``scan`` for insertion."""
        row = ScanRow(id=scan.id)
        _apply_fields(row, scan)
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(
                exc, unique_indexes=_UNIQUE, check_prefix="ck_scan_"
            ) from exc
        return _to_domain(row)

    async def save(self, scan: Scan) -> Scan:
        """Replace the persisted row for ``scan``."""
        row = await self._session.get(ScanRow, scan.id)
        if row is None:
            raise NotFoundError("Scan", scan.id)
        _apply_fields(row, scan)
        await self._session.flush()
        return _to_domain(row)

    async def get(self, scan_id: UUID) -> Scan | None:
        """Return the scan with ``scan_id``, or ``None``."""
        row = await self._session.get(ScanRow, scan_id)
        return None if row is None else _to_domain(row)

    async def find_by_idempotency_key(self, key: str) -> Scan | None:
        """Return the scan previously queued under ``key``, or ``None``."""
        statement = select(ScanRow).where(ScanRow.idempotency_key == key)
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return None if row is None else _to_domain(row)

    async def list_page(
        self,
        *,
        filters: ScanFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[Scan]:
        """Return up to ``limit`` scans, most recently queued first."""
        statement = _apply_filters(select(ScanRow), filters)
        if after is not None:
            statement = statement.where(after_cursor(ScanRow.queued_at, ScanRow.id, after))
        statement = statement.order_by(ScanRow.queued_at.desc(), ScanRow.id.desc()).limit(limit)
        rows = (await self._session.execute(statement)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def count_in_flight(self) -> int:
        """Return how many scans are queued or running."""
        statement = (
            select(func.count())
            .select_from(ScanRow)
            .where(ScanRow.status.in_((ScanStatus.QUEUED, ScanStatus.RUNNING)))
        )
        return int((await self._session.execute(statement)).scalar_one())


def _apply_filters(
    statement: Select[tuple[ScanRow]], filters: ScanFilter
) -> Select[tuple[ScanRow]]:
    """Attach the WHERE clauses implied by ``filters``."""
    if filters.endpoint_id is not None:
        statement = statement.where(ScanRow.endpoint_id == filters.endpoint_id)
    if filters.status is not None:
        statement = statement.where(ScanRow.status == filters.status)
    if filters.kind is not None:
        statement = statement.where(ScanRow.kind == filters.kind)
    return statement
