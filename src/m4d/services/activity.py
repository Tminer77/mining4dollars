"""Append a system event inside an already-open unit of work.

Mining state changes and the activity record that describes them must commit
together. This helper is the only way services write events, so they cannot
forget the shared transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from m4d.domain.events import EventSeverity, NewEvent, SystemEvent
from m4d.domain.ports import Clock, UnitOfWork

__all__ = ["record_activity"]


async def record_activity(
    uow: UnitOfWork,
    clock: Clock,
    *,
    kind: str,
    payload: Mapping[str, Any] | None = None,
    source: str = "mining",
    severity: EventSeverity = EventSeverity.INFO,
) -> SystemEvent:
    """Record ``kind`` on ``uow.events`` stamped by ``clock``."""
    event = NewEvent(
        source=source,
        kind=kind,
        severity=severity,
        payload=dict(payload or {}),
    ).materialise(now=clock.now())
    return await uow.events.add(event)
