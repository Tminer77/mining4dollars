"""Request and response models.

These are the wire contract and are intentionally separate from the domain
entities. If routes serialised domain objects directly, every internal rename
would silently become a breaking API change, and every new internal field would
be published whether or not it was meant to be.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field

from m4d.domain.events import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_KIND_LENGTH,
    MAX_SOURCE_LENGTH,
    EventSeverity,
    NewEvent,
    SystemEvent,
)
from m4d.domain.pagination import Page

__all__ = [
    "EventCreateRequest",
    "EventPageResponse",
    "EventResponse",
    "LivenessResponse",
    "ProblemDetail",
    "ReadinessResponse",
]


class _Schema(BaseModel):
    """Base for every wire model."""

    # `extra="forbid"` makes a misspelled field a loud 422 rather than a value
    # that is silently ignored — the failure mode that produces "the API isn't
    # saving my field" bug reports.
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventCreateRequest(_Schema):
    """Body of ``POST /v1/events``."""

    source: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_SOURCE_LENGTH,
            description="Component reporting the event.",
            examples=["ingest-worker"],
        ),
    ]
    kind: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_KIND_LENGTH,
            description="Dotted event name.",
            examples=["pipeline.run.completed"],
        ),
    ]
    severity: Annotated[
        EventSeverity, Field(description="How much attention the event deserves.")
    ] = EventSeverity.INFO
    payload: Annotated[dict[str, Any], Field(description="Arbitrary structured detail.")] = Field(
        default_factory=dict
    )
    occurred_at: Annotated[
        dt.datetime | None,
        Field(
            description=(
                "When the event actually happened, with a timezone offset. "
                "Defaults to the time the server received it."
            )
        ),
    ] = None
    idempotency_key: Annotated[
        str | None,
        Field(
            max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
            description=(
                "Optional de-duplication key. Replaying a request with a key "
                "already recorded returns the original event and 200 rather "
                "than creating a duplicate."
            ),
        ),
    ] = None

    def to_domain(self) -> NewEvent:
        """Translate into the domain's own request object."""
        return NewEvent(
            source=self.source,
            kind=self.kind,
            severity=self.severity,
            payload=self.payload,
            occurred_at=self.occurred_at,
            idempotency_key=self.idempotency_key,
        )


class EventResponse(_Schema):
    """A recorded event as returned to clients."""

    id: str
    source: str
    kind: str
    severity: EventSeverity
    payload: dict[str, Any]
    occurred_at: dt.datetime
    recorded_at: dt.datetime
    idempotency_key: str | None
    ingest_lag_ms: Annotated[
        float,
        Field(description="Milliseconds between occurred_at and recorded_at."),
    ]

    @classmethod
    def from_domain(cls, event: SystemEvent) -> Self:
        """Build a response from a domain entity."""
        return cls(
            id=str(event.id),
            source=event.source,
            kind=event.kind,
            severity=event.severity,
            payload=dict(event.payload),
            occurred_at=event.occurred_at,
            recorded_at=event.recorded_at,
            idempotency_key=event.idempotency_key,
            ingest_lag_ms=round(event.ingest_lag.total_seconds() * 1000, 3),
        )


class EventPageResponse(_Schema):
    """One page of events."""

    items: list[EventResponse]
    next_cursor: Annotated[
        str | None,
        Field(
            description=(
                "Opaque token for the next page. Pass it back as `cursor`. "
                "Null when the end of the sequence has been reached."
            )
        ),
    ]

    @classmethod
    def from_domain(cls, page: Page[SystemEvent]) -> Self:
        """Build a response from a domain page."""
        return cls(
            items=[EventResponse.from_domain(event) for event in page.items],
            next_cursor=page.next_cursor,
        )


class LivenessResponse(_Schema):
    """Body of ``GET /healthz``."""

    status: str
    version: str


class DependencyStatus(_Schema):
    """Health of a single dependency."""

    name: str
    healthy: bool
    latency_ms: float
    error: str | None = None


class ReadinessResponse(_Schema):
    """Body of ``GET /readyz``."""

    status: str
    checks: list[DependencyStatus]


class ProblemDetail(_Schema):
    """RFC 9457 error body.

    Declared so that the shape appears in the OpenAPI document; it is never
    constructed at runtime.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    type: str
    title: str
    status: int
    detail: str
    code: str
    instance: str | None = None
    request_id: str | None = None
