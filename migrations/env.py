"""Alembic environment.

The database URL comes from application settings rather than ``alembic.ini`` so
that a migration run and the service itself can never point at different
databases. An explicit override is still available for one-off targets::

    alembic -x db_url=postgresql+asyncpg://... upgrade head
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from m4d.config import get_settings
from m4d.db.autogenerate import include_object
from m4d.db.base import Base

# Importing the table modules is what registers them on the metadata; without
# this, autogenerate would confidently propose dropping every table.
from m4d.db import tables  # noqa: F401  isort:skip

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """Resolve the URL to migrate."""
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    return override or str(get_settings().database_url)


def _configure(connection: Connection) -> None:
    """Apply the shared migration context options."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Without these, a changed column type or default is silently missed by
        # autogenerate, and the schema drifts away from the models.
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
        include_schemas=False,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting.

    Used to hand a reviewable script to a DBA for a gated production change.
    """
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    """Run migrations on an established connection."""
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect and run migrations."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        # A migration process is short-lived and single-connection; pooling
        # would only leave a connection open after the work is done.
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
