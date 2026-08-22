"""In-memory implementations of the domain ports.

Their existence is the point of the port abstraction: the service layer can be
exercised exhaustively, including its concurrency handling, with no database and
no I/O. If these fakes were hard to write, the ports would be badly drawn.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from uuid import UUID

from m4d.domain.antivirus import Finding, FindingFilter, Scan, ScanFilter
from m4d.domain.endpoints import Endpoint, EndpointFilter, EndpointStatus
from m4d.domain.errors import ConflictError
from m4d.domain.events import EventFilter, EventSeverity, SystemEvent
from m4d.domain.optimizers import OptimizationPlan, PlanFilter, PlanStatus
from m4d.domain.pagination import Cursor

__all__ = [
    "FakeEndpointRepository",
    "FakeEventRepository",
    "FakeFindingRepository",
    "FakePlanRepository",
    "FakeScanRepository",
    "FakeUnitOfWork",
]


class FakeEventRepository:
    """A dictionary pretending to be the event table."""

    def __init__(self) -> None:
        self.by_id: dict[UUID, SystemEvent] = {}
        self.by_key: dict[str, SystemEvent] = {}

    async def add(self, event: SystemEvent) -> SystemEvent:
        """Store ``event``, enforcing the idempotency key's uniqueness."""
        if event.idempotency_key is not None and event.idempotency_key in self.by_key:
            # Mirrors the unique index in PostgreSQL. Without this the fake
            # would be more forgiving than production and the service's race
            # handling would go untested.
            raise ConflictError("An event with this idempotency key already exists.")
        self.by_id[event.id] = event
        if event.idempotency_key is not None:
            self.by_key[event.idempotency_key] = event
        return event

    async def get(self, event_id: UUID) -> SystemEvent | None:
        return self.by_id.get(event_id)

    async def find_by_idempotency_key(self, key: str) -> SystemEvent | None:
        return self.by_key.get(key)

    async def list_page(
        self,
        *,
        filters: EventFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[SystemEvent]:
        """Apply the same ordering and filtering semantics as the real store."""
        events = sorted(
            self.by_id.values(), key=lambda event: (event.occurred_at, event.id), reverse=True
        )
        events = [event for event in events if _event_matches(event, filters)]

        if after is not None:
            events = [
                event
                for event in events
                if (event.occurred_at, event.id) < (after.occurred_at, after.id)
            ]

        return events[:limit]


def _event_matches(event: SystemEvent, filters: EventFilter) -> bool:
    """Whether ``event`` satisfies ``filters``."""
    if filters.source is not None and event.source != filters.source:
        return False
    if filters.kind is not None and event.kind != filters.kind:
        return False
    if filters.min_severity is not None and event.severity not in EventSeverity.at_or_above(
        filters.min_severity
    ):
        return False
    if filters.occurred_after is not None and event.occurred_at <= filters.occurred_after:
        return False
    return not (
        filters.occurred_before is not None and event.occurred_at >= filters.occurred_before
    )


class FakeEndpointRepository:
    """A dictionary pretending to be the endpoint table."""

    def __init__(self) -> None:
        self.by_id: dict[UUID, Endpoint] = {}
        self.by_hostname: dict[str, Endpoint] = {}

    async def add(self, endpoint: Endpoint) -> Endpoint:
        if endpoint.hostname in self.by_hostname:
            raise ConflictError("An endpoint with this hostname is already enrolled.")
        self.by_id[endpoint.id] = endpoint
        self.by_hostname[endpoint.hostname] = endpoint
        return endpoint

    async def save(self, endpoint: Endpoint) -> Endpoint:
        previous = self.by_id[endpoint.id]
        if previous.hostname != endpoint.hostname:
            del self.by_hostname[previous.hostname]
        self.by_id[endpoint.id] = endpoint
        self.by_hostname[endpoint.hostname] = endpoint
        return endpoint

    async def get(self, endpoint_id: UUID) -> Endpoint | None:
        return self.by_id.get(endpoint_id)

    async def find_by_hostname(self, hostname: str) -> Endpoint | None:
        return self.by_hostname.get(hostname)

    async def list_page(
        self,
        *,
        filters: EndpointFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[Endpoint]:
        items = sorted(
            self.by_id.values(), key=lambda item: (item.last_seen_at, item.id), reverse=True
        )
        items = [item for item in items if _endpoint_matches(item, filters)]
        if after is not None:
            items = [
                item
                for item in items
                if (item.last_seen_at, item.id) < (after.occurred_at, after.id)
            ]
        return items[:limit]

    async def count(self, *, status: EndpointStatus | None = None) -> int:
        if status is None:
            return len(self.by_id)
        return sum(1 for item in self.by_id.values() if item.status is status)


def _endpoint_matches(endpoint: Endpoint, filters: EndpointFilter) -> bool:
    """Whether ``endpoint`` satisfies ``filters``."""
    if filters.status is not None and endpoint.status is not filters.status:
        return False
    if filters.role is not None and endpoint.role is not filters.role:
        return False
    if filters.platform is not None and endpoint.platform is not filters.platform:
        return False
    return not (filters.hostname is not None and endpoint.hostname != filters.hostname)


class FakeScanRepository:
    """A dictionary pretending to be the scan table."""

    def __init__(self) -> None:
        self.by_id: dict[UUID, Scan] = {}
        self.by_key: dict[str, Scan] = {}

    async def add(self, scan: Scan) -> Scan:
        if scan.idempotency_key is not None and scan.idempotency_key in self.by_key:
            raise ConflictError("A scan with this idempotency key already exists.")
        self.by_id[scan.id] = scan
        if scan.idempotency_key is not None:
            self.by_key[scan.idempotency_key] = scan
        return scan

    async def save(self, scan: Scan) -> Scan:
        self.by_id[scan.id] = scan
        if scan.idempotency_key is not None:
            self.by_key[scan.idempotency_key] = scan
        return scan

    async def get(self, scan_id: UUID) -> Scan | None:
        return self.by_id.get(scan_id)

    async def find_by_idempotency_key(self, key: str) -> Scan | None:
        return self.by_key.get(key)

    async def list_page(
        self,
        *,
        filters: ScanFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[Scan]:
        items = sorted(
            self.by_id.values(), key=lambda item: (item.queued_at, item.id), reverse=True
        )
        items = [item for item in items if _scan_matches(item, filters)]
        if after is not None:
            items = [
                item for item in items if (item.queued_at, item.id) < (after.occurred_at, after.id)
            ]
        return items[:limit]

    async def count_in_flight(self) -> int:
        return sum(1 for item in self.by_id.values() if item.status.is_in_flight)


def _scan_matches(scan: Scan, filters: ScanFilter) -> bool:
    """Whether ``scan`` satisfies ``filters``."""
    if filters.endpoint_id is not None and scan.endpoint_id != filters.endpoint_id:
        return False
    if filters.status is not None and scan.status is not filters.status:
        return False
    return not (filters.kind is not None and scan.kind is not filters.kind)


class FakeFindingRepository:
    """A dictionary pretending to be the finding table."""

    def __init__(self) -> None:
        self.by_id: dict[UUID, Finding] = {}
        self.by_key: dict[str, Finding] = {}

    async def add(self, finding: Finding) -> Finding:
        if finding.idempotency_key is not None and finding.idempotency_key in self.by_key:
            raise ConflictError("A finding with this idempotency key already exists.")
        self.by_id[finding.id] = finding
        if finding.idempotency_key is not None:
            self.by_key[finding.idempotency_key] = finding
        return finding

    async def save(self, finding: Finding) -> Finding:
        self.by_id[finding.id] = finding
        if finding.idempotency_key is not None:
            self.by_key[finding.idempotency_key] = finding
        return finding

    async def get(self, finding_id: UUID) -> Finding | None:
        return self.by_id.get(finding_id)

    async def find_by_idempotency_key(self, key: str) -> Finding | None:
        return self.by_key.get(key)

    async def list_page(
        self,
        *,
        filters: FindingFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[Finding]:
        items = sorted(
            self.by_id.values(), key=lambda item: (item.recorded_at, item.id), reverse=True
        )
        items = [item for item in items if _finding_matches(item, filters)]
        if after is not None:
            items = [
                item
                for item in items
                if (item.recorded_at, item.id) < (after.occurred_at, after.id)
            ]
        return items[:limit]

    async def list_open_for_endpoint(self, endpoint_id: UUID) -> Sequence[Finding]:
        items = [
            item
            for item in self.by_id.values()
            if item.endpoint_id == endpoint_id and item.status.is_open
        ]
        return sorted(items, key=lambda item: (item.recorded_at, item.id), reverse=True)

    async def count_open(self, *, actionable_only: bool = False) -> int:
        items = [item for item in self.by_id.values() if item.status.is_open]
        if actionable_only:
            items = [item for item in items if item.severity.is_actionable]
        return len(items)


def _finding_matches(finding: Finding, filters: FindingFilter) -> bool:
    """Whether ``finding`` satisfies ``filters``."""
    if filters.endpoint_id is not None and finding.endpoint_id != filters.endpoint_id:
        return False
    if filters.scan_id is not None and finding.scan_id != filters.scan_id:
        return False
    if filters.status is not None and finding.status is not filters.status:
        return False
    if filters.category is not None and finding.category is not filters.category:
        return False
    return not (
        filters.min_severity is not None
        and finding.severity not in EventSeverity.at_or_above(filters.min_severity)
    )


class FakePlanRepository:
    """A dictionary pretending to be the optimizer plan table."""

    def __init__(self) -> None:
        self.by_id: dict[UUID, OptimizationPlan] = {}
        self.by_key: dict[str, OptimizationPlan] = {}

    async def add(self, plan: OptimizationPlan) -> OptimizationPlan:
        if plan.idempotency_key is not None and plan.idempotency_key in self.by_key:
            raise ConflictError("A plan with this idempotency key already exists.")
        self.by_id[plan.id] = plan
        if plan.idempotency_key is not None:
            self.by_key[plan.idempotency_key] = plan
        return plan

    async def save(self, plan: OptimizationPlan) -> OptimizationPlan:
        self.by_id[plan.id] = plan
        if plan.idempotency_key is not None:
            self.by_key[plan.idempotency_key] = plan
        return plan

    async def get(self, plan_id: UUID) -> OptimizationPlan | None:
        return self.by_id.get(plan_id)

    async def find_by_idempotency_key(self, key: str) -> OptimizationPlan | None:
        return self.by_key.get(key)

    async def list_page(
        self,
        *,
        filters: PlanFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[OptimizationPlan]:
        items = sorted(
            self.by_id.values(), key=lambda item: (item.proposed_at, item.id), reverse=True
        )
        items = [item for item in items if _plan_matches(item, filters)]
        if after is not None:
            items = [
                item
                for item in items
                if (item.proposed_at, item.id) < (after.occurred_at, after.id)
            ]
        return items[:limit]

    async def count(self, *, status: PlanStatus | None = None) -> int:
        if status is None:
            return len(self.by_id)
        return sum(1 for item in self.by_id.values() if item.status is status)


def _plan_matches(plan: OptimizationPlan, filters: PlanFilter) -> bool:
    """Whether ``plan`` satisfies ``filters``."""
    if filters.endpoint_id is not None and plan.endpoint_id != filters.endpoint_id:
        return False
    if filters.status is not None and plan.status is not filters.status:
        return False
    return not (filters.category is not None and plan.category is not filters.category)


class FakeUnitOfWork:
    """A unit of work that records how it was used.

    ``committed`` and ``rolled_back`` let tests assert that a service actually
    closed its transaction, which is the failure the real implementation is
    designed to make visible.
    """

    def __init__(
        self,
        repository: FakeEventRepository | None = None,
        *,
        endpoints: FakeEndpointRepository | None = None,
        scans: FakeScanRepository | None = None,
        findings: FakeFindingRepository | None = None,
        plans: FakePlanRepository | None = None,
    ) -> None:
        self.events = repository or FakeEventRepository()
        self.endpoints = endpoints or FakeEndpointRepository()
        self.scans = scans or FakeScanRepository()
        self.findings = findings or FakeFindingRepository()
        self.plans = plans or FakePlanRepository()
        self.committed = False
        self.rolled_back = False
        self.entered = 0

    async def __aenter__(self) -> FakeUnitOfWork:
        self.entered += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.committed:
            self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
