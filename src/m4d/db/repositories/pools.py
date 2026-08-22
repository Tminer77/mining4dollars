"""Pool catalog persistence."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from m4d.db.integrity import translate_integrity_error
from m4d.db.tables import MiningPoolRow
from m4d.domain.coins import Pool
from m4d.domain.errors import ConflictError

__all__ = ["SqlAlchemyPoolRepository"]


def _to_domain(row: MiningPoolRow) -> Pool:
    return Pool(
        id=row.id,
        name=row.name,
        coin_id=row.coin_id,
        url=row.url,
        worker_template=row.worker_template,
        enabled=row.enabled,
        created_at=row.created_at,
    )


def _to_row(pool: Pool) -> MiningPoolRow:
    return MiningPoolRow(
        id=pool.id,
        name=pool.name,
        coin_id=pool.coin_id,
        url=pool.url,
        worker_template=pool.worker_template,
        enabled=pool.enabled,
        created_at=pool.created_at,
    )


class SqlAlchemyPoolRepository:
    """Implements :class:`~m4d.domain.ports.PoolRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, pool: Pool) -> Pool:
        row = _to_row(pool)
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(
                exc,
                {
                    "uq_mining_pool_coin_id_name": ConflictError(
                        f"Pool '{pool.name}' is already registered for this coin."
                    )
                },
            ) from exc
        return _to_domain(row)

    async def get(self, pool_id: UUID) -> Pool | None:
        row = await self._session.get(MiningPoolRow, pool_id)
        return None if row is None else _to_domain(row)

    async def list_all(self) -> Sequence[Pool]:
        statement = select(MiningPoolRow).order_by(MiningPoolRow.name)
        rows = (await self._session.execute(statement)).scalars().all()
        return [_to_domain(row) for row in rows]
