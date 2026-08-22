"""Coin catalog persistence."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from m4d.db.integrity import translate_integrity_error
from m4d.db.tables import MiningCoinRow
from m4d.domain.coins import Coin
from m4d.domain.errors import ConflictError

__all__ = ["SqlAlchemyCoinRepository"]


def _to_domain(row: MiningCoinRow) -> Coin:
    return Coin(
        id=row.id,
        ticker=row.ticker,
        name=row.name,
        algorithm=row.algorithm,
        enabled=row.enabled,
        created_at=row.created_at,
    )


def _to_row(coin: Coin) -> MiningCoinRow:
    return MiningCoinRow(
        id=coin.id,
        ticker=coin.ticker,
        name=coin.name,
        algorithm=coin.algorithm,
        enabled=coin.enabled,
        created_at=coin.created_at,
    )


class SqlAlchemyCoinRepository:
    """Implements :class:`~m4d.domain.ports.CoinRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, coin: Coin) -> Coin:
        row = _to_row(coin)
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(
                exc,
                {
                    "uq_mining_coin_ticker": ConflictError(
                        f"Coin '{coin.ticker}' is already listed."
                    )
                },
            ) from exc
        return _to_domain(row)

    async def get(self, coin_id: UUID) -> Coin | None:
        row = await self._session.get(MiningCoinRow, coin_id)
        return None if row is None else _to_domain(row)

    async def find_by_ticker(self, ticker: str) -> Coin | None:
        statement = select(MiningCoinRow).where(MiningCoinRow.ticker == ticker)
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return None if row is None else _to_domain(row)

    async def list_all(self) -> Sequence[Coin]:
        statement = select(MiningCoinRow).order_by(MiningCoinRow.ticker)
        rows = (await self._session.execute(statement)).scalars().all()
        return [_to_domain(row) for row in rows]
