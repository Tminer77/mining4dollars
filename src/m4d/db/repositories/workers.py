"""Worker inventory persistence, including capabilities and assignment."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import DateTime, delete, literal, select, tuple_
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from m4d.db.integrity import translate_integrity_error
from m4d.db.tables import MiningAssignmentRow, MiningCapabilityRow, MiningWorkerRow
from m4d.domain.errors import ConflictError
from m4d.domain.hashrate import Hashrate, PowerWatts
from m4d.domain.money import Money
from m4d.domain.pagination import Cursor
from m4d.domain.workers import Assignment, AssignmentReason, Capability, Worker

__all__ = ["SqlAlchemyWorkerRepository"]


def _capabilities_to_domain(rows: Sequence[MiningCapabilityRow]) -> tuple[Capability, ...]:
    return tuple(
        Capability(
            algorithm=row.algorithm,
            hashrate=Hashrate(row.hashrate_hps),
            power=None if row.power_watts is None else PowerWatts(row.power_watts),
        )
        for row in sorted(rows, key=lambda item: item.algorithm)
    )


def _assignment_to_domain(row: MiningAssignmentRow | None) -> Assignment | None:
    if row is None:
        return None
    return Assignment(
        coin_id=row.coin_id,
        algorithm=row.algorithm,
        pool_id=row.pool_id,
        revenue_usd_per_day=Money(row.revenue_usd_per_day),
        cost_usd_per_day=Money(row.cost_usd_per_day),
        profit_usd_per_day=Money(row.profit_usd_per_day),
        assigned_at=row.assigned_at,
        reason=AssignmentReason(row.reason),
    )


def _to_domain(
    row: MiningWorkerRow,
    capabilities: Sequence[MiningCapabilityRow],
    assignment: MiningAssignmentRow | None,
) -> Worker:
    return Worker(
        id=row.id,
        name=row.name,
        hostname=row.hostname,
        enabled=row.enabled,
        power=PowerWatts(row.power_watts),
        electricity_usd_per_kwh=Money(row.electricity_usd_per_kwh),
        capabilities=_capabilities_to_domain(capabilities),
        assignment=_assignment_to_domain(assignment),
        last_seen_at=row.last_seen_at,
        last_algorithm=row.last_algorithm,
        last_hashrate=None if row.last_hashrate_hps is None else Hashrate(row.last_hashrate_hps),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_row(worker: Worker) -> MiningWorkerRow:
    return MiningWorkerRow(
        id=worker.id,
        name=worker.name,
        hostname=worker.hostname,
        enabled=worker.enabled,
        power_watts=worker.power.watts,
        electricity_usd_per_kwh=worker.electricity_usd_per_kwh.amount,
        last_seen_at=worker.last_seen_at,
        last_algorithm=worker.last_algorithm,
        last_hashrate_hps=None if worker.last_hashrate is None else worker.last_hashrate.hps,
        created_at=worker.created_at,
        updated_at=worker.updated_at,
    )


class SqlAlchemyWorkerRepository:
    """Implements :class:`~m4d.domain.ports.WorkerRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, worker: Worker) -> Worker:
        row = _to_row(worker)
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(
                exc,
                {
                    "uq_mining_worker_name": ConflictError(
                        f"Worker '{worker.name}' is already enrolled."
                    )
                },
            ) from exc
        await self._replace_children(worker)
        return await self._assemble(row)

    async def get(self, worker_id: UUID) -> Worker | None:
        row = await self._session.get(MiningWorkerRow, worker_id)
        if row is None:
            return None
        return await self._assemble(row)

    async def find_by_name(self, name: str) -> Worker | None:
        statement = select(MiningWorkerRow).where(MiningWorkerRow.name == name)
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        return await self._assemble(row)

    async def save(self, worker: Worker) -> Worker:
        existing = await self._session.get(MiningWorkerRow, worker.id)
        if existing is None:
            return await self.add(worker)
        existing.name = worker.name
        existing.hostname = worker.hostname
        existing.enabled = worker.enabled
        existing.power_watts = worker.power.watts
        existing.electricity_usd_per_kwh = worker.electricity_usd_per_kwh.amount
        existing.last_seen_at = worker.last_seen_at
        existing.last_algorithm = worker.last_algorithm
        existing.last_hashrate_hps = (
            None if worker.last_hashrate is None else worker.last_hashrate.hps
        )
        existing.updated_at = worker.updated_at
        await self._replace_children(worker)
        await self._session.flush()
        return await self._assemble(existing)

    async def list_page(self, *, after: Cursor | None, limit: int) -> Sequence[Worker]:
        statement = select(MiningWorkerRow)
        if after is not None:
            statement = statement.where(
                tuple_(MiningWorkerRow.created_at, MiningWorkerRow.id)
                < tuple_(
                    literal(after.occurred_at, DateTime(timezone=True)),
                    literal(after.id, PgUUID(as_uuid=True)),
                )
            )
        statement = statement.order_by(
            MiningWorkerRow.created_at.desc(), MiningWorkerRow.id.desc()
        ).limit(limit)
        rows = (await self._session.execute(statement)).scalars().all()
        return [await self._assemble(row) for row in rows]

    async def list_all(self) -> Sequence[Worker]:
        statement = select(MiningWorkerRow).order_by(MiningWorkerRow.name)
        rows = (await self._session.execute(statement)).scalars().all()
        return [await self._assemble(row) for row in rows]

    async def _assemble(self, row: MiningWorkerRow) -> Worker:
        capabilities = (
            (
                await self._session.execute(
                    select(MiningCapabilityRow).where(MiningCapabilityRow.worker_id == row.id)
                )
            )
            .scalars()
            .all()
        )
        assignment = await self._session.get(MiningAssignmentRow, row.id)
        return _to_domain(row, capabilities, assignment)

    async def _replace_children(self, worker: Worker) -> None:
        await self._session.execute(
            delete(MiningCapabilityRow).where(MiningCapabilityRow.worker_id == worker.id)
        )
        for capability in worker.capabilities:
            self._session.add(
                MiningCapabilityRow(
                    worker_id=worker.id,
                    algorithm=capability.algorithm,
                    hashrate_hps=capability.hashrate.hps,
                    power_watts=None if capability.power is None else capability.power.watts,
                )
            )
        await self._session.execute(
            delete(MiningAssignmentRow).where(MiningAssignmentRow.worker_id == worker.id)
        )
        if worker.assignment is not None:
            assignment = worker.assignment
            self._session.add(
                MiningAssignmentRow(
                    worker_id=worker.id,
                    coin_id=assignment.coin_id,
                    pool_id=assignment.pool_id,
                    algorithm=assignment.algorithm,
                    revenue_usd_per_day=assignment.revenue_usd_per_day.amount,
                    cost_usd_per_day=assignment.cost_usd_per_day.amount,
                    profit_usd_per_day=assignment.profit_usd_per_day.amount,
                    assigned_at=assignment.assigned_at,
                    reason=assignment.reason.value,
                )
            )
        await self._session.flush()
