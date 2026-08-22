"""Mining workers: the hardware that turns electricity into hashes.

A worker is a rig, appliance, or logical miner. Status is derived from the
last heartbeat rather than stored, so "online" cannot drift from "last seen".
"""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass, field, replace
from decimal import Decimal
from uuid import UUID, uuid4

from m4d.domain.coins import parse_algorithm
from m4d.domain.errors import ValidationError
from m4d.domain.hashrate import Hashrate, PowerWatts
from m4d.domain.money import ZERO, Money
from m4d.domain.primitives import require_aware, require_text

__all__ = [
    "HEARTBEAT_STALE_AFTER",
    "MAX_HOSTNAME_LENGTH",
    "MAX_WORKER_NAME_LENGTH",
    "Assignment",
    "AssignmentReason",
    "Capability",
    "Heartbeat",
    "NewWorker",
    "Worker",
    "WorkerStatus",
]

MAX_WORKER_NAME_LENGTH = 64
MAX_HOSTNAME_LENGTH = 128
HEARTBEAT_STALE_AFTER = dt.timedelta(minutes=5)


class WorkerStatus(enum.StrEnum):
    """Operator-visible state of a worker.

    ``pending`` / ``online`` / ``offline`` are derived from heartbeats.
    ``disabled`` is the only value the operator sets directly.
    """

    PENDING = "pending"
    ONLINE = "online"
    OFFLINE = "offline"
    DISABLED = "disabled"


class AssignmentReason(enum.StrEnum):
    """Why this worker is mining this coin."""

    MOST_PROFITABLE = "most_profitable"
    OPERATOR = "operator"


@dataclass(frozen=True, slots=True)
class Capability:
    """A benchmarked (algorithm, hashrate) pair this worker can run.

    ``power`` is optional. When set, this algorithm's electricity cost uses it
    instead of the worker's default draw — which is how a thirsty algo loses to
    a modest efficient one after the power bill.
    """

    algorithm: str
    hashrate: Hashrate
    power: PowerWatts | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "algorithm", parse_algorithm(self.algorithm))
        if self.hashrate.hps <= 0:
            raise ValidationError(
                "A capability hashrate must be greater than zero.",
                field="hashrate",
                algorithm=self.algorithm,
            )


@dataclass(frozen=True, slots=True)
class Assignment:
    """What a worker is currently pointed at, and what that is estimated to earn."""

    coin_id: UUID
    algorithm: str
    pool_id: UUID | None
    revenue_usd_per_day: Money
    cost_usd_per_day: Money
    profit_usd_per_day: Money
    assigned_at: dt.datetime
    reason: AssignmentReason

    @property
    def ticker_slot(self) -> UUID:
        """Identity used when matching this assignment to a profit option."""
        return self.coin_id


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """A telemetry sample from a worker."""

    algorithm: str | None = None
    hashrate: Hashrate | None = None
    power: PowerWatts | None = None
    occurred_at: dt.datetime | None = None

    def __post_init__(self) -> None:
        if self.algorithm is not None:
            object.__setattr__(self, "algorithm", parse_algorithm(self.algorithm))
        if self.occurred_at is not None:
            object.__setattr__(
                self, "occurred_at", require_aware(self.occurred_at, field="occurred_at")
            )
        if (self.algorithm is None) != (self.hashrate is None):
            raise ValidationError(
                "Heartbeat hashrate and algorithm must be supplied together.",
                field="hashrate",
            )


