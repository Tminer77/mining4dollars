"""Wire contract for the mining-for-dollars API.

Dollar amounts and hashrates travel as decimal strings so clients never have
to guess whether a JSON number was rounded.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from m4d.domain.coins import Coin, NewCoin, NewPool, Pool
from m4d.domain.hashrate import Hashrate, PowerWatts
from m4d.domain.money import Money
from m4d.domain.pagination import Page
from m4d.domain.profit import ProfitOption
from m4d.domain.quotes import NewQuote, Quote
from m4d.domain.workers import (
    Capability,
    Heartbeat,
    NewWorker,
    Worker,
    WorkerStatus,
)
from m4d.services.mining import AssignResult, FleetSnapshot

__all__ = [
    "AssignRequest",
    "AssignResponse",
    "CapabilitiesRequest",
    "CapabilityRequest",
    "CoinCreateRequest",
    "CoinResponse",
    "FleetResponse",
    "HeartbeatRequest",
    "PoolCreateRequest",
    "PoolResponse",
    "ProfitOptionResponse",
    "QuoteCreateRequest",
    "QuoteResponse",
    "QuotesIngestRequest",
    "WorkerCreateRequest",
    "WorkerPageResponse",
    "WorkerResponse",
]


class _Schema(BaseModel):
    """Base for every mining wire model."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _money(value: Money) -> str:
    return value.as_str()


class CoinCreateRequest(_Schema):
    """Body of ``POST /v1/coins``."""

    ticker: Annotated[str, Field(min_length=2, max_length=10, examples=["ETHW"])]
    name: Annotated[str, Field(min_length=1, max_length=64, examples=["EthereumPoW"])]
    algorithm: Annotated[str, Field(min_length=1, max_length=32, examples=["ethash"])]
    enabled: bool = True

    def to_domain(self) -> NewCoin:
        return NewCoin(
            ticker=self.ticker, name=self.name, algorithm=self.algorithm, enabled=self.enabled
        )


class CoinResponse(_Schema):
    """A listed coin."""

    id: str
    ticker: str
    name: str
    algorithm: str
    enabled: bool
    created_at: dt.datetime

    @classmethod
    def from_domain(cls, coin: Coin) -> Self:
        return cls(
            id=str(coin.id),
            ticker=coin.ticker,
            name=coin.name,
            algorithm=coin.algorithm,
            enabled=coin.enabled,
            created_at=coin.created_at,
        )


class PoolCreateRequest(_Schema):
    """Body of ``POST /v1/pools``."""

    name: Annotated[str, Field(min_length=1, max_length=64)]
    coin_id: UUID
    url: Annotated[str, Field(min_length=1, max_length=256)]
    worker_template: Annotated[str, Field(min_length=1, max_length=128)] = "{wallet}.{worker}"
    enabled: bool = True

    def to_domain(self) -> NewPool:
        return NewPool(
            name=self.name,
            coin_id=self.coin_id,
            url=self.url,
            worker_template=self.worker_template,
            enabled=self.enabled,
        )


class PoolResponse(_Schema):
    """A registered pool."""

    id: str
    name: str
    coin_id: str
    url: str
    worker_template: str
    enabled: bool
    created_at: dt.datetime

    @classmethod
    def from_domain(cls, pool: Pool) -> Self:
        return cls(
            id=str(pool.id),
            name=pool.name,
            coin_id=str(pool.coin_id),
            url=pool.url,
            worker_template=pool.worker_template,
            enabled=pool.enabled,
            created_at=pool.created_at,
        )


class CapabilityRequest(_Schema):
    """One benchmarked algorithm."""

    algorithm: Annotated[str, Field(min_length=1, max_length=32)]
    hashrate_hps: Annotated[str, Field(description="Hashes per second.")]
    power_watts: Annotated[str | None, Field(description="Draw while running this algorithm.")] = (
        None
    )

    def to_domain(self) -> Capability:
        return Capability(
            algorithm=self.algorithm,
            hashrate=Hashrate(self.hashrate_hps),
            power=None if self.power_watts is None else PowerWatts(self.power_watts),
        )


class CapabilitiesRequest(_Schema):
    """Body of ``POST /v1/workers/{id}/capabilities``."""

    capabilities: list[CapabilityRequest]

    def to_domain(self) -> tuple[Capability, ...]:
        return tuple(item.to_domain() for item in self.capabilities)


