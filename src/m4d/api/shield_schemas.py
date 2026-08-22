"""Wire models for the Shield control plane.

Kept separate from the event schemas so the antivirus and optimizer contract
can evolve without dragging the activity log along with it.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from m4d.domain.antivirus import (
    MAX_DETAIL_LENGTH,
    MAX_ERROR_MESSAGE_LENGTH,
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_INDICATOR_LENGTH,
    MAX_TITLE_LENGTH,
    Finding,
    FindingCategory,
    FindingStatus,
    NewFinding,
    NewScan,
    Scan,
    ScanKind,
    ScanStatus,
)
from m4d.domain.endpoints import (
    MAX_AGENT_VERSION_LENGTH,
    MAX_HOSTNAME_LENGTH,
    MAX_QUARANTINE_REASON_LENGTH,
    Endpoint,
    EndpointPlatform,
    EndpointRole,
    EndpointStatus,
    FleetSnapshot,
    NewEndpoint,
)
from m4d.domain.events import EventSeverity
from m4d.domain.optimizers import (
    ActionKind,
    ActionRisk,
    ActionStatus,
    OptimizationAction,
    OptimizationPlan,
    OptimizerCategory,
    PlanStatus,
)
from m4d.domain.pagination import Page

__all__ = [
    "EndpointCreateRequest",
    "EndpointPageResponse",
    "EndpointResponse",
    "FindingCreateRequest",
    "FindingDispositionRequest",
    "FindingPageResponse",
    "FindingResponse",
    "FleetSnapshotResponse",
    "HeartbeatRequest",
    "OptimizerProposeRequest",
    "PlanActionResponse",
    "PlanPageResponse",
    "PlanResponse",
    "QuarantineRequest",
    "ScanCompleteRequest",
    "ScanCreateRequest",
    "ScanFailRequest",
    "ScanPageResponse",
    "ScanResponse",
]


class _Schema(BaseModel):
    """Base for every Shield wire model."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EndpointCreateRequest(_Schema):
    """Body of ``POST /v1/endpoints``."""

    hostname: Annotated[str, Field(min_length=1, max_length=MAX_HOSTNAME_LENGTH)]
    platform: EndpointPlatform
    role: EndpointRole
    agent_version: Annotated[str | None, Field(max_length=MAX_AGENT_VERSION_LENGTH)] = None
    labels: dict[str, str] = Field(default_factory=dict)

    def to_domain(self) -> NewEndpoint:
        """Translate into the domain's own request object."""
        return NewEndpoint(
            hostname=self.hostname,
            platform=self.platform,
            role=self.role,
            agent_version=self.agent_version,
            labels=self.labels,
        )


class HeartbeatRequest(_Schema):
    """Body of ``POST /v1/endpoints/{id}/heartbeat``."""

    agent_version: Annotated[str | None, Field(max_length=MAX_AGENT_VERSION_LENGTH)] = None
    labels: dict[str, str] | None = None


class QuarantineRequest(_Schema):
    """Body of ``POST /v1/endpoints/{id}/quarantine``."""

    reason: Annotated[str, Field(min_length=1, max_length=MAX_QUARANTINE_REASON_LENGTH)]


class EndpointResponse(_Schema):
    """An enrolled machine as returned to clients."""

    id: str
    hostname: str
    platform: EndpointPlatform
    role: EndpointRole
    status: EndpointStatus
    agent_version: str | None
    labels: dict[str, str]
    last_seen_at: dt.datetime
    registered_at: dt.datetime
    quarantine_reason: str | None

    @classmethod
    def from_domain(cls, endpoint: Endpoint) -> Self:
        """Build a response from a domain entity."""
        return cls(
            id=str(endpoint.id),
            hostname=endpoint.hostname,
            platform=endpoint.platform,
            role=endpoint.role,
            status=endpoint.status,
            agent_version=endpoint.agent_version,
            labels=dict(endpoint.labels),
            last_seen_at=endpoint.last_seen_at,
            registered_at=endpoint.registered_at,
            quarantine_reason=endpoint.quarantine_reason,
        )


