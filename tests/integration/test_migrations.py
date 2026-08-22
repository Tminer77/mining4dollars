"""The migrations and the models must not drift apart."""

from __future__ import annotations

from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, inspect, text

from m4d.db.autogenerate import include_object
from m4d.db.base import Base
from m4d.db.engine import Database

pytestmark = pytest.mark.integration


#: The autogenerate operations that constitute real drift between the models and
#: the deployed schema. Index and constraint operations are excluded on purpose:
#: Alembic cannot reflect expression indexes such as ``occurred_at DESC``, and
#: the CHECK backing a non-native Enum is emitted at DDL time and never appears
#: in the metadata, so both produce permanent false positives. Those objects are
#: asserted on directly by the tests below instead.
STRUCTURAL_OPERATIONS = frozenset(
    {
        "add_table",
        "remove_table",
        "add_column",
        "remove_column",
        "modify_type",
        "modify_nullable",
        "modify_default",
        "modify_comment",
    }
)


def _diff(connection: Connection) -> list[Any]:
    """Return what autogenerate would still want to change."""
    context = MigrationContext.configure(
        connection,
        opts={"compare_type": True, "include_object": include_object},
    )
    return list(compare_metadata(context, Base.metadata))


def _operation_name(difference: Any) -> str:
    """Extract the operation name from an autogenerate diff entry.

    Column-level differences arrive as a list of tuples rather than a bare
    tuple, so both shapes have to be unwrapped.
    """
    if isinstance(difference, list):
        return str(difference[0][0]) if difference else ""
    return str(difference[0])


async def test_models_and_migrations_agree(database: Database) -> None:
    """Catches a model edited without a matching revision."""
    async with database.engine.connect() as connection:
        differences = await connection.run_sync(_diff)

    drift = [
        difference
        for difference in differences
        if _operation_name(difference) in STRUCTURAL_OPERATIONS
    ]
    assert drift == [], f"Models have drifted from the migrations: {drift}"


async def test_schema_contains_the_expected_table(database: Database) -> None:
    async with database.engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    assert {
        "system_event",
        "endpoint",
        "scan",
        "finding",
        "optimization_plan",
    } <= set(tables)


async def test_keyset_index_exists(database: Database) -> None:
    """Pagination performance depends on this index; assert it survived."""
    async with database.engine.connect() as connection:
        result = await connection.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'system_event'")
        )
        indexes = {row[0] for row in result}

    assert "ix_system_event_occurred_at_id" in indexes
    assert "uq_system_event_idempotency_key" in indexes


async def test_idempotency_index_is_partial(database: Database) -> None:
    """It must exclude NULLs, or unkeyed events would collide with each other."""
    async with database.engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_system_event_idempotency_key'"
            )
        )
        definition = result.scalar_one()

    assert "WHERE" in definition.upper()
    assert "IS NOT NULL" in definition.upper()


async def test_shield_indexes_exist(database: Database) -> None:
    """Hostname uniqueness and scan de-duplication are load-bearing."""
    async with database.engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename IN ('endpoint', 'scan', 'finding', 'optimization_plan')"
            )
        )
        indexes = {row[0] for row in result}

    assert "uq_endpoint_hostname" in indexes
    assert "uq_scan_idempotency_key" in indexes
    assert "uq_finding_idempotency_key" in indexes
    assert "uq_optimization_plan_idempotency_key" in indexes
    assert "ix_endpoint_last_seen_at_id" in indexes


async def test_severity_is_constrained(database: Database) -> None:
    """The column must reject values the enum does not define."""
    async with database.engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'system_event'::regclass AND contype = 'c'"
            )
        )
        constraints = {row[0] for row in result}

    assert "ck_system_event_event_severity" in constraints
    assert "ck_system_event_recorded_after_occurred" in constraints