class WorkerCreateRequest(_Schema):
    """Body of ``POST /v1/workers``."""

    name: Annotated[str, Field(min_length=1, max_length=64, examples=["rig-1"])]
    hostname: Annotated[str | None, Field(max_length=128)] = None
    power_watts: Annotated[str, Field(description="Default electrical draw.")] = "0"
    electricity_usd_per_kwh: Annotated[str, Field(description="Power tariff.")] = "0"

    def to_domain(self) -> NewWorker:
        return NewWorker(
            name=self.name,
            hostname=self.hostname,
            power=PowerWatts(self.power_watts),
            electricity_usd_per_kwh=Money(self.electricity_usd_per_kwh),
        )


class AssignmentResponse(_Schema):
    """The coin a worker is pointed at, with the dollar estimate."""

    coin_id: str
    algorithm: str
    pool_id: str | None
    revenue_usd_per_day: str
    cost_usd_per_day: str
    profit_usd_per_day: str
    assigned_at: dt.datetime
    reason: str


class WorkerResponse(_Schema):
    """An enrolled mining worker."""

    id: str
    name: str
    hostname: str | None
    enabled: bool
    status: WorkerStatus
    power_watts: str
    electricity_usd_per_kwh: str
    capabilities: list[CapabilityRequest]
    assignment: AssignmentResponse | None
    last_seen_at: dt.datetime | None
    last_algorithm: str | None
    last_hashrate_hps: str | None
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def from_domain(cls, worker: Worker, *, now: dt.datetime) -> Self:
        assignment = None
        if worker.assignment is not None:
            assignment = AssignmentResponse(
                coin_id=str(worker.assignment.coin_id),
                algorithm=worker.assignment.algorithm,
                pool_id=None
                if worker.assignment.pool_id is None
                else str(worker.assignment.pool_id),
                revenue_usd_per_day=_money(worker.assignment.revenue_usd_per_day),
                cost_usd_per_day=_money(worker.assignment.cost_usd_per_day),
                profit_usd_per_day=_money(worker.assignment.profit_usd_per_day),
                assigned_at=worker.assignment.assigned_at,
                reason=worker.assignment.reason.value,
            )
        return cls(
            id=str(worker.id),
            name=worker.name,
            hostname=worker.hostname,
            enabled=worker.enabled,
            status=worker.status_at(now),
            power_watts=worker.power.as_str(),
            electricity_usd_per_kwh=_money(worker.electricity_usd_per_kwh),
            capabilities=[
                CapabilityRequest(
                    algorithm=item.algorithm,
                    hashrate_hps=item.hashrate.as_str(),
                    power_watts=None if item.power is None else item.power.as_str(),
                )
                for item in worker.capabilities
            ],
            assignment=assignment,
            last_seen_at=worker.last_seen_at,
            last_algorithm=worker.last_algorithm,
            last_hashrate_hps=(
                None if worker.last_hashrate is None else worker.last_hashrate.as_str()
            ),
            created_at=worker.created_at,
            updated_at=worker.updated_at,
        )


class WorkerPageResponse(_Schema):
    """One page of workers."""

    items: list[WorkerResponse]
    next_cursor: str | None

    @classmethod
    def from_domain(cls, page: Page[Worker], *, now: dt.datetime) -> Self:
        return cls(
            items=[WorkerResponse.from_domain(worker, now=now) for worker in page.items],
            next_cursor=page.next_cursor,
        )


class HeartbeatRequest(_Schema):
    """Body of ``POST /v1/workers/{id}/heartbeat``."""

    algorithm: str | None = None
    hashrate_hps: str | None = None
    power_watts: str | None = None
    occurred_at: dt.datetime | None = None

    def to_domain(self) -> Heartbeat:
        hashrate = None if self.hashrate_hps is None else Hashrate(self.hashrate_hps)
        return Heartbeat(
            algorithm=self.algorithm,
            hashrate=hashrate,
            power=None if self.power_watts is None else PowerWatts(self.power_watts),
            occurred_at=self.occurred_at,
        )


class QuoteCreateRequest(_Schema):
    """One coin's estimated daily revenue at a reference hashrate."""

    coin_id: UUID
    algorithm: Annotated[str, Field(min_length=1, max_length=32)]
    revenue_usd_per_day: Annotated[
        str, Field(description="Gross USD / 24h at reference_hashrate_hps.")
    ]
    reference_hashrate_hps: Annotated[str, Field(description="Hashes per second the quote is for.")]
    source: Annotated[str, Field(min_length=1, max_length=64)] = "manual"
    quoted_at: dt.datetime | None = None

    def to_domain(self) -> NewQuote:
        return NewQuote(
            coin_id=self.coin_id,
            algorithm=self.algorithm,
            revenue_usd_per_day=Money(self.revenue_usd_per_day),
            reference_hashrate=Hashrate(self.reference_hashrate_hps),
            source=self.source,
            quoted_at=self.quoted_at,
        )


