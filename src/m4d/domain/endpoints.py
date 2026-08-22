"""Fleet inventory: the machines Shield is responsible for.

An endpoint is a company asset — a mining rig, a workstation, a gateway, a
server. The control plane does not scan it itself; the agent on the box does.
This module is the register of those boxes and the rules that govern whether
they are trusted to keep running.
"""

from __future__ import annotations

import datetime as dt
import enum
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from uuid import UUID, uuid4

from m4d.domain.errors import ConflictError, ValidationError
from m4d.domain.primitives import require_text

__all__ = [
    "Endpoint",
    "EndpointFilter",
    "EndpointPlatform",
    "EndpointRole",
    "EndpointStatus",
    "FleetSnapshot",
    "NewEndpoint",
]

MAX_HOSTNAME_LENGTH = 255
MAX_AGENT_VERSION_LENGTH = 64
MAX_QUARANTINE_REASON_LENGTH = 512
MAX_LABEL_KEY_LENGTH = 64
MAX_LABEL_VALUE_LENGTH = 128
MAX_LABELS = 32


class EndpointPlatform(enum.StrEnum):
    """Operating system family the agent reports."""

    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"


class EndpointRole(enum.StrEnum):
    """What the machine is for.

    Role is a first-class value rather than a free-text label because the
    optimizer keys off it: a miner is tuned for hashrate and thermals, a
    gateway is tuned for exposure, a workstation is tuned for the people on it.
    """

    MINER = "miner"
    WORKSTATION = "workstation"
    GATEWAY = "gateway"
    SERVER = "server"


class EndpointStatus(enum.StrEnum):
    """Operational state of the machine from the control plane's point of view."""

    ONLINE = "online"
    OFFLINE = "offline"
    QUARANTINED = "quarantined"
    RETIRING = "retiring"

    @property
    def accepts_optimizer_performance(self) -> bool:
        """Whether performance/thermal plans may be applied.

        A quarantined box is isolated on purpose; raising clocks on it would
        fight the isolation. Security and cleanup plans still may.
        """
        return self is EndpointStatus.ONLINE


