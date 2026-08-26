"""Configuration validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from m4d.config import Environment, Settings, get_settings
from tests.conftest import UNIT_TEST_DSN


class TestDatabaseUrl:
    def test_accepts_the_async_driver(self) -> None:
        settings = Settings(database_url=UNIT_TEST_DSN)
        assert settings.database_url.scheme == "postgresql+asyncpg"

    def test_rejects_the_sync_driver(self) -> None:
        """A sync DSN would block the event loop, so it must fail at startup."""
        with pytest.raises(PydanticValidationError, match="postgresql\\+asyncpg"):
            Settings(database_url="postgresql://postgres@localhost:5432/m4d")


class TestCorsOrigins:
    def test_splits_a_comma_separated_string(self) -> None:
        settings = Settings(
            database_url=UNIT_TEST_DSN,
            cors_origins="https://a.example, https://b.example",
        )
        assert settings.cors_origins == ("https://a.example", "https://b.example")

    def test_ignores_blank_entries(self) -> None:
        settings = Settings(database_url=UNIT_TEST_DSN, cors_origins="https://a.example, ,")
        assert settings.cors_origins == ("https://a.example",)

    def test_defaults_to_empty(self) -> None:
        assert Settings(database_url=UNIT_TEST_DSN).cors_origins == ()

    def test_empty_env_string_is_no_origins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Copying .env.example leaves M4D_CORS_ORIGINS= blank; that must boot."""
        from m4d.config import get_settings

        env_file = tmp_path / ".env"
        env_file.write_text(
            f"M4D_DATABASE_URL={UNIT_TEST_DSN}\nM4D_CORS_ORIGINS=\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        get_settings.cache_clear()
        try:
            assert get_settings().cors_origins == ()
        finally:
            get_settings.cache_clear()


class TestEnvironment:
    @pytest.mark.parametrize(
        ("environment", "expected"),
        [
            (Environment.LOCAL, False),
            (Environment.TEST, False),
            (Environment.STAGING, True),
            (Environment.PRODUCTION, True),
        ],
    )
    def test_production_like_classification(self, environment: Environment, expected: bool) -> None:
        assert environment.is_production_like is expected

    def test_debug_is_suppressed_in_production(self) -> None:
        """Debug must not be switchable on in production by a stray env var."""
        settings = Settings(
            database_url=UNIT_TEST_DSN, environment=Environment.PRODUCTION, debug=True
        )
        assert settings.debug is True
        assert settings.is_debug is False

    def test_debug_is_honoured_locally(self) -> None:
        settings = Settings(database_url=UNIT_TEST_DSN, environment=Environment.LOCAL, debug=True)
        assert settings.is_debug is True


class TestBounds:
    @pytest.mark.parametrize("port", [0, 65536, -1])
    def test_rejects_out_of_range_ports(self, port: int) -> None:
        with pytest.raises(PydanticValidationError):
            Settings(database_url=UNIT_TEST_DSN, api_port=port)

    def test_rejects_a_zero_pool(self) -> None:
        with pytest.raises(PydanticValidationError):
            Settings(database_url=UNIT_TEST_DSN, db_pool_size=0)


class TestSettingsObject:
    def test_is_immutable(self) -> None:
        """Settings are frozen so nothing can reconfigure the app at runtime."""
        settings = Settings(database_url=UNIT_TEST_DSN)
        with pytest.raises(PydanticValidationError):
            settings.api_port = 9999  # type: ignore[misc]

    def test_get_settings_is_cached(self) -> None:
        get_settings.cache_clear()
        try:
            assert get_settings() is get_settings()
        finally:
            get_settings.cache_clear()