class EndpointPageResponse(_Schema):
    """One page of endpoints."""

    items: list[EndpointResponse]
    next_cursor: str | None

    @classmethod
    def from_domain(cls, page: Page[Endpoint]) -> Self:
        """Build a response from a domain page."""
        return cls(
            items=[EndpointResponse.from_domain(item) for item in page.items],
            next_cursor=page.next_cursor,
        )


class FleetSnapshotResponse(_Schema):
    """Body of ``GET /v1/fleet``."""

    endpoints_total: int
    endpoints_online: int
    endpoints_offline: int
    endpoints_quarantined: int
    scans_in_flight: int
    findings_open: int
    findings_open_actionable: int
    plans_pending: int

    @classmethod
    def from_domain(cls, snapshot: FleetSnapshot) -> Self:
        """Build a response from a domain snapshot."""
        return cls(
            endpoints_total=snapshot.endpoints_total,
            endpoints_online=snapshot.endpoints_online,
            endpoints_offline=snapshot.endpoints_offline,
            endpoints_quarantined=snapshot.endpoints_quarantined,
            scans_in_flight=snapshot.scans_in_flight,
            findings_open=snapshot.findings_open,
            findings_open_actionable=snapshot.findings_open_actionable,
            plans_pending=snapshot.plans_pending,
        )


class ScanCreateRequest(_Schema):
    """Body of ``POST /v1/endpoints/{id}/scans``."""

    kind: ScanKind = ScanKind.QUICK
    idempotency_key: Annotated[str | None, Field(max_length=MAX_IDEMPOTENCY_KEY_LENGTH)] = None

    def to_domain(self, *, endpoint_id: UUID) -> NewScan:
        """Translate into the domain's own request object."""
        return NewScan(
            endpoint_id=endpoint_id, kind=self.kind, idempotency_key=self.idempotency_key
        )


class ScanCompleteRequest(_Schema):
    """Body of ``POST /v1/scans/{id}/complete``."""

    files_examined: Annotated[int, Field(ge=0)]


class ScanFailRequest(_Schema):
    """Body of ``POST /v1/scans/{id}/fail``."""

    error_message: Annotated[str, Field(min_length=1, max_length=MAX_ERROR_MESSAGE_LENGTH)]


class ScanResponse(_Schema):
    """A scan job as returned to clients."""

    id: str
    endpoint_id: str
    kind: ScanKind
    status: ScanStatus
    queued_at: dt.datetime
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    files_examined: int | None
    findings_count: int
    error_message: str | None
    idempotency_key: str | None

    @classmethod
    def from_domain(cls, scan: Scan) -> Self:
        """Build a response from a domain entity."""
        return cls(
            id=str(scan.id),
            endpoint_id=str(scan.endpoint_id),
            kind=scan.kind,
            status=scan.status,
            queued_at=scan.queued_at,
            started_at=scan.started_at,
            completed_at=scan.completed_at,
            files_examined=scan.files_examined,
            findings_count=scan.findings_count,
            error_message=scan.error_message,
            idempotency_key=scan.idempotency_key,
        )


class ScanPageResponse(_Schema):
    """One page of scans."""

    items: list[ScanResponse]
    next_cursor: str | None

    @classmethod
    def from_domain(cls, page: Page[Scan]) -> Self:
        """Build a response from a domain page."""
        return cls(
            items=[ScanResponse.from_domain(item) for item in page.items],
            next_cursor=page.next_cursor,
        )


class FindingCreateRequest(_Schema):
    """Body of ``POST /v1/scans/{id}/findings``."""

    endpoint_id: UUID
    category: FindingCategory
    indicator: Annotated[str, Field(min_length=1, max_length=MAX_INDICATOR_LENGTH)]
    title: Annotated[str, Field(min_length=1, max_length=MAX_TITLE_LENGTH)]
    detail: Annotated[str, Field(max_length=MAX_DETAIL_LENGTH)] = ""
    severity: EventSeverity | None = None
    idempotency_key: Annotated[str | None, Field(max_length=MAX_IDEMPOTENCY_KEY_LENGTH)] = None

    def to_domain(self, *, scan_id: UUID) -> NewFinding:
        """Translate into the domain's own request object."""
        return NewFinding(
            scan_id=scan_id,
            endpoint_id=self.endpoint_id,
            category=self.category,
            indicator=self.indicator,
            title=self.title,
            detail=self.detail,
            severity=self.severity,
            idempotency_key=self.idempotency_key,
        )


