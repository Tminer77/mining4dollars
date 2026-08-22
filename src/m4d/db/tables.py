"""ORM table definitions.

These classes describe *storage*, not the domain. They stay free of behaviour so
that a schema change is never accidentally a business-logic change; translation
between rows and domain objects happens in the repositories.
"""

from __future__ import annotations

import datetime as dt
import enum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from m4d.db.base import Base
from m4d.domain.antivirus import (
    MAX_ERROR_MESSAGE_LENGTH,
    MAX_INDICATOR_LENGTH,
    MAX_RATIONALE_LENGTH,
    MAX_TITLE_LENGTH,
    FindingCategory,
    FindingStatus,
    ScanKind,
    ScanStatus,
)
from m4d.domain.antivirus import (
    MAX_IDEMPOTENCY_KEY_LENGTH as SCAN_IDEMPOTENCY_KEY_LENGTH,
)
from m4d.domain.endpoints import (
    MAX_AGENT_VERSION_LENGTH,
    MAX_HOSTNAME_LENGTH,
    MAX_QUARANTINE_REASON_LENGTH,
    EndpointPlatform,
    EndpointRole,
    EndpointStatus,
)
from m4d.domain.events import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    MAX_KIND_LENGTH,
    MAX_SOURCE_LENGTH,
    EventSeverity,
)
from m4d.domain.optimizers import (
    MAX_IDEMPOTENCY_KEY_LENGTH as PLAN_IDEMPOTENCY_KEY_LENGTH,
)
from m4d.domain.optimizers import (
    MAX_RATIONALE_LENGTH as PLAN_RATIONALE_LENGTH,
)
from m4d.domain.optimizers import (
    MAX_SUMMARY_LENGTH,
    OptimizerCategory,
    PlanStatus,
)

