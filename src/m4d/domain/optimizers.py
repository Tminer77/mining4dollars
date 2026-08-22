"""Optimizer plans: the other half of Shield.

Antivirus isolates. The optimizer proposes what to do next — restore hashrate,
close a CVE, drop unused services, refresh signatures — as a plan of discrete
actions an operator can accept, reject, or apply.

Proposal is deterministic policy keyed off the endpoint's role and its open
findings. A later model can sit behind :class:`~m4d.domain.ports.OptimizerEngine`;
the rules here still have to hold, because "do not raise clocks on a quarantined
miner" is not a suggestion.
"""

from __future__ import annotations

import datetime as dt
import enum
from collections.abc import Sequence
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from m4d.domain.antivirus import Finding, FindingCategory
from m4d.domain.endpoints import Endpoint, EndpointRole, EndpointStatus
from m4d.domain.errors import ConflictError, ValidationError
from m4d.domain.primitives import require_text

__all__ = [
    "ActionKind",
    "ActionRisk",
    "ActionStatus",
    "NewOptimizationPlan",
    "OptimizationAction",
    "OptimizationPlan",
    "OptimizerCategory",
    "PlanFilter",
    "PlanStatus",
    "propose_plan",
]

MAX_SUMMARY_LENGTH = 512
MAX_TITLE_LENGTH = 256
MAX_DETAIL_LENGTH = 2000
MAX_GAIN_LENGTH = 128
MAX_RATIONALE_LENGTH = 2000
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_ACTIONS = 16


class OptimizerCategory(enum.StrEnum):
    """The primary intent of a plan.

    A plan can contain mixed actions; the category is the reason it was
    proposed, so an operator can filter "show me security plans" without
    reading every action.
    """

    PERFORMANCE = "performance"
    SECURITY = "security"
    THERMAL = "thermal"
    RESOURCE = "resource"


class PlanStatus(enum.StrEnum):
    """Lifecycle of an optimizer plan."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    APPLIED = "applied"
    REJECTED = "rejected"

    @property
    def is_terminal(self) -> bool:
        """Whether the plan can still change."""
        return self in (PlanStatus.APPLIED, PlanStatus.REJECTED)

    @property
    def is_pending(self) -> bool:
        """Whether an operator still owes a decision."""
        return self in (PlanStatus.PROPOSED, PlanStatus.ACCEPTED)


class ActionKind(enum.StrEnum):
    """Closed vocabulary of things Shield knows how to propose.

    Agents interpret these. Adding a kind is a deliberate product change, not
    a free-text field an agent invents.
    """

    GPU_POWER_LIMIT = "gpu.power_limit"
    GPU_THERMAL_PROFILE = "gpu.thermal_profile"
    OS_DISABLE_UNUSED_SERVICE = "os.disable_unused_service"
    OS_HARDEN_SSH = "os.harden_ssh"
    OS_CLEANUP_TEMP = "os.cleanup_temp"
    OS_HARDEN = "os.harden"
    AV_UPDATE_SIGNATURES = "av.update_signatures"
    AV_SCHEDULE_FULL_SCAN = "av.schedule_full_scan"
    NET_RESTRICT_EGRESS = "net.restrict_egress"


class ActionRisk(enum.StrEnum):
    """How likely the action is to disrupt the workload."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionStatus(enum.StrEnum):
    """Lifecycle of one action inside a plan."""

    PENDING = "pending"
    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OptimizationAction:
    """One recommended change."""

    id: UUID
    kind: ActionKind
    title: str
    detail: str
    risk: ActionRisk
    status: ActionStatus = ActionStatus.PENDING
    expected_gain: str | None = None

    def as_applied(self) -> OptimizationAction:
        """Mark this action carried out."""
        return replace(self, status=ActionStatus.APPLIED)

    def to_record(self) -> dict[str, str | None]:
        """JSON-shaped snapshot for persistence."""
        return {
            "id": str(self.id),
            "kind": self.kind.value,
            "title": self.title,
            "detail": self.detail,
            "risk": self.risk.value,
            "status": self.status.value,
            "expected_gain": self.expected_gain,
        }

    @classmethod
    def from_record(cls, record: dict[str, str | None]) -> OptimizationAction:
        """Rebuild from a persisted snapshot."""
        gain = record.get("expected_gain")
        return cls(
            id=UUID(str(record["id"])),
            kind=ActionKind(str(record["kind"])),
            title=str(record["title"]),
            detail=str(record["detail"]),
            risk=ActionRisk(str(record["risk"])),
            status=ActionStatus(str(record["status"])),
            expected_gain=gain,
        )


