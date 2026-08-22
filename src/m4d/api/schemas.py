"""Request and response models.

These are the wire contract and are intentionally separate from the domain
entities. If routes serialised domain objects directly, every internal rename
would silently become a breaking API change, and every new internal field would
be published whether or not it was meant to be.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from m4d.domain.events import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_KIND_LENGTH,
    MAX_SOURCE_LENGTH,
    EventSeverity,
    NewEvent,
    SystemEvent,
)
from m4d.domain.glossary import (
    MAX_ALIASES,
    MAX_DEFINITION_LENGTH,
    MAX_NAME_LENGTH,
    MAX_SLUG_LENGTH,
    MAX_UTTERANCE_LENGTH,
    GlossaryTerm,
    Interpretation,
    NewTerm,
)
from m4d.domain.pagination import Page
from m4d.domain.protocol import (
    Kind,
    NodeStatus,
    ProtocolHead,
    ProtocolNode,
    TapeEntry,
    TreeSnapshot,
)
from m4d.services.protocol import BootstrapResult, TapePage

__all__ = [
    "BindingResponse",
    "BootstrapResponse",
    "EventCreateRequest",
    "EventPageResponse",
    "EventResponse",
    "GlossaryTermCreateRequest",
    "GlossaryTermResponse",
    "HeadResponse",
    "InterpretRequest",
    "InterpretationResponse",
    "LivenessResponse",
    "NodeProposeRequest",
    "NodeRejectRequest",
    "NodeResponse",
    "ProblemDetail",
    "ReadinessResponse",
    "TapeEntryResponse",
    "TapePageResponse",
    "TreeSnapshotResponse",
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


class GlossaryTermCreateRequest(_Schema):
    """Body of ``POST /v1/protocol/terms``."""

    slug: Annotated[str, Field(min_length=1, max_length=MAX_SLUG_LENGTH)]
    name: Annotated[str, Field(min_length=1, max_length=MAX_NAME_LENGTH)]
    definition: Annotated[str, Field(min_length=1, max_length=MAX_DEFINITION_LENGTH)]
    aliases: Annotated[list[str], Field(max_length=MAX_ALIASES)] = Field(default_factory=list)

    def to_domain(self) -> NewTerm:
        """Translate into the domain's own request object."""
        return NewTerm(
            slug=self.slug,
            name=self.name,
            definition=self.definition,
            aliases=tuple(alias for alias in self.aliases if alias.strip()),
        )


class GlossaryTermResponse(_Schema):
    """A glossary term as returned to clients."""

    id: str
    slug: str
    name: str
    definition: str
    aliases: list[str]
    version: int
    status: str
    created_at: dt.datetime
    superseded_by: str | None

    @classmethod
    def from_domain(cls, term: GlossaryTerm) -> Self:
        """Build a response from a domain term."""
        return cls(
            id=str(term.id),
            slug=term.slug,
            name=term.name,
            definition=term.definition,
            aliases=list(term.aliases),
            version=term.version,
            status=term.status.value,
            created_at=term.created_at,
            superseded_by=str(term.superseded_by) if term.superseded_by else None,
        )


class InterpretRequest(_Schema):
    """Body of ``POST /v1/protocol/interpret``."""

    utterance: Annotated[str, Field(min_length=1, max_length=MAX_UTTERANCE_LENGTH)]


class BindingResponse(_Schema):
    """One glossary binding inside an interpretation."""

    span: str
    slug: str
    definition: str
    version: int
    status: str


class InterpretationResponse(_Schema):
    """The glossary's reading of an utterance."""

    id: str
    utterance: str
    tokens: list[str]
    bindings: list[BindingResponse]
    unbound: list[str]
    deprecated: list[str]
    complete: bool
    interpreted_at: dt.datetime

    @classmethod
    def from_domain(cls, interpretation: Interpretation) -> Self:
        """Build a response from a domain interpretation."""
        return cls(
            id=str(interpretation.id),
            utterance=interpretation.utterance,
            tokens=list(interpretation.tokens),
            bindings=[
                BindingResponse(
                    span=binding.span,
                    slug=binding.slug,
                    definition=binding.definition,
                    version=binding.version,
                    status=binding.status.value,
                )
                for binding in interpretation.bindings
            ],
            unbound=list(interpretation.unbound),
            deprecated=list(interpretation.deprecated_slugs),
            complete=interpretation.is_complete,
            interpreted_at=interpretation.interpreted_at,
        )


class NodeProposeRequest(_Schema):
    """Body of ``POST /v1/protocol/nodes``."""

    utterance: Annotated[str, Field(min_length=1, max_length=MAX_UTTERANCE_LENGTH)]
    kind: Kind = Kind.ACT
    parent_ids: list[UUID] = Field(default_factory=list)