__all__ = [
    "EndpointRow",
    "FindingRow",
    "OptimizationPlanRow",
    "ScanRow",
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


def _str_enum(enum_cls: type[enum.StrEnum], *, name: str, length: int = 32) -> Enum:
    """VARCHAR + CHECK enum, same rationale as ``_SEVERITY_TYPE``."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
        name=name,
        create_constraint=True,
    )


_PLATFORM_TYPE = _str_enum(EndpointPlatform, name="endpoint_platform", length=16)
_ROLE_TYPE = _str_enum(EndpointRole, name="endpoint_role", length=16)
_ENDPOINT_STATUS_TYPE = _str_enum(EndpointStatus, name="endpoint_status", length=16)
_SCAN_KIND_TYPE = _str_enum(ScanKind, name="scan_kind", length=16)
_SCAN_STATUS_TYPE = _str_enum(ScanStatus, name="scan_status", length=16)
_FINDING_CATEGORY_TYPE = _str_enum(FindingCategory, name="finding_category", length=32)
_FINDING_STATUS_TYPE = _str_enum(FindingStatus, name="finding_status", length=16)
_PLAN_CATEGORY_TYPE = _str_enum(OptimizerCategory, name="optimizer_category", length=16)
_PLAN_STATUS_TYPE = _str_enum(PlanStatus, name="plan_status", length=16)


class EndpointRow(Base):
    """Row mapping for the company fleet inventory."""

    __tablename__ = "endpoint"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(MAX_HOSTNAME_LENGTH), nullable=False)
    platform: Mapped[EndpointPlatform] = mapped_column(_PLATFORM_TYPE, nullable=False)
    role: Mapped[EndpointRole] = mapped_column(_ROLE_TYPE, nullable=False)
    status: Mapped[EndpointStatus] = mapped_column(_ENDPOINT_STATUS_TYPE, nullable=False)
    agent_version: Mapped[str | None] = mapped_column(
        String(MAX_AGENT_VERSION_LENGTH), nullable=True
    )
    labels: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    registered_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    quarantine_reason: Mapped[str | None] = mapped_column(
        String(MAX_QUARANTINE_REASON_LENGTH), nullable=True
    )

    __table_args__ = (
        Index("uq_endpoint_hostname", hostname, unique=True),
        Index("ix_endpoint_last_seen_at_id", last_seen_at.desc(), id.desc()),
        Index("ix_endpoint_status_last_seen_at", status, last_seen_at.desc()),
        Index("ix_endpoint_role_last_seen_at", role, last_seen_at.desc()),
        CheckConstraint("length(btrim(hostname)) > 0", name="hostname_not_blank"),
        CheckConstraint(
            "(status <> 'quarantined' AND quarantine_reason IS NULL) OR "
            "(status = 'quarantined' AND quarantine_reason IS NOT NULL)",
            name="quarantine_reason_matches_status",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<EndpointRow id={self.id} hostname={self.hostname!r} status={self.status}>"


class ScanRow(Base):
    """Row mapping for an antivirus scan job."""

    __tablename__ = "scan"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    endpoint_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("endpoint.id"), nullable=False
    )
    kind: Mapped[ScanKind] = mapped_column(_SCAN_KIND_TYPE, nullable=False)
    status: Mapped[ScanStatus] = mapped_column(_SCAN_STATUS_TYPE, nullable=False)
    queued_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    files_examined: Mapped[int | None] = mapped_column(Integer, nullable=True)
    findings_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(
        String(MAX_ERROR_MESSAGE_LENGTH), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(SCAN_IDEMPOTENCY_KEY_LENGTH), nullable=True
    )

    __table_args__ = (
        Index("ix_scan_queued_at_id", queued_at.desc(), id.desc()),
        Index("ix_scan_endpoint_id_queued_at", endpoint_id, queued_at.desc()),
        Index("ix_scan_status_queued_at", status, queued_at.desc()),
        Index(
            "uq_scan_idempotency_key",
            idempotency_key,
            unique=True,
            postgresql_where=idempotency_key.isnot(None),
        ),
        CheckConstraint("findings_count >= 0", name="findings_count_non_negative"),
        CheckConstraint(
            "files_examined IS NULL OR files_examined >= 0", name="files_examined_non_negative"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ScanRow id={self.id} endpoint_id={self.endpoint_id} status={self.status}>"


class FindingRow(Base):
    """Row mapping for a classified detection."""

    __tablename__ = "finding"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    scan_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("scan.id"), nullable=False
    )
    endpoint_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("endpoint.id"), nullable=False
    )
    category: Mapped[FindingCategory] = mapped_column(_FINDING_CATEGORY_TYPE, nullable=False)
    severity: Mapped[EventSeverity] = mapped_column(_SEVERITY_TYPE, nullable=False)
    status: Mapped[FindingStatus] = mapped_column(_FINDING_STATUS_TYPE, nullable=False)
    indicator: Mapped[str] = mapped_column(String(MAX_INDICATOR_LENGTH), nullable=False)
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    ai_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    ai_rationale: Mapped[str] = mapped_column(String(MAX_RATIONALE_LENGTH), nullable=False)
    recorded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(SCAN_IDEMPOTENCY_KEY_LENGTH), nullable=True
    )

    __table_args__ = (
        Index("ix_finding_recorded_at_id", recorded_at.desc(), id.desc()),
        Index("ix_finding_endpoint_id_recorded_at", endpoint_id, recorded_at.desc()),
        Index("ix_finding_scan_id", scan_id),
        Index("ix_finding_status_recorded_at", status, recorded_at.desc()),
        Index(
            "uq_finding_idempotency_key",
            idempotency_key,
            unique=True,
            postgresql_where=idempotency_key.isnot(None),
        ),
        CheckConstraint("length(btrim(indicator)) > 0", name="indicator_not_blank"),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "ai_confidence >= 0 AND ai_confidence <= 1", name="ai_confidence_unit_interval"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FindingRow id={self.id} category={self.category} status={self.status}>"


class OptimizationPlanRow(Base):
    """Row mapping for an optimizer plan. Actions live in JSONB as a unit."""

    __tablename__ = "optimization_plan"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    endpoint_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("endpoint.id"), nullable=False
    )
    category: Mapped[OptimizerCategory] = mapped_column(_PLAN_CATEGORY_TYPE, nullable=False)
    status: Mapped[PlanStatus] = mapped_column(_PLAN_STATUS_TYPE, nullable=False)
    summary: Mapped[str] = mapped_column(String(MAX_SUMMARY_LENGTH), nullable=False)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    ai_rationale: Mapped[str] = mapped_column(String(PLAN_RATIONALE_LENGTH), nullable=False)
    proposed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(PLAN_IDEMPOTENCY_KEY_LENGTH), nullable=True
    )

    __table_args__ = (
        Index("ix_optimization_plan_proposed_at_id", proposed_at.desc(), id.desc()),
        Index("ix_optimization_plan_endpoint_id_proposed_at", endpoint_id, proposed_at.desc()),
        Index("ix_optimization_plan_status_proposed_at", status, proposed_at.desc()),
        Index(
            "uq_optimization_plan_idempotency_key",
            idempotency_key,
            unique=True,
            postgresql_where=idempotency_key.isnot(None),
        ),
        CheckConstraint("length(btrim(summary)) > 0", name="summary_not_blank"),
        CheckConstraint("jsonb_typeof(actions) = 'array'", name="actions_is_array"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<OptimizationPlanRow id={self.id} category={self.category} status={self.status}>"
