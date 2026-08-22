"""Mining use cases on in-memory ports — original intent, end to end."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from m4d.domain.coins import Coin, NewCoin, NewPool
from m4d.domain.errors import ValidationError
from m4d.domain.hashrate import Hashrate, PowerWatts
from m4d.domain.money import Money
from m4d.domain.quotes import NewQuote
from m4d.domain.workers import Capability, Heartbeat, NewWorker, Worker
from m4d.services.clock import FrozenClock
from m4d.services.mining import MiningService
from tests.unit.fakes import (
    FakeCoinRepository,
    FakeEventRepository,
    FakePoolRepository,
    FakeQuoteRepository,
    FakeUnitOfWork,
    FakeWorkerRepository,
)

NOW = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)
MH = Decimal("1_000_000")


class Harness:
    def __init__(self) -> None:
        self.events = FakeEventRepository()
        self.coins = FakeCoinRepository()
        self.pools = FakePoolRepository()
        self.workers = FakeWorkerRepository()
        self.quotes = FakeQuoteRepository()
        self.units: list[FakeUnitOfWork] = []

        def factory() -> FakeUnitOfWork:
            unit = FakeUnitOfWork(
                self.events,
                coins=self.coins,
                pools=self.pools,
                workers=self.workers,
                quotes=self.quotes,
            )
            self.units.append(unit)
            return unit

        self.service = MiningService(uow_factory=factory, clock=FrozenClock(NOW))


@pytest.fixture
def harness() -> Harness:
    return Harness()


async def _listed_ethash_pair(harness: Harness) -> tuple[Coin, Coin, Worker]:
    ethw = await harness.service.create_coin(NewCoin("ETHW", "EthereumPoW", "ethash"))
    etc = await harness.service.create_coin(NewCoin("ETC", "Ethereum Classic", "ethash"))
    worker = await harness.service.enrol_worker(
        NewWorker(
            name="rig-1",
            power=PowerWatts(1000),
            electricity_usd_per_kwh=Money("0.10"),
        )
    )
    worker = await harness.service.set_capabilities(
        worker.id,
        (Capability(algorithm="ethash", hashrate=Hashrate(100 * MH)),),
    )
    await harness.service.ingest_quotes(
        (
            NewQuote(ethw.id, "ethash", Money("10.00"), Hashrate(100 * MH), source="whattomine"),
            NewQuote(etc.id, "ethash", Money("5.00"), Hashrate(100 * MH), source="whattomine"),
        )
    )
    return ethw, etc, worker


class TestOriginalIntent:
    async def test_assign_picks_the_coin_that_makes_more_dollars(self, harness: Harness) -> None:
        ethw, _etc, worker = await _listed_ethash_pair(harness)

        result = await harness.service.assign(worker.id)

        assert result.changed is True
        assert result.worker.assignment is not None
        assert result.worker.assignment.coin_id == ethw.id
        assert result.worker.assignment.profit_usd_per_day == Money("7.60")
        assert result.decision.reason == "most_profitable"

        kinds = [event.kind for event in harness.events.by_id.values()]
        assert "mining.assignment.applied" in kinds
        assert harness.units[-1].committed is True

    async def test_second_assign_is_a_no_op_when_already_on_best(self, harness: Harness) -> None:
        _ethw, _etc, worker = await _listed_ethash_pair(harness)
        first = await harness.service.assign(worker.id)
        second = await harness.service.assign(worker.id)
        assert first.changed is True
        assert second.changed is False
        assert second.decision.reason == "already_on_best"

    async def test_refuses_auto_assign_when_every_coin_loses_money(self, harness: Harness) -> None:
        coin = await harness.service.create_coin(NewCoin("ETHW", "EthereumPoW", "ethash"))
        worker = await harness.service.enrol_worker(
            NewWorker(name="rig-1", power=PowerWatts(1000), electricity_usd_per_kwh=Money("0.20"))
        )
        await harness.service.set_capabilities(
            worker.id, (Capability(algorithm="ethash", hashrate=Hashrate(100 * MH)),)
        )
        await harness.service.ingest_quotes(
            (NewQuote(coin.id, "ethash", Money("3.00"), Hashrate(100 * MH)),)
        )

        with pytest.raises(ValidationError, match="No profitable coin"):
            await harness.service.assign(worker.id)

    async def test_fleet_sums_dollars_only_for_online_assigned_workers(
        self, harness: Harness
    ) -> None:
        _ethw, _etc, worker = await _listed_ethash_pair(harness)
        await harness.service.assign(worker.id)
        # No heartbeat yet: pending, so dollars are not counted as live.
        snapshot = await harness.service.fleet()
        assert snapshot.estimated_profit_usd_per_day == "0.00000000"
        assert snapshot.assigned_count == 1
        assert snapshot.online_count == 0

        await harness.service.heartbeat(
            worker.id, Heartbeat(algorithm="ethash", hashrate=Hashrate(100 * MH))
        )
        live = await harness.service.fleet()
        assert live.online_count == 1
        assert live.estimated_profit_usd_per_day == "7.60000000"
        assert live.estimated_revenue_usd_per_day == "10.00000000"
        assert live.estimated_cost_usd_per_day == "2.40000000"

    async def test_duplicate_worker_name_conflicts(self, harness: Harness) -> None:
        await harness.service.enrol_worker(NewWorker(name="rig-1"))
        from m4d.domain.errors import ConflictError

        with pytest.raises(ConflictError):
            await harness.service.enrol_worker(NewWorker(name="rig-1"))

    async def test_pool_requires_a_listed_coin(self, harness: Harness) -> None:
        from uuid import uuid4

        from m4d.domain.errors import NotFoundError

        with pytest.raises(NotFoundError):
            await harness.service.create_pool(
                NewPool(name="2miners", coin_id=uuid4(), url="stratum+tcp://example:4040")
            )

    async def test_profitability_ranks_before_assign(self, harness: Harness) -> None:
        ethw, etc, worker = await _listed_ethash_pair(harness)
        ranked = await harness.service.profitability(worker.id)
        assert [option.ticker for option in ranked] == ["ETHW", "ETC"]
        assert ranked[0].coin_id == ethw.id
        assert ranked[1].coin_id == etc.id
