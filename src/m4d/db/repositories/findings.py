"""Finding persistence backed by PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from m4d.db.integrity import translate_integrity_error
from m4d.db.keyset import after_cursor
from m4d.db.tables import FindingRow
from m4d.domain.antivirus import Finding, FindingFilter, FindingStatus
from m4d.domain.errors import NotFoundError
from m4d.domain.events import EventSeverity
from m4d.domain.pagination import Cursor

__all__ = ["SqlAlchemyFindingRepository"]

_UNIQUE = {"uq_finding_idempotency_key": "A finding with this idempotency key already exists."}
_OPEN_STATUSES = (FindingStatus.OPEN, FindingStatus.ACKNOWLEDGED, FindingStatus.QUARANTINED)


def _to_domain(row: FindingRow) -> Finding:
    """Translate a persistence row into a domain entity."""
    return Finding(
        id=row.id,
        scan_id=row.scan_id,
        endpoint_id=row.endpoint_id,
        category=row.category,
        severity=row.severity,
        status=row.status,
        indicator=row.indicator,
        title=row.title,
        detail=row.detail,
        ai_confidence=row.ai_confidence,
        ai_rationale=row.ai_rationale,
        recorded_at=row.recorded_at,
        resolved_at=row.resolved_at,
        idempotency_key=row.idempotency_key,
    )


def _apply_fields(row: FindingRow, finding: Finding) -> None:
    """Copy domain fields onto an existing row."""
    row.scan_id = finding.scan_id
    row.endpoint_id = finding.endpoint_id
    row.category = finding.category
    row.severity = finding.severity
    row.status = finding.status
    row.indicator = finding.indicator
    row.title = finding.title
    row.detail = finding.detail
    row.ai_confidence = finding.ai_confidence
    row.ai_rationale = finding.ai_rationale
    row.recorded_at = finding.recorded_at
    row.resolved_at = finding.resolved_at
    row.idempotency_key = finding.idempotency_key


class SqlAlchemyFindingRepository:
    """Implements :class:`~m4d.domain.ports.FindingRepository` over a session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, finding: Finding) -> Finding:
        """Stage ``finding`` for insertion."""
        row = FindingRow(id=finding.id)
        _apply_fields(row, finding)
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(
                exc, unique_indexes=_UNIQUE, check_prefix="ck_finding_"
            ) from exc
        return _to_domain(row)

    async def save(self, finding: Finding) -> Finding:
        """Replace the persisted row for ``finding``."""
        row = await self._session.get(FindingRow, finding.id)
        if row is None:
            raise NotFoundError("Finding", finding.id)
        _apply_fields(row, finding)
        await self._session.flush()
        return _to_domain(row)

    async def get(self, finding_id: UUID) -> Finding | None:
        """Return the finding with ``finding_id``, or ``None``."""
        row = await self._session.get(FindingRow, finding_id)
        return None if row is None else _to_domain(row)

    async def find_by_idempotency_key(self, key: str) -> Finding | None:
        """Return the finding previously recorded under ``key``, or ``None``."""
        statement = select(FindingRow).where(FindingRow.idempotency_key == key)
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return None if row is None else _to_domain(row)

    async def list_page(
        self,
        *,
        filters: FindingFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[Finding]:
        """Return up to ``limit`` findings, most recently recorded first."""
        statement = _apply_filters(select(FindingRow), filters)
        if after is not None:
            statement = statement.where(after_cursor(FindingRow.recorded_at, FindingRow.id, after))
        statement = statement.order_by(FindingRow.recorded_at.desc(), FindingRow.id.desc()).limit(
            limit
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def list_open_for_endpoint(self, endpoint_id: UUID) -> Sequence[Finding]:
        """Return every still-open finding on ``endpoint_id``."""
        statement = (
            select(FindingRow)
            .where(FindingRow.endpoint_id == endpoint_id)
            .where(FindingRow.status.in_(_OPEN_STATUSES))
            .order_by(FindingRow.recorded_at.desc(), FindingRow.id.desc())
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def count_open(self, *, actionable_only: bool = False) -> int:
        """Return how many findings still need a decision."""
        statement = (
            select(func.count())
            .select_from(FindingRow)
            .where(FindingRow.status.in_(_OPEN_STATUSES))
        )
        if actionable_only:
            statement = statement.where(
                FindingRow.severity.in_(EventSeverity.at_or_above(EventSeverity.ERROR))
            )
        return int((await self._session.execute(statement)).scalar_one())


def _apply_filters(
    statement: Select[tuple[FindingRow]], filters: FindingFilter
) -> Select[tuple[FindingRow]]:
    """Attach the WHERE clauses implied by ``filters``."""
    if filters.endpoint_id is not None:
        statement = statement.where(FindingRow.endpoint_id == filters.endpoint_id)
    if filters.scan_id is not None:
        statement = statement.where(FindingRow.scan_id == filters.scan_id)
    if filters.status is not None:
        statement = statement.where(FindingRow.status == filters.status)
    if filters.category is not None:
        statement = statement.where(FindingRow.category == filters.category)
    if filters.min_severity is not None:
        statement = statement.where(
            FindingRow.severity.in_(EventSeverity.at_or_above(filters.min_severity))
        )
    return statement
