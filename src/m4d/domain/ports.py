"""Ports: the interfaces the domain requires of the outside world.

These are :class:`~typing.Protocol` definitions, so implementations satisfy them
structurally and never import the domain to inherit from it. Dependencies point
inward; the database layer knows about the domain, and the domain knows only
about these shapes.

The practical payoff is that services are unit-testable against a dictionary
without a database, and that swapping storage does not touch business logic.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from types import TracebackType
from typing import Protocol, runtime_checkable
from uuid import UUID

from m4d.domain.events import EventFilter, SystemEvent
from m4d.domain.glossary import GlossaryTerm
from m4d.domain.pagination import Cursor
from m4d.domain.protocol import ProtocolHead, ProtocolNode, TapeEntry

__all__ = [
    "Clock",
    "EventRepository",
    "GlossaryRepository",
    "ProtocolRepository",
    "UnitOfWork",
]


@runtime_checkable
class EventRepository(Protocol):
    """Persistence for :class:`~m4d.domain.events.SystemEvent`.

    Note the absence of update and delete: the event log is append-only.
    """

    async def add(self, event: SystemEvent) -> SystemEvent:
        """Persist ``event`` and return it."""
        ...

    async def get(self, event_id: UUID) -> SystemEvent | None:
        """Return the event with ``event_id``, or ``None``."""
        ...

    async def find_by_idempotency_key(self, key: str) -> SystemEvent | None:
        """Return the event previously recorded under ``key``, or ``None``."""
        ...

    async def list_page(
        self,
        *,
        filters: EventFilter,
        after: Cursor | None,
        limit: int,
    ) -> Sequence[SystemEvent]:
        """Return up to ``limit`` events matching ``filters``, after ``after``.

        Ordered by ``(occurred_at DESC, id DESC)`` — newest first.
        """
        ...


@runtime_checkable
class GlossaryRepository(Protocol):
    """Persistence for the canonical vocabulary."""

    async def add(self, term: GlossaryTerm) -> GlossaryTerm:
        """Insert ``term``.

        Raises:
            ConflictError: if the slug or an alias collides with an existing term.
        """
        ...

    async def save(self, term: GlossaryTerm) -> GlossaryTerm:
        """Replace the stored row for ``term.id``."""
        ...

    async def get(self, term_id: UUID) -> GlossaryTerm | None:
        """Return the term with ``term_id``, or ``None``."""
        ...

    async def get_by_slug(self, slug: str) -> GlossaryTerm | None:
        """Return the term whose canonical slug is ``slug``, or ``None``."""
        ...

    async def find_by_key(self, key: str) -> GlossaryTerm | None:
        """Return the term that owns ``key`` as a slug, name-key, or alias."""
        ...

    async def list_all(self) -> Sequence[GlossaryTerm]:
        """Return every term, active first, then by slug."""
        ...


@runtime_checkable
class ProtocolRepository(Protocol):
    """Persistence for the linear tape and the Tree of Claude."""

    async def get_head(self, *, for_update: bool = False) -> ProtocolHead:
        """Return the last committed instant.

        ``for_update`` takes a row lock so concurrent commits serialise on
        the clock rather than racing two ticks onto the same number.
        """
        ...

    async def save_head(self, head: ProtocolHead) -> None:
        """Persist ``head`` as the current clock."""
        ...

    async def add_node(self, node: ProtocolNode) -> ProtocolNode:
        """Insert ``node`` and its parent edges."""
        ...

    async def save_node(self, node: ProtocolNode) -> ProtocolNode:
        """Replace the stored row for ``node.id``."""
        ...

    async def get_node(self, node_id: UUID) -> ProtocolNode | None:
        """Return the node with ``node_id``, or ``None``."""
        ...

    async def list_nodes(self) -> Sequence[ProtocolNode]:
        """Return every node, proposed-at ascending."""
        ...

    async def add_tick(self, entry: TapeEntry) -> TapeEntry:
        """Append ``entry`` to the tape.

        Raises:
            ConflictError: if that tick number is already occupied.
        """
        ...

    async def list_tape(self, *, after_tick: int, limit: int) -> Sequence[TapeEntry]:
        """Return up to ``limit`` ticks strictly after ``after_tick``, oldest first."""
        ...


class UnitOfWork(Protocol):
    """A transactional boundary over one or more repositories.

    Services declare what must succeed or fail together; they do not manage
    sessions, connections, or commits. Exiting the context without an explicit
    :meth:`commit` rolls back, so a forgotten commit loses work loudly in tests
    rather than half-writing in production.
    """

    @property
    def events(self) -> EventRepository:
        """The event repository enrolled in this transaction.

        A read-only property rather than a plain attribute so the type is
        covariant: an implementation may expose a concrete
        ``SqlAlchemyEventRepository`` here and still satisfy the port. A mutable
        attribute would be invariant and reject every real implementation.
        """
        ...

    @property
    def glossary(self) -> GlossaryRepository:
        """The glossary repository enrolled in this transaction."""
        ...

    @property
    def protocol(self) -> ProtocolRepository:
        """The protocol repository enrolled in this transaction."""
        ...

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None:
        """Durably apply everything done in this unit of work."""
        ...

    async def rollback(self) -> None:
        """Discard everything done in this unit of work."""
        ...


class Clock(Protocol):
    """A source of the current time.

    Injected so that time-dependent behaviour is deterministic under test.
    """

    def now(self) -> dt.datetime:
        """Return the current timezone-aware UTC time."""
        ...
