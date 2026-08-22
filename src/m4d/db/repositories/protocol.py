"""Linear-timestamp protocol persistence backed by PostgreSQL."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from m4d.db.tables import (
    CLOCK_ROW_ID,
    ProtocolClockRow,
    ProtocolEdgeRow,
    ProtocolNodeRow,
    ProtocolTickRow,
)
from m4d.domain.errors import ConflictError, ValidationError
from m4d.domain.protocol import (
    LinearInstant,
    ProtocolHead,
    ProtocolNode,
    TapeEntry,
    empty_head,
    interpretation_from_mapping,
)

__all__ = ["SqlAlchemyProtocolRepository"]

TICK_INDEX = "uq_protocol_tick_tick"


def _translate_integrity_error(exc: IntegrityError) -> Exception:
    """Map a driver-level constraint violation onto a domain error."""
    detail = str(exc.orig)
    if TICK_INDEX in detail or "uq_protocol_node_tick" in detail:
        return ConflictError("That tick is already on the tape.")
    if "ck_protocol_" in detail:
        return ValidationError("The protocol row violates a database constraint.", detail=detail)
    return exc


def _instant_from_node(row: ProtocolNodeRow) -> LinearInstant | None:
    if row.tick is None or row.wall is None or row.instant_id is None:
        return None
    return LinearInstant(
        tick=row.tick,
        wall=row.wall,
        id=row.instant_id,
        clock_skewed=row.clock_skewed,
    )


def _to_node(row: ProtocolNodeRow, parent_ids: tuple[UUID, ...]) -> ProtocolNode:
    interpretation = interpretation_from_mapping(row.interpretation)
    return ProtocolNode(
        id=row.id,
        kind=row.kind,
        utterance=row.utterance,
        status=row.status,
        parent_ids=parent_ids,
        interpretation=interpretation,
        instant=_instant_from_node(row),
        proposed_at=row.proposed_at,
        committed_at=row.committed_at,
        rejected_at=row.rejected_at,
        rejection=row.rejection,
    )


def _to_tick(row: ProtocolTickRow) -> TapeEntry:
    slugs = row.bound_slugs if isinstance(row.bound_slugs, list) else []
    return TapeEntry(
        instant=LinearInstant(
            tick=row.tick,
            wall=row.wall,
            id=row.id,
            clock_skewed=row.clock_skewed,
        ),
        node_id=row.node_id,
        kind=row.kind,
        utterance=row.utterance,
        bound_slugs=tuple(str(slug) for slug in slugs),
        recorded_at=row.recorded_at,
    )


class SqlAlchemyProtocolRepository:
    """Implements :class:`~m4d.domain.ports.ProtocolRepository` over a session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_head(self, *, for_update: bool = False) -> ProtocolHead:
        """Return the last committed instant, locking the clock row if asked."""
        statement = select(ProtocolClockRow).where(ProtocolClockRow.id == CLOCK_ROW_ID)
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            return empty_head()
        return ProtocolHead(
            tick=int(row.last_tick),
            wall=row.last_wall,
            instant_id=row.last_instant_id,
        )

    async def save_head(self, head: ProtocolHead) -> None:
        """Persist ``head`` as the current clock."""
        row = await self._session.get(ProtocolClockRow, CLOCK_ROW_ID)
        if row is None:
            self._session.add(
                ProtocolClockRow(
                    id=CLOCK_ROW_ID,
                    last_tick=head.tick,
                    last_wall=head.wall,
                    last_instant_id=head.instant_id,
                )
            )
        else:
            row.last_tick = head.tick
            row.last_wall = head.wall
            row.last_instant_id = head.instant_id
        await self._session.flush()

    async def add_node(self, node: ProtocolNode) -> ProtocolNode:
        """Insert ``node`` and its parent edges."""
        row = ProtocolNodeRow(
            id=node.id,
            kind=node.kind,
            utterance=node.utterance,
            status=node.status,
            interpretation=(
                dict(node.interpretation.to_payload()) if node.interpretation is not None else None
            ),
            tick=node.instant.tick if node.instant is not None else None,
            wall=node.instant.wall if node.instant is not None else None,
            instant_id=node.instant.id if node.instant is not None else None,
            clock_skewed=node.instant.clock_skewed if node.instant is not None else False,
            proposed_at=node.proposed_at,
            committed_at=node.committed_at,
            rejected_at=node.rejected_at,
            rejection=node.rejection,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
                for parent_id in node.parent_ids:
                    self._session.add(ProtocolEdgeRow(parent_id=parent_id, child_id=node.id))
                await self._session.flush()
        except IntegrityError as exc:
            raise _translate_integrity_error(exc) from exc
        return _to_node(row, node.parent_ids)

    async def save_node(self, node: ProtocolNode) -> ProtocolNode:
        """Replace the stored row for ``node.id``."""
        row = await self._session.get(ProtocolNodeRow, node.id)
        if row is None:
            return await self.add_node(node)
        row.kind = node.kind
        row.utterance = node.utterance
        row.status = node.status
        row.interpretation = (
            dict(node.interpretation.to_payload()) if node.interpretation is not None else None
        )
        row.tick = node.instant.tick if node.instant is not None else None
        row.wall = node.instant.wall if node.instant is not None else None
        row.instant_id = node.instant.id if node.instant is not None else None
        row.clock_skewed = node.instant.clock_skewed if node.instant is not None else False
        row.committed_at = node.committed_at
        row.rejected_at = node.rejected_at
        row.rejection = node.rejection
        await self._session.flush()
        return _to_node(row, node.parent_ids)

    async def get_node(self, node_id: UUID) -> ProtocolNode | None:
        """Return the node with ``node_id``, or ``None``."""
        row = await self._session.get(ProtocolNodeRow, node_id)
        if row is None:
            return None
        parent_ids = await self._parent_ids_for(node_id)
        return _to_node(row, parent_ids)

    async def list_nodes(self) -> Sequence[ProtocolNode]:
        """Return every node, proposed-at ascending."""
        statement = select(ProtocolNodeRow).order_by(
            ProtocolNodeRow.proposed_at, ProtocolNodeRow.id
        )
        rows = (await self._session.execute(statement)).scalars().all()
        edges = (await self._session.execute(select(ProtocolEdgeRow))).scalars().all()
        parents: dict[UUID, list[UUID]] = {}
        for edge in edges:
            parents.setdefault(edge.child_id, []).append(edge.parent_id)
        return [_to_node(row, tuple(parents.get(row.id, ()))) for row in rows]

    async def add_tick(self, entry: TapeEntry) -> TapeEntry:
        """Append ``entry`` to the tape."""
        row = ProtocolTickRow(
            id=entry.instant.id,
            tick=entry.instant.tick,
            wall=entry.instant.wall,
            clock_skewed=entry.instant.clock_skewed,
            node_id=entry.node_id,
            kind=entry.kind,
            utterance=entry.utterance,
            bound_slugs=list(entry.bound_slugs),
            recorded_at=entry.recorded_at,
        )
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError as exc:
            raise _translate_integrity_error(exc) from exc
        return _to_tick(row)

    async def list_tape(self, *, after_tick: int, limit: int) -> Sequence[TapeEntry]:
        """Return up to ``limit`` ticks strictly after ``after_tick``, oldest first."""
        statement = (
            select(ProtocolTickRow)
            .where(ProtocolTickRow.tick > after_tick)
            .order_by(ProtocolTickRow.tick.asc())
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [_to_tick(row) for row in rows]

    async def _parent_ids_for(self, child_id: UUID) -> tuple[UUID, ...]:
        statement = select(ProtocolEdgeRow.parent_id).where(ProtocolEdgeRow.child_id == child_id)
        rows = (await self._session.execute(statement)).scalars().all()
        return tuple(rows)
