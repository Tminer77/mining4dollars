"""Application composition.

This module is the composition root: the one place where concrete
implementations are chosen and wired together. Everything else depends on
abstractions, which is what keeps the layers swappable and testable.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from m4d import __version__
from m4d.api.errors import install_error_handlers
from m4d.api.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from m4d.api.routes import events as events_routes
from m4d.api.routes import health as health_routes
from m4d.config import Settings, get_settings
from m4d.db.engine import Database
from m4d.db.uow import SqlAlchemyUnitOfWork
from m4d.ipad.routes import mount_ipad
from m4d.observability.logging import setup_logging
from m4d.services.clock import SystemClock
from m4d.services.events import EventService
from m4d.services.health import HealthService

__all__ = ["create_app"]

logger = logging.getLogger(__name__)

DESCRIPTION = """
Foundation services for the mining4dollars platform.

Every error is returned as an RFC 9457 problem document, and every response
carries an `X-Request-ID` header that correlates it with server logs.
"""


def _build_lifespan(
    settings: Settings,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Create the startup/shutdown handler bound to ``settings``."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Constructed here rather than at import time so that importing the
        # module never opens a socket, and so tests can build an app per case.
        database = Database(settings)

        app.state.settings = settings
        app.state.database = database
        app.state.event_service = EventService(
            uow_factory=lambda: SqlAlchemyUnitOfWork(database.session_factory),
            clock=SystemClock(),
        )
        app.state.health_service = HealthService(database)

        logger.info(
            "application started",
            extra={"version": __version__, "environment": settings.environment.value},
        )
        try:
            yield
        finally:
            await database.dispose()
            logger.info("application stopped")

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fully wired application.

    Args:
        settings: Configuration to use. Defaults to the process settings;
            tests pass their own instead of mutating the environment.
    """
    settings = settings or get_settings()
    setup_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=DESCRIPTION,
        lifespan=_build_lifespan(settings),
        # Interactive docs describe internals, so they are served only where
        # that is appropriate.
        docs_url=None if settings.environment.is_production_like else "/docs",
        redoc_url=None,
        openapi_url=None if settings.environment.is_production_like else "/openapi.json",
    )

    # Settings are also placed here eagerly: dependencies that read app.state
    # must work before lifespan runs, as they do when a test builds the app
    # without entering its lifespan.
    app.state.settings = settings

    app.add_middleware(RequestContextMiddleware)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[REQUEST_ID_HEADER],
        )

    install_error_handlers(app, settings)

    app.include_router(health_routes.router)
    app.include_router(events_routes.router)
    mount_ipad(app)

    return app