@dataclass(frozen=True, slots=True)
class NewOptimizationPlan:
    """A request to store a plan that has already been composed."""

    endpoint_id: UUID
    category: OptimizerCategory
    summary: str
    actions: tuple[OptimizationAction, ...]
    ai_rationale: str
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "summary",
            require_text(self.summary, name="summary", max_length=MAX_SUMMARY_LENGTH),
        )
        object.__setattr__(
            self,
            "ai_rationale",
            require_text(self.ai_rationale, name="ai_rationale", max_length=MAX_RATIONALE_LENGTH),
        )
        if not self.actions:
            raise ValidationError("A plan must contain at least one action.")
        if len(self.actions) > MAX_ACTIONS:
            raise ValidationError(
                f"A plan may contain at most {MAX_ACTIONS} actions.",
                count=len(self.actions),
                max_actions=MAX_ACTIONS,
            )
        if self.idempotency_key is not None:
            object.__setattr__(
                self,
                "idempotency_key",
                require_text(
                    self.idempotency_key,
                    name="idempotency_key",
                    max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
                ),
            )

    def materialise(self, *, now: dt.datetime) -> OptimizationPlan:
        """Give this request an identity and a proposal time."""
        return OptimizationPlan(
            id=uuid4(),
            endpoint_id=self.endpoint_id,
            category=self.category,
            status=PlanStatus.PROPOSED,
            summary=self.summary,
            actions=self.actions,
            ai_rationale=self.ai_rationale,
            proposed_at=now,
            decided_at=None,
            applied_at=None,
            idempotency_key=self.idempotency_key,
        )


@dataclass(frozen=True, slots=True)
class OptimizationPlan:
    """A recorded optimizer plan."""

    id: UUID
    endpoint_id: UUID
    category: OptimizerCategory
    status: PlanStatus
    summary: str
    actions: tuple[OptimizationAction, ...]
    ai_rationale: str
    proposed_at: dt.datetime
    decided_at: dt.datetime | None
    applied_at: dt.datetime | None
    idempotency_key: str | None = None

    @property
    def raises_performance(self) -> bool:
        """Whether applying this plan would fight an isolation decision."""
        return self.category in (OptimizerCategory.PERFORMANCE, OptimizerCategory.THERMAL)

    def accept(self, *, now: dt.datetime) -> OptimizationPlan:
        """Operator agrees; the agent has not yet carried it out.

        Raises:
            ConflictError: if the plan is not currently proposed.
        """
        if self.status is not PlanStatus.PROPOSED:
            raise ConflictError(
                "Only a proposed plan can be accepted.",
                plan_id=str(self.id),
                status=self.status.value,
            )
        return replace(self, status=PlanStatus.ACCEPTED, decided_at=now)

    def reject(self, *, now: dt.datetime) -> OptimizationPlan:
        """Operator declines.

        Raises:
            ConflictError: if the plan is already terminal.
        """
        if self.status.is_terminal:
            raise ConflictError(
                "A finished plan cannot be rejected.",
                plan_id=str(self.id),
                status=self.status.value,
            )
        return replace(self, status=PlanStatus.REJECTED, decided_at=now)

    def apply(self, *, now: dt.datetime, endpoint: Endpoint) -> OptimizationPlan:
        """Mark every pending action applied.

        Applying from ``proposed`` is a shortcut (accept + apply) so an
        operator who trusts the plan does not click twice.

        Raises:
            ConflictError: if the plan is rejected, already applied, or would
                raise performance on a quarantined box.
        """
        if self.status is PlanStatus.REJECTED:
            raise ConflictError(
                "A rejected plan cannot be applied.",
                plan_id=str(self.id),
            )
        if self.status is PlanStatus.APPLIED:
            raise ConflictError(
                "The plan has already been applied.",
                plan_id=str(self.id),
            )
        if self.raises_performance and not endpoint.status.accepts_optimizer_performance:
            raise ConflictError(
                "Performance and thermal plans cannot be applied while the endpoint is isolated.",
                plan_id=str(self.id),
                endpoint_id=str(endpoint.id),
                endpoint_status=endpoint.status.value,
                category=self.category.value,
            )
        applied_actions = tuple(action.as_applied() for action in self.actions)
        decided_at = self.decided_at or now
        return replace(
            self,
            status=PlanStatus.APPLIED,
            actions=applied_actions,
            decided_at=decided_at,
            applied_at=now,
        )