class QuotesIngestRequest(_Schema):
    """Body of ``POST /v1/quotes``."""

    quotes: Annotated[list[QuoteCreateRequest], Field(min_length=1)]

    def to_domain(self) -> tuple[NewQuote, ...]:
        return tuple(item.to_domain() for item in self.quotes)


class QuoteResponse(_Schema):
    """A stored market quote."""

    id: str
    coin_id: str
    algorithm: str
    revenue_usd_per_day: str
    reference_hashrate_hps: str
    source: str
    quoted_at: dt.datetime
    recorded_at: dt.datetime

    @classmethod
    def from_domain(cls, quote: Quote) -> Self:
        return cls(
            id=str(quote.id),
            coin_id=str(quote.coin_id),
            algorithm=quote.algorithm,
            revenue_usd_per_day=_money(quote.revenue_usd_per_day),
            reference_hashrate_hps=quote.reference_hashrate.as_str(),
            source=quote.source,
            quoted_at=quote.quoted_at,
            recorded_at=quote.recorded_at,
        )


class ProfitOptionResponse(_Schema):
    """One scored (worker, coin) pair."""

    coin_id: str
    ticker: str
    algorithm: str
    pool_id: str | None
    hashrate_hps: str
    revenue_usd_per_day: str
    cost_usd_per_day: str
    profit_usd_per_day: str
    is_current: bool
    is_profitable: bool

    @classmethod
    def from_domain(cls, option: ProfitOption) -> Self:
        return cls(
            coin_id=str(option.coin_id),
            ticker=option.ticker,
            algorithm=option.algorithm,
            pool_id=None if option.pool_id is None else str(option.pool_id),
            hashrate_hps=option.hashrate_hps,
            revenue_usd_per_day=_money(option.revenue_usd_per_day),
            cost_usd_per_day=_money(option.cost_usd_per_day),
            profit_usd_per_day=_money(option.profit_usd_per_day),
            is_current=option.is_current,
            is_profitable=option.is_profitable,
        )


class AssignRequest(_Schema):
    """Body of ``POST /v1/workers/{id}/assign``. Empty means auto."""

    coin_id: UUID | None = None


class AssignResponse(_Schema):
    """Result of an assign call."""

    worker: WorkerResponse
    changed: bool
    reason: str
    recommended: ProfitOptionResponse | None

    @classmethod
    def from_domain(cls, result: AssignResult, *, now: dt.datetime) -> Self:
        recommended = (
            None
            if result.decision.recommended is None
            else ProfitOptionResponse.from_domain(result.decision.recommended)
        )
        return cls(
            worker=WorkerResponse.from_domain(result.worker, now=now),
            changed=result.changed,
            reason=result.decision.reason,
            recommended=recommended,
        )


class FleetWorkerResponse(_Schema):
    """One fleet row."""

    worker: WorkerResponse
    status: WorkerStatus
    option: ProfitOptionResponse | None


class FleetResponse(_Schema):
    """How many dollars the fleet is estimated to be making."""

    worker_count: int
    online_count: int
    assigned_count: int
    estimated_revenue_usd_per_day: str
    estimated_cost_usd_per_day: str
    estimated_profit_usd_per_day: str
    workers: list[FleetWorkerResponse]

    @classmethod
    def from_domain(cls, snapshot: FleetSnapshot, *, now: dt.datetime) -> Self:
        return cls(
            worker_count=snapshot.worker_count,
            online_count=snapshot.online_count,
            assigned_count=snapshot.assigned_count,
            estimated_revenue_usd_per_day=snapshot.estimated_revenue_usd_per_day,
            estimated_cost_usd_per_day=snapshot.estimated_cost_usd_per_day,
            estimated_profit_usd_per_day=snapshot.estimated_profit_usd_per_day,
            workers=[
                FleetWorkerResponse(
                    worker=WorkerResponse.from_domain(row.worker, now=now),
                    status=row.status,
                    option=(
                        None if row.option is None else ProfitOptionResponse.from_domain(row.option)
                    ),
                )
                for row in snapshot.workers
            ],
        )
