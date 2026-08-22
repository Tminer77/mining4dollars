"""Antivirus: scans, findings, and the classification policy.

The agent on an endpoint examines files. This module is what the control plane
does with the result: record the scan, classify each finding against company
policy, and decide whether the box itself must be isolated.

Classification is a deterministic policy, not a model call. A later adapter can
sit behind :class:`~m4d.domain.ports.ThreatClassifier` and consult a model; the
rules here still run, because "eicar is malware and we isolate it" is not a
prediction. It is policy. The rationale string is what an operator reads when
they ask *why* Shield acted.
"""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from m4d.domain.errors import ConflictError, ValidationError
from m4d.domain.events import EventSeverity
from m4d.domain.primitives import require_text, require_unit_interval

__all__ = [
    "AUTO_QUARANTINE_CONFIDENCE",
    "Classification",
    "Finding",
    "FindingCategory",
    "FindingFilter",
    "FindingStatus",
    "NewFinding",
    "NewScan",
    "Scan",
    "ScanFilter",
    "ScanKind",
    "ScanStatus",
    "classify_finding",
    "materialise_finding",
]

MAX_INDICATOR_LENGTH = 512
MAX_TITLE_LENGTH = 256
MAX_DETAIL_LENGTH = 4000
MAX_RATIONALE_LENGTH = 2000
MAX_ERROR_MESSAGE_LENGTH = 1024
MAX_IDEMPOTENCY_KEY_LENGTH = 200

AUTO_QUARANTINE_CONFIDENCE = 0.90


class ScanKind(enum.StrEnum):
    """How thorough the agent should be."""

    QUICK = "quick"
    FULL = "full"
    CUSTOM = "custom"


class ScanStatus(enum.StrEnum):
    """Lifecycle of a scan job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """Whether the scan can still change."""
        return self in (ScanStatus.COMPLETED, ScanStatus.FAILED)

    @property
    def is_in_flight(self) -> bool:
        """Whether the agent is expected to still be working."""
        return self in (ScanStatus.QUEUED, ScanStatus.RUNNING)


class FindingCategory(enum.StrEnum):
    """What kind of thing was found.

    Categories are closed so the classifier, the optimizer, and the operator
    overview all share one vocabulary. A free-text "type" would fork into
    synonyms on day two.
    """

    MALWARE = "malware"
    PUA = "pua"
    SUSPICIOUS = "suspicious"
    VULNERABILITY = "vulnerability"
    MISCONFIGURATION = "misconfiguration"


class FindingStatus(enum.StrEnum):
    """Disposition of a finding."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    QUARANTINED = "quarantined"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"

    @property
    def is_terminal(self) -> bool:
        """Whether the finding is closed as evidence."""
        return self in (FindingStatus.RESOLVED, FindingStatus.FALSE_POSITIVE)

    @property
    def is_open(self) -> bool:
        """Whether the finding still needs an operator decision."""
        return self in (
            FindingStatus.OPEN,
            FindingStatus.ACKNOWLEDGED,
            FindingStatus.QUARANTINED,
        )

    def can_transition_to(self, target: FindingStatus) -> bool:
        """Whether ``target`` is a legal next state from here."""
        if self is target:
            return False
        if self.is_terminal:
            return False
        allowed: dict[FindingStatus, frozenset[FindingStatus]] = {
            FindingStatus.OPEN: frozenset(
                {
                    FindingStatus.ACKNOWLEDGED,
                    FindingStatus.QUARANTINED,
                    FindingStatus.RESOLVED,
                    FindingStatus.FALSE_POSITIVE,
                }
            ),
            FindingStatus.ACKNOWLEDGED: frozenset(
                {
                    FindingStatus.QUARANTINED,
                    FindingStatus.RESOLVED,
                    FindingStatus.FALSE_POSITIVE,
                }
            ),
            FindingStatus.QUARANTINED: frozenset(
                {FindingStatus.RESOLVED, FindingStatus.FALSE_POSITIVE}
            ),
            FindingStatus.RESOLVED: frozenset(),
            FindingStatus.FALSE_POSITIVE: frozenset(),
        }
        return target in allowed[self]


