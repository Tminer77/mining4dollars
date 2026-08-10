"""System events: the platform's append-only activity record.

Every subsystem that will be built on this foundation, whatever the eventual
domain turns out to be, needs to answer the same questions: what happened, when,
which component reported it, and how bad was it. That is this module.

Events are immutable once recorded. There is no update or delete in the port
below, and that is deliberate: an activity record you can rewrite is not
evidence. Corrections are made by appending a further event.
"""

from __future__ import annotations

import datetime as dt
import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from m4d.domain.errors import ValidationError

__all__ = ["EventFilter", "EventSeverity", "NewEvent", "SystemEvent"]

MAX_SOURCE_LENGTH = 128
MAX_KIND_LENGTH = 128
MAX_IDEMPOTENCY_KEY_LENGTH = 200


class EventSeverity(enum.StrEnum):
    """How much attention an event deserves.

    Mirrors syslog/stdlib level names so that operators do not have to learn a
    second severity vocabulary.
    """

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Numeric order, ascending in urgency.

        Severity is stored as text, so "at least this severe" cannot be a ``>=``
        in SQL. Ranking here lets callers expand a minimum into an explicit set
        of values, which the database can satisfy from an index.
        """
        return _SEVERITY_RANK[self]

    @property
    def is_actionable(self) -> bool:
        """Whether this severity should page or alert someone."""
        return self.rank >= EventSeverity.ERROR.rank

    @classmethod
    def at_or_above(cls, minimum: EventSeverity) -> tuple[EventSeverity, ...]:
        """Return every severity at least as urgent as ``minimum``."""
        return tuple(severity for severity in cls if severity.rank >= minimum.rank)


_SEVERITY_RANK: dict[EventSeverity, int] = {
    EventSeverity.DEBUG: 10,
    EventSeverity.INFO: 20,
    EventSeverity.WARNING: 30,
    EventSeverity.ERROR: 40,
    EventSeverity.CRITICAL: 50,
}


def _require_text(value: str, *, name: str, max_length: int) -> str:
    """Validate and normalise a short identifying string."""
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError(f"{name} must not be blank.", field=name)
    if len(cleaned) > max_length:
        raise ValidationError(
            f"{name} must be at most {max_length} characters.",
            field=name,
            length=len(cleaned),
            max_length=max_length,
        )
    return cleaned


@dataclass(frozen=True, slots=True)
class NewEvent:
    """A request to record an event; an event that does not have an identity yet.

    Keeping this separate from :class:`SystemEvent` means the fields the storage
    layer owns (``id``, ``recorded_at``) cannot be forged by a caller, and the
    type system enforces it rather than a code review.
    """

    source: str
    kind: str
    severity: EventSeverity = EventSeverity.INFO
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: dt.datetime | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        # Frozen dataclasses need object.__setattr__ to normalise in place.
        object.__setattr__(
            self, "source", _require_text(self.source, name="source", max_length=MAX_SOURCE_LENGTH)
        )
        object.__setattr__(
            self, "kind", _require_text(self.kind, name="kind", max_length=MAX_KIND_LENGTH)
        )

        if self.idempotency_key is not None:
            object.__setattr__(
                self,
                "idempotency_key",
                _require_text(
                    self.idempotency_key,
                    name="idempotency_key",
                    max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
                ),
            )

        if self.occurred_at is not None:
            object.__setattr__(self, "occurred_at", _require_aware(self.occurred_at))

    def materialise(self, *, now: dt.datetime) -> SystemEvent:
        """Give this request an identity and a recording time.

        ``now`` is injected rather than read from the clock so that callers and
        tests control time; nothing in the domain reads the wall clock itself.
        """
        return SystemEvent(
            id=uuid4(),
            source=self.source,
            kind=self.kind,
            severity=self.severity,
            payload=dict(self.payload),
            occurred_at=self.occurred_at or now,
            recorded_at=now,
            idempotency_key=self.idempotency_key,
        )


def _require_aware(value: dt.datetime) -> dt.datetime:
    """Reject naive datetimes and normalise to UTC.

    Naive timestamps are the classic source of off-by-hours bugs once a second
    region or a daylight-saving boundary is involved. The domain only ever holds
    timezone-aware UTC values.
    """
    if value.tzinfo is None:
        raise ValidationError(
            "Timestamps must include a timezone offset.",
            field="occurred_at",
            value=value.isoformat(),
        )
    return value.astimezone(dt.UTC)


@dataclass(frozen=True, slots=True)
class SystemEvent:
    """A recorded event.

    Attributes:
        id: Server-assigned identity.
        source: Component that reported the event, e.g. ``"api"``.
        kind: Dotted event name, e.g. ``"pipeline.run.completed"``.
        severity: How much attention it deserves.
        payload: Arbitrary structured detail. Deliberately schemaless so that
            new producers do not require a migration.
        occurred_at: When the thing actually happened, per the producer.
        recorded_at: When this system durably stored it. Separate from
            ``occurred_at`` because late and backfilled delivery is normal, and
            conflating the two makes ingest lag impossible to measure.
        idempotency_key: Optional producer-supplied de-duplication key.
    """

    id: UUID
    source: str
    kind: str
    severity: EventSeverity
    payload: Mapping[str, Any]
    occurred_at: dt.datetime
    recorded_at: dt.datetime
    idempotency_key: str | None = None

    @property
    def ingest_lag(self) -> dt.timedelta:
        """How long the event took to reach durable storage."""
        return self.recorded_at - self.occurred_at


@dataclass(frozen=True, slots=True)
class EventFilter:
    """Criteria narrowing an event listing.

    A value object rather than a bag of keyword arguments: the criteria travel
    as one thing through the API, service, and repository layers, and adding a
    dimension later does not change three signatures.

    An unset field means "do not constrain on this". All bounds are half-open
    (``occurred_after`` exclusive, ``occurred_before`` exclusive) so that
    adjacent time windows tile without double-counting an event.
    """

    source: str | None = None
    kind: str | None = None
    min_severity: EventSeverity | None = None
    occurred_after: dt.datetime | None = None
    occurred_before: dt.datetime | None = None

    def __post_init__(self) -> None:
        for name in ("occurred_after", "occurred_before"):
            value: dt.datetime | None = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_aware(value))

        if (
            self.occurred_after is not None
            and self.occurred_before is not None
            and self.occurred_after >= self.occurred_before
        ):
            raise ValidationError(
                "occurred_after must be strictly before occurred_before.",
                occurred_after=self.occurred_after.isoformat(),
                occurred_before=self.occurred_before.isoformat(),
            )

    @property
    def is_empty(self) -> bool:
        """Whether this filter constrains nothing."""
        return all(
            getattr(self, name) is None
            for name in ("source", "kind", "min_severity", "occurred_after", "occurred_before")
        )
