"""ORM table definitions.

These classes describe *storage*, not the domain. They stay free of behaviour so
that a schema change is never accidentally a business-logic change; translation
between rows and domain objects happens in the repositories.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
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

__all__ = [
    "MiningAssignmentRow",
    "MiningCapabilityRow",
    "MiningCoinRow",
    "MiningPoolRow",
    "MiningQuoteRow",
    "MiningWorkerRow",
    "SystemEventRow",
]

_MONEY = Numeric(20, 8)
_HASHRATE = Numeric(40, 8)
_WATTS = Numeric(12, 3)

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


class MiningCoinRow(Base):
    """A cryptocurrency the fleet may mine."""

    __tablename__ = "mining_coin"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("ticker", name="uq_mining_coin_ticker"),
        CheckConstraint("length(btrim(ticker)) >= 2", name="ticker_not_blank"),
        CheckConstraint("length(btrim(algorithm)) > 0", name="algorithm_not_blank"),
        Index("ix_mining_coin_algorithm", "algorithm"),
    )


class MiningPoolRow(Base):
    """A stratum endpoint that accepts shares for a coin."""

    __tablename__ = "mining_pool"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    coin_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("mining_coin.id"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(256), nullable=False)
    worker_template: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("coin_id", "name", name="uq_mining_pool_coin_id_name"),
        CheckConstraint("length(btrim(url)) > 0", name="url_not_blank"),
        Index("ix_mining_pool_coin_id", "coin_id"),
    )


class MiningWorkerRow(Base):
    """A mining rig enrolled in the fleet."""

    __tablename__ = "mining_worker"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    power_watts: Mapped[Decimal] = mapped_column(_WATTS, nullable=False)
    electricity_usd_per_kwh: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_algorithm: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_hashrate_hps: Mapped[Decimal | None] = mapped_column(_HASHRATE, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("name", name="uq_mining_worker_name"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("power_watts >= 0", name="power_watts_non_negative"),
        CheckConstraint("electricity_usd_per_kwh >= 0", name="electricity_non_negative"),
        Index("ix_mining_worker_created_at_id", created_at.desc(), id.desc()),
    )


class MiningCapabilityRow(Base):
    """A benchmarked algorithm this worker can run."""

    __tablename__ = "mining_capability"

    worker_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("mining_worker.id", ondelete="CASCADE"), primary_key=True
    )
    algorithm: Mapped[str] = mapped_column(String(32), primary_key=True)
    hashrate_hps: Mapped[Decimal] = mapped_column(_HASHRATE, nullable=False)
    power_watts: Mapped[Decimal | None] = mapped_column(_WATTS, nullable=True)

    __table_args__ = (CheckConstraint("hashrate_hps > 0", name="capability_hashrate_positive"),)


class MiningAssignmentRow(Base):
    """The coin a worker is currently pointed at."""

    __tablename__ = "mining_assignment"

    worker_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("mining_worker.id", ondelete="CASCADE"), primary_key=True
    )
    coin_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("mining_coin.id"), nullable=False
    )
    pool_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("mining_pool.id", ondelete="SET NULL"), nullable=True
    )
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    revenue_usd_per_day: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    cost_usd_per_day: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    profit_usd_per_day: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    assigned_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)


class MiningQuoteRow(Base):
    """One observation of a coin's estimated 24-hour gross revenue."""

    __tablename__ = "mining_quote"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    coin_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("mining_coin.id"), nullable=False
    )
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    revenue_usd_per_day: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    reference_hashrate_hps: Mapped[Decimal] = mapped_column(_HASHRATE, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    quoted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("revenue_usd_per_day >= 0", name="revenue_non_negative"),
        CheckConstraint("reference_hashrate_hps > 0", name="reference_hashrate_positive"),
        Index(
            "ix_mining_quote_coin_id_quoted_at",
            coin_id,
            quoted_at.desc(),
            recorded_at.desc(),
        ),
    )
