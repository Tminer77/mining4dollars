"""Linear Timestamp Protocol: the tape, the tree, and the rules that join them.

The Tree of Claude is a DAG of nodes. Parallel branches may be *proposed* at
the same moment. They become real only when they are *committed*, and commit
is a total order: tick n+1 cannot exist until tick n does. That serialisation
is the protocol. Without it, two agents acting on two branches invent two
histories and there is no fact of the matter about what happened first.

The wall clock is consulted and recorded, but it is not in charge. If the
machine's clock runs backwards, the tape does not: the previous wall is kept
and the tick still advances. Time, here, is a control.
"""

from __future__ import annotations

import datetime as dt
import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from m4d.domain.errors import GuardrailError, ValidationError
from m4d.domain.glossary import Interpretation

__all__ = [
    "MAX_PARENTS",
    "PROTOCOL_SOURCE",
    "Kind",
    "LinearInstant",
    "NodeStatus",
    "ProtocolHead",
    "ProtocolNode",
    "TapeEntry",
    "TreeSnapshot",
    "advance",
    "assert_commit_allowed",
    "empty_head",
    "require_aware",
]

MAX_PARENTS = 16
PROTOCOL_SOURCE = "protocol"

KIND_GENESIS = "protocol.genesis"
KIND_TERM_DEFINED = "protocol.term.defined"
KIND_NODE_PROPOSED = "protocol.node.proposed"
KIND_NODE_COMMITTED = "protocol.node.committed"
KIND_NODE_REJECTED = "protocol.node.rejected"
KIND_UTTERANCE_INTERPRETED = "protocol.utterance.interpreted"


class NodeStatus(enum.StrEnum):
    """Lifecycle of a tree node.

    ``proposed`` is a draft. ``committed`` is history. ``rejected`` is a
    draft that was refused and cannot later be committed; a correction is a
    new node.
    """

    PROPOSED = "proposed"
    COMMITTED = "committed"
    REJECTED = "rejected"


class Kind(enum.StrEnum):
    """What a node is for.

    Mirrors the DAG working agreement: genesis is the origin, act is one
    atomic job, verify is an independent check of a committed parent.
    """

    GENESIS = "genesis"
    ACT = "act"
    VERIFY = "verify"


def require_aware(value: dt.datetime, *, field: str = "wall") -> dt.datetime:
    """Reject naive datetimes and normalise to UTC."""
    if value.tzinfo is None:
        raise ValidationError(
            "Timestamps must include a timezone offset.",
            field=field,
            value=value.isoformat(),
        )
    return value.astimezone(dt.UTC)


@dataclass(frozen=True, slots=True)
class LinearInstant:
    """One position on the tape.

    ``tick`` is the logical coordinate and is strictly increasing.
    ``wall`` is when the machine believed it was, at or after the previous
    wall. ``clock_skewed`` is set when the wall clock tried to run backwards
    and the protocol froze it.
    """

    tick: int
    wall: dt.datetime
    id: UUID
    clock_skewed: bool = False

    def __post_init__(self) -> None:
        if self.tick < 0:
            raise ValidationError("tick must be >= 0.", field="tick", tick=self.tick)
        object.__setattr__(self, "wall", require_aware(self.wall))

    def precedes(self, other: LinearInstant) -> bool:
        """Whether this instant is strictly earlier on the tape than ``other``."""
        return (self.tick, self.wall, self.id) < (other.tick, other.wall, other.id)


@dataclass(frozen=True, slots=True)
class ProtocolHead:
    """The last committed instant, or the empty origin before genesis.

    ``tick == -1`` and ``wall is None`` means the tape has not started.
    """

    tick: int
    wall: dt.datetime | None
    instant_id: UUID | None

    def __post_init__(self) -> None:
        if self.tick < -1:
            raise ValidationError("head tick must be >= -1.", field="tick", tick=self.tick)
        if self.wall is not None:
            object.__setattr__(self, "wall", require_aware(self.wall))

    @property
    def is_empty(self) -> bool:
        """Whether genesis has not yet been committed."""
        return self.tick < 0

    @property
    def next_tick(self) -> int:
        """The tick the next commit will receive."""
        return self.tick + 1


def empty_head() -> ProtocolHead:
    """The head before anything has been committed."""
    return ProtocolHead(tick=-1, wall=None, instant_id=None)


