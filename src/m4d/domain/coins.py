"""Coins, algorithms, and mining pools.

A coin is what you sell. An algorithm is what the hardware actually runs. A
pool is where the shares go. Ranking dollars depends on all three: you cannot
mine a coin whose algorithm the rig cannot run, and an assignment without a
pool is a decision that has not been operationalised yet.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import UUID, uuid4

from m4d.domain.primitives import require_identifier, require_text, require_ticker

__all__ = [
    "MAX_ALGORITHM_LENGTH",
    "MAX_COIN_NAME_LENGTH",
    "MAX_POOL_NAME_LENGTH",
    "MAX_POOL_URL_LENGTH",
    "MAX_TICKER_LENGTH",
    "MAX_WORKER_TEMPLATE_LENGTH",
    "Coin",
    "NewCoin",
    "NewPool",
    "Pool",
    "parse_algorithm",
]

MAX_ALGORITHM_LENGTH = 32
MAX_TICKER_LENGTH = 10
MAX_COIN_NAME_LENGTH = 64
MAX_POOL_NAME_LENGTH = 64
MAX_POOL_URL_LENGTH = 256
MAX_WORKER_TEMPLATE_LENGTH = 128


def parse_algorithm(value: str) -> str:
    """Normalise a mining algorithm name."""
    return require_identifier(value, name="algorithm", max_length=MAX_ALGORITHM_LENGTH)


@dataclass(frozen=True, slots=True)
class NewCoin:
    """A request to list a coin the fleet may mine."""

    ticker: str
    name: str
    algorithm: str
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "ticker", require_ticker(self.ticker))
        object.__setattr__(
            self, "name", require_text(self.name, name="name", max_length=MAX_COIN_NAME_LENGTH)
        )
        object.__setattr__(self, "algorithm", parse_algorithm(self.algorithm))

    def materialise(self, *, now: dt.datetime, coin_id: UUID | None = None) -> Coin:
        """Give this listing an identity."""
        return Coin(
            id=coin_id or uuid4(),
            ticker=self.ticker,
            name=self.name,
            algorithm=self.algorithm,
            enabled=self.enabled,
            created_at=now,
        )


@dataclass(frozen=True, slots=True)
class Coin:
    """A cryptocurrency the fleet knows how to mine."""

    id: UUID
    ticker: str
    name: str
    algorithm: str
    enabled: bool
    created_at: dt.datetime


@dataclass(frozen=True, slots=True)
class NewPool:
    """A request to register a pool endpoint for a coin."""

    name: str
    coin_id: UUID
    url: str
    worker_template: str = "{wallet}.{worker}"
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "name", require_text(self.name, name="name", max_length=MAX_POOL_NAME_LENGTH)
        )
        object.__setattr__(
            self, "url", require_text(self.url, name="url", max_length=MAX_POOL_URL_LENGTH)
        )
        object.__setattr__(
            self,
            "worker_template",
            require_text(
                self.worker_template,
                name="worker_template",
                max_length=MAX_WORKER_TEMPLATE_LENGTH,
            ),
        )

    def materialise(self, *, now: dt.datetime, pool_id: UUID | None = None) -> Pool:
        """Give this pool an identity."""
        return Pool(
            id=pool_id or uuid4(),
            name=self.name,
            coin_id=self.coin_id,
            url=self.url,
            worker_template=self.worker_template,
            enabled=self.enabled,
            created_at=now,
        )


@dataclass(frozen=True, slots=True)
class Pool:
    """A stratum (or similar) endpoint that accepts shares for a coin."""

    id: UUID
    name: str
    coin_id: UUID
    url: str
    worker_template: str
    enabled: bool
    created_at: dt.datetime


def pick_pool_for_coin(pools: tuple[Pool, ...], coin_id: UUID) -> Pool | None:
    """Return the first enabled pool for ``coin_id``, if any.

    Pool selection is operational, not a profitability input. Ranking still
    works when no pool is configured; assignment then records the coin without
    a destination so the operator can fill it in.
    """
    for pool in pools:
        if pool.coin_id == coin_id and pool.enabled:
            return pool
    return None
