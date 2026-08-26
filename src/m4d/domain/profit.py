"""Profit ranking: which coin makes the most dollars after electricity.

This is the original product. Everything else in the mining slice exists to
feed this calculation:

    profit = (quoted revenue scaled to this rig's hashrate) - (24h electricity)

Ranking lives in the domain so it is unit-testable without a database, a
market adapter, or HTTP. A later adapter can fetch WhatToMine (or anywhere);
it cannot replace the arithmetic.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from m4d.domain.coins import Coin, Pool, pick_pool_for_coin
from m4d.domain.money import Money
from m4d.domain.quotes import Quote
from m4d.domain.workers import Assignment, AssignmentReason, Worker

__all__ = [
    "DEFAULT_SWITCH_MARGIN",
    "ProfitOption",
    "SwitchDecision",
    "assignment_from_option",
    "decide_assignment",
    "rank_options",
]

#: A new coin must beat the current assignment by this much USD/day before we
#: switch. Without a margin the fleet flaps on quote noise.
DEFAULT_SWITCH_MARGIN = Money("0.10")


@dataclass(frozen=True, slots=True)
class ProfitOption:
    """One (worker, coin) pair scored in dollars per day."""

    coin_id: UUID
    ticker: str
    algorithm: str
    pool_id: UUID | None
    hashrate_hps: str
    revenue_usd_per_day: Money
    cost_usd_per_day: Money
    profit_usd_per_day: Money
    is_current: bool

    @property
    def is_profitable(self) -> bool:
        """Whether this option covers electricity."""
        return self.profit_usd_per_day.is_positive


@dataclass(frozen=True, slots=True)
class SwitchDecision:
    """Whether to move a worker, and why.

    ``should_switch`` is the only thing an assigner has to read. ``reason`` is
    for the event log and the operator.
    """

    should_switch: bool
    recommended: ProfitOption | None
    current: ProfitOption | None
    reason: str


def rank_options(
    worker: Worker,
    coins: Sequence[Coin],
    quotes: Mapping[UUID, Quote],
    pools: Sequence[Pool] = (),
) -> tuple[ProfitOption, ...]:
    """Score every coin this worker can actually mine, best profit first.

    A coin is eligible when it is enabled, the worker has a capability for its
    algorithm, and a quote exists. Electricity is subtracted per algorithm so a
    high-gross thirsty coin can lose to a modest efficient one — which is the
    whole product.
    """
    current_id = worker.assignment.coin_id if worker.assignment is not None else None
    options: list[ProfitOption] = []

    for coin in coins:
        if not coin.enabled:
            continue
        capability = worker.capability_for(coin.algorithm)
        if capability is None:
            continue
        quote = quotes.get(coin.id)
        if quote is None:
            continue
        if quote.algorithm != coin.algorithm:
            continue

        revenue = quote.revenue_for(capability.hashrate)
        cost = worker.daily_electricity_cost(coin.algorithm)
        pool = pick_pool_for_coin(tuple(pools), coin.id)
        options.append(
            ProfitOption(
                coin_id=coin.id,
                ticker=coin.ticker,
                algorithm=coin.algorithm,
                pool_id=pool.id if pool is not None else None,
                hashrate_hps=capability.hashrate.as_str(),
                revenue_usd_per_day=revenue,
                cost_usd_per_day=cost,
                profit_usd_per_day=revenue - cost,
                is_current=coin.id == current_id,
            )
        )

    options.sort(key=lambda option: (-option.profit_usd_per_day.amount, option.ticker))
    return tuple(options)


def decide_assignment(
    options: Sequence[ProfitOption],
    *,
    current: Assignment | None,
    margin: Money = DEFAULT_SWITCH_MARGIN,
    force_coin_id: UUID | None = None,
) -> SwitchDecision:
    """Choose whether to (re)assign, with hysteresis against quote noise.

    Auto-assign only commits to a *profitable* coin. An operator force can
    point at any scored option, including a loss, because that is a human
    decision rather than the product's.
    """
    by_coin = {option.coin_id: option for option in options}
    current_option = by_coin.get(current.coin_id) if current is not None else None

    if force_coin_id is not None:
        forced = by_coin.get(force_coin_id)
        if forced is None:
            return SwitchDecision(
                should_switch=False,
                recommended=None,
                current=current_option,
                reason="forced_coin_not_mineable",
            )
        already = current is not None and current.coin_id == force_coin_id
        return SwitchDecision(
            should_switch=not already,
            recommended=forced,
            current=current_option,
            reason="operator" if not already else "already_on_forced_coin",
        )

    profitable = tuple(option for option in options if option.is_profitable)
    if not profitable:
        return SwitchDecision(
            should_switch=False,
            recommended=None,
            current=current_option,
            reason="no_profitable_option",
        )

    best = profitable[0]

    if current_option is None:
        return SwitchDecision(
            should_switch=True,
            recommended=best,
            current=None,
            reason="most_profitable",
        )

    if best.coin_id == current_option.coin_id:
        return SwitchDecision(
            should_switch=False,
            recommended=best,
            current=current_option,
            reason="already_on_best",
        )

    gain = best.profit_usd_per_day - current_option.profit_usd_per_day
    if gain < margin:
        return SwitchDecision(
            should_switch=False,
            recommended=current_option,
            current=current_option,
            reason="switch_margin_not_met",
        )

    return SwitchDecision(
        should_switch=True,
        recommended=best,
        current=current_option,
        reason="most_profitable",
    )


def assignment_from_option(
    option: ProfitOption,
    *,
    now: dt.datetime,
    reason: AssignmentReason,
) -> Assignment:
    """Materialise a scored option as a stored assignment."""
    return Assignment(
        coin_id=option.coin_id,
        algorithm=option.algorithm,
        pool_id=option.pool_id,
        revenue_usd_per_day=option.revenue_usd_per_day,
        cost_usd_per_day=option.cost_usd_per_day,
        profit_usd_per_day=option.profit_usd_per_day,
        assigned_at=now,
        reason=reason,
    )