class FindingDispositionRequest(_Schema):
    """Body of ``POST /v1/findings/{id}/disposition``."""

    status: FindingStatus


class FindingResponse(_Schema):
    """A classified detection as returned to clients."""

    id: str
    scan_id: str
    endpoint_id: str
    category: FindingCategory
    severity: EventSeverity
    status: FindingStatus
    indicator: str
    title: str
    detail: str
    ai_confidence: float
    ai_rationale: str
    recorded_at: dt.datetime
    resolved_at: dt.datetime | None
    idempotency_key: str | None
    actionable: bool

    @classmethod
    def from_domain(cls, finding: Finding) -> Self:
        """Build a response from a domain entity."""
        return cls(
            id=str(finding.id),
            scan_id=str(finding.scan_id),
            endpoint_id=str(finding.endpoint_id),
            category=finding.category,
            severity=finding.severity,
            status=finding.status,
            indicator=finding.indicator,
            title=finding.title,
            detail=finding.detail,
            ai_confidence=finding.ai_confidence,
            ai_rationale=finding.ai_rationale,
            recorded_at=finding.recorded_at,
            resolved_at=finding.resolved_at,
            idempotency_key=finding.idempotency_key,
            actionable=finding.is_actionable,
        )


class FindingPageResponse(_Schema):
    """One page of findings."""

    items: list[FindingResponse]
    next_cursor: str | None

    @classmethod
    def from_domain(cls, page: Page[Finding]) -> Self:
        """Build a response from a domain page."""
        return cls(
            items=[FindingResponse.from_domain(item) for item in page.items],
            next_cursor=page.next_cursor,
        )


class OptimizerProposeRequest(_Schema):
    """Body of ``POST /v1/endpoints/{id}/optimizer/plans``."""

    idempotency_key: Annotated[str | None, Field(max_length=MAX_IDEMPOTENCY_KEY_LENGTH)] = None


class PlanActionResponse(_Schema):
    """One action inside an optimizer plan."""

    id: str
    kind: ActionKind
    title: str
    detail: str
    risk: ActionRisk
    status: ActionStatus
    expected_gain: str | None

    @classmethod
    def from_domain(cls, action: OptimizationAction) -> Self:
        """Build a response from a domain value object."""
        return cls(
            id=str(action.id),
            kind=action.kind,
            title=action.title,
            detail=action.detail,
            risk=action.risk,
            status=action.status,
            expected_gain=action.expected_gain,
        )


class PlanResponse(_Schema):
    """An optimizer plan as returned to clients."""

    id: str
    endpoint_id: str
    category: OptimizerCategory
    status: PlanStatus
    summary: str
    actions: list[PlanActionResponse]
    ai_rationale: str
    proposed_at: dt.datetime
    decided_at: dt.datetime | None
    applied_at: dt.datetime | None
    idempotency_key: str | None

    @classmethod
    def from_domain(cls, plan: OptimizationPlan) -> Self:
        """Build a response from a domain entity."""
        return cls(
            id=str(plan.id),
            endpoint_id=str(plan.endpoint_id),
            category=plan.category,
            status=plan.status,
            summary=plan.summary,
            actions=[PlanActionResponse.from_domain(action) for action in plan.actions],
            ai_rationale=plan.ai_rationale,
            proposed_at=plan.proposed_at,
            decided_at=plan.decided_at,
            applied_at=plan.applied_at,
            idempotency_key=plan.idempotency_key,
        )


class PlanPageResponse(_Schema):
    """One page of optimizer plans."""

    items: list[PlanResponse]
    next_cursor: str | None

    @classmethod
    def from_domain(cls, page: Page[OptimizationPlan]) -> Self:
        """Build a response from a domain page."""
        return cls(
            items=[PlanResponse.from_domain(item) for item in page.items],
            next_cursor=page.next_cursor,
        )
