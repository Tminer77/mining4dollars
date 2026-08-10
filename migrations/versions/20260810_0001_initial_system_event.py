"""Create the system_event log.

Revision ID: 0001_initial
Revises:
Created: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the event log table and its indexes."""
    op.create_table(
        "system_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=128), nullable=False),
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
                # Without this the column is an unconstrained VARCHAR; the
                # CHECK is what stops a bad value arriving from outside the app.
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.CheckConstraint("length(btrim(source)) > 0", name="source_not_blank"),
        sa.CheckConstraint("length(btrim(kind)) > 0", name="kind_not_blank"),
        sa.CheckConstraint("recorded_at >= occurred_at", name="recorded_after_occurred"),
        sa.PrimaryKeyConstraint("id", name="pk_system_event"),
    )

    # Mirrors the default ORDER BY exactly, so a page is an index range scan
    # with no sort step.
    op.create_index(
        "ix_system_event_occurred_at_id",
        "system_event",
        [sa.text("occurred_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_system_event_source_occurred_at",
        "system_event",
        ["source", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_system_event_kind_occurred_at",
        "system_event",
        ["kind", sa.text("occurred_at DESC")],
    )
    # Partial: only events that opted into de-duplication participate, so the
    # many NULL keys never collide with one another.
    op.create_index(
        "uq_system_event_idempotency_key",
        "system_event",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop the event log table."""
    op.drop_index("uq_system_event_idempotency_key", table_name="system_event")
    op.drop_index("ix_system_event_kind_occurred_at", table_name="system_event")
    op.drop_index("ix_system_event_source_occurred_at", table_name="system_event")
    op.drop_index("ix_system_event_occurred_at_id", table_name="system_event")
    op.drop_table("system_event")
