"""Linear Timestamp Protocol use cases.

The service is the only place that opens a unit of work over the glossary, the
tree, the tape, and the system event log together. Domain rules live in
``m4d.domain.protocol`` and ``m4d.domain.glossary``; this module sequences them
and records what happened.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID, uuid4

from m4d.domain.errors import ConflictError, GuardrailError, NotFoundError, ValidationError
from m4d.domain.events import EventSeverity, NewEvent
from m4d.domain.glossary import (
    CORE_GLOSSARY,
    GlossaryTerm,
    Interpretation,
    NewTerm,
    interpret,
    normalise_key,
)
from m4d.domain.pagination import MAX_PAGE_SIZE, normalise_page_size
from m4d.domain.ports import Clock, UnitOfWork
from m4d.domain.protocol import (
    KIND_GENESIS,
    KIND_NODE_COMMITTED,
    KIND_NODE_PROPOSED,
    KIND_NODE_REJECTED,
    KIND_TERM_DEFINED,
    PROTOCOL_SOURCE,
    Kind,
    NodeStatus,
    ProtocolHead,
    ProtocolNode,
    TapeEntry,
    TreeSnapshot,
    advance,
    assert_commit_allowed,
    node_event_payload,
)

__all__ = [
    "GENESIS_UTTERANCE",
    "BootstrapResult",
    "DefineResult",
    "ProtocolService",
    "TapePage",
]

logger = logging.getLogger(__name__)

GENESIS_UTTERANCE = "genesis of the linear-time protocol tape"


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Outcome of ensuring the tape and glossary exist."""

    head: ProtocolHead
    genesis: ProtocolNode
    terms_seeded: int
    was_created: bool


@dataclass(frozen=True, slots=True)
class DefineResult:
    """Outcome of defining a glossary term."""

    term: GlossaryTerm
    was_created: bool


@dataclass(frozen=True, slots=True)
class TapePage:
    """One page of the tape, oldest first.

    ``after_tick`` on the next request is ``items[-1].instant.tick`` when
    ``next_after_tick`` is not None. Tick is already a total order, so the
    cursor is just that integer.
    """

    items: tuple[TapeEntry, ...]
    next_after_tick: int | None


def _event(kind: str, payload: dict[str, object], *, severity: EventSeverity) -> NewEvent:
    """Build a protocol event; time is applied at materialise."""
    return NewEvent(
        source=PROTOCOL_SOURCE,
        kind=kind,
        severity=severity,
        payload=payload,
    )


