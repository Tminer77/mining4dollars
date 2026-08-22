"""Optimizer plan persistence backed by PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from m4d.db.integrity import translate_integrity_error
from m4d.db.keyset import after_cursor
from m4d.db.tables import OptimizationPlanRow
from m4d.domain.errors import NotFoundError
from m4d.domain.optimizers import OptimizationAction, OptimizationPlan, PlanFilter, PlanStatus
from m4d.domain.pagination import Cursor

__all__ = ["SqlAlchemyPlanRepository"]

_UNIQUE = {
    "uq_optimization_plan_idempotency_key": "A plan with this idempotency key already exists."
}


def _to_domain(row: OptimizationPlanRow) -> OptimizationPlan:
    """Translate a persistence row into a domain entity."""
    records = cast(list[dict[str, str | None]], list(row.actions))
    return OptimizationPlan(
        id=row.id,
        endpoint_id=row.endpoint_id,
        category=row.category,
        status=row.status,
        summary=row.summary,
        actions=tuple(OptimizationAction.from_record(record) for record in records),
        ai_rationale=row.ai_rationale,
        proposed_at=row.proposed_at,
        decided_at=row.decided_at,
        applied_at=row.applied_at,
        idempotency_key=row.idempotency_key,
    )


def _apply_fields(row: OptimizationPlanRow, plan: OptimizationPlan) -> None:
    """Copy domain fields onto an existing row."""
    row.endpoint_id = plan.endpoint_id
    row.category = plan.category
    row.status = plan.status
    row.summary = plan.summary
    row.actions = [cast(dict[str, Any], action.to_record()) for action in plan.actions]
    row.ai_rationale = plan.ai_rationale
    row.proposed_at = plan.proposed_at
    row.decided_at = plan.decided_at
    row.applied_at = plan.applied_at
    row.idempotency_key = plan.idempotency_key


class SqlAlchemyPlanRepository:
    """Implements :class:`~m4d.domain.ports.OptimizationPlanRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, plan: OptimizationPlan) -> OptimizationPlan:
        """Stage ``plan`` for insertion."""
        row = OptimizationPlanRow(id=plan.id)
        _apply_fields(row, plan)
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(
                exc, unique_indexes=_UNIQUE, check_prefix="ck_optimization_plan_"
            ) from exc
        return _to_domain(row)

    async def save(self, plan: OptimizationPlan) -> OptimizationPlan:
        """Replace the persisted row for ``plan``."""
        row = await self._session.get(OptimizationPlanRow, plan.id)
        if row is None:
            raise NotFoundError("OptimizationPlan", plan.id)
        _apply_fields(row, plan)
        await self._session.flush()
        return _to_domain(row)

    async def get(self, plan_id: UUID) -> OptimizationPlan | None:
        """Return the plan with ``plan_id``, or ``None``."""
        row = await self._session.get(OptimizationPlanRow, plan_id)
        return None if row is None else _to_domain(row)

    async def find_by_idempotency_key(self, key: str) -> OptimizationPlan | None:
        """Return the plan previously proposed under ``key``, or ``None``."""
        statement = select(OptimizationPlanRow).where(OptimizationPlanRow.idempotency_key == key)
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return None if row is None else _to_domain(row)

    async def list_page(
        self,
        *,
        filters: PlanFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[OptimizationPlan]:
        """Return up to ``limit`` plans, most recently proposed first."""
        statement = _apply_filters(select(OptimizationPlanRow), filters)
        if after is not None:
            statement = statement.where(
                after_cursor(OptimizationPlanRow.proposed_at, OptimizationPlanRow.id, after)
            )
        statement = statement.order_by(
            OptimizationPlanRow.proposed_at.desc(), OptimizationPlanRow.id.desc()
        ).limit(limit)
        rows = (await self._session.execute(statement)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def count(self, *, status: PlanStatus | None = None) -> int:
        """Return how many plans match ``status``, or the total."""
        statement = select(func.count()).select_from(OptimizationPlanRow)
        if status is not None:
            statement = statement.where(OptimizationPlanRow.status == status)
        return int((await self._session.execute(statement)).scalar_one())


def _apply_filters(
    statement: Select[tuple[OptimizationPlanRow]], filters: PlanFilter
) -> Select[tuple[OptimizationPlanRow]]:
    """Attach the WHERE clauses implied by ``filters``."""
    if filters.endpoint_id is not None:
        statement = statement.where(OptimizationPlanRow.endpoint_id == filters.endpoint_id)
    if filters.status is not None:
        statement = statement.where(OptimizationPlanRow.status == filters.status)
    if filters.category is not None:
        statement = statement.where(OptimizationPlanRow.category == filters.category)
    return statement
