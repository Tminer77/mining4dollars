"""Mining use cases: enrol hardware, ingest quotes, assign the dollar winner.

This is the product the foundation was built to carry. The event log remains
the activity record; every state change here writes a ``mining.*`` event in
the same transaction.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from m4d.domain.coins import Coin, NewCoin, NewPool, Pool
from m4d.domain.errors import ConflictError, NotFoundError, ValidationError
from m4d.domain.pagination import Cursor, Page, normalise_page_size
from m4d.domain.ports import Clock, UnitOfWork
from m4d.domain.profit import (
    ProfitOption,
    SwitchDecision,
    assignment_from_option,
    decide_assignment,
    rank_options,
)
from m4d.domain.quotes import NewQuote, Quote, latest_quote_per_coin
from m4d.domain.workers import (
    AssignmentReason,
    Capability,
    Heartbeat,
    NewWorker,
    Worker,
    WorkerStatus,
)
from m4d.services.activity import record_activity

__all__ = ["AssignResult", "FleetSnapshot", "FleetWorker", "MiningService"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AssignResult:
    """Outcome of an assign call."""

    worker: Worker
    decision: SwitchDecision
    changed: bool


@dataclass(frozen=True, slots=True)
class FleetWorker:
    """One row of the operator dollars snapshot."""

    worker: Worker
    status: WorkerStatus
    option: ProfitOption | None


@dataclass(frozen=True, slots=True)
class FleetSnapshot:
    """How many dollars the fleet is estimated to be making right now."""

    workers: tuple[FleetWorker, ...]
    online_count: int
    assigned_count: int

    @property
    def worker_count(self) -> int:
        """Enrolled workers, including disabled and pending."""
        return len(self.workers)

    @property
    def estimated_revenue_usd_per_day(self) -> str:
        """Sum of assigned online revenue."""
        return _sum_money(self._online_assigned(), "revenue_usd_per_day")

    @property
    def estimated_cost_usd_per_day(self) -> str:
        """Sum of assigned online electricity."""
        return _sum_money(self._online_assigned(), "cost_usd_per_day")

    @property
    def estimated_profit_usd_per_day(self) -> str:
        """Sum of assigned online profit — the number the product exists for."""
        return _sum_money(self._online_assigned(), "profit_usd_per_day")

    def _online_assigned(self) -> tuple[ProfitOption, ...]:
        return tuple(
            row.option
            for row in self.workers
            if row.status is WorkerStatus.ONLINE and row.option is not None
        )


def _sum_money(options: Sequence[ProfitOption], field: str) -> str:
    from m4d.domain.money import ZERO

    total = ZERO
    for option in options:
        total = total + getattr(option, field)
    return total.as_str()


class MiningService:
    """Use cases over the mining catalog, fleet, market, and ranking."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def create_coin(self, request: NewCoin) -> Coin:
        """List a coin the fleet may mine."""
        coin = request.materialise(now=self._clock.now())
        async with self._uow_factory() as uow:
            existing = await uow.coins.find_by_ticker(coin.ticker)
            if existing is not None:
                raise ConflictError(f"Coin '{coin.ticker}' is already listed.")
            stored = await uow.coins.add(coin)
            await record_activity(
                uow,
                self._clock,
                kind="mining.coin.listed",
                payload={"coin_id": str(stored.id), "ticker": stored.ticker},
            )
            await uow.commit()
        return stored

    async def get_coin(self, coin_id: UUID) -> Coin:
        """Return one coin."""
        async with self._uow_factory() as uow:
            coin = await uow.coins.get(coin_id)
        if coin is None:
            raise NotFoundError("Coin", coin_id)
        return coin

    async def list_coins(self) -> tuple[Coin, ...]:
        """Return the coin catalog."""
        async with self._uow_factory() as uow:
            coins = await uow.coins.list_all()
        return tuple(coins)

    async def create_pool(self, request: NewPool) -> Pool:
        """Register a pool endpoint."""
        pool = request.materialise(now=self._clock.now())
        async with self._uow_factory() as uow:
            if await uow.coins.get(pool.coin_id) is None:
                raise NotFoundError("Coin", pool.coin_id)
            stored = await uow.pools.add(pool)
            await record_activity(
                uow,
                self._clock,
                kind="mining.pool.registered",
                payload={"pool_id": str(stored.id), "coin_id": str(stored.coin_id)},
            )
            await uow.commit()
        return stored

    async def list_pools(self) -> tuple[Pool, ...]:
        """Return every pool."""
        async with self._uow_factory() as uow:
            pools = await uow.pools.list_all()
        return tuple(pools)

    async def enrol_worker(self, request: NewWorker) -> Worker:
        """Enrol a mining worker."""
        worker = request.materialise(now=self._clock.now())
        async with self._uow_factory() as uow:
            existing = await uow.workers.find_by_name(worker.name)
            if existing is not None:
                raise ConflictError(f"Worker '{worker.name}' is already enrolled.")
            stored = await uow.workers.add(worker)
            await record_activity(
                uow,
                self._clock,
                kind="mining.worker.enrolled",
                payload={"worker_id": str(stored.id), "name": stored.name},
            )
            await uow.commit()
        return stored

    async def get_worker(self, worker_id: UUID) -> Worker:
        """Return one worker."""
        async with self._uow_factory() as uow:
            worker = await uow.workers.get(worker_id)
        if worker is None:
            raise NotFoundError("Worker", worker_id)
        return worker

    async def list_workers(
        self, *, cursor_token: str | None = None, limit: int | None = None
    ) -> Page[Worker]:
        """Return one page of workers, newest enrolment first."""
        page_size = normalise_page_size(limit)
        cursor = Cursor.decode(cursor_token) if cursor_token else None
        async with self._uow_factory() as uow:
            rows = await uow.workers.list_page(after=cursor, limit=page_size + 1)
        items = tuple(rows[:page_size])
        next_cursor = (
            Cursor(occurred_at=items[-1].created_at, id=items[-1].id).encode()
            if len(rows) > page_size and items
            else None
        )
        return Page(items=items, next_cursor=next_cursor)

    async def set_capabilities(self, worker_id: UUID, capabilities: Sequence[Capability]) -> Worker:
        """Replace a worker's benchmarked hashrates."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            worker = await self._require_worker(uow, worker_id)
            updated = await uow.workers.save(worker.with_capabilities(tuple(capabilities), now=now))
            await record_activity(
                uow,
                self._clock,
                kind="mining.worker.capabilities_set",
                payload={
                    "worker_id": str(updated.id),
                    "algorithms": [capability.algorithm for capability in updated.capabilities],
                },
            )
            await uow.commit()
        return updated

    async def heartbeat(self, worker_id: UUID, sample: Heartbeat) -> Worker:
        """Record telemetry and mark the worker seen."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            worker = await self._require_worker(uow, worker_id)
            updated = await uow.workers.save(worker.with_heartbeat(sample, now=now))
            await record_activity(
                uow,
                self._clock,
                kind="mining.worker.heartbeat",
                payload={
                    "worker_id": str(updated.id),
                    "algorithm": updated.last_algorithm,
                    "hashrate_hps": (
                        updated.last_hashrate.as_str()
                        if updated.last_hashrate is not None
                        else None
                    ),
                },
            )
            await uow.commit()
        return updated

    async def set_enabled(self, worker_id: UUID, enabled: bool) -> Worker:
        """Pause or resume a worker."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            worker = await self._require_worker(uow, worker_id)
            updated = await uow.workers.save(worker.with_enabled(enabled, now=now))
            await record_activity(
                uow,
                self._clock,
                kind="mining.worker.enabled" if enabled else "mining.worker.disabled",
                payload={"worker_id": str(updated.id)},
            )
            await uow.commit()
        return updated

    async def ingest_quotes(self, requests: Sequence[NewQuote]) -> tuple[Quote, ...]:
        """Record a market snapshot. Each item is one coin's estimated revenue."""
        if not requests:
            raise ValidationError("A quote snapshot must contain at least one quote.")
        stored: list[Quote] = []
        async with self._uow_factory() as uow:
            for request in requests:
                coin = await uow.coins.get(request.coin_id)
                if coin is None:
                    raise NotFoundError("Coin", request.coin_id)
                if coin.algorithm != request.algorithm:
                    raise ValidationError(
                        "Quote algorithm does not match the coin.",
                        coin=coin.ticker,
                        coin_algorithm=coin.algorithm,
                        quote_algorithm=request.algorithm,
                    )
                quote = request.materialise(now=self._clock.now())
                stored.append(await uow.quotes.add(quote))
            await record_activity(
                uow,
                self._clock,
                kind="mining.quote.ingested",
                payload={"count": len(stored), "coin_ids": [str(item.coin_id) for item in stored]},
            )
            await uow.commit()
        return tuple(stored)

    async def latest_quotes(self) -> tuple[Quote, ...]:
        """Return the newest quote per coin."""
        async with self._uow_factory() as uow:
            quotes = await uow.quotes.latest_per_coin()
        return tuple(quotes)

    async def profitability(self, worker_id: UUID) -> tuple[ProfitOption, ...]:
        """Rank every coin this worker can mine, best profit first."""
        async with self._uow_factory() as uow:
            worker = await self._require_worker(uow, worker_id)
            coins = await uow.coins.list_all()
            pools = await uow.pools.list_all()
            quotes = latest_quote_per_coin(tuple(await uow.quotes.latest_per_coin()))
        return rank_options(worker, coins, quotes, pools)

    async def assign(
        self,
        worker_id: UUID,
        *,
        coin_id: UUID | None = None,
    ) -> AssignResult:
        """Point a worker at the most profitable coin, or a forced one."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            worker = await self._require_worker(uow, worker_id)
            if not worker.enabled:
                raise ValidationError(
                    "A disabled worker cannot be assigned.", worker_id=str(worker_id)
                )
            coins = await uow.coins.list_all()
            pools = await uow.pools.list_all()
            quotes = latest_quote_per_coin(tuple(await uow.quotes.latest_per_coin()))
            options = rank_options(worker, coins, quotes, pools)
            decision = decide_assignment(options, current=worker.assignment, force_coin_id=coin_id)
            if not decision.should_switch:
                if decision.reason == "forced_coin_not_mineable":
                    raise ValidationError(
                        "That coin is not mineable on this worker.",
                        worker_id=str(worker_id),
                        coin_id=str(coin_id),
                    )
                if decision.reason == "no_profitable_option" and coin_id is None:
                    raise ValidationError(
                        "No profitable coin is available; the worker was left unassigned.",
                        worker_id=str(worker_id),
                    )
                return AssignResult(worker=worker, decision=decision, changed=False)

            recommended = decision.recommended
            if recommended is None:  # pragma: no cover - decide_assignment contract
                raise ValidationError("Assignment produced no recommended coin.")
            reason = (
                AssignmentReason.OPERATOR
                if coin_id is not None
                else AssignmentReason.MOST_PROFITABLE
            )
            updated = await uow.workers.save(
                worker.with_assignment(
                    assignment_from_option(recommended, now=now, reason=reason),
                    now=now,
                )
            )
            await record_activity(
                uow,
                self._clock,
                kind="mining.assignment.applied",
                payload={
                    "worker_id": str(updated.id),
                    "coin_id": str(recommended.coin_id),
                    "ticker": recommended.ticker,
                    "profit_usd_per_day": recommended.profit_usd_per_day.as_str(),
                    "reason": decision.reason,
                },
            )
            await uow.commit()
        return AssignResult(worker=updated, decision=decision, changed=True)

    async def fleet(self) -> FleetSnapshot:
        """Operator snapshot: who is mining, and how many dollars per day."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            workers = await uow.workers.list_all()
            coins = await uow.coins.list_all()
            pools = await uow.pools.list_all()
            quotes = latest_quote_per_coin(tuple(await uow.quotes.latest_per_coin()))

        rows: list[FleetWorker] = []
        assigned = 0
        online = 0
        for worker in workers:
            status = worker.status_at(now)
            if status is WorkerStatus.ONLINE:
                online += 1
            option: ProfitOption | None = None
            if worker.assignment is not None:
                assigned += 1
                ranked = rank_options(worker, coins, quotes, pools)
                option = next(
                    (item for item in ranked if item.coin_id == worker.assignment.coin_id),
                    None,
                )
                if option is None:
                    # Quote or coin disappeared; still report the stored estimate.
                    option = ProfitOption(
                        coin_id=worker.assignment.coin_id,
                        ticker="?",
                        algorithm=worker.assignment.algorithm,
                        pool_id=worker.assignment.pool_id,
                        hashrate_hps=(
                            worker.last_hashrate.as_str()
                            if worker.last_hashrate is not None
                            else "0"
                        ),
                        revenue_usd_per_day=worker.assignment.revenue_usd_per_day,
                        cost_usd_per_day=worker.assignment.cost_usd_per_day,
                        profit_usd_per_day=worker.assignment.profit_usd_per_day,
                        is_current=True,
                    )
            rows.append(FleetWorker(worker=worker, status=status, option=option))

        return FleetSnapshot(
            workers=tuple(rows),
            online_count=online,
            assigned_count=assigned,
        )

    @staticmethod
    async def _require_worker(uow: UnitOfWork, worker_id: UUID) -> Worker:
        worker = await uow.workers.get(worker_id)
        if worker is None:
            raise NotFoundError("Worker", worker_id)
        return worker
