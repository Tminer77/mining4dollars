"""Application configuration.

Configuration is read from the process environment (12-factor). Every value is
validated once at startup so that a misconfigured deployment fails immediately
and loudly rather than at the first request that happens to touch the bad
setting.

Nothing in the codebase reads ``os.environ`` directly; everything goes through
:class:`Settings` so that the full configuration surface is discoverable in one
place and trivially overridable in tests.
"""

from __future__ import annotations

import enum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Environment", "Settings", "get_settings"]


class Environment(enum.StrEnum):
    """Deployment environment.

    Behaviour that must differ between environments keys off this value rather
    than off an ad-hoc ``DEBUG`` flag, so the intent stays explicit.
    """

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"

    @property
    def is_production_like(self) -> bool:
        """True where safety rails should be strict and internals never leak."""
        return self in (Environment.STAGING, Environment.PRODUCTION)


class Settings(BaseSettings):
    """Validated application settings.

    Resolution order: real environment variables, then ``.env``, then the
    defaults declared here.
    """

    model_config = SettingsConfigDict(
        env_prefix="M4D_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # ---- Application -------------------------------------------------------
    app_name: str = "mining4dollars"
    environment: Environment = Environment.LOCAL
    debug: bool = False

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    # ---- HTTP --------------------------------------------------------------
    api_host: str = "0.0.0.0"  # noqa: S104 - binding all interfaces is intended in a container
    api_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    cors_origins: tuple[str, ...] = ()

    # ---- Database ----------------------------------------------------------
    database_url: PostgresDsn = PostgresDsn("postgresql+asyncpg://postgres@127.0.0.1:5432/m4d")
    db_pool_size: Annotated[int, Field(ge=1, le=100)] = 5
    db_max_overflow: Annotated[int, Field(ge=0, le=100)] = 10
    db_echo: bool = False
    db_connect_timeout_seconds: Annotated[float, Field(gt=0)] = 5.0
    db_statement_timeout_ms: Annotated[int, Field(ge=0)] = 10_000

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a comma-separated string, since env vars cannot hold lists."""
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: PostgresDsn) -> PostgresDsn:
        """Reject sync DSNs early.

        A ``postgresql://`` URL silently selects psycopg and then deadlocks the
        event loop. Failing here turns a confusing runtime hang into a clear
        startup error.
        """
        if value.scheme != "postgresql+asyncpg":
            msg = (
                f"database_url must use the 'postgresql+asyncpg' scheme, got "
                f"{value.scheme!r}. The application is async end to end."
            )
            raise ValueError(msg)
        return value

    @property
    def is_debug(self) -> bool:
        """Debug is force-disabled in production-like environments."""
        return self.debug and not self.environment.is_production_like


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that validation happens once. Tests that need different values
    should call ``get_settings.cache_clear()`` or override the FastAPI
    dependency rather than mutating the returned object, which is frozen.
    """
    return Settings()