@dataclass(frozen=True, slots=True)
class PlanFilter:
    """Criteria narrowing a plan listing. Unset fields do not constrain."""

    endpoint_id: UUID | None = None
    status: PlanStatus | None = None
    category: OptimizerCategory | None = None


def _action(
    kind: ActionKind,
    *,
    title: str,
    detail: str,
    risk: ActionRisk,
    expected_gain: str | None = None,
) -> OptimizationAction:
    """Build a pending action with a fresh identity."""
    return OptimizationAction(
        id=uuid4(),
        kind=kind,
        title=title,
        detail=detail,
        risk=risk,
        expected_gain=expected_gain,
    )


def propose_plan(
    endpoint: Endpoint, findings: Sequence[Finding], *, now: dt.datetime
) -> OptimizationPlan:
    """Compose a plan from the endpoint's role and its still-open findings.

    Raises:
        ConflictError: if the endpoint is retiring.
        ValidationError: if nothing useful can be proposed.
    """
    if endpoint.status is EndpointStatus.RETIRING:
        raise ConflictError(
            "A retiring endpoint is not optimized.",
            endpoint_id=str(endpoint.id),
        )

    open_findings = [finding for finding in findings if finding.status.is_open]
    malware = [f for f in open_findings if f.category is FindingCategory.MALWARE]
    misconfigs = [
        f
        for f in open_findings
        if f.category in (FindingCategory.MISCONFIGURATION, FindingCategory.VULNERABILITY)
    ]

    actions: list[OptimizationAction] = []
    kinds: set[ActionKind] = set()

    def add(action: OptimizationAction) -> None:
        if action.kind not in kinds:
            kinds.add(action.kind)
            actions.append(action)

    if malware:
        add(
            _action(
                ActionKind.AV_UPDATE_SIGNATURES,
                title="Refresh threat signatures",
                detail=(
                    f"{len(malware)} open malware finding(s) on {endpoint.hostname}. "
                    "Pull the latest signature pack before the next full scan."
                ),
                risk=ActionRisk.LOW,
                expected_gain="close detection gap on known families",
            )
        )
        add(
            _action(
                ActionKind.AV_SCHEDULE_FULL_SCAN,
                title="Schedule a full rescan",
                detail="A full scan after signature refresh is what confirms cleanup.",
                risk=ActionRisk.LOW,
                expected_gain="re-verify the box after isolation",
            )
        )

    if misconfigs:
        add(
            _action(
                ActionKind.OS_HARDEN,
                title="Apply security hardening",
                detail=(
                    f"{len(misconfigs)} open misconfiguration or vulnerability "
                    f"finding(s). Tighten the settings the agent reported."
                ),
                risk=ActionRisk.MEDIUM,
                expected_gain="close exposed configuration drift",
            )
        )
        if endpoint.role in (EndpointRole.SERVER, EndpointRole.GATEWAY):
            add(
                _action(
                    ActionKind.OS_HARDEN_SSH,
                    title="Harden SSH",
                    detail=(
                        "Disable password auth and restrict listen addresses "
                        "on the management plane."
                    ),
                    risk=ActionRisk.MEDIUM,
                    expected_gain="reduce remote-attack surface",
                )
            )
            add(
                _action(
                    ActionKind.NET_RESTRICT_EGRESS,
                    title="Restrict unexpected egress",
                    detail=(
                        "Allow mining pools, signature updates, and the "
                        "control plane; deny the rest."
                    ),
                    risk=ActionRisk.HIGH,
                    expected_gain="cut command-and-control paths",
                )
            )

    if endpoint.status.accepts_optimizer_performance and not malware:
        if endpoint.role is EndpointRole.MINER:
            add(
                _action(
                    ActionKind.GPU_THERMAL_PROFILE,
                    title="Apply the mining thermal profile",
                    detail=(
                        "Cap fan-curve overshoot and hold VRAM in the efficient "
                        "band so hashrate stays up without thermal throttle."
                    ),
                    risk=ActionRisk.LOW,
                    expected_gain="3-5% sustained hashrate, fewer thermal trips",
                )
            )
            add(
                _action(
                    ActionKind.GPU_POWER_LIMIT,
                    title="Set an efficient power limit",
                    detail="Drop the power target to the last known efficient watt/hash point.",
                    risk=ActionRisk.MEDIUM,
                    expected_gain="8-12% lower watts at near-constant hashrate",
                )
            )
        elif endpoint.role is EndpointRole.WORKSTATION:
            add(
                _action(
                    ActionKind.OS_DISABLE_UNUSED_SERVICE,
                    title="Disable unused startup services",
                    detail="Remove auto-start entries that are not in the company baseline.",
                    risk=ActionRisk.LOW,
                    expected_gain="faster boot, less background CPU",
                )
            )
            add(
                _action(
                    ActionKind.OS_CLEANUP_TEMP,
                    title="Clear stale temp and cache",
                    detail="Reclaim disk from leftover installer and browser cache.",
                    risk=ActionRisk.LOW,
                    expected_gain="recover working disk headroom",
                )
            )
        elif endpoint.role in (EndpointRole.SERVER, EndpointRole.GATEWAY) and not misconfigs:
            add(
                _action(
                    ActionKind.OS_CLEANUP_TEMP,
                    title="Clear stale logs and temp",
                    detail="Rotate and drop logs older than the retention window.",
                    risk=ActionRisk.LOW,
                    expected_gain="recover disk for telemetry",
                )
            )

    if not actions:
        raise ValidationError(
            "No optimizer actions are indicated for this endpoint.",
            endpoint_id=str(endpoint.id),
            role=endpoint.role.value,
            status=endpoint.status.value,
        )

    if malware or misconfigs:
        category = OptimizerCategory.SECURITY
        summary = f"Secure and recover {endpoint.hostname}"
        rationale = (
            "Security findings outrank performance. Signatures, a full rescan, "
            "and hardening go first; performance plans wait until the box is trusted."
        )
    elif endpoint.role is EndpointRole.MINER:
        category = OptimizerCategory.PERFORMANCE
        summary = f"Tune hashrate and thermals on {endpoint.hostname}"
        rationale = (
            "Miner with a clean finding set. Thermal profile and an efficient "
            "power limit are the default company tune."
        )
    else:
        category = OptimizerCategory.RESOURCE
        summary = f"Reclaim resources on {endpoint.hostname}"
        rationale = "No open security findings. Propose low-risk cleanup only."

    request = NewOptimizationPlan(
        endpoint_id=endpoint.id,
        category=category,
        summary=summary,
        actions=tuple(actions),
        ai_rationale=rationale,
    )
    return request.materialise(now=now)