def advance(head: ProtocolHead, now: dt.datetime) -> tuple[LinearInstant, ProtocolHead]:
    """Return the next legal instant after ``head``.

    The wall clock is allowed to stall (two commits in the same microsecond)
    but never to run backwards. A backwards clock keeps the previous wall and
    still increments the tick, so the tape remains a total order.
    """
    now = require_aware(now, field="now")
    if head.wall is None or now >= head.wall:
        wall = now
        skewed = False
    else:
        wall = head.wall
        skewed = True

    instant = LinearInstant(
        tick=head.next_tick,
        wall=wall,
        id=uuid4(),
        clock_skewed=skewed,
    )
    new_head = ProtocolHead(tick=instant.tick, wall=instant.wall, instant_id=instant.id)
    return instant, new_head


@dataclass(frozen=True, slots=True)
class ProtocolNode:
    """One vertex of the Tree of Claude."""

    id: UUID
    kind: Kind
    utterance: str
    status: NodeStatus
    parent_ids: tuple[UUID, ...]
    interpretation: Interpretation | None
    instant: LinearInstant | None
    proposed_at: dt.datetime
    committed_at: dt.datetime | None = None
    rejected_at: dt.datetime | None = None
    rejection: str | None = None

    def __post_init__(self) -> None:
        if len(self.parent_ids) > MAX_PARENTS:
            raise ValidationError(
                f"a node may have at most {MAX_PARENTS} parents.",
                field="parent_ids",
                count=len(self.parent_ids),
            )
        if self.id in self.parent_ids:
            raise GuardrailError(
                "a node cannot be its own parent.",
                rule="no_cycle",
                node_id=str(self.id),
            )
        object.__setattr__(
            self, "proposed_at", require_aware(self.proposed_at, field="proposed_at")
        )
        if self.committed_at is not None:
            object.__setattr__(
                self, "committed_at", require_aware(self.committed_at, field="committed_at")
            )
        if self.rejected_at is not None:
            object.__setattr__(
                self, "rejected_at", require_aware(self.rejected_at, field="rejected_at")
            )

    def with_interpretation(self, interpretation: Interpretation) -> ProtocolNode:
        """Attach an interpretation without changing status."""
        return ProtocolNode(
            id=self.id,
            kind=self.kind,
            utterance=self.utterance,
            status=self.status,
            parent_ids=self.parent_ids,
            interpretation=interpretation,
            instant=self.instant,
            proposed_at=self.proposed_at,
            committed_at=self.committed_at,
            rejected_at=self.rejected_at,
            rejection=self.rejection,
        )

    def committed(self, instant: LinearInstant, *, at: dt.datetime) -> ProtocolNode:
        """Return the committed form of this node."""
        return ProtocolNode(
            id=self.id,
            kind=self.kind,
            utterance=self.utterance,
            status=NodeStatus.COMMITTED,
            parent_ids=self.parent_ids,
            interpretation=self.interpretation,
            instant=instant,
            proposed_at=self.proposed_at,
            committed_at=at,
            rejected_at=None,
            rejection=None,
        )

    def rejected(self, reason: str, *, at: dt.datetime) -> ProtocolNode:
        """Return the rejected form of this node."""
        cleaned = reason.strip()
        if not cleaned:
            raise ValidationError("rejection reason must not be blank.", field="rejection")
        return ProtocolNode(
            id=self.id,
            kind=self.kind,
            utterance=self.utterance,
            status=NodeStatus.REJECTED,
            parent_ids=self.parent_ids,
            interpretation=self.interpretation,
            instant=None,
            proposed_at=self.proposed_at,
            committed_at=None,
            rejected_at=at,
            rejection=cleaned,
        )


