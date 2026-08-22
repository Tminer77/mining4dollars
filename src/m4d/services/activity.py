"""Helpers shared by Shield use cases: activity emission and write outcomes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from m4d.domain.events import EventSeverity, NewEvent
from m4d.domain.ports import Clock, UnitOfWork

__all__ = ["SHIELD_SOURCE", "WriteResult", "emit"]

SHIELD_SOURCE = "shield"


@dataclass(frozen=True, slots=True)
class WriteResult[T]:
    """The outcome of an idempotent write.

    ``was_created`` lets the API answer ``201`` for a genuine write and ``200``
    for a replay, matching the event ingest contract.
    """

    value: T
    was_created: bool


async def emit(
    uow: UnitOfWork,
    *,
    clock: Clock,
    kind: str,
    payload: Mapping[str, Any],
    severity: EventSeverity = EventSeverity.INFO,
) -> None:
    """Append a Shield activity record inside an already-open unit of work.

    The caller owns the transaction; this never commits. That is what lets a
    quarantine, a finding, and the event that explains them land together.
    """
    event = NewEvent(
        source=SHIELD_SOURCE,
        kind=kind,
        severity=severity,
        payload=dict(payload),
    ).materialise(now=clock.now())
    await uow.events.add(event)
