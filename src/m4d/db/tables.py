"""ORM table definitions.

These classes describe *storage*, not the domain. They stay free of behaviour so
that a schema change is never accidentally a business-logic change; translation
between rows and domain objects happens in the repositories.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, String, text
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

__all__ = ["SystemEventRow"]

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