class NodeRejectRequest(_Schema):
    """Body of ``POST /v1/protocol/nodes/{id}/reject``."""

    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class InstantResponse(_Schema):
    """A position on the linear tape."""

    tick: int
    wall: dt.datetime
    id: str
    clock_skewed: bool


class NodeResponse(_Schema):
    """A Tree of Claude node as returned to clients."""

    id: str
    kind: Kind
    utterance: str
    status: NodeStatus
    parent_ids: list[str]
    interpretation: InterpretationResponse | None
    instant: InstantResponse | None
    proposed_at: dt.datetime
    committed_at: dt.datetime | None
    rejected_at: dt.datetime | None
    rejection: str | None

    @classmethod
    def from_domain(cls, node: ProtocolNode) -> Self:
        """Build a response from a domain node."""
        instant = None
        if node.instant is not None:
            instant = InstantResponse(
                tick=node.instant.tick,
                wall=node.instant.wall,
                id=str(node.instant.id),
                clock_skewed=node.instant.clock_skewed,
            )
        return cls(
            id=str(node.id),
            kind=node.kind,
            utterance=node.utterance,
            status=node.status,
            parent_ids=[str(parent_id) for parent_id in node.parent_ids],
            interpretation=(
                InterpretationResponse.from_domain(node.interpretation)
                if node.interpretation is not None
                else None
            ),
            instant=instant,
            proposed_at=node.proposed_at,
            committed_at=node.committed_at,
            rejected_at=node.rejected_at,
            rejection=node.rejection,
        )


class TapeEntryResponse(_Schema):
    """One committed tick as replayed from the origin."""

    tick: int
    wall: dt.datetime
    id: str
    clock_skewed: bool
    node_id: str
    kind: Kind
    utterance: str
    bound_slugs: list[str]
    recorded_at: dt.datetime

    @classmethod
    def from_domain(cls, entry: TapeEntry) -> Self:
        """Build a response from a tape entry."""
        return cls(
            tick=entry.instant.tick,
            wall=entry.instant.wall,
            id=str(entry.instant.id),
            clock_skewed=entry.instant.clock_skewed,
            node_id=str(entry.node_id),
            kind=entry.kind,
            utterance=entry.utterance,
            bound_slugs=list(entry.bound_slugs),
            recorded_at=entry.recorded_at,
        )


class TapePageResponse(_Schema):
    """One page of the tape, oldest first."""

    items: list[TapeEntryResponse]
    next_after_tick: int | None

    @classmethod
    def from_domain(cls, page: TapePage) -> Self:
        """Build a response from a domain page."""
        return cls(
            items=[TapeEntryResponse.from_domain(entry) for entry in page.items],
            next_after_tick=page.next_after_tick,
        )


class HeadResponse(_Schema):
    """The last committed instant."""

    tick: int
    wall: dt.datetime | None
    instant_id: str | None
    empty: bool
    next_tick: int

    @classmethod
    def from_domain(cls, head: ProtocolHead) -> Self:
        """Build a response from the protocol head."""
        return cls(
            tick=head.tick,
            wall=head.wall,
            instant_id=str(head.instant_id) if head.instant_id else None,
            empty=head.is_empty,
            next_tick=head.next_tick,
        )


class TreeSnapshotResponse(_Schema):
    """The tree and the tape at one moment."""

    head: HeadResponse
    nodes: list[NodeResponse]
    tape: list[TapeEntryResponse]
    glossary_size: int
    proposed_count: int
    committed_count: int

    @classmethod
    def from_domain(cls, snapshot: TreeSnapshot) -> Self:
        """Build a response from a domain snapshot."""
        return cls(
            head=HeadResponse.from_domain(snapshot.head),
            nodes=[NodeResponse.from_domain(node) for node in snapshot.nodes],
            tape=[TapeEntryResponse.from_domain(entry) for entry in snapshot.tape],
            glossary_size=snapshot.glossary_size,
            proposed_count=snapshot.proposed_count,
            committed_count=snapshot.committed_count,
        )


class BootstrapResponse(_Schema):
    """Outcome of ensuring genesis and the core glossary exist."""

    head: HeadResponse
    genesis: NodeResponse
    terms_seeded: int
    was_created: bool

    @classmethod
    def from_domain(cls, result: BootstrapResult) -> Self:
        """Build a response from a bootstrap result."""
        return cls(
            head=HeadResponse.from_domain(result.head),
            genesis=NodeResponse.from_domain(result.genesis),
            terms_seeded=result.terms_seeded,
            was_created=result.was_created,
        )
