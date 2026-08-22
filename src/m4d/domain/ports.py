"""Ports: the interfaces the domain requires of the outside world.

These are :class:`~typing.Protocol` definitions, so implementations satisfy them
structurally and never import the domain to inherit from it. Dependencies point
inward; the database layer knows about the domain, and the domain knows only
about these shapes.

The practical payoff is that services are unit-testable against a dictionary
without a database, and that swapping storage does not touch business logic.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from types import TracebackType
from typing import Protocol, runtime_checkable
from uuid import UUID

from m4d.domain.antivirus import Finding, FindingFilter, Scan, ScanFilter
from m4d.domain.endpoints import Endpoint, EndpointFilter, EndpointStatus
from m4d.domain.events import EventFilter, SystemEvent
from m4d.domain.optimizers import OptimizationPlan, PlanFilter, PlanStatus
from m4d.domain.pagination import Cursor

__all__ = [
    "Clock",
    "EndpointRepository",
    "EventRepository",
    "FindingRepository",
    "OptimizationPlanRepository",
    "ScanRepository",
    "UnitOfWork",
]


@runtime_checkable
class EventRepository(Protocol):
    """Persistence for :class:`~m4d.domain.events.SystemEvent`.

    Note the absence of update and delete: the event log is append-only.
    """

    async def add(self, event: SystemEvent) -> SystemEvent:
        """Persist ``event`` and return it."""
        ...

    async def get(self, event_id: UUID) -> SystemEvent | None:
        """Return the event with ``event_id``, or ``None``."""
        ...

    async def find_by_idempotency_key(self, key: str) -> SystemEvent | None:
        """Return the event previously recorded under ``key``, or ``None``."""
        ...

    async def list_page(
        self,
        *,
        filters: EventFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[SystemEvent]:
        """Return up to ``limit`` events matching ``filters``, after ``after``.

        Ordered by ``(occurred_at DESC, id DESC)`` — newest first.
        """
        ...


@runtime_checkable
class EndpointRepository(Protocol):
    """Persistence for :class:`~m4d.domain.endpoints.Endpoint`."""

    async def add(self, endpoint: Endpoint) -> Endpoint:
        """Insert ``endpoint``.

        Raises:
            ConflictError: if the hostname is already enrolled.
        """
        ...

    async def save(self, endpoint: Endpoint) -> Endpoint:
        """Replace the persisted row for an already-enrolled endpoint."""
        ...

    async def get(self, endpoint_id: UUID) -> Endpoint | None:
        """Return the endpoint with ``endpoint_id``, or ``None``."""
        ...

    async def find_by_hostname(self, hostname: str) -> Endpoint | None:
        """Return the endpoint enrolled under ``hostname``, or ``None``."""
        ...

    async def list_page(
        self,
        *,
        filters: EndpointFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[Endpoint]:
        """Return up to ``limit`` endpoints, most recently seen first."""
        ...

    async def count(self, *, status: EndpointStatus | None = None) -> int:
        """Return how many endpoints match ``status``, or the fleet size."""
        ...


@runtime_checkable
class ScanRepository(Protocol):
    """Persistence for :class:`~m4d.domain.antivirus.Scan`."""

    async def add(self, scan: Scan) -> Scan:
        """Insert ``scan``.

        Raises:
            ConflictError: if its idempotency key is already recorded.
        """
        ...

    async def save(self, scan: Scan) -> Scan:
        """Replace the persisted row for an existing scan."""
        ...

    async def get(self, scan_id: UUID) -> Scan | None:
        """Return the scan with ``scan_id``, or ``None``."""
        ...

    async def find_by_idempotency_key(self, key: str) -> Scan | None:
        """Return the scan previously queued under ``key``, or ``None``."""
        ...

    async def list_page(
        self,
        *,
        filters: ScanFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[Scan]:
        """Return up to ``limit`` scans, most recently queued first."""
        ...

    async def count_in_flight(self) -> int:
        """Return how many scans are queued or running."""
        ...


@runtime_checkable
class FindingRepository(Protocol):
    """Persistence for :class:`~m4d.domain.antivirus.Finding`."""

    async def add(self, finding: Finding) -> Finding:
        """Insert ``finding``.

        Raises:
            ConflictError: if its idempotency key is already recorded.
        """
        ...

    async def save(self, finding: Finding) -> Finding:
        """Replace the persisted row for an existing finding."""
        ...

    async def get(self, finding_id: UUID) -> Finding | None:
        """Return the finding with ``finding_id``, or ``None``."""
        ...

    async def find_by_idempotency_key(self, key: str) -> Finding | None:
        """Return the finding previously recorded under ``key``, or ``None``."""
        ...

    async def list_page(
        self,
        *,
        filters: FindingFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[Finding]:
        """Return up to ``limit`` findings, most recently recorded first."""
        ...

    async def list_open_for_endpoint(self, endpoint_id: UUID) -> Sequence[Finding]:
        """Return every still-open finding on ``endpoint_id``.

        Used by the optimizer, which needs the full set rather than a page.
        """
        ...

    async def count_open(self, *, actionable_only: bool = False) -> int:
        """Return how many findings still need a decision."""
        ...


@runtime_checkable
class OptimizationPlanRepository(Protocol):
    """Persistence for :class:`~m4d.domain.optimizers.OptimizationPlan`."""

    async def add(self, plan: OptimizationPlan) -> OptimizationPlan:
        """Insert ``plan``.

        Raises:
            ConflictError: if its idempotency key is already recorded.
        """
        ...

    async def save(self, plan: OptimizationPlan) -> OptimizationPlan:
        """Replace the persisted row for an existing plan."""
        ...

    async def get(self, plan_id: UUID) -> OptimizationPlan | None:
        """Return the plan with ``plan_id``, or ``None``."""
        ...

    async def find_by_idempotency_key(self, key: str) -> OptimizationPlan | None:
        """Return the plan previously proposed under ``key``, or ``None``."""
        ...

    async def list_page(
        self,
        *,
        filters: PlanFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[OptimizationPlan]:
        """Return up to ``limit`` plans, most recently proposed first."""
        ...

    async def count(self, *, status: PlanStatus | None = None) -> int:
        """Return how many plans match ``status``, or the total."""
        ...


class UnitOfWork(Protocol):
    """A transactional boundary over one or more repositories.

    Services declare what must succeed or fail together; they do not manage
    sessions, connections, or commits. Exiting the context without an explicit
    :meth:`commit` rolls back, so a forgotten commit loses work loudly in tests
    rather than half-writing in production.
    """

    @property
    def events(self) -> EventRepository:
        """The event repository enrolled in this transaction.

        A read-only property rather than a plain attribute so the type is
        covariant: an implementation may expose a concrete
        ``SqlAlchemyEventRepository`` here and still satisfy the port. A mutable
        attribute would be invariant and reject every real implementation.
        """
        ...

    @property
    def endpoints(self) -> EndpointRepository:
        """Fleet inventory enrolled in this transaction."""
        ...

    @property
    def scans(self) -> ScanRepository:
        """Scan jobs enrolled in this transaction."""
        ...

    @property
    def findings(self) -> FindingRepository:
        """Detections enrolled in this transaction."""
        ...

    @property
    def plans(self) -> OptimizationPlanRepository:
        """Optimizer plans enrolled in this transaction."""
        ...

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None:
        """Durably apply everything done in this unit of work."""
        ...

    async def rollback(self) -> None:
        """Discard everything done in this unit of work."""
        ...


class Clock(Protocol):
    """A source of the current time.

    Injected so that time-dependent behaviour is deterministic under test.
    """

    def now(self) -> dt.datetime:
        """Return the current timezone-aware UTC time."""
        ...
