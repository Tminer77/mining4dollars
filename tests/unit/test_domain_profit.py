"""Original-intent tests: mine the coin that makes the most dollars.

These are the product. A worker, two coins, electricity, and a ranking. If
this file disagrees with the code, the code is wrong — not the other way around.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from uuid import uuid4

import pytest

from m4d.domain.coins import NewCoin, NewPool
from m4d.domain.errors import ValidationError
from m4d.domain.hashrate import Hashrate, PowerWatts
from m4d.domain.money import Money
from m4d.domain.profit import (
    DEFAULT_SWITCH_MARGIN,
    assignment_from_option,
    decide_assignment,
    rank_options,
)
from m4d.domain.quotes import NewQuote, latest_quote_per_coin
from m4d.domain.workers import (
    HEARTBEAT_STALE_AFTER,
    AssignmentReason,
    Capability,
    Heartbeat,
    NewWorker,
    WorkerStatus,
)

NOW = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.UTC)
MH = Decimal("1_000_000")


def _worker(
    *,
    name: str = "rig-1",
    watts: int = 1000,
    kwh: str = "0.10",
    capabilities: tuple[Capability, ...] = (),
) -> NewWorker:
    return NewWorker(
        name=name,
        hostname="rig-1.local",
        power=PowerWatts(watts),
        electricity_usd_per_kwh=Money(kwh),
    )


def _coin(ticker: str, algorithm: str, *, enabled: bool = True) -> NewCoin:
    return NewCoin(ticker=ticker, name=ticker, algorithm=algorithm, enabled=enabled)


def _quote(coin_id: object, algorithm: str, revenue: str, *, hps: Decimal = 100 * MH) -> NewQuote:
    from uuid import UUID

    assert isinstance(coin_id, UUID)
    return NewQuote(
        coin_id=coin_id,
        algorithm=algorithm,
        revenue_usd_per_day=Money(revenue),
        reference_hashrate=Hashrate(hps),
        source="whattomine",
    )


class TestMoney:
    def test_quantises_and_adds(self) -> None:
        assert (Money("1.234567891") + Money("0.000000009")).as_str() == "1.23456790"

    def test_rejects_nan(self) -> None:
        with pytest.raises(ValidationError):
            Money("NaN")


class TestOriginalIntent:
    """The 2017 product: which coin makes the most dollars after electricity."""

    def test_picks_the_coin_that_makes_more_dollars_after_electricity(self) -> None:
        """100 MH/s, 1000 W, $0.10/kWh → $2.40/day in power.

        ETHW quoted at $10/day for 100 MH/s → profit $7.60
        ETC  quoted at $5/day  for 100 MH/s → profit $2.60
        The platform must recommend ETHW.
        """
        worker = (
            _worker()
            .materialise(now=NOW)
            .with_capabilities(
                (Capability(algorithm="ethash", hashrate=Hashrate(100 * MH)),),
                now=NOW,
            )
        )
        ethw = _coin("ETHW", "ethash").materialise(now=NOW)
        etc = _coin("ETC", "ethash").materialise(now=NOW)
        quotes = latest_quote_per_coin(
            (
                _quote(ethw.id, "ethash", "10.00").materialise(now=NOW),
                _quote(etc.id, "ethash", "5.00").materialise(now=NOW),
            )
        )

        ranked = rank_options(worker, (ethw, etc), quotes)

        assert [option.ticker for option in ranked] == ["ETHW", "ETC"]
        assert ranked[0].profit_usd_per_day == Money("7.60")
        assert ranked[1].profit_usd_per_day == Money("2.60")
        assert ranked[0].cost_usd_per_day == Money("2.40")

        decision = decide_assignment(ranked, current=None)
        assert decision.should_switch is True
        assert decision.recommended is not None
        assert decision.recommended.ticker == "ETHW"
        assert decision.reason == "most_profitable"

    def test_a_thirsty_high_gross_coin_loses_to_an_efficient_one(self) -> None:
        """Electricity is why this is a dollars product, not a hashrate product.

        KawPow: $12/day gross at 1500 W → cost $3.60 → profit $8.40
        Autolykos: $10/day gross at 200 W → cost $0.48 → profit $9.52
        Autolykos wins even though it prints fewer gross dollars.
        """
        worker = (
            _worker(watts=1500)
            .materialise(now=NOW)
            .with_capabilities(
                (
                    Capability(
                        algorithm="kawpow",
                        hashrate=Hashrate(30 * MH),
                        power=PowerWatts(1500),
                    ),
                    Capability(
                        algorithm="autolykos2",
                        hashrate=Hashrate(100 * MH),
                        power=PowerWatts(200),
                    ),
                ),
                now=NOW,
            )
        )
        rvn = _coin("RVN", "kawpow").materialise(now=NOW)
        erg = _coin("ERG", "autolykos2").materialise(now=NOW)
        quotes = latest_quote_per_coin(
            (
                _quote(rvn.id, "kawpow", "12.00", hps=30 * MH).materialise(now=NOW),
                _quote(erg.id, "autolykos2", "10.00", hps=100 * MH).materialise(now=NOW),
            )
        )

        ranked = rank_options(worker, (rvn, erg), quotes)

        assert [option.ticker for option in ranked] == ["ERG", "RVN"]
        assert ranked[0].profit_usd_per_day == Money("9.52")
        assert ranked[1].profit_usd_per_day == Money("8.40")

    def test_refuses_to_auto_assign_when_every_coin_loses_money(self) -> None:
        """Original intent is mining *for dollars*. Idle beats a guaranteed loss."""
        worker = (
            _worker(watts=1000, kwh="0.20")
            .materialise(now=NOW)
            .with_capabilities(
                (Capability(algorithm="ethash", hashrate=Hashrate(100 * MH)),),
                now=NOW,
            )
        )
        # Cost = 1 kW * 24h * $0.20 = $4.80/day; revenue $3.00 → profit -$1.80
        coin = _coin("ETHW", "ethash").materialise(now=NOW)
        quotes = latest_quote_per_coin((_quote(coin.id, "ethash", "3.00").materialise(now=NOW),))

        ranked = rank_options(worker, (coin,), quotes)
        assert ranked[0].is_profitable is False

        decision = decide_assignment(ranked, current=None)
        assert decision.should_switch is False
        assert decision.reason == "no_profitable_option"

    def test_cannot_mine_a_coin_whose_algorithm_the_rig_does_not_run(self) -> None:
        worker = (
            _worker()
            .materialise(now=NOW)
            .with_capabilities(
                (Capability(algorithm="kawpow", hashrate=Hashrate(30 * MH)),),
                now=NOW,
            )
        )
        btc = _coin("BTC", "sha256").materialise(now=NOW)
        rvn = _coin("RVN", "kawpow").materialise(now=NOW)
        quotes = latest_quote_per_coin(
            (
                _quote(btc.id, "sha256", "100.00", hps=Decimal("1e12")).materialise(now=NOW),
                _quote(rvn.id, "kawpow", "8.00", hps=30 * MH).materialise(now=NOW),
            )
        )

        ranked = rank_options(worker, (btc, rvn), quotes)
        assert [option.ticker for option in ranked] == ["RVN"]

    def test_disabled_coins_are_not_ranked(self) -> None:
        worker = (
            _worker()
            .materialise(now=NOW)
            .with_capabilities(
                (Capability(algorithm="ethash", hashrate=Hashrate(100 * MH)),),
                now=NOW,
            )
        )
        live = _coin("ETHW", "ethash").materialise(now=NOW)
        dead = _coin("ETC", "ethash", enabled=False).materialise(now=NOW)
        quotes = latest_quote_per_coin(
            (
                _quote(live.id, "ethash", "4.00").materialise(now=NOW),
                _quote(dead.id, "ethash", "40.00").materialise(now=NOW),
            )
        )
        ranked = rank_options(worker, (live, dead), quotes)
        assert [option.ticker for option in ranked] == ["ETHW"]

    def test_switch_requires_a_dollar_margin_to_avoid_thrashing(self) -> None:
        worker = (
            _worker()
            .materialise(now=NOW)
            .with_capabilities(
                (Capability(algorithm="ethash", hashrate=Hashrate(100 * MH)),),
                now=NOW,
            )
        )
        ethw = _coin("ETHW", "ethash").materialise(now=NOW)
        etc = _coin("ETC", "ethash").materialise(now=NOW)
        quotes = latest_quote_per_coin(
            (
                _quote(ethw.id, "ethash", "10.00").materialise(now=NOW),
                _quote(etc.id, "ethash", "10.05").materialise(now=NOW),
            )
        )
        ranked = rank_options(worker, (ethw, etc), quotes)
        # ETC is technically ahead by $0.05, under the $0.10 default margin.
        current = assignment_from_option(
            next(option for option in ranked if option.ticker == "ETHW"),
            now=NOW,
            reason=AssignmentReason.MOST_PROFITABLE,
        )
        decision = decide_assignment(ranked, current=current)
        assert decision.should_switch is False
        assert decision.reason == "switch_margin_not_met"
        assert decision.recommended is not None
        assert decision.recommended.ticker == "ETHW"

        wide = decide_assignment(ranked, current=current, margin=Money("0.01"))
        assert wide.should_switch is True
        assert wide.recommended is not None
        assert wide.recommended.ticker == "ETC"

    def test_operator_can_force_an_unprofitable_coin(self) -> None:
        worker = (
            _worker(kwh="0.20")
            .materialise(now=NOW)
            .with_capabilities(
                (Capability(algorithm="ethash", hashrate=Hashrate(100 * MH)),),
                now=NOW,
            )
        )
        coin = _coin("ETHW", "ethash").materialise(now=NOW)
        quotes = latest_quote_per_coin((_quote(coin.id, "ethash", "3.00").materialise(now=NOW),))
        ranked = rank_options(worker, (coin,), quotes)
        decision = decide_assignment(ranked, current=None, force_coin_id=coin.id)
        assert decision.should_switch is True
        assert decision.reason == "operator"
        assert decision.recommended is not None
        assert decision.recommended.is_profitable is False

    def test_hashrate_scales_the_quote(self) -> None:
        """A 50 MH/s rig earns half of a quote published for 100 MH/s."""
        worker = (
            _worker(watts=0)
            .materialise(now=NOW)
            .with_capabilities(
                (Capability(algorithm="ethash", hashrate=Hashrate(50 * MH)),),
                now=NOW,
            )
        )
        coin = _coin("ETHW", "ethash").materialise(now=NOW)
        quotes = latest_quote_per_coin(
            (_quote(coin.id, "ethash", "10.00", hps=100 * MH).materialise(now=NOW),)
        )
        ranked = rank_options(worker, (coin,), quotes)
        assert ranked[0].revenue_usd_per_day == Money("5.00")
        assert ranked[0].profit_usd_per_day == Money("5.00")


class TestWorkerStatus:
    def test_pending_until_the_first_heartbeat(self) -> None:
        worker = _worker().materialise(now=NOW)
        assert worker.status_at(NOW) is WorkerStatus.PENDING

    def test_online_within_the_stale_window(self) -> None:
        worker = (
            _worker()
            .materialise(now=NOW)
            .with_heartbeat(Heartbeat(algorithm="ethash", hashrate=Hashrate(MH)), now=NOW)
        )
        assert worker.status_at(NOW + dt.timedelta(minutes=4, seconds=59)) is WorkerStatus.ONLINE

    def test_offline_after_the_stale_window(self) -> None:
        worker = (
            _worker()
            .materialise(now=NOW)
            .with_heartbeat(Heartbeat(algorithm="ethash", hashrate=Hashrate(MH)), now=NOW)
        )
        assert worker.status_at(NOW + HEARTBEAT_STALE_AFTER + dt.timedelta(seconds=1)) is (
            WorkerStatus.OFFLINE
        )

    def test_disabled_wins_over_a_fresh_heartbeat(self) -> None:
        worker = (
            _worker()
            .materialise(now=NOW)
            .with_heartbeat(Heartbeat(algorithm="ethash", hashrate=Hashrate(MH)), now=NOW)
            .with_enabled(False, now=NOW)
        )
        assert worker.status_at(NOW) is WorkerStatus.DISABLED


class TestPools:
    def test_assignment_records_the_first_enabled_pool(self) -> None:
        worker = (
            _worker()
            .materialise(now=NOW)
            .with_capabilities(
                (Capability(algorithm="kawpow", hashrate=Hashrate(30 * MH)),),
                now=NOW,
            )
        )
        coin = _coin("RVN", "kawpow").materialise(now=NOW)
        pool = NewPool(
            name="2miners",
            coin_id=coin.id,
            url="stratum+tcp://rvn.2miners.com:6060",
        ).materialise(now=NOW)
        quotes = latest_quote_per_coin(
            (_quote(coin.id, "kawpow", "8.00", hps=30 * MH).materialise(now=NOW),)
        )
        ranked = rank_options(worker, (coin,), quotes, pools=(pool,))
        assert ranked[0].pool_id == pool.id


class TestValidation:
    def test_ticker_must_be_uppercase_alnum(self) -> None:
        with pytest.raises(ValidationError):
            NewCoin(ticker="eth!", name="Ethereum", algorithm="ethash")

    def test_capability_hashrate_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            Capability(algorithm="ethash", hashrate=Hashrate(0))

    def test_quote_reference_hashrate_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            NewQuote(
                coin_id=uuid4(),
                algorithm="ethash",
                revenue_usd_per_day=Money("1"),
                reference_hashrate=Hashrate(0),
            )

    def test_default_switch_margin_is_ten_cents(self) -> None:
        assert Money("0.10") == DEFAULT_SWITCH_MARGIN