@dataclass(frozen=True, slots=True)
class NewWorker:
    """A request to enrol a mining worker."""

    name: str
    hostname: str | None = None
    power: PowerWatts = field(default_factory=lambda: PowerWatts(0))
    electricity_usd_per_kwh: Money = ZERO

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "name", require_text(self.name, name="name", max_length=MAX_WORKER_NAME_LENGTH)
        )
        if self.hostname is not None:
            object.__setattr__(
                self,
                "hostname",
                require_text(self.hostname, name="hostname", max_length=MAX_HOSTNAME_LENGTH),
            )
        if self.electricity_usd_per_kwh.is_negative:
            raise ValidationError(
                "Electricity rate cannot be negative.",
                field="electricity_usd_per_kwh",
            )

    def materialise(self, *, now: dt.datetime, worker_id: UUID | None = None) -> Worker:
        """Give this enrolment an identity."""
        return Worker(
            id=worker_id or uuid4(),
            name=self.name,
            hostname=self.hostname,
            enabled=True,
            power=self.power,
            electricity_usd_per_kwh=self.electricity_usd_per_kwh,
            capabilities=(),
            assignment=None,
            last_seen_at=None,
            last_algorithm=None,
            last_hashrate=None,
            created_at=now,
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class Worker:
    """A mining rig (or logical worker) enrolled in the fleet."""

    id: UUID
    name: str
    hostname: str | None
    enabled: bool
    power: PowerWatts
    electricity_usd_per_kwh: Money
    capabilities: tuple[Capability, ...]
    assignment: Assignment | None
    last_seen_at: dt.datetime | None
    last_algorithm: str | None
    last_hashrate: Hashrate | None
    created_at: dt.datetime
    updated_at: dt.datetime

    def status_at(
        self,
        now: dt.datetime,
        *,
        stale_after: dt.timedelta = HEARTBEAT_STALE_AFTER,
    ) -> WorkerStatus:
        """Derive the operator-visible status at ``now``."""
        if not self.enabled:
            return WorkerStatus.DISABLED
        if self.last_seen_at is None:
            return WorkerStatus.PENDING
        if now - self.last_seen_at > stale_after:
            return WorkerStatus.OFFLINE
        return WorkerStatus.ONLINE

    def capability_for(self, algorithm: str) -> Capability | None:
        """Return the capability matching ``algorithm``, if the worker has one."""
        for capability in self.capabilities:
            if capability.algorithm == algorithm:
                return capability
        return None

    def power_for(self, algorithm: str) -> PowerWatts:
        """Draw used when mining ``algorithm``."""
        capability = self.capability_for(algorithm)
        if capability is not None and capability.power is not None:
            return capability.power
        return self.power

    def daily_electricity_cost(self, algorithm: str | None = None) -> Money:
        """USD burned in 24 hours at the relevant draw and tariff."""
        power = self.power if algorithm is None else self.power_for(algorithm)
        kilowatt_hours = power.kilowatts() * Decimal(24)
        return self.electricity_usd_per_kwh.scale(kilowatt_hours)

    def with_capabilities(
        self, capabilities: tuple[Capability, ...], *, now: dt.datetime
    ) -> Worker:
        """Replace the benchmarked hashrates."""
        seen: set[str] = set()
        for capability in capabilities:
            if capability.algorithm in seen:
                raise ValidationError(
                    "Each algorithm may appear only once in a worker's capabilities.",
                    field="capabilities",
                    algorithm=capability.algorithm,
                )
            seen.add(capability.algorithm)
        return self._copy(capabilities=capabilities, updated_at=now)

    def with_heartbeat(self, heartbeat: Heartbeat, *, now: dt.datetime) -> Worker:
        """Apply a telemetry sample.

        Power in the heartbeat, when present, replaces the enrolled draw so
        profitability uses the number the wall is actually seeing.
        """
        occurred = heartbeat.occurred_at or now
        power = heartbeat.power if heartbeat.power is not None else self.power
        return self._copy(
            last_seen_at=occurred,
            last_algorithm=(
                heartbeat.algorithm if heartbeat.algorithm is not None else self.last_algorithm
            ),
            last_hashrate=(
                heartbeat.hashrate if heartbeat.hashrate is not None else self.last_hashrate
            ),
            power=power,
            updated_at=now,
        )

    def with_assignment(self, assignment: Assignment | None, *, now: dt.datetime) -> Worker:
        """Point this worker at a coin, or clear the assignment."""
        return self._copy(assignment=assignment, updated_at=now)

    def with_enabled(self, enabled: bool, *, now: dt.datetime) -> Worker:
        """Pause or resume this worker's participation in ranking."""
        return self._copy(enabled=enabled, updated_at=now)

    def with_power(
        self,
        *,
        power: PowerWatts | None = None,
        electricity_usd_per_kwh: Money | None = None,
        now: dt.datetime,
    ) -> Worker:
        """Update the cost side of the profit equation."""
        rate = (
            self.electricity_usd_per_kwh
            if electricity_usd_per_kwh is None
            else electricity_usd_per_kwh
        )
        if rate.is_negative:
            raise ValidationError(
                "Electricity rate cannot be negative.",
                field="electricity_usd_per_kwh",
            )
        return self._copy(
            power=self.power if power is None else power,
            electricity_usd_per_kwh=rate,
            updated_at=now,
        )

    def _copy(self, **changes: object) -> Worker:
        """Return a copy with ``changes`` applied."""
        return replace(self, **changes)  # type: ignore[arg-type]