def assert_commit_allowed(
    node: ProtocolNode,
    parents: Sequence[ProtocolNode],
    *,
    genesis_committed: bool,
) -> None:
    """Raise :class:`GuardrailError` if ``node`` cannot enter the tape.

    The checks, in order:

    1. The node is still a draft.
    2. Genesis is on the tape, unless this node *is* genesis.
    3. Every listed parent exists, is committed, and is not this node.
    4. The interpretation is complete — no unbound words, no deprecated terms.
    5. A verify node has at least one parent (there is nothing to check otherwise).
    """
    if node.status is NodeStatus.COMMITTED:
        raise GuardrailError(
            "a committed node cannot be committed again.",
            rule="immutable_history",
            node_id=str(node.id),
        )
    if node.status is NodeStatus.REJECTED:
        raise GuardrailError(
            "a rejected node cannot be committed; propose a correction instead.",
            rule="rejected_is_terminal",
            node_id=str(node.id),
        )

    if node.kind is Kind.GENESIS:
        if node.parent_ids:
            raise GuardrailError(
                "genesis cannot have parents.",
                rule="genesis_is_origin",
                node_id=str(node.id),
            )
        if genesis_committed:
            raise GuardrailError(
                "genesis has already been committed; the tape has one origin.",
                rule="single_genesis",
                node_id=str(node.id),
            )
    elif not genesis_committed:
        raise GuardrailError(
            "nothing can commit before genesis.",
            rule="genesis_first",
            node_id=str(node.id),
        )

    by_id = {parent.id: parent for parent in parents}
    missing = [str(parent_id) for parent_id in node.parent_ids if parent_id not in by_id]
    if missing:
        raise GuardrailError(
            "every parent must exist before a child can commit.",
            rule="parent_exists",
            node_id=str(node.id),
            missing=missing,
        )

    for parent_id in node.parent_ids:
        parent = by_id[parent_id]
        if parent.status is not NodeStatus.COMMITTED:
            raise GuardrailError(
                "every parent must be committed before a child can commit.",
                rule="parent_committed",
                node_id=str(node.id),
                parent_id=str(parent.id),
                parent_status=parent.status.value,
            )

    if node.kind is Kind.VERIFY and not node.parent_ids:
        raise GuardrailError(
            "a verify node must name the node it checks.",
            rule="verify_has_parent",
            node_id=str(node.id),
        )

    interpretation = node.interpretation
    if interpretation is None:
        raise GuardrailError(
            "a node cannot commit without an interpretation.",
            rule="must_interpret",
            node_id=str(node.id),
        )
    if not interpretation.tokens:
        raise GuardrailError(
            "silence is not a command; the utterance bound no content.",
            rule="non_empty_utterance",
            node_id=str(node.id),
        )
    if interpretation.unbound:
        raise GuardrailError(
            "unbound language cannot commit; every content word must be in the glossary.",
            rule="bound_language",
            node_id=str(node.id),
            unbound=list(interpretation.unbound),
        )
    if interpretation.deprecated_slugs:
        raise GuardrailError(
            "deprecated terms cannot commit; replace them with their successors.",
            rule="no_deprecated_terms",
            node_id=str(node.id),
            deprecated=list(interpretation.deprecated_slugs),
        )


@dataclass(frozen=True, slots=True)
class TapeEntry:
    """One committed tick, as replayed from the origin."""

    instant: LinearInstant
    node_id: UUID
    kind: Kind
    utterance: str
    bound_slugs: tuple[str, ...]
    recorded_at: dt.datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "recorded_at", require_aware(self.recorded_at, field="recorded_at")
        )


@dataclass(frozen=True, slots=True)
class TreeSnapshot:
    """The tree and the tape at one moment, for operators and the view."""

    head: ProtocolHead
    nodes: tuple[ProtocolNode, ...]
    tape: tuple[TapeEntry, ...]
    glossary_size: int

    @property
    def proposed_count(self) -> int:
        """How many drafts are waiting to commit or be rejected."""
        return sum(1 for node in self.nodes if node.status is NodeStatus.PROPOSED)

    @property
    def committed_count(self) -> int:
        """How many nodes have entered the tape."""
        return sum(1 for node in self.nodes if node.status is NodeStatus.COMMITTED)


def node_event_payload(node: ProtocolNode) -> dict[str, object]:
    """Structured detail written onto the system event log."""
    payload: dict[str, object] = {
        "node_id": str(node.id),
        "kind": node.kind.value,
        "status": node.status.value,
        "utterance": node.utterance,
        "parent_ids": [str(parent_id) for parent_id in node.parent_ids],
    }
    if node.interpretation is not None:
        payload["interpretation"] = node.interpretation.to_payload()
    if node.instant is not None:
        payload["tick"] = node.instant.tick
        payload["wall"] = node.instant.wall.isoformat()
        payload["clock_skewed"] = node.instant.clock_skewed
    if node.rejection is not None:
        payload["rejection"] = node.rejection
    return payload


def interpretation_from_mapping(payload: Mapping[str, object] | None) -> Interpretation | None:
    """Rehydrate a stored interpretation, or ``None`` if nothing was stored."""
    if not payload:
        return None
    return Interpretation.from_payload(payload)
