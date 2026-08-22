"""ORM table definitions.

These classes describe *storage*, not the domain. They stay free of behaviour so
that a schema change is never accidentally a business-logic change; translation
between rows and domain objects happens in the repositories.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from m4d.db.base import Base
from m4d.domain.events import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_KIND_LENGTH,
    MAX_SOURCE_LENGTH,
    EventSeverity,
)
from m4d.domain.glossary import (
    MAX_DEFINITION_LENGTH,
    MAX_NAME_LENGTH,
    MAX_SLUG_LENGTH,
    TermStatus,
)
from m4d.domain.protocol import Kind, NodeStatus

__all__ = [
    "CLOCK_ROW_ID",
    "GlossaryTermRow",
    "ProtocolClockRow",
    "ProtocolEdgeRow",
    "ProtocolNodeRow",
    "ProtocolTickRow",
    "SystemEventRow",
]

# Stored as VARCHAR + CHECK rather than a native PostgreSQL ENUM. Adding a value
# to a native enum requires a migration that cannot run inside a transaction on
# older servers; a CHECK constraint is edited with an ordinary ALTER.
_SEVERITY_TYPE = Enum(
    EventSeverity,
    native_enum=False,
    length=16,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
    name="event_severity",
    # SQLAlchemy 2.0 defaults this to False, which would leave the column an
    # unconstrained VARCHAR and let any string through from outside the app.
    create_constraint=True,
)


class SystemEventRow(Base):
    """Row mapping for the append-only system event log."""

    __tablename__ = "system_event"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)

    source: Mapped[str] = mapped_column(String(MAX_SOURCE_LENGTH), nullable=False)
    kind: Mapped[str] = mapped_column(String(MAX_KIND_LENGTH), nullable=False)
    severity: Mapped[EventSeverity] = mapped_column(_SEVERITY_TYPE, nullable=False)

    # JSONB rather than JSON: it is queryable and indexable, and it normalises
    # key order so equal payloads compare equal.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    idempotency_key: Mapped[str | None] = mapped_column(
        String(MAX_IDEMPOTENCY_KEY_LENGTH), nullable=True
    )

    __table_args__ = (
        # Serves the default listing and its keyset pagination in one index.
        # Column order and direction mirror the ORDER BY exactly, which is what
        # lets PostgreSQL satisfy the page without a sort.
        Index(
            "ix_system_event_occurred_at_id",
            occurred_at.desc(),
            id.desc(),
        ),
        # Narrowing by producer is the most common filter; occurred_at trails so
        # the same index still answers "recent events from this source".
        Index("ix_system_event_source_occurred_at", source, occurred_at.desc()),
        Index("ix_system_event_kind_occurred_at", kind, occurred_at.desc()),
        # Partial unique index: de-duplication applies only to producers that
        # opted in by sending a key. NULLs are excluded so unkeyed events are
        # never in conflict with one another.
        Index(
            "uq_system_event_idempotency_key",
            idempotency_key,
            unique=True,
            postgresql_where=idempotency_key.isnot(None),
        ),
        CheckConstraint("length(btrim(source)) > 0", name="source_not_blank"),
        CheckConstraint("length(btrim(kind)) > 0", name="kind_not_blank"),
        # The domain guarantees this, but the database is the last line of
        # defence against a bad backfill run outside the application.
        CheckConstraint("recorded_at >= occurred_at", name="recorded_after_occurred"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SystemEventRow id={self.id} source={self.source!r} kind={self.kind!r}>"


_TERM_STATUS_TYPE = Enum(
    TermStatus,
    native_enum=False,
    length=16,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
    name="term_status",
    create_constraint=True,
)

_NODE_STATUS_TYPE = Enum(
    NodeStatus,
    native_enum=False,
    length=16,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
    name="node_status",
    create_constraint=True,
)

_NODE_KIND_TYPE = Enum(
    Kind,
    native_enum=False,
    length=16,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
    name="protocol_node_kind",
    create_constraint=True,
)

_TICK_KIND_TYPE = Enum(
    Kind,
    native_enum=False,
    length=16,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
    name="protocol_tick_kind",
    create_constraint=True,
)

CLOCK_ROW_ID = 1


class GlossaryTermRow(Base):
    """Row mapping for one canonical glossary term."""

    __tablename__ = "glossary_term"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    slug: Mapped[str] = mapped_column(String(MAX_SLUG_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(MAX_NAME_LENGTH), nullable=False)
    definition: Mapped[str] = mapped_column(String(MAX_DEFINITION_LENGTH), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[TermStatus] = mapped_column(_TERM_STATUS_TYPE, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index("uq_glossary_term_slug", slug, unique=True),
        CheckConstraint("length(btrim(slug)) > 0", name="slug_not_blank"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("length(btrim(definition)) > 0", name="definition_not_blank"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GlossaryTermRow slug={self.slug!r} status={self.status}>"


class ProtocolClockRow(Base):
    """Singleton row holding the last committed instant.

    One row, locked with ``FOR UPDATE`` around every commit, is what makes
    concurrent commits serialise onto consecutive ticks instead of colliding.
    """

    __tablename__ = "protocol_clock"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last_tick: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("-1"))
    last_wall: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_instant_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)

    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ProtocolClockRow tick={self.last_tick}>"


class ProtocolNodeRow(Base):
    """Row mapping for one Tree of Claude vertex."""

    __tablename__ = "protocol_node"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    kind: Mapped[Kind] = mapped_column(_NODE_KIND_TYPE, nullable=False)
    utterance: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[NodeStatus] = mapped_column(_NODE_STATUS_TYPE, nullable=False)
    interpretation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tick: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    wall: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    instant_id: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    clock_skewed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    proposed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_protocol_node_status_proposed_at", status, proposed_at),
        Index("uq_protocol_node_tick", tick, unique=True, postgresql_where=tick.isnot(None)),
        Index(
            "uq_protocol_node_genesis",
            kind,
            unique=True,
            postgresql_where=text("kind = 'genesis'"),
        ),
        CheckConstraint("length(btrim(utterance)) > 0", name="utterance_not_blank"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ProtocolNodeRow id={self.id} kind={self.kind} status={self.status}>"


class ProtocolEdgeRow(Base):
    """Directed edge: parent must commit before child."""

    __tablename__ = "protocol_edge"

    parent_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("protocol_node.id", ondelete="CASCADE"),
        primary_key=True,
    )
    child_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("protocol_node.id", ondelete="CASCADE"),
        primary_key=True,
    )

    __table_args__ = (
        Index("ix_protocol_edge_child_id", child_id),
        CheckConstraint("parent_id <> child_id", name="no_self_parent"),
    )


class ProtocolTickRow(Base):
    """One committed instant on the linear tape."""

    __tablename__ = "protocol_tick"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tick: Mapped[int] = mapped_column(BigInteger, nullable=False)
    wall: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    clock_skewed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    node_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("protocol_node.id"),
        nullable=False,
    )
    kind: Mapped[Kind] = mapped_column(_TICK_KIND_TYPE, nullable=False)
    utterance: Mapped[str] = mapped_column(Text, nullable=False)
    bound_slugs: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    recorded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("uq_protocol_tick_tick", tick, unique=True),
        CheckConstraint("tick >= 0", name="tick_non_negative"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ProtocolTickRow tick={self.tick} node_id={self.node_id}>"
