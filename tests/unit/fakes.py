"""In-memory implementations of the domain ports.

Their existence is the point of the port abstraction: the service layer can be
exercised exhaustively, including its concurrency handling, with no database and
no I/O. If these fakes were hard to write, the ports would be badly drawn.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from uuid import UUID

from m4d.domain.errors import ConflictError
from m4d.domain.events import EventFilter, EventSeverity, SystemEvent
from m4d.domain.glossary import GlossaryTerm, TermStatus, normalise_key
from m4d.domain.pagination import Cursor
from m4d.domain.protocol import Kind, ProtocolHead, ProtocolNode, TapeEntry, empty_head

__all__ = [
    "FakeEventRepository",
    "FakeGlossaryRepository",
    "FakeProtocolRepository",
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
        events = [event for event in events if _matches(event, filters)]

        if after is not None:
            events = [
                event
                for event in events
                if (event.occurred_at, event.id) < (after.occurred_at, after.id)
            ]

        return events[:limit]


def _matches(event: SystemEvent, filters: EventFilter) -> bool:
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


class FakeGlossaryRepository:
    """A dictionary pretending to be the glossary table."""

    def __init__(self) -> None:
        self.by_id: dict[UUID, GlossaryTerm] = {}
        self.by_slug: dict[str, GlossaryTerm] = {}

    async def add(self, term: GlossaryTerm) -> GlossaryTerm:
        if term.slug in self.by_slug:
            raise ConflictError("A glossary term with this slug already exists.")
        self.by_id[term.id] = term
        self.by_slug[term.slug] = term
        return term

    async def save(self, term: GlossaryTerm) -> GlossaryTerm:
        self.by_id[term.id] = term
        self.by_slug[term.slug] = term
        return term

    async def get(self, term_id: UUID) -> GlossaryTerm | None:
        return self.by_id.get(term_id)

    async def get_by_slug(self, slug: str) -> GlossaryTerm | None:
        return self.by_slug.get(slug)

    async def find_by_key(self, key: str) -> GlossaryTerm | None:
        needle = normalise_key(key)
        for term in self.by_id.values():
            if needle in term.lookup_keys():
                return term
        return None

    async def list_all(self) -> Sequence[GlossaryTerm]:
        return sorted(
            self.by_id.values(),
            key=lambda term: (term.status is TermStatus.DEPRECATED, term.slug),
        )


class FakeProtocolRepository:
    """In-memory tape, tree, and clock."""

    def __init__(self) -> None:
        self.head: ProtocolHead = empty_head()
        self.nodes: dict[UUID, ProtocolNode] = {}
        self.tape: list[TapeEntry] = []

    async def get_head(self, *, for_update: bool = False) -> ProtocolHead:
        return self.head

    async def save_head(self, head: ProtocolHead) -> None:
        self.head = head

    async def add_node(self, node: ProtocolNode) -> ProtocolNode:
        if node.kind is Kind.GENESIS and any(
            existing.kind is Kind.GENESIS for existing in self.nodes.values()
        ):
            raise ConflictError("genesis has already been committed; the tape has one origin.")
        if node.id in self.nodes:
            raise ConflictError("A node with this id already exists.")
        self.nodes[node.id] = node
        return node

    async def save_node(self, node: ProtocolNode) -> ProtocolNode:
        self.nodes[node.id] = node
        return node

    async def get_node(self, node_id: UUID) -> ProtocolNode | None:
        return self.nodes.get(node_id)

    async def list_nodes(self) -> Sequence[ProtocolNode]:
        return sorted(self.nodes.values(), key=lambda node: (node.proposed_at, node.id))

    async def add_tick(self, entry: TapeEntry) -> TapeEntry:
        if any(existing.instant.tick == entry.instant.tick for existing in self.tape):
            raise ConflictError("That tick is already on the tape.")
        self.tape.append(entry)
        self.tape.sort(key=lambda item: item.instant.tick)
        return entry

    async def list_tape(self, *, after_tick: int, limit: int) -> Sequence[TapeEntry]:
        items = [entry for entry in self.tape if entry.instant.tick > after_tick]
        return items[:limit]


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
        glossary: FakeGlossaryRepository | None = None,
        protocol: FakeProtocolRepository | None = None,
    ) -> None:
        self.events = repository or FakeEventRepository()
        self.glossary = glossary or FakeGlossaryRepository()
        self.protocol = protocol or FakeProtocolRepository()
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
