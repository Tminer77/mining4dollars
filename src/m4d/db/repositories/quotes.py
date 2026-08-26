"""Market quote persistence."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from m4d.db.tables import MiningQuoteRow
from m4d.domain.hashrate import Hashrate
from m4d.domain.money import Money
from m4d.domain.quotes import Quote

__all__ = ["SqlAlchemyQuoteRepository"]


def _to_domain(row: MiningQuoteRow) -> Quote:
    return Quote(
        id=row.id,
        coin_id=row.coin_id,
        algorithm=row.algorithm,
        revenue_usd_per_day=Money(row.revenue_usd_per_day),
        reference_hashrate=Hashrate(row.reference_hashrate_hps),
        source=row.source,
        quoted_at=row.quoted_at,
        recorded_at=row.recorded_at,
    )


def _to_row(quote: Quote) -> MiningQuoteRow:
    return MiningQuoteRow(
        id=quote.id,
        coin_id=quote.coin_id,
        algorithm=quote.algorithm,
        revenue_usd_per_day=quote.revenue_usd_per_day.amount,
        reference_hashrate_hps=quote.reference_hashrate.hps,
        source=quote.source,
        quoted_at=quote.quoted_at,
        recorded_at=quote.recorded_at,
    )


class SqlAlchemyQuoteRepository:
    """Implements :class:`~m4d.domain.ports.QuoteRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, quote: Quote) -> Quote:
        row = _to_row(quote)
        self._session.add(row)
        await self._session.flush()
        return _to_domain(row)

    async def latest_per_coin(self) -> Sequence[Quote]:
        """Newest quote for each coin, via PostgreSQL ``DISTINCT ON``."""
        statement = (
            select(MiningQuoteRow)
            .distinct(MiningQuoteRow.coin_id)
            .order_by(
                MiningQuoteRow.coin_id,
                MiningQuoteRow.quoted_at.desc(),
                MiningQuoteRow.recorded_at.desc(),
                MiningQuoteRow.id.desc(),
            )
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [_to_domain(row) for row in rows]
