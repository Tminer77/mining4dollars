"""Shared test fixtures."""

from __future__ import annotations

import pytest

from m4d.config import Environment, Settings

# The URL every unit test uses. Nothing connects to it; a valid DSN is required
# only because Settings validates the scheme.
UNIT_TEST_DSN = "postgresql+asyncpg://postgres@127.0.0.1:5432/m4d_unit"


@pytest.fixture
def settings() -> Settings:
    """Settings for tests that need one but never touch a database.

    Values are passed explicitly, which takes priority over both the ambient
    environment and any `.env` file, so the suite behaves the same on a
    developer machine as it does in CI.
    """
    return Settings(
        environment=Environment.TEST,
        database_url=UNIT_TEST_DSN,
        log_format="console",
        log_level="WARNING",
    )