@dataclass(frozen=True, slots=True)
class NewEndpoint:
    """A request to enrol a machine that does not yet have an identity."""

    hostname: str
    platform: EndpointPlatform
    role: EndpointRole
    agent_version: str | None = None
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "hostname",
            require_text(self.hostname, name="hostname", max_length=MAX_HOSTNAME_LENGTH).lower(),
        )
        if self.agent_version is not None:
            object.__setattr__(
                self,
                "agent_version",
                require_text(
                    self.agent_version,
                    name="agent_version",
                    max_length=MAX_AGENT_VERSION_LENGTH,
                ),
            )
        object.__setattr__(self, "labels", _normalise_labels(self.labels))

    def materialise(self, *, now: dt.datetime) -> Endpoint:
        """Give this request an identity and a first-seen time."""
        return Endpoint(
            id=uuid4(),
            hostname=self.hostname,
            platform=self.platform,
            role=self.role,
            status=EndpointStatus.ONLINE,
            agent_version=self.agent_version,
            labels=dict(self.labels),
            last_seen_at=now,
            registered_at=now,
            quarantine_reason=None,
        )


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A recorded company machine.

    Attributes:
        id: Server-assigned identity.
        hostname: Unique, lower-cased DNS name or inventory tag.
        platform: OS family.
        role: What the machine is for; drives optimizer policy.
        status: Whether the control plane currently trusts it to run.
        agent_version: Last reported Shield agent version, if any.
        labels: Operator-supplied inventory tags (site, rack, gpu_model, …).
        last_seen_at: Most recent heartbeat or registration.
        registered_at: When the control plane first enrolled it.
        quarantine_reason: Set only while ``status`` is quarantined.
    """

    id: UUID
    hostname: str
    platform: EndpointPlatform
    role: EndpointRole
    status: EndpointStatus
    agent_version: str | None
    labels: Mapping[str, str]
    last_seen_at: dt.datetime
    registered_at: dt.datetime
    quarantine_reason: str | None = None

    @property
    def is_quarantined(self) -> bool:
        """Whether the machine is isolated from the rest of the fleet."""
        return self.status is EndpointStatus.QUARANTINED

    def heartbeat(
        self,
        *,
        now: dt.datetime,
        agent_version: str | None = None,
        labels: Mapping[str, str] | None = None,
    ) -> Endpoint:
        """Record that the agent is still alive.

        A heartbeat never lifts quarantine: isolation is an operator decision,
        not something an infected box can talk its way out of. It does mark an
        ``offline`` box ``online`` again, because a heartbeat is the definition
        of being reachable.
        """
        version = self.agent_version
        if agent_version is not None:
            version = require_text(
                agent_version, name="agent_version", max_length=MAX_AGENT_VERSION_LENGTH
            )

        new_status = self.status
        if self.status is EndpointStatus.OFFLINE:
            new_status = EndpointStatus.ONLINE

        new_labels = dict(self.labels) if labels is None else _normalise_labels(labels)
        return replace(
            self,
            last_seen_at=now,
            agent_version=version,
            status=new_status,
            labels=new_labels,
        )

    def rebind(
        self,
        *,
        now: dt.datetime,
        agent_version: str | None,
        labels: Mapping[str, str],
        platform: EndpointPlatform,
        role: EndpointRole,
    ) -> Endpoint:
        """Refresh inventory on a re-registration of the same hostname.

        Agents reinstall. The hostname is the natural key, so a second register
        is a rebind, not a duplicate — unless the box is quarantined, in which
        case the control plane keeps it isolated and only updates last-seen.
        """
        refreshed = replace(self, platform=platform, role=role)
        return refreshed.heartbeat(now=now, agent_version=agent_version, labels=labels)

    def quarantine(self, *, reason: str, now: dt.datetime) -> Endpoint:
        """Isolate the machine.

        Raises:
            ConflictError: if it is already quarantined, or is retiring.
        """
        if self.status is EndpointStatus.RETIRING:
            raise ConflictError(
                "A retiring endpoint cannot be quarantined.",
                endpoint_id=str(self.id),
                status=self.status.value,
            )
        if self.status is EndpointStatus.QUARANTINED:
            raise ConflictError(
                "The endpoint is already quarantined.",
                endpoint_id=str(self.id),
            )
        cleaned = require_text(
            reason, name="quarantine_reason", max_length=MAX_QUARANTINE_REASON_LENGTH
        )
        return replace(
            self,
            status=EndpointStatus.QUARANTINED,
            quarantine_reason=cleaned,
            last_seen_at=now,
        )

    def release(self, *, now: dt.datetime) -> Endpoint:
        """Return a quarantined machine to the fleet.

        Does not resolve findings: those are evidence and are closed on their
        own path. Releasing only restores trust in the box itself.

        Raises:
            ConflictError: if the endpoint is not currently quarantined.
        """
        if self.status is not EndpointStatus.QUARANTINED:
            raise ConflictError(
                "Only a quarantined endpoint can be released.",
                endpoint_id=str(self.id),
                status=self.status.value,
            )
        return replace(
            self,
            status=EndpointStatus.ONLINE,
            quarantine_reason=None,
            last_seen_at=now,
        )


@dataclass(frozen=True, slots=True)
class EndpointFilter:
    """Criteria narrowing an endpoint listing. Unset fields do not constrain."""

    status: EndpointStatus | None = None
    role: EndpointRole | None = None
    platform: EndpointPlatform | None = None
    hostname: str | None = None

    def __post_init__(self) -> None:
        if self.hostname is not None:
            object.__setattr__(
                self,
                "hostname",
                require_text(
                    self.hostname, name="hostname", max_length=MAX_HOSTNAME_LENGTH
                ).lower(),
            )

    @property
    def is_empty(self) -> bool:
        """Whether this filter constrains nothing."""
        return all(
            getattr(self, name) is None for name in ("status", "role", "platform", "hostname")
        )


@dataclass(frozen=True, slots=True)
class FleetSnapshot:
    """Point-in-time counts for the operator overview."""

    endpoints_total: int
    endpoints_online: int
    endpoints_offline: int
    endpoints_quarantined: int
    scans_in_flight: int
    findings_open: int
    findings_open_actionable: int
    plans_pending: int


def _normalise_labels(labels: Mapping[str, str]) -> dict[str, str]:
    """Validate inventory tags and freeze them as a plain dict."""
    if len(labels) > MAX_LABELS:
        raise ValidationError(
            f"An endpoint may carry at most {MAX_LABELS} labels.",
            count=len(labels),
            max_labels=MAX_LABELS,
        )
    cleaned: dict[str, str] = {}
    for raw_key, raw_value in labels.items():
        key = require_text(raw_key, name="label_key", max_length=MAX_LABEL_KEY_LENGTH)
        value = require_text(raw_value, name="label_value", max_length=MAX_LABEL_VALUE_LENGTH)
        cleaned[key] = value
    return cleaned
