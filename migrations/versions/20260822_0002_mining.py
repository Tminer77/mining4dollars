"""Add the mining catalog, fleet, quotes, and assignments.

Revision ID: 0002_mining
Revises: 0001_initial
Created: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_mining"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the mining tables."""
    op.create_table(
        "mining_coin",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(btrim(ticker)) >= 2", name="ticker_not_blank"),
        sa.CheckConstraint("length(btrim(algorithm)) > 0", name="algorithm_not_blank"),
        sa.PrimaryKeyConstraint("id", name="pk_mining_coin"),
        sa.UniqueConstraint("ticker", name="uq_mining_coin_ticker"),
    )
    op.create_index("ix_mining_coin_algorithm", "mining_coin", ["algorithm"])

    op.create_table(
        "mining_pool",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("coin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.String(length=256), nullable=False),
        sa.Column("worker_template", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(btrim(url)) > 0", name="url_not_blank"),
        sa.ForeignKeyConstraint(
            ["coin_id"], ["mining_coin.id"], name="fk_mining_pool_coin_id_mining_coin"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mining_pool"),
        sa.UniqueConstraint("coin_id", "name", name="uq_mining_pool_coin_id_name"),
    )
    op.create_index("ix_mining_pool_coin_id", "mining_pool", ["coin_id"])

    op.create_table(
        "mining_worker",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("hostname", sa.String(length=128), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("power_watts", sa.Numeric(12, 3), nullable=False),
        sa.Column("electricity_usd_per_kwh", sa.Numeric(20, 8), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_algorithm", sa.String(length=32), nullable=True),
        sa.Column("last_hashrate_hps", sa.Numeric(40, 8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        sa.CheckConstraint("power_watts >= 0", name="power_watts_non_negative"),
        sa.CheckConstraint("electricity_usd_per_kwh >= 0", name="electricity_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_mining_worker"),
        sa.UniqueConstraint("name", name="uq_mining_worker_name"),
    )
    op.create_index(
        "ix_mining_worker_created_at_id",
        "mining_worker",
        [sa.text("created_at DESC"), sa.text("id DESC")],
    )

    op.create_table(
        "mining_capability",
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("hashrate_hps", sa.Numeric(40, 8), nullable=False),
        sa.Column("power_watts", sa.Numeric(12, 3), nullable=True),
        sa.CheckConstraint("hashrate_hps > 0", name="capability_hashrate_positive"),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["mining_worker.id"],
            name="fk_mining_capability_worker_id_mining_worker",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("worker_id", "algorithm", name="pk_mining_capability"),
    )

    op.create_table(
        "mining_assignment",
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("coin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pool_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("revenue_usd_per_day", sa.Numeric(20, 8), nullable=False),
        sa.Column("cost_usd_per_day", sa.Numeric(20, 8), nullable=False),
        sa.Column("profit_usd_per_day", sa.Numeric(20, 8), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["mining_worker.id"],
            name="fk_mining_assignment_worker_id_mining_worker",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["coin_id"], ["mining_coin.id"], name="fk_mining_assignment_coin_id_mining_coin"
        ),
        sa.ForeignKeyConstraint(
            ["pool_id"],
            ["mining_pool.id"],
            name="fk_mining_assignment_pool_id_mining_pool",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("worker_id", name="pk_mining_assignment"),
    )

    op.create_table(
        "mining_quote",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("coin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False),
        sa.Column("revenue_usd_per_day", sa.Numeric(20, 8), nullable=False),
        sa.Column("reference_hashrate_hps", sa.Numeric(40, 8), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("quoted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revenue_usd_per_day >= 0", name="revenue_non_negative"),
        sa.CheckConstraint("reference_hashrate_hps > 0", name="reference_hashrate_positive"),
        sa.ForeignKeyConstraint(
            ["coin_id"], ["mining_coin.id"], name="fk_mining_quote_coin_id_mining_coin"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mining_quote"),
    )
    op.create_index(
        "ix_mining_quote_coin_id_quoted_at",
        "mining_quote",
        ["coin_id", sa.text("quoted_at DESC"), sa.text("recorded_at DESC")],
    )


def downgrade() -> None:
    """Drop the mining tables."""
    op.drop_index("ix_mining_quote_coin_id_quoted_at", table_name="mining_quote")
    op.drop_table("mining_quote")
    op.drop_table("mining_assignment")
    op.drop_table("mining_capability")
    op.drop_index("ix_mining_worker_created_at_id", table_name="mining_worker")
    op.drop_table("mining_worker")
    op.drop_index("ix_mining_pool_coin_id", table_name="mining_pool")
    op.drop_table("mining_pool")
    op.drop_index("ix_mining_coin_algorithm", table_name="mining_coin")
    op.drop_table("mining_coin")