@dataclass(frozen=True, slots=True)
class NewScan:
    """A request to queue a scan on an enrolled endpoint."""

    endpoint_id: UUID
    kind: ScanKind = ScanKind.QUICK
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
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

    def materialise(self, *, now: dt.datetime) -> Scan:
        """Give this request an identity and a queue time."""
        return Scan(
            id=uuid4(),
            endpoint_id=self.endpoint_id,
            kind=self.kind,
            status=ScanStatus.QUEUED,
            queued_at=now,
            started_at=None,
            completed_at=None,
            files_examined=None,
            findings_count=0,
            error_message=None,
            idempotency_key=self.idempotency_key,
        )


@dataclass(frozen=True, slots=True)
class Scan:
    """A recorded scan job."""

    id: UUID
    endpoint_id: UUID
    kind: ScanKind
    status: ScanStatus
    queued_at: dt.datetime
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    files_examined: int | None
    findings_count: int
    error_message: str | None
    idempotency_key: str | None = None

    def start(self, *, now: dt.datetime) -> Scan:
        """Mark the agent as actively examining the box.

        Raises:
            ConflictError: if the scan is not queued.
        """
        if self.status is not ScanStatus.QUEUED:
            raise ConflictError(
                "Only a queued scan can be started.",
                scan_id=str(self.id),
                status=self.status.value,
            )
        return replace(self, status=ScanStatus.RUNNING, started_at=now)

    def complete(self, *, now: dt.datetime, files_examined: int, findings_count: int) -> Scan:
        """Record a successful finish.

        Raises:
            ConflictError: if the scan is not running.
            ValidationError: if the counts are negative.
        """
        if self.status is not ScanStatus.RUNNING:
            raise ConflictError(
                "Only a running scan can be completed.",
                scan_id=str(self.id),
                status=self.status.value,
            )
        if files_examined < 0:
            raise ValidationError("files_examined must be >= 0.", files_examined=files_examined)
        if findings_count < 0:
            raise ValidationError("findings_count must be >= 0.", findings_count=findings_count)
        return replace(
            self,
            status=ScanStatus.COMPLETED,
            completed_at=now,
            files_examined=files_examined,
            findings_count=findings_count,
            error_message=None,
        )

    def fail(self, *, now: dt.datetime, error_message: str) -> Scan:
        """Record that the agent could not finish.

        Raises:
            ConflictError: if the scan is already terminal.
        """
        if self.status.is_terminal:
            raise ConflictError(
                "A finished scan cannot fail.",
                scan_id=str(self.id),
                status=self.status.value,
            )
        cleaned = require_text(
            error_message, name="error_message", max_length=MAX_ERROR_MESSAGE_LENGTH
        )
        return replace(
            self,
            status=ScanStatus.FAILED,
            completed_at=now,
            error_message=cleaned,
        )

    def with_finding_recorded(self) -> Scan:
        """Increment the finding tally as each one is ingested.

        Allowed on a running or just-completed scan so an agent can stream
        findings during the run and a late one still lands after complete.
        """
        if self.status is ScanStatus.FAILED:
            raise ConflictError(
                "Findings cannot be attached to a failed scan.",
                scan_id=str(self.id),
            )
        if self.status is ScanStatus.QUEUED:
            raise ConflictError(
                "Findings cannot be attached before the scan has started.",
                scan_id=str(self.id),
            )
        return replace(self, findings_count=self.findings_count + 1)


@dataclass(frozen=True, slots=True)
class ScanFilter:
    """Criteria narrowing a scan listing. Unset fields do not constrain."""

    endpoint_id: UUID | None = None
    status: ScanStatus | None = None
    kind: ScanKind | None = None


@dataclass(frozen=True, slots=True)
class NewFinding:
    """A request to record something the agent found.

    ``category`` is the agent's claim. The classifier may confirm, upgrade, or
    disagree; the stored finding carries the *classified* category.
    """

    scan_id: UUID
    endpoint_id: UUID
    category: FindingCategory
    indicator: str
    title: str
    detail: str = ""
    severity: EventSeverity | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "indicator",
            require_text(self.indicator, name="indicator", max_length=MAX_INDICATOR_LENGTH),
        )
        object.__setattr__(
            self, "title", require_text(self.title, name="title", max_length=MAX_TITLE_LENGTH)
        )
        if self.detail:
            object.__setattr__(
                self,
                "detail",
                require_text(self.detail, name="detail", max_length=MAX_DETAIL_LENGTH),
            )
        else:
            object.__setattr__(self, "detail", "")
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