class ProtocolService:
    """Use cases over the glossary, the Tree of Claude, and the linear tape."""

    def __init__(self, uow_factory: Callable[[], UnitOfWork], clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def bootstrap(self) -> BootstrapResult:
        """Seed the core glossary and commit genesis if they are missing.

        Idempotent: a second call returns the existing origin and seeds nothing.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            result = await self._ensure(uow, now)
            await uow.commit()
        return result

    async def define_term(self, request: NewTerm) -> DefineResult:
        """Add a term to the glossary.

        Slug and alias collisions against any existing key are conflicts: two
        terms must not claim the same word, or the interpreter would have to
        guess, which is the failure the glossary exists to prevent.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await self._ensure(uow, now)
            existing = await uow.glossary.get_by_slug(request.slug)
            if existing is not None:
                await uow.commit()
                return DefineResult(term=existing, was_created=False)

            collision = await self._collision(uow, request)
            if collision is not None:
                raise ConflictError(
                    f"the key '{collision}' already belongs to another term.",
                    key=collision,
                    slug=request.slug,
                )

            term = request.materialise(now=now)
            stored = await uow.glossary.add(term)
            await uow.events.add(
                _event(
                    KIND_TERM_DEFINED,
                    {
                        "term_id": str(stored.id),
                        "slug": stored.slug,
                        "name": stored.name,
                        "aliases": list(stored.aliases),
                    },
                    severity=EventSeverity.INFO,
                ).materialise(now=now)
            )
            await uow.commit()

        logger.info("glossary term defined", extra={"slug": stored.slug})
        return DefineResult(term=stored, was_created=True)

    async def deprecate_term(self, slug: str, *, successor_slug: str | None = None) -> GlossaryTerm:
        """Mark ``slug`` deprecated so the interpreter will not treat it as live."""
        async with self._uow_factory() as uow:
            term = await uow.glossary.get_by_slug(slug)
            if term is None:
                raise NotFoundError("Term", slug)
            successor_id = None
            if successor_slug is not None:
                successor = await uow.glossary.get_by_slug(successor_slug)
                if successor is None:
                    raise NotFoundError("Term", successor_slug)
                successor_id = successor.id
            updated = await uow.glossary.save(term.deprecate(successor_id=successor_id))
            await uow.commit()
        return updated

    async def list_terms(self) -> Sequence[GlossaryTerm]:
        """Return the full glossary, seeding the core terms if needed."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await self._ensure(uow, now)
            terms = tuple(await uow.glossary.list_all())
            await uow.commit()
        return terms

    async def interpret_utterance(self, utterance: str) -> Interpretation:
        """Bind ``utterance`` against the current glossary without committing a node."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await self._ensure(uow, now)
            terms = await uow.glossary.list_all()
            await uow.commit()
        return interpret(utterance, terms, now=now)

    async def get_term(self, slug: str) -> GlossaryTerm:
        """Return one term by canonical slug."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await self._ensure(uow, now)
            term = await uow.glossary.get_by_slug(slug)
            await uow.commit()
        if term is None:
            raise NotFoundError("Term", slug)
        return term

    async def propose(
        self,
        utterance: str,
        *,
        kind: Kind = Kind.ACT,
        parent_ids: Sequence[UUID] = (),
    ) -> ProtocolNode:
        """Draft a node on the tree.

        The utterance is interpreted immediately and stored with the node, so a
        later operator can see why a commit would be refused. Incomplete
        interpretations are allowed at this step: proposing is drafting,
        committing is acting.
        """
        if kind is Kind.GENESIS:
            raise GuardrailError(
                "genesis is created by bootstrap, not proposed by callers.",
                rule="single_genesis",
            )
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await self._ensure(uow, now)
            terms = await uow.glossary.list_all()
            interpretation = interpret(utterance, terms, now=now)
            resolved_parents = await self._resolve_parents(uow, tuple(parent_ids), kind=kind)
            node = ProtocolNode(
                id=uuid4(),
                kind=kind,
                utterance=interpretation.utterance,
                status=NodeStatus.PROPOSED,
                parent_ids=resolved_parents,
                interpretation=interpretation,
                instant=None,
                proposed_at=now,
            )
            stored = await uow.protocol.add_node(node)
            await uow.events.add(
                _event(
                    KIND_NODE_PROPOSED,
                    node_event_payload(stored),
                    severity=EventSeverity.INFO,
                ).materialise(now=now)
            )
            await uow.commit()

        logger.info(
            "protocol node proposed",
            extra={
                "node_id": str(stored.id),
                "kind": stored.kind.value,
                "complete": bool(stored.interpretation and stored.interpretation.is_complete),
            },
        )
        return stored

    async def commit_node(self, node_id: UUID) -> ProtocolNode:
        """Serialise ``node_id`` onto the tape as the next tick.

        Re-interprets the utterance against the live glossary at commit time so
        a term deprecated between propose and commit cannot sneak onto the
        tape. The clock row is locked for the duration of the write.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await self._ensure(uow, now)
            head = await uow.protocol.get_head(for_update=True)
            node = await uow.protocol.get_node(node_id)
            if node is None:
                raise NotFoundError("Node", node_id)

            terms = await uow.glossary.list_all()
            node = node.with_interpretation(interpret(node.utterance, terms, now=now))

            parents = []
            for parent_id in node.parent_ids:
                parent = await uow.protocol.get_node(parent_id)
                if parent is not None:
                    parents.append(parent)

            genesis_committed = not head.is_empty
            assert_commit_allowed(node, parents, genesis_committed=genesis_committed)

            instant, new_head = advance(head, now)
            committed = node.committed(instant, at=now)
            stored = await uow.protocol.save_node(committed)
            bound = stored.interpretation.bound_slugs if stored.interpretation else ()
            await uow.protocol.add_tick(
                TapeEntry(
                    instant=instant,
                    node_id=stored.id,
                    kind=stored.kind,
                    utterance=stored.utterance,
                    bound_slugs=bound,
                    recorded_at=now,
                )
            )
            await uow.protocol.save_head(new_head)
            await uow.events.add(
                _event(
                    KIND_NODE_COMMITTED,
                    node_event_payload(stored),
                    severity=EventSeverity.INFO,
                ).materialise(now=now)
            )
            await uow.commit()

        logger.info(
            "protocol node committed",
            extra={"node_id": str(stored.id), "tick": instant.tick, "skewed": instant.clock_skewed},
        )
        return stored

    async def reject_node(self, node_id: UUID, reason: str) -> ProtocolNode:
        """Refuse a draft. A correction is a new node, never an edit."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            node = await uow.protocol.get_node(node_id)
            if node is None:
                raise NotFoundError("Node", node_id)
            if node.status is NodeStatus.COMMITTED:
                raise GuardrailError(
                    "a committed node cannot be rejected; history is append-only.",
                    rule="immutable_history",
                    node_id=str(node_id),
                )
            rejected = await uow.protocol.save_node(node.rejected(reason, at=now))
            await uow.events.add(
                _event(
                    KIND_NODE_REJECTED,
                    node_event_payload(rejected),
                    severity=EventSeverity.WARNING,
                ).materialise(now=now)
            )
            await uow.commit()
        return rejected

    async def get_node(self, node_id: UUID) -> ProtocolNode:
        """Return one node."""
        async with self._uow_factory() as uow:
            node = await uow.protocol.get_node(node_id)
        if node is None:
            raise NotFoundError("Node", node_id)
        return node

    async def snapshot(self) -> TreeSnapshot:
        """Return the tree, the tape, and the head together."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await self._ensure(uow, now)
            await uow.commit()
            head = await uow.protocol.get_head()
            nodes = tuple(await uow.protocol.list_nodes())
            tape = tuple(await uow.protocol.list_tape(after_tick=-1, limit=MAX_PAGE_SIZE))
            glossary_size = len(await uow.glossary.list_all())
        return TreeSnapshot(head=head, nodes=nodes, tape=tape, glossary_size=glossary_size)

    async def head(self) -> ProtocolHead:
        """Return the last committed instant."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await self._ensure(uow, now)
            await uow.commit()
            return await uow.protocol.get_head()

    async def list_tape(self, *, after_tick: int = -1, limit: int | None = None) -> TapePage:
        """Return one page of the tape, oldest first."""
        if after_tick < -1:
            raise ValidationError(
                "after_tick must be >= -1.",
                field="after_tick",
                after_tick=after_tick,
            )
        page_size = normalise_page_size(limit)
        async with self._uow_factory() as uow:
            rows = await uow.protocol.list_tape(after_tick=after_tick, limit=page_size + 1)
        items = tuple(rows[:page_size])
        next_after = items[-1].instant.tick if len(rows) > page_size and items else None
        return TapePage(items=items, next_after_tick=next_after)

    async def _ensure(self, uow: UnitOfWork, now: dt.datetime) -> BootstrapResult:
        """Seed glossary and genesis inside an already-open unit of work."""
        terms_seeded = await self._seed_glossary(uow, now)
        head = await uow.protocol.get_head(for_update=True)
        genesis, was_created = await self._ensure_genesis(uow, now, head)
        if was_created:
            head = await uow.protocol.get_head()
        return BootstrapResult(
            head=head,
            genesis=genesis,
            terms_seeded=terms_seeded,
            was_created=was_created,
        )

    async def _seed_glossary(self, uow: UnitOfWork, now: dt.datetime) -> int:
        existing = {term.slug: term for term in await uow.glossary.list_all()}
        seeded = 0
        for request in CORE_GLOSSARY:
            if request.slug in existing:
                continue
            collision = await self._collision(uow, request)
            if collision is not None:
                continue
            stored = await uow.glossary.add(request.materialise(now=now))
            existing[stored.slug] = stored
            seeded += 1
        return seeded

    async def _ensure_genesis(
        self,
        uow: UnitOfWork,
        now: dt.datetime,
        head: ProtocolHead,
    ) -> tuple[ProtocolNode, bool]:
        nodes = await uow.protocol.list_nodes()
        for node in nodes:
            if node.kind is Kind.GENESIS:
                return node, False

        terms = await uow.glossary.list_all()
        interpretation = interpret(GENESIS_UTTERANCE, terms, now=now)
        node = ProtocolNode(
            id=uuid4(),
            kind=Kind.GENESIS,
            utterance=interpretation.utterance,
            status=NodeStatus.PROPOSED,
            parent_ids=(),
            interpretation=interpretation,
            instant=None,
            proposed_at=now,
        )
        try:
            node = await uow.protocol.add_node(node)
        except ConflictError:
            nodes = await uow.protocol.list_nodes()
            for existing in nodes:
                if existing.kind is Kind.GENESIS:
                    return existing, False
            raise

        assert_commit_allowed(node, (), genesis_committed=not head.is_empty)
        instant, new_head = advance(head, now)
        committed = node.committed(instant, at=now)
        stored = await uow.protocol.save_node(committed)
        bound = stored.interpretation.bound_slugs if stored.interpretation else ()
        await uow.protocol.add_tick(
            TapeEntry(
                instant=instant,
                node_id=stored.id,
                kind=stored.kind,
                utterance=stored.utterance,
                bound_slugs=bound,
                recorded_at=now,
            )
        )
        await uow.protocol.save_head(new_head)
        await uow.events.add(
            _event(
                KIND_GENESIS,
                node_event_payload(stored),
                severity=EventSeverity.INFO,
            ).materialise(now=now)
        )
        return stored, True

    async def _resolve_parents(
        self,
        uow: UnitOfWork,
        parent_ids: tuple[UUID, ...],
        *,
        kind: Kind,
    ) -> tuple[UUID, ...]:
        nodes = await uow.protocol.list_nodes()
        genesis = next((node for node in nodes if node.kind is Kind.GENESIS), None)
        if not parent_ids:
            if genesis is None:
                raise GuardrailError(
                    "nothing can be proposed before genesis.",
                    rule="genesis_first",
                )
            return (genesis.id,)

        known = {node.id for node in nodes}
        missing = [str(parent_id) for parent_id in parent_ids if parent_id not in known]
        if missing:
            raise GuardrailError(
                "every parent must exist before a child can be proposed.",
                rule="parent_exists",
                missing=missing,
            )
        if kind is Kind.VERIFY and not parent_ids:
            raise GuardrailError(
                "a verify node must name the node it checks.",
                rule="verify_has_parent",
            )
        return parent_ids

    async def _collision(self, uow: UnitOfWork, request: NewTerm) -> str | None:
        """Return a key of ``request`` already owned by a different term, if any."""
        claimed = {request.slug, normalise_key(request.name), *request.aliases}
        for term in await uow.glossary.list_all():
            owned = set(term.lookup_keys())
            overlap = claimed & owned
            if overlap:
                return sorted(overlap)[0]
        return None
