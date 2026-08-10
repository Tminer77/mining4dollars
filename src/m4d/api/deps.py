"""Dependency wiring.

Collaborators are constructed once during startup and stored on the application
state; these providers only read them back. That keeps per-request work to a
minimum and makes overriding a single dependency in a test a one-liner via
``app.dependency_overrides``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from m4d.config import Settings
from m4d.db.engine import Database
from m4d.services.events import EventService
from m4d.services.health import HealthService

__all__ = ["DatabaseDep", "EventServiceDep", "HealthServiceDep", "SettingsDep"]


def get_settings(request: Request) -> Settings:
    """Return the validated settings for this application instance."""
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    """Return the process-wide database handle."""
    database: Database = request.app.state.database
    return database


def get_event_service(request: Request) -> EventService:
    """Return the event use cases."""
    service: EventService = request.app.state.event_service
    return service


def get_health_service(request: Request) -> HealthService:
    """Return the health probes."""
    service: HealthService = request.app.state.health_service
    return service


SettingsDep = Annotated[Settings, Depends(get_settings)]
DatabaseDep = Annotated[Database, Depends(get_database)]
EventServiceDep = Annotated[EventService, Depends(get_event_service)]
HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]
