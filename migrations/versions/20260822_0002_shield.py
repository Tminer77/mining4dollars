"""Add Shield fleet, scan, finding, and optimizer plan tables.

Revision ID: 0002_shield
Revises: 0001_initial
Created: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_shield"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str, name: str, length: int = 16) -> sa.Enum:
    """VARCHAR + CHECK enum, matching the models."""
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        length=length,
        create_constraint=True,
    )


def upgrade() -> None:
    """Create the Shield control-plane tables."""
    op.create_table(
        "endpoint",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column(
            "platform",
            _enum("linux", "windows", "macos", name="endpoint_platform"),
            nullable=False,
        ),
        sa.Column(
            "role",
            _enum("miner", "workstation", "gateway", "server", name="endpoint_role"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("online", "offline", "quarantined", "retiring", name="endpoint_status"),
            nullable=False,
        ),
        sa.Column("agent_version", sa.String(length=64), nullable=True),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("quarantine_reason", sa.String(length=512), nullable=True),
        sa.CheckConstraint("length(btrim(hostname)) > 0", name="hostname_not_blank"),
        sa.CheckConstraint(
            "(status <> 'quarantined' AND quarantine_reason IS NULL) OR "
            "(status = 'quarantined' AND quarantine_reason IS NOT NULL)",
            name="quarantine_reason_matches_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_endpoint"),
    )
    op.create_index("uq_endpoint_hostname", "endpoint", ["hostname"], unique=True)
    op.create_index(
        "ix_endpoint_last_seen_at_id",
        "endpoint",
        [sa.text("last_seen_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_endpoint_status_last_seen_at",
        "endpoint",
        ["status", sa.text("last_seen_at DESC")],
    )
    op.create_index(
        "ix_endpoint_role_last_seen_at",
        "endpoint",
        ["role", sa.text("last_seen_at DESC")],
    )

    op.create_table(
        "scan",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            _enum("quick", "full", "custom", name="scan_kind"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("queued", "running", "completed", "failed", name="scan_status"),
            nullable=False,
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("files_examined", sa.Integer(), nullable=True),
        sa.Column("findings_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.CheckConstraint("findings_count >= 0", name="findings_count_non_negative"),
        sa.CheckConstraint(
            "files_examined IS NULL OR files_examined >= 0",
            name="files_examined_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"], ["endpoint.id"], name="fk_scan_endpoint_id_endpoint"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scan"),
    )
    op.create_index(
        "ix_scan_queued_at_id",
        "scan",
        [sa.text("queued_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_scan_endpoint_id_queued_at",
        "scan",
        ["endpoint_id", sa.text("queued_at DESC")],
    )
    op.create_index("ix_scan_status_queued_at", "scan", ["status", sa.text("queued_at DESC")])
    op.create_index(
        "uq_scan_idempotency_key",
        "scan",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "finding",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "category",
            _enum(
                "malware",
                "pua",
                "suspicious",
                "vulnerability",
                "misconfiguration",
                name="finding_category",
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum(
                "debug",
                "info",
                "warning",
                "error",
                "critical",
                name="event_severity",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "open",
                "acknowledged",
                "quarantined",
                "resolved",
                "false_positive",
                name="finding_status",
            ),
            nullable=False,
        ),
        sa.Column("indicator", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("detail", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("ai_confidence", sa.Float(), nullable=False),
        sa.Column("ai_rationale", sa.String(length=2000), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.CheckConstraint("length(btrim(indicator)) > 0", name="indicator_not_blank"),
        sa.CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        sa.CheckConstraint(
            "ai_confidence >= 0 AND ai_confidence <= 1",
            name="ai_confidence_unit_interval",
        ),
        sa.ForeignKeyConstraint(["scan_id"], ["scan.id"], name="fk_finding_scan_id_scan"),
        sa.ForeignKeyConstraint(
            ["endpoint_id"], ["endpoint.id"], name="fk_finding_endpoint_id_endpoint"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_finding"),
    )
    op.create_index(
        "ix_finding_recorded_at_id",
        "finding",
        [sa.text("recorded_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_finding_endpoint_id_recorded_at",
        "finding",
        ["endpoint_id", sa.text("recorded_at DESC")],
    )
    op.create_index("ix_finding_scan_id", "finding", ["scan_id"])
    op.create_index(
        "ix_finding_status_recorded_at",
        "finding",
        ["status", sa.text("recorded_at DESC")],
    )
    op.create_index(
        "uq_finding_idempotency_key",
        "finding",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "optimization_plan",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "category",
            _enum(
                "performance",
                "security",
                "thermal",
                "resource",
                name="optimizer_category",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("proposed", "accepted", "applied", "rejected", name="plan_status"),
            nullable=False,
        ),
        sa.Column("summary", sa.String(length=512), nullable=False),
        sa.Column(
            "actions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("ai_rationale", sa.String(length=2000), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.CheckConstraint("length(btrim(summary)) > 0", name="summary_not_blank"),
        sa.CheckConstraint("jsonb_typeof(actions) = 'array'", name="actions_is_array"),
        sa.ForeignKeyConstraint(
            ["endpoint_id"], ["endpoint.id"], name="fk_optimization_plan_endpoint_id_endpoint"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_optimization_plan"),
    )
    op.create_index(
        "ix_optimization_plan_proposed_at_id",
        "optimization_plan",
        [sa.text("proposed_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_optimization_plan_endpoint_id_proposed_at",
        "optimization_plan",
        ["endpoint_id", sa.text("proposed_at DESC")],
    )
    op.create_index(
        "ix_optimization_plan_status_proposed_at",
        "optimization_plan",
        ["status", sa.text("proposed_at DESC")],
    )
    op.create_index(
        "uq_optimization_plan_idempotency_key",
        "optimization_plan",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop the Shield tables, scans and findings first because of FKs."""
    op.drop_index("uq_optimization_plan_idempotency_key", table_name="optimization_plan")
    op.drop_index("ix_optimization_plan_status_proposed_at", table_name="optimization_plan")
    op.drop_index("ix_optimization_plan_endpoint_id_proposed_at", table_name="optimization_plan")
    op.drop_index("ix_optimization_plan_proposed_at_id", table_name="optimization_plan")
    op.drop_table("optimization_plan")

    op.drop_index("uq_finding_idempotency_key", table_name="finding")
    op.drop_index("ix_finding_status_recorded_at", table_name="finding")
    op.drop_index("ix_finding_scan_id", table_name="finding")
    op.drop_index("ix_finding_endpoint_id_recorded_at", table_name="finding")
    op.drop_index("ix_finding_recorded_at_id", table_name="finding")
    op.drop_table("finding")

    op.drop_index("uq_scan_idempotency_key", table_name="scan")
    op.drop_index("ix_scan_status_queued_at", table_name="scan")
    op.drop_index("ix_scan_endpoint_id_queued_at", table_name="scan")
    op.drop_index("ix_scan_queued_at_id", table_name="scan")
    op.drop_table("scan")

    op.drop_index("ix_endpoint_role_last_seen_at", table_name="endpoint")
    op.drop_index("ix_endpoint_status_last_seen_at", table_name="endpoint")
    op.drop_index("ix_endpoint_last_seen_at_id", table_name="endpoint")
    op.drop_index("uq_endpoint_hostname", table_name="endpoint")
    op.drop_table("endpoint")
