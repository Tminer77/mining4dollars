"""Create the linear timestamp protocol: glossary, clock, tree, and tape.

Revision ID: 0002_protocol
Revises: 0001_initial
Created: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_protocol"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create glossary, clock, tree, and tape tables."""
    op.create_table(
        "glossary_term",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("definition", sa.String(length=2000), nullable=False),
        sa.Column(
            "aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "deprecated",
                name="term_status",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("length(btrim(slug)) > 0", name="slug_not_blank"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        sa.CheckConstraint("length(btrim(definition)) > 0", name="definition_not_blank"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_glossary_term"),
    )
    op.create_index("uq_glossary_term_slug", "glossary_term", ["slug"], unique=True)

    op.create_table(
        "protocol_clock",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("last_tick", sa.BigInteger(), server_default=sa.text("-1"), nullable=False),
        sa.Column("last_wall", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_instant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.PrimaryKeyConstraint("id", name="pk_protocol_clock"),
    )
    # The singleton row is inserted here so every commit can ``FOR UPDATE`` it.
    # An empty table would mean two concurrent first-commits lock nothing.
    op.execute(sa.text("INSERT INTO protocol_clock (id, last_tick) VALUES (1, -1)"))

    op.create_table(
        "protocol_node",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "genesis",
                "act",
                "verify",
                name="protocol_node_kind",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("utterance", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "proposed",
                "committed",
                "rejected",
                name="node_status",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("interpretation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tick", sa.BigInteger(), nullable=True),
        sa.Column("wall", sa.DateTime(timezone=True), nullable=True),
        sa.Column("instant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("clock_skewed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection", sa.Text(), nullable=True),
        sa.CheckConstraint("length(btrim(utterance)) > 0", name="utterance_not_blank"),
        sa.PrimaryKeyConstraint("id", name="pk_protocol_node"),
    )
    op.create_index(
        "ix_protocol_node_status_proposed_at",
        "protocol_node",
        ["status", "proposed_at"],
    )
    op.create_index(
        "uq_protocol_node_tick",
        "protocol_node",
        ["tick"],
        unique=True,
        postgresql_where=sa.text("tick IS NOT NULL"),
    )
    op.create_index(
        "uq_protocol_node_genesis",
        "protocol_node",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("kind = 'genesis'"),
    )

    op.create_table(
        "protocol_edge",
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("child_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint("parent_id <> child_id", name="no_self_parent"),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["protocol_node.id"],
            name="fk_protocol_edge_parent_id_protocol_node",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["child_id"],
            ["protocol_node.id"],
            name="fk_protocol_edge_child_id_protocol_node",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("parent_id", "child_id", name="pk_protocol_edge"),
    )
    op.create_index("ix_protocol_edge_child_id", "protocol_edge", ["child_id"])

    op.create_table(
        "protocol_tick",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tick", sa.BigInteger(), nullable=False),
        sa.Column("wall", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clock_skewed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "genesis",
                "act",
                "verify",
                name="protocol_tick_kind",
                native_enum=False,
                length=16,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("utterance", sa.Text(), nullable=False),
        sa.Column(
            "bound_slugs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("tick >= 0", name="tick_non_negative"),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["protocol_node.id"],
            name="fk_protocol_tick_node_id_protocol_node",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_protocol_tick"),
    )
    op.create_index("uq_protocol_tick_tick", "protocol_tick", ["tick"], unique=True)


def downgrade() -> None:
    """Drop the protocol tables."""
    op.drop_index("uq_protocol_tick_tick", table_name="protocol_tick")
    op.drop_table("protocol_tick")
    op.drop_index("ix_protocol_edge_child_id", table_name="protocol_edge")
    op.drop_table("protocol_edge")
    op.drop_index("uq_protocol_node_genesis", table_name="protocol_node")
    op.drop_index("uq_protocol_node_tick", table_name="protocol_node")
    op.drop_index("ix_protocol_node_status_proposed_at", table_name="protocol_node")
    op.drop_table("protocol_node")
    op.drop_table("protocol_clock")
    op.drop_index("uq_glossary_term_slug", table_name="glossary_term")
    op.drop_table("glossary_term")
