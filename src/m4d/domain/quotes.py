"""Market quotes: estimated USD per day at a reference hashrate.

This is the WhatToMine-shaped input. A quote is not a price: it is "if you ran
this coin at H hashes/second for 24 hours, you would gross R dollars", already
accounting for network difficulty and coin price. Scaling that onto a worker is
a ratio of hashrates; the domain never fetches a market itself.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import UUID, uuid4

from m4d.domain.coins import parse_algorithm
from m4d.domain.errors import ValidationError
from m4d.domain.hashrate import Hashrate
from m4d.domain.money import Money
from m4d.domain.primitives import require_identifier

__all__ = ["MAX_QUOTE_SOURCE_LENGTH", "NewQuote", "Quote", "latest_quote_per_coin"]

MAX_QUOTE_SOURCE_LENGTH = 64


@dataclass(frozen=True, slots=True)
class NewQuote:
    """A request to record one coin's estimated daily revenue."""

    coin_id: UUID
    algorithm: str
    revenue_usd_per_day: Money
    reference_hashrate: Hashrate
    source: str = "manual"
    quoted_at: dt.datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "algorithm", parse_algorithm(self.algorithm))
        object.__setattr__(
            self,
            "source",
            require_identifier(self.source, name="source", max_length=MAX_QUOTE_SOURCE_LENGTH),
        )
        if self.revenue_usd_per_day.is_negative:
            raise ValidationError(
                "Quoted revenue cannot be negative.",
                field="revenue_usd_per_day",
            )
        if self.reference_hashrate.hps <= 0:
            raise ValidationError(
                "A market quote's reference hashrate must be greater than zero.",
                field="reference_hashrate_hps",
            )

    def materialise(self, *, now: dt.datetime, quote_id: UUID | None = None) -> Quote:
        """Give this quote an identity and a recording time."""
        return Quote(
            id=quote_id or uuid4(),
            coin_id=self.coin_id,
            algorithm=self.algorithm,
            revenue_usd_per_day=self.revenue_usd_per_day,
            reference_hashrate=self.reference_hashrate,
            source=self.source,
            quoted_at=self.quoted_at or now,
            recorded_at=now,
        )


@dataclass(frozen=True, slots=True)
class Quote:
    """One observation of a coin's estimated 24-hour gross revenue."""

    id: UUID
    coin_id: UUID
    algorithm: str
    revenue_usd_per_day: Money
    reference_hashrate: Hashrate
    source: str
    quoted_at: dt.datetime
    recorded_at: dt.datetime

    def revenue_for(self, hashrate: Hashrate) -> Money:
        """Scale this quote onto ``hashrate``."""
        return self.revenue_usd_per_day.scale(hashrate.ratio_to(self.reference_hashrate))


def latest_quote_per_coin(quotes: tuple[Quote, ...]) -> dict[UUID, Quote]:
    """Keep the newest quote for each coin (by ``quoted_at``, then ``recorded_at``)."""
    latest: dict[UUID, Quote] = {}
    for quote in quotes:
        current = latest.get(quote.coin_id)
        if current is None or (quote.quoted_at, quote.recorded_at, quote.id) > (
            current.quoted_at,
            current.recorded_at,
            current.id,
        ):
            latest[quote.coin_id] = quote
    return latest
