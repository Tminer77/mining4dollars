"""Integration fixtures.

These tests run against a real PostgreSQL database, never SQLite. The schema
depends on JSONB, partial unique indexes, and row-value comparison — none of
which SQLite models — so a suite that passed on SQLite would be evidence about a
database we do not deploy.

The schema is built by running the Alembic migrations, not
``metadata.create_all``. That way the migration path itself is covered on every
run, rather than only the models it is supposed to produce.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from m4d.api.app import create_app
from m4d.config import Environment, Settings
from m4d.db.engine import Database
from m4d.db.uow import SqlAlchemyUnitOfWork

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Every table the suite truncates between tests. `alembic_version` is
#: deliberately excluded: wiping it would undo the migration state.
MANAGED_TABLES = (
    "mining_assignment",
    "mining_quote",
    "mining_capability",
    "mining_pool",
    "mining_worker",
    "mining_coin",
    "system_event",
)


@pytest.fixture(scope="session")
def integration_settings() -> Settings:
    """Settings pointed at the test database, or skip the suite."""
    url = os.environ.get("M4D_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "Set M4D_TEST_DATABASE_URL to run integration tests, e.g. "
            "postgresql+asyncpg://postgres@127.0.0.1:5432/m4d_test"
        )
    return Settings(
        environment=Environment.TEST,
        database_url=url,
        log_format="console",
        log_level="WARNING",
    )


@pytest.fixture(scope="session", autouse=True)
def migrated_database(integration_settings: Settings) -> Iterator[None]:
    """Bring the test database to head before any test runs.

    Synchronous on purpose: Alembic's ``env.py`` calls :func:`asyncio.run`,
    which cannot be nested inside an already-running event loop.

    Downgrading to base first guarantees a clean, known starting point and
    incidentally proves the downgrade path still works.
    """
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # How `-x db_url=...` is supplied programmatically; keeps the test database
    # out of the process environment.
    config.cmd_opts = argparse.Namespace(x=[f"db_url={integration_settings.database_url}"])

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield


@pytest.fixture
async def database(integration_settings: Settings) -> AsyncIterator[Database]:
    """A database handle scoped to one test.

    Function-scoped so that every test owns its own event loop and connection
    pool; a shared async engine across differently-scoped loops is the classic
    source of "attached to a different loop" failures.
    """
    handle = Database(integration_settings)
    try:
        yield handle
    finally:
        await handle.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(database: Database) -> None:
    """Empty the managed tables before each test."""
    async with database.engine.begin() as connection:
        await connection.execute(
            text(f"TRUNCATE {', '.join(MANAGED_TABLES)} RESTART IDENTITY CASCADE")
        )


@pytest.fixture
async def uow(database: Database) -> SqlAlchemyUnitOfWork:
    """A unit of work bound to the test database."""
    return SqlAlchemyUnitOfWork(database.session_factory)


@pytest.fixture
async def client(integration_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client wired to the real application.

    The lifespan context is entered explicitly because the ASGI transport does
    not run it, and without it the application state the routes depend on would
    never be built.
    """
    app = create_app(integration_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as session:
            yield session
