"""Liveness and readiness reporting.

The two questions are different and conflating them causes outages:

* **Liveness** — is this process functioning? A failure here should restart it.
* **Readiness** — can it serve traffic right now? A failure here should remove
  it from the load balancer but leave it running.

If readiness checked the database and doubled as liveness, a brief database
blip would restart every pod at once and turn a recoverable incident into a
cold-start stampede.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from dataclasses import dataclass

from m4d.db.engine import Database

__all__ = ["HealthReport", "HealthService", "HealthStatus"]

logger = logging.getLogger(__name__)

# A readiness probe must answer faster than the prober's own timeout, so the
# check is bounded independently of the general statement timeout.
READINESS_TIMEOUT_SECONDS = 2.0


class HealthStatus(enum.StrEnum):
    """Aggregate health verdict."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class DependencyCheck:
    """The result of probing one dependency."""

    name: str
    healthy: bool
    latency_ms: float
    error: str | None = None


@dataclass(frozen=True, slots=True)
class HealthReport:
    """The outcome of a readiness probe."""

    status: HealthStatus
    checks: tuple[DependencyCheck, ...]

    @property
    def is_healthy(self) -> bool:
        """Whether every dependency passed."""
        return self.status is HealthStatus.HEALTHY


class HealthService:
    """Probes the dependencies this service cannot serve traffic without."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def readiness(self) -> HealthReport:
        """Probe every required dependency and summarise."""
        checks = (await self._check_database(),)
        status = (
            HealthStatus.HEALTHY
            if all(check.healthy for check in checks)
            else HealthStatus.UNHEALTHY
        )
        return HealthReport(status=status, checks=checks)

    async def _check_database(self) -> DependencyCheck:
        """Round-trip the database under a hard timeout."""
        started = time.perf_counter()
        try:
            async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
                await self._database.check()
        except Exception as exc:
            # Deliberately broad (TimeoutError included): any driver, network,
            # or pool failure means the same thing to a load balancer.
            # Narrowing it here would let an unanticipated exception escape and
            # turn a clean "not ready" into a 500 the prober cannot interpret.
            elapsed = (time.perf_counter() - started) * 1000
            logger.warning("readiness check failed", exc_info=exc, extra={"dependency": "database"})
            return DependencyCheck(
                name="database",
                healthy=False,
                latency_ms=round(elapsed, 2),
                error=type(exc).__name__,
            )

        elapsed = (time.perf_counter() - started) * 1000
        return DependencyCheck(name="database", healthy=True, latency_ms=round(elapsed, 2))