@dataclass(frozen=True, slots=True)
class Classification:
    """The policy engine's judgement of a finding.

    ``recommended_status`` is what Shield will apply *if* confidence clears
    :data:`AUTO_QUARANTINE_CONFIDENCE` for quarantine, or otherwise the initial
    status of a newly recorded finding.
    """

    category: FindingCategory
    severity: EventSeverity
    confidence: float
    recommended_status: FindingStatus
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "confidence", require_unit_interval(self.confidence, name="confidence")
        )
        object.__setattr__(
            self,
            "rationale",
            require_text(self.rationale, name="rationale", max_length=MAX_RATIONALE_LENGTH),
        )

    @property
    def auto_quarantines(self) -> bool:
        """Whether company policy isolates the box on this finding alone."""
        return (
            self.recommended_status is FindingStatus.QUARANTINED
            and self.confidence >= AUTO_QUARANTINE_CONFIDENCE
            and self.category is FindingCategory.MALWARE
        )


@dataclass(frozen=True, slots=True)
class Finding:
    """A recorded detection."""

    id: UUID
    scan_id: UUID
    endpoint_id: UUID
    category: FindingCategory
    severity: EventSeverity
    status: FindingStatus
    indicator: str
    title: str
    detail: str
    ai_confidence: float
    ai_rationale: str
    recorded_at: dt.datetime
    resolved_at: dt.datetime | None = None
    idempotency_key: str | None = None

    @property
    def is_actionable(self) -> bool:
        """Whether this finding should page someone."""
        return self.severity.is_actionable and self.status.is_open

    def dispose(self, *, status: FindingStatus, now: dt.datetime) -> Finding:
        """Apply an operator disposition.

        Raises:
            ConflictError: if the transition is illegal.
        """
        if not self.status.can_transition_to(status):
            raise ConflictError(
                f"A {self.status.value} finding cannot become {status.value}.",
                finding_id=str(self.id),
                from_status=self.status.value,
                to_status=status.value,
            )
        return replace(
            self,
            status=status,
            resolved_at=now if status.is_terminal else self.resolved_at,
        )


@dataclass(frozen=True, slots=True)
class FindingFilter:
    """Criteria narrowing a finding listing. Unset fields do not constrain."""

    endpoint_id: UUID | None = None
    scan_id: UUID | None = None
    status: FindingStatus | None = None
    category: FindingCategory | None = None
    min_severity: EventSeverity | None = None


# ---------------------------------------------------------------------------
# Policy classifier
# ---------------------------------------------------------------------------

_CATEGORY_DEFAULTS: dict[FindingCategory, tuple[EventSeverity, float, FindingStatus]] = {
    FindingCategory.MALWARE: (EventSeverity.ERROR, 0.75, FindingStatus.OPEN),
    FindingCategory.PUA: (EventSeverity.WARNING, 0.70, FindingStatus.ACKNOWLEDGED),
    FindingCategory.SUSPICIOUS: (EventSeverity.WARNING, 0.55, FindingStatus.OPEN),
    FindingCategory.VULNERABILITY: (EventSeverity.ERROR, 0.80, FindingStatus.OPEN),
    FindingCategory.MISCONFIGURATION: (EventSeverity.WARNING, 0.85, FindingStatus.OPEN),
}

# Ordered from most specific to least. The first match wins. Patterns are
# matched against a lowercase blob of indicator + title so an agent that puts
# the family in either field is still classified.
_SIGNATURE_RULES: tuple[tuple[str, Classification], ...] = (
    (
        "eicar",
        Classification(
            category=FindingCategory.MALWARE,
            severity=EventSeverity.CRITICAL,
            confidence=0.99,
            recommended_status=FindingStatus.QUARANTINED,
            rationale=(
                "Matched the EICAR test signature. Company policy treats this as "
                "confirmed malware and isolates the endpoint."
            ),
        ),
    ),
    (
        "family:",
        Classification(
            category=FindingCategory.MALWARE,
            severity=EventSeverity.CRITICAL,
            confidence=0.95,
            recommended_status=FindingStatus.QUARANTINED,
            rationale=(
                "Indicator names a known malware family. Confidence is high enough "
                "that isolation is mandatory; an operator may release after cleanup."
            ),
        ),
    ),
    (
        "hash:",
        Classification(
            category=FindingCategory.MALWARE,
            severity=EventSeverity.ERROR,
            confidence=0.92,
            recommended_status=FindingStatus.QUARANTINED,
            rationale=(
                "A content hash was supplied. Policy quarantines hashed malware "
                "samples pending operator review."
            ),
        ),
    ),
    (
        "pua:",
        Classification(
            category=FindingCategory.PUA,
            severity=EventSeverity.WARNING,
            confidence=0.80,
            recommended_status=FindingStatus.ACKNOWLEDGED,
            rationale=(
                "Potentially unwanted application. Logged and acknowledged; not "
                "isolated, because PUAs are noisy and isolation would strand miners."
            ),
        ),
    ),
    (
        "cve-",
        Classification(
            category=FindingCategory.VULNERABILITY,
            severity=EventSeverity.ERROR,
            confidence=0.88,
            recommended_status=FindingStatus.OPEN,
            rationale=(
                "Named CVE. Left open for the optimizer to propose a hardening "
                "action; a vulnerability is not itself an infection."
            ),
        ),
    ),
    (
        "cve:",
        Classification(
            category=FindingCategory.VULNERABILITY,
            severity=EventSeverity.ERROR,
            confidence=0.88,
            recommended_status=FindingStatus.OPEN,
            rationale=(
                "Named CVE. Left open for the optimizer to propose a hardening "
                "action; a vulnerability is not itself an infection."
            ),
        ),
    ),
    (
        "cfg:",
        Classification(
            category=FindingCategory.MISCONFIGURATION,
            severity=EventSeverity.WARNING,
            confidence=0.90,
            recommended_status=FindingStatus.OPEN,
            rationale=(
                "Configuration drift. The optimizer will propose a hardening plan; "
                "Shield does not isolate a box for a setting."
            ),
        ),
    ),
    (
        "misconfig:",
        Classification(
            category=FindingCategory.MISCONFIGURATION,
            severity=EventSeverity.WARNING,
            confidence=0.90,
            recommended_status=FindingStatus.OPEN,
            rationale=(
                "Configuration drift. The optimizer will propose a hardening plan; "
                "Shield does not isolate a box for a setting."
            ),
        ),
    ),
)


def classify_finding(request: NewFinding) -> Classification:
    """Apply company threat policy to ``request``.

    Signature rules beat the agent's claimed category when they match, because
    an indicator of ``family:emotet`` is malware even if the agent labelled it
    suspicious. When nothing matches, the agent's category is honoured with the
    default confidence for that category. An explicit ``severity`` on the
    request is kept if it is at least as urgent as the policy default, so an
    agent can escalate but cannot quietly downgrade a malware finding.
    """
    blob = f"{request.indicator} {request.title}".lower()
    matched: Classification | None = None
    for needle, classification in _SIGNATURE_RULES:
        if needle in blob:
            matched = classification
            break

    if matched is None:
        severity, confidence, status = _CATEGORY_DEFAULTS[request.category]
        matched = Classification(
            category=request.category,
            severity=severity,
            confidence=confidence,
            recommended_status=status,
            rationale=(
                f"No signature rule matched; honouring the agent's "
                f"{request.category.value} classification at default confidence."
            ),
        )

    if request.severity is not None and request.severity.rank > matched.severity.rank:
        matched = Classification(
            category=matched.category,
            severity=request.severity,
            confidence=matched.confidence,
            recommended_status=matched.recommended_status,
            rationale=matched.rationale + " Agent-supplied severity raised the urgency.",
        )

    return matched


def materialise_finding(
    request: NewFinding, *, classification: Classification, now: dt.datetime
) -> Finding:
    """Build a stored finding from a request and its classification.

    Initial status is the classification's recommendation, except that
    quarantine is only applied when :attr:`Classification.auto_quarantines` is
    true. A recommended quarantine that misses the confidence bar stays open
    so an operator still decides.
    """
    if classification.auto_quarantines:
        status = FindingStatus.QUARANTINED
    else:
        status = (
            classification.recommended_status
            if classification.recommended_status is not FindingStatus.QUARANTINED
            else FindingStatus.OPEN
        )
    return Finding(
        id=uuid4(),
        scan_id=request.scan_id,
        endpoint_id=request.endpoint_id,
        category=classification.category,
        severity=classification.severity,
        status=status,
        indicator=request.indicator,
        title=request.title,
        detail=request.detail,
        ai_confidence=classification.confidence,
        ai_rationale=classification.rationale,
        recorded_at=now,
        resolved_at=None,
        idempotency_key=request.idempotency_key,
    )
